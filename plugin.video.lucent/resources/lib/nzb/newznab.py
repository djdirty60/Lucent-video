# -*- coding: utf-8 -*-
"""Your indexers (the Newznab API that almost every Usenet indexer speaks): search a film or an episode, get the NZB.

An indexer's links carry your API key. Lucent never keeps a link with the key in it: the key is taken out before a
result is stored (in the source list, the 'ready for tonight' file or the cache) and only put back at the moment the
NZB is fetched. Errors and log lines never show an address or a key.
"""
import gzip
import hashlib
import re
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import util

NS = "{http://www.newznab.com/DTD/2010/feeds/attributes/}"
KEEP = 3 * 3600                         # a search answer is reused for three hours
SLOTS = 3
KEY_PARAMS = ("apikey", "r")            # Newznab uses apikey=; nZEDb-style sites put the key in r=


class IndexerError(Exception):
    """The indexer said no (wrong key, limit reached) or could not be reached."""


def fetch(url, timeout=20):
    """The raw answer of an address. The error never holds the address (it carries the key)."""
    request = urllib.request.Request(url, headers={"User-Agent": "Lucent-Video", "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        raise IndexerError("HTTP %d" % error.code) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise IndexerError("unreachable") from None
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


def indexers():
    """[(slot, base address, key)] of the filled-in indexers. The address may be the site or its /api."""
    out = []
    for slot in range(1, SLOTS + 1):
        base = util.setting("nzb_ix%d_url" % slot).strip().rstrip("/")
        key = util.setting("nzb_ix%d_key" % slot).strip()
        if base and key:
            if not base.startswith(("http://", "https://")):
                base = "https://" + base
            if not base.endswith("/api"):
                base += "/api"
            out.append((slot, base, key))
    return out


def configured():
    return bool(indexers())


def _get(base, key, params, timeout=20):
    body = fetch(base + "?" + urllib.parse.urlencode(dict(params, apikey=key, o="xml")), timeout)
    return body.replace(key.encode("utf-8"), b"[key]") if key else body


def _from_json(body):
    """Some indexers answer JSON even when asked for XML (nZEDb style): turn it into the same XML tree."""
    import json
    data = json.loads(body.decode("utf-8", "replace"))
    if isinstance(data, dict) and data.get("error"):
        error = data["error"] if isinstance(data["error"], dict) else {"code": str(data["error"])}
        attrs = error.get("@attributes") or error
        root = ET.Element("error")
        root.set("code", str(attrs.get("code") or "?"))
        return root
    channel = (data.get("channel") if isinstance(data, dict) else None) or data
    items = channel.get("item") if isinstance(channel, dict) else channel
    if isinstance(items, dict):
        items = [items]
    root = ET.Element("rss")
    holder = ET.SubElement(root, "channel")
    for raw in items or []:
        if not isinstance(raw, dict):
            continue
        item = ET.SubElement(holder, "item")
        ET.SubElement(item, "title").text = str(raw.get("title") or "")
        ET.SubElement(item, "link").text = str(raw.get("link") or "")
        enclosure = raw.get("enclosure") or {}
        enclosure = enclosure.get("@attributes") or enclosure
        if enclosure.get("url"):
            ET.SubElement(item, "enclosure", url=str(enclosure["url"]), length=str(enclosure.get("length") or 0))
        attrs = raw.get("attr") or raw.get("newznab:attr") or []
        for attr in attrs if isinstance(attrs, list) else [attrs]:
            attr = (attr or {}).get("@attributes") or attr or {}
            if attr.get("name"):
                ET.SubElement(item, NS + "attr", name=str(attr["name"]), value=str(attr.get("value") or ""))
    return root


def _describe(body):
    """What a non-XML answer looks like, for the log: the start of it, letters only, never long."""
    head = body[:400].decode("utf-8", "replace")
    if "<html" in head.lower() or "<!doctype html" in head.lower():
        return "a web page (blocked or a login page?)"
    text = re.sub(r"[^\w .,:;/()\-]", " ", head)
    return "'%s'" % re.sub(r"\s+", " ", text).strip()[:80]


def _root(body):
    stripped = body.lstrip()
    if stripped[:1] in (b"{", b"["):
        try:
            root = _from_json(stripped)
        except (ValueError, AttributeError, TypeError):
            util.log("usenet: indexer answer not readable: %s" % _describe(body))
            raise IndexerError("answer is not readable") from None
    else:
        try:
            root = ET.fromstring(body)
        except ET.ParseError:
            util.log("usenet: indexer answer not readable: %s" % _describe(body))
            raise IndexerError("answer is %s" % ("a web page" if "web page" in _describe(body) else "not XML")) from None
    tag = root.tag.rsplit("}", 1)[-1].lower()
    if tag not in ("rss", "caps", "error"):
        util.log("usenet: indexer answer not readable: %s" % _describe(body))
        raise IndexerError("answer is %s" % ("a web page" if tag == "html" else "not a Newznab answer"))
    if root.tag == "error":
        code = root.get("code") or "?"
        raise IndexerError({"100": "wrong API key", "101": "account suspended", "429": "limit reached",
                            "500": "limit reached"}.get(code, "error %s" % code))
    return root


def _strip_key(link):
    """(link without the key, name of the key parameter)."""
    parts = urllib.parse.urlsplit(link)
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True)
    found = next((k for k, _v in query if k in KEY_PARAMS), "apikey")
    kept = [(k, v) for k, v in query if k not in KEY_PARAMS]
    return urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(kept), "")), found


