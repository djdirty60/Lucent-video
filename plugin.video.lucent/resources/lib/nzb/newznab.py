# -*- coding: utf-8 -*-
"""Your indexers (the Newznab API that almost every Usenet indexer speaks): search a film or an episode, get the NZB.

An indexer's links carry your API key. Lucent never keeps a link with the key in it: the key is taken out before a
result is stored (in the source list, the 'ready for tonight' file or the cache) and only put back at the moment the
NZB is fetched. Errors and log lines never show an address or a key.
"""
import gzip
import hashlib
import re
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
import xml.etree.ElementTree as ET

import util

NS = "{http://www.newznab.com/DTD/2010/feeds/attributes/}"
KEEP = 3 * 3600                         # a search answer is reused for three hours
SLOTS = 10
KEY_PARAMS = ("apikey", "r")            # Newznab uses apikey=; nZEDb-style sites put the key in r=


AGENT = "LucentVideo/1.0 (Kodi video add-on; Newznab search for Usenet streaming; +https://github.com/djdirty60/Lucent-video)"
# some indexers refuse anything that is not a known download tool: the setting 'introduce yourself as' picks one of these
AGENTS = [AGENT, "SABnzbd/4.4.1", "NZBGet/24.5", "Sonarr/4.0.14.2939", "Radarr/5.21.1.9799", "Prowlarr/1.32.2.4987"]


def agent():
    try:
        return AGENTS[int(util.setting("nzb_identity") or 0)]
    except (ValueError, IndexError):
        return AGENT


def _reason(error):
    """The indexer's own words when it refuses (most send <error code="..." description="..."/> with a 401 or 403).
    Only the description, cut short; it never holds the key (that is only in our request)."""
    try:
        body = error.read()[:2000]
        if body[:2] == b"\x1f\x8b":
            body = gzip.decompress(body)
        text = body.decode("utf-8", "replace")
    except Exception:          # noqa: BLE001 - the reason is a courtesy
        return ""
    found = re.search(r'description="([^"]{1,160})"', text) or re.search(r'"(?:description|message|error)"\s*:\s*"([^"]{1,160})"', text)
    if found:
        return found.group(1)
    plain = re.sub(r"<[^>]+>", " ", text)
    plain = re.sub(r"\s+", " ", plain).strip()
    return plain[:100] if plain and "<html" not in text[:300].lower() else ""


class IndexerError(Exception):
    """The indexer said no (wrong key, limit reached) or could not be reached."""


def fetch(url, timeout=20):
    """The raw answer of an address. The error never holds the address (it carries the key)."""
    request = urllib.request.Request(url, headers={"User-Agent": agent(), "Accept-Encoding": "gzip"})
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            body = response.read()
    except urllib.error.HTTPError as error:
        reason = _reason(error)
        raise IndexerError("HTTP %d%s" % (error.code, (" (%s)" % reason) if reason else "")) from None
    except (urllib.error.URLError, OSError, ValueError):
        raise IndexerError("unreachable") from None
    return gzip.decompress(body) if body[:2] == b"\x1f\x8b" else body


# ------------------------------------------------------------------ daily limits
HITS = "nzb_hits.json"                  # per indexer: today's API and NZB hits, the limits it told us, a pause
STOP_AT = 0.9                           # stop at 90% of a known limit, so there is room left for the rest of the day
STOP_AT_BACKGROUND = 0.5                # getting things ready beforehand may use at most half
UNKNOWN_BACKGROUND = 25                 # an indexer that never tells its limit: at most this many background hits a day
_hits_lock = threading.Lock()


def _today():
    return time.strftime("%Y-%m-%d", time.gmtime())


def _entry(saved, base):
    entry = saved.get(base) or {}
    if entry.get("day") != _today():
        entry = {"day": _today(), "api": 0, "grab": 0, "api_max": entry.get("api_max"), "grab_max": entry.get("grab_max"),
                 "pause": entry.get("pause", 0)}
    return entry


def _update(base, change):
    with _hits_lock:
        saved = util.load_json(HITS, {})
        entry = _entry(saved, base)
        change(entry)
        saved[base] = entry
        util.save_json(HITS, saved)


def _count(base, kind):
    _update(base, lambda e: e.__setitem__(kind, int(e.get(kind) or 0) + 1))


def _read_limits(base, root):
    """Many indexers tell their limits in every answer: <newznab:apilimits apicurrent= apimax= grabcurrent= grabmax=>."""
    for element in root.iter():
        if element.tag.rsplit("}", 1)[-1].lower() != "apilimits":
            continue
        values = {}
        for name, key in (("apicurrent", "api"), ("apimax", "api_max"), ("grabcurrent", "grab"), ("grabmax", "grab_max")):
            raw = element.get(name)
            if raw and raw.isdigit():
                values[key] = int(raw)
        if values:
            _update(base, lambda e: e.update(values))
        return


def _pause(base, seconds=None, reason=""):
    """After 'limit reached': rest until the next day (UTC), or the given seconds (HTTP 429, too many at once)."""
    if seconds is None:
        now = time.time()
        until = now - now % 86400 + 86400 + 300
    else:
        until = time.time() + seconds
    _update(base, lambda e: e.update(pause=until, why=reason))
    util.log("usenet: indexer %s said %s: resting until %s UTC" % (urllib.parse.urlsplit(base).hostname or "?", reason or "limit",
                                                                    time.strftime("%H:%M", time.gmtime(until))))


def allowed(base, kind="api", background=False):
    """May we ask this indexer now? kind 'api' (a search) or 'grab' (an NZB)."""
    entry = _entry(util.load_json(HITS, {}), base)
    if entry.get("pause", 0) > time.time():
        return False
    used, most = int(entry.get(kind) or 0), entry.get(kind + "_max")
    if most:
        return used < int(most) * (STOP_AT_BACKGROUND if background else STOP_AT)
    return not background or used < UNKNOWN_BACKGROUND


def grab_room(base):
    """Room to fetch an NZB only to check it: never more than half of a known daily NZB limit."""
    entry = _entry(util.load_json(HITS, {}), base)
    if entry.get("pause", 0) > time.time():
        return False
    most = entry.get("grab_max")
    return not most or int(most) >= 1000000 or int(entry.get("grab") or 0) < int(most) * 0.5


def usage(base, log=False):
    """'41 searches (unlimited), 15 NZBs' for the test button: no huge numbers, no '?' when the indexer does not tell."""
    entry = _entry(util.load_json(HITS, {}), base)
    T = (lambda nl, en: en) if log else util.T

    def part(used, most, nl, en):
        used = used or 0
        try:
            most = int(most or 0)
        except (TypeError, ValueError):
            most = 0
        if most >= 1000000:
            return T("%d %s (onbeperkt)", "%d %s (unlimited)") % (used, T(nl, en))
        if most > 0:
            return T("%d van %d %s", "%d of %d %s") % (used, most, T(nl, en))
        return "%d %s" % (used, T(nl, en))

    return ", ".join((part(entry.get("api"), entry.get("api_max"), "zoekopdrachten", "searches"),
                      part(entry.get("grab"), entry.get("grab_max"), "NZB's", "NZBs")))


_said = {"text": ""}                    # what the indexer itself said last (code and description), for the log


def _limit_hit(base, error):
    text = str(error)
    if text == "limit reached":
        _pause(base, reason=_said["text"] or text)
    elif text.startswith("HTTP 429"):
        _pause(base, 3600, reason="HTTP 429, too many at once")


def pause_text(base):
    """'paused until 01:05 (code 500, 'Request limit reached')' or '' when it is not paused, for the log."""
    entry = _entry(util.load_json(HITS, {}), base)
    until = entry.get("pause", 0)
    if until <= time.time():
        return ""
    return "paused until %s UTC (%s)" % (time.strftime("%H:%M", time.gmtime(until)), entry.get("why") or "?")


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