def _rows(root, slot):
    out = []
    for item in root.iter("item"):
        attrs = {a.get("name"): a.get("value") for a in item.iter(NS + "attr")}
        title = (item.findtext("title") or "").strip()
        enclosure = item.find("enclosure")
        link = (enclosure.get("url") if enclosure is not None else None) or item.findtext("link") or ""
        if not title or not link:
            continue
        try:
            size = int(attrs.get("size") or (enclosure.get("length") if enclosure is not None else 0) or 0)
        except ValueError:
            size = 0
        if attrs.get("password") in ("1", "2"):
            continue                        # passworded releases can never be played
        link, param = _strip_key(link)
        out.append({"title": title, "size": size, "slot": slot, "link": link, "param": param,
                    "grabs": int(attrs.get("grabs") or 0) if (attrs.get("grabs") or "0").isdigit() else 0,
                    "posted": item.findtext("pubDate") or ""})
    return out


def _queries(meta):
    """The questions to ask, best first: by id, then by name."""
    if meta["kind"] == "movie":
        asks = []
        imdb = re.sub(r"\D", "", meta.get("imdb") or "")
        if imdb:
            asks.append({"t": "movie", "imdbid": imdb, "cat": "2000"})
        return asks
    season, episode = int(meta["season"]), int(meta["episode"])
    asks = []
    if meta.get("tvdb"):
        asks.append({"t": "tvsearch", "tvdbid": meta["tvdb"], "season": season, "ep": episode, "cat": "5000"})
    imdb = re.sub(r"\D", "", meta.get("show_imdb") or meta.get("imdb") or "")
    if imdb:
        asks.append({"t": "tvsearch", "imdbid": imdb, "season": season, "ep": episode, "cat": "5000"})
    return asks


def search(slot, base, key, meta, text_titles):
    """Rows from one indexer, from the cache when it asked the same in the last three hours."""
    asks = _queries(meta)
    if meta["kind"] == "movie":
        asks += [{"t": "search", "q": "%s %s" % (t, meta.get("year") or ""), "cat": "2000"} for t in text_titles[:1]]
    else:
        asks += [{"t": "tvsearch", "q": t, "season": int(meta["season"]), "ep": int(meta["episode"]), "cat": "5000"} for t in text_titles[:1]]
    cache = "nzbsearch" + hashlib.sha1((base + repr(asks)).encode("utf-8")).hexdigest()
    saved = util.cache_get(cache, KEEP)
    if saved is not None:
        return saved
    rows, error = [], None
    for ask in asks:
        params = dict(ask, extended="1", limit="100")
        try:
            rows = _rows(_root(_get(base, key, params)), slot)
        except IndexerError as failed:
            error = failed
            if "key" in str(failed) or "limit" in str(failed) or "suspended" in str(failed):
                break
            continue
        if rows:
            break
    if not rows and error is not None:
        raise error
    util.cache_set(cache, rows)
    return rows


def nzb(row):
    """The NZB bytes of a row (the key is put back only for this request)."""
    match = next(((base, key) for slot, base, key in indexers() if slot == row["slot"]), None)
    if not match:
        raise IndexerError("indexer not set up any more")
    _base, key = match
    parts = urllib.parse.urlsplit(row["link"])
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + [(row.get("param") or "apikey", key)]
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))
    try:
        body = fetch(url, 30)
    except IndexerError as error:
        raise IndexerError("NZB: %s" % error) from None
    if b"<nzb" not in body[:4096] and b"<nzb" not in body:
        try:
            _root(body)
        except IndexerError as error:
            raise IndexerError("NZB: %s" % error) from None
        raise IndexerError("NZB: not an NZB")
    return body


def test(slot, base, key):
    """(ok, text) for the settings' test button. 'caps' needs no key at most indexers, so it proves nothing about the
    key: a real (tiny) search does. That costs one API hit."""
    try:
        _root(_get(base, key, {"t": "caps"}, timeout=15))
    except IndexerError as error:
        return False, str(error)
    try:
        _root(_get(base, key, {"t": "search", "q": "test", "limit": "1"}, timeout=15))
    except IndexerError as error:
        text = str(error)
        if text in ("HTTP 401", "HTTP 403", "wrong API key"):
            return False, "API key not accepted"
        return False, text
    return True, "ok"