def latest(slot, base, key, count=10):
    """A few of the newest films at this indexer (one search), for the speed test."""
    if not allowed(base, "api"):
        raise IndexerError("daily limit")
    return _rows(_ask(base, key, {"t": "movie", "cat": "2000", "limit": str(count), "extended": "1"}), slot)


def _get(base, key, params, timeout=20):
    _count(base, "api")
    try:
        body = fetch(base + "?" + urllib.parse.urlencode(dict(params, apikey=key, o="xml")), timeout)
    except IndexerError as error:
        _limit_hit(base, error)
        raise
    return body.replace(key.encode("utf-8"), b"[key]") if len(key) >= 8 else body     # a real key, not a short word


def _ask(base, key, params, timeout=20):
    """_get + _root, and remember the limits the indexer told and a 'limit reached'."""
    try:
        root = _root(_get(base, key, params, timeout))
    except IndexerError as error:
        _limit_hit(base, error)
        raise
    _read_limits(base, root)
    return root


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
        _said["text"] = "code %s%s" % (code, (", '%s'" % re.sub(r"[^\w .,:;()\-]", " ", root.get("description") or "")[:80]) if root.get("description") else "")
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
                    "posted": item.findtext("pubDate") or "",
                    "info": {k: attrs[k] for k in INFO if attrs.get(k)}})
    _tell_attrs(slot, root)
    return out


INFO = ("language", "subs", "audio", "video", "resolution")      # Newznab extended attributes that say what is inside
_told = set()


def _tell_attrs(slot, root):
    """Once per indexer per session: which attribute names it gives (names only), for an error report."""
    if slot in _told:
        return
    names = sorted({a.get("name") for item in root.iter("item") for a in item.iter(NS + "attr") if a.get("name")})
    if names:
        _told.add(slot)
        subs = next((a.get("value") for item in root.iter("item") for a in item.iter(NS + "attr")
                     if a.get("name") == "subs" and a.get("value")), "")
        util.log("usenet: indexer %s gives: %s%s" % (slot, ", ".join(names),
                                                    ("; subs for example: %s" % subs[:80]) if subs else ""))


FORMATS = "nzb_imdb_format.json"       # per indexer: does it want the IMDb id with 'tt' (some do, the spec says digits)


def _imdb_forms(base, imdb):
    digits = re.sub(r"\D", "", imdb or "")
    if not digits:
        return []
    forms = [digits, "tt" + digits]
    if util.load_json(FORMATS, {}).get(base) == "tt":
        forms.reverse()
    return forms


def _remember_form(base, form):
    saved = util.load_json(FORMATS, {})
    wanted = "tt" if str(form).startswith("tt") else "digits"
    if saved.get(base) != wanted:
        saved[base] = wanted
        util.save_json(FORMATS, saved)


def _queries(meta, base=""):
    """The questions to ask, best first: by id (both IMDb forms), then by name."""
    if meta["kind"] == "movie":
        return [{"t": "movie", "imdbid": form, "cat": "2000"} for form in _imdb_forms(base, meta.get("imdb"))]
    season, episode = int(meta["season"]), int(meta["episode"])
    asks = []
    if meta.get("tvdb"):
        asks.append({"t": "tvsearch", "tvdbid": meta["tvdb"], "season": season, "ep": episode, "cat": "5000"})
    asks += [{"t": "tvsearch", "imdbid": form, "season": season, "ep": episode, "cat": "5000"}
             for form in _imdb_forms(base, meta.get("show_imdb") or meta.get("imdb"))[:1]]
    return asks


def search(slot, base, key, meta, text_titles, background=False):
    """Rows from one indexer, from the cache when it asked the same in the last three hours. Background searches (getting
    things ready beforehand) stop at half of the daily limit; normal ones at 90%."""
    asks = _queries(meta, base)
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
        if not allowed(base, "api", background):
            error = IndexerError("daily limit")
            break
        params = dict(ask, extended="1", limit="100")
        try:
            rows = _rows(_ask(base, key, params), slot)
        except IndexerError as failed:
            error = failed
            if "key" in str(failed) or "limit" in str(failed) or "suspended" in str(failed):
                break
            continue
        if rows:
            if ask.get("imdbid"):
                _remember_form(base, ask["imdbid"])
            break
    if meta["kind"] != "movie" and (rows or error is None):
        rows = rows + _packs(slot, base, key, meta, background)
    if not rows and error is not None:
        raise error
    util.cache_set(cache, rows)
    return rows


def _packs(slot, base, key, meta, background):
    """Season packs: the whole season in one release. One extra search (by id, without the episode); which rows really
    are packs of this season is decided by the caller."""
    season = int(meta["season"])
    if meta.get("tvdb"):
        ask = {"t": "tvsearch", "tvdbid": meta["tvdb"], "season": season, "cat": "5000"}
    else:
        forms = _imdb_forms(base, meta.get("show_imdb") or meta.get("imdb"))
        if not forms:
            return []
        ask = {"t": "tvsearch", "imdbid": forms[0], "season": season, "cat": "5000"}
    if not allowed(base, "api", background):
        return []
    try:
        found = _rows(_ask(base, key, dict(ask, extended="1", limit="100")), slot)
    except IndexerError as error:
        util.log("usenet: season packs: %s" % error)
        return []
    for row in found:
        row["maybe_pack"] = True
    return found


def nzb(row):
    """The NZB bytes of a row (the key is put back only for this request)."""
    match = next(((base, key) for slot, base, key in indexers() if slot == row["slot"]), None)
    if not match:
        raise IndexerError("indexer not set up any more")
    base, key = match
    if not allowed(base, "grab"):
        raise IndexerError("NZB: daily limit")
    _count(base, "grab")
    parts = urllib.parse.urlsplit(row["link"])
    query = urllib.parse.parse_qsl(parts.query, keep_blank_values=True) + [(row.get("param") or "apikey", key)]
    url = urllib.parse.urlunsplit((parts.scheme, parts.netloc, parts.path, urllib.parse.urlencode(query), ""))
    try:
        body = fetch(url, 30)
    except IndexerError as error:
        _limit_hit(base, error)
        util.log("usenet: NZB link that was refused: %s" % _link_shape(row["link"], row.get("param")))
        raise IndexerError("NZB: %s" % error) from None
    if b"<nzb" not in body[:4096] and b"<nzb" not in body:
        util.log("usenet: NZB link that gave no NZB: %s" % _link_shape(row["link"], row.get("param")))
        try:
            _root(body)
        except IndexerError as error:
            raise IndexerError("NZB: %s" % error) from None
        raise IndexerError("NZB: not an NZB")
    return body


def _link_shape(link, param):
    """What an NZB link looks like, for the log, without anything that identifies the release or the user:
    host and path with long parts replaced, and only the names of the parameters."""
    parts = urllib.parse.urlsplit(link)
    path = re.sub(r"[A-Za-z0-9_\-]{12,}", "[id]", parts.path)
    path = re.sub(r"(?<=/)\d{3,}(?=/|\.|$)", "[n]", path)
    path = re.sub(r"=[^&/?;]+", "=[..]", path)             # parameters written into the path (a link without '?')
    names = [k for k, _v in urllib.parse.parse_qsl(parts.query, keep_blank_values=True)] + ["%s=[key]" % (param or "apikey")]
    return "%s://%s%s?%s" % (parts.scheme, parts.hostname or "?", path, "&".join(names))


def test(slot, base, key):
    """(ok, text) for the settings' test button. 'caps' needs no key at most indexers, so it proves nothing about the
    key: a real (tiny) search does. That costs one API hit."""
    try:
        _ask(base, key, {"t": "caps"}, timeout=15)
        _ask(base, key, {"t": "search", "q": "test", "limit": "1"}, timeout=15)
    except IndexerError as error:
        text = str(error)
        if text.startswith(("HTTP 401", "HTTP 403")) or text == "wrong API key":
            reason = text.split("(", 1)[1].rstrip(")") if "(" in text else ""
            return False, "API key not accepted" + ((": " + reason) if reason else "")
        return False, text
    return True, "ok"

