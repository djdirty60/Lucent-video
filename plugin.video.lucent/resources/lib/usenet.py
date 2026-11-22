# -*- coding: utf-8 -*-
"""Easynews and TorBox: they do the usenet (NNTP) or debrid work on their side and give back a normal web stream.

Passwords and keys are never logged and never written to the cache.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import re
import time
import urllib.parse
import urllib.request
import uuid

import xbmc

import mylang
import ranking
import streams
import tmdb
import util

T = util.T
EASYNEWS = "https://members.easynews.com"
TB_SEARCH = "https://search-api.torbox.app"
TB_API = "https://api.torbox.app/v1/api"
VIDEO = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm")
MIN_SECONDS = 600
KEEP_RESULTS = 600
FLAG_OF = [("dv2", "DV2"), ("fel", "aio_FEL"), ("p7", "aio_P7"), ("hdr", "HDR"), ("hlg", "HDR"), ("sdr", "SDR"), ("3d", "3D"), ("imax", "aio_IMAX"), ("10bit", "aio_10BIT"), ("hevc", "HEVC"), ("avc", "AVC"), ("av1", "AV1"),
           ("remux", "REMUX"), ("bluray", "BLURAY"), ("web", "WEB"), ("hdtv", "HDTV"), ("cam", "CAM"), ("atmos", "ATMOS"), ("truehd", "TRUEHD"), ("dtsx", "DTS-X"), ("dtsma", "DTS-HDMA"),
           ("dtshd", "DTS-HD"), ("dts", "DTS"), ("ddplus", "DD+"), ("dd", "DD"), ("aac", "AAC"), ("opus", "OPUS"), ("71", "7.1"), ("51", "5.1"), ("20", "2.0"), ("multi", "aio_MULTI")]
AGENT = "Lucent-Video"


def _flags(name):
    tags = ranking.tags_of({"name": name, "resolution": "SD", "cached": True, "size": 0})
    if tags & {"dtsx", "dtsma", "dtshd"}:
        tags.discard("dts")                         # DTS-HD already says it
    if "ddplus" in tags:
        tags.discard("dd")
    flags = ["DV"] if "dv" in tags and "dv2" not in tags else []
    flags += [flag for tag, flag in FLAG_OF if tag in tags]
    if "nl" in tags:                                # your language (mylang.py): its own label, NL / RO / DE ...
        flags.append(mylang.audio_flag())
    if "nlsubs" in tags:
        flags.append(mylang.subs_flag())
    return flags


def _source(name, size, service, url="", cached=True, resolve=None):
    size = size or 0
    return {"url": url, "name": name, "resolution": streams.resolution({}, name), "flags": _flags(name), "badges": [], "size": size, "service": service, "cached": cached, "resolve": resolve,
            "meta": " · ".join(p for p in (service, streams.size_text(size)) if p)}


def _words(text):
    return re.sub(r"[^a-z0-9]+", " ", text.lower().replace("&", " and ")).split()


def title_matches(name, titles, meta):
    """Search engines return lookalikes: the name must start with the title, then the right year or episode."""
    words = _words(name)
    for title in titles:
        wanted = _words(title)
        if wanted and words[:len(wanted)] == wanted:
            rest = " ".join(words[len(wanted):])
            if meta["kind"] == "movie":
                year = int(meta.get("year") or 0)
                if not year or any(re.search(r"\b%d\b" % y, rest) for y in (year - 1, year, year + 1)):
                    return not re.search(r"\bs\d{1,2} ?e\d{1,3}\b", rest)
            elif re.search(r"\bs0*%d ?e0*%d\b|\b0*%dx0*%d\b" % (int(meta["season"]), int(meta["episode"]), int(meta["season"]), int(meta["episode"])), rest):
                return True
    return False


def titles_for(meta):
    """English and original titles: releases are almost never named after the Dutch title."""
    kind = "movie" if meta["kind"] == "movie" else "tvshow"
    api = "movie" if kind == "movie" else "tv"
    names = [meta.get("show") or meta.get("title") or ""]
    raw = tmdb.get("%s/%s" % (api, meta["tmdb"]), ttl=7 * tmdb.DAY, language="en-US")
    for key in ("title", "name", "original_title", "original_name"):
        if raw.get(key):
            names.insert(0, raw[key]) if key in ("title", "name") else names.append(raw[key])
    return list(dict.fromkeys(n for n in names if n))


# ------------------------------------------------------------------------ Easynews
def _en_login():
    return util.setting("easynews_user"), util.setting("easynews_pass")


def _en_header():
    user, password = _en_login()
    return "Basic " + base64.b64encode(("%s:%s" % (user, password)).encode("utf-8")).decode("ascii")


def _en_size(value):
    if isinstance(value, (int, float)):
        return int(value)
    found = re.match(r"\s*([\d.]+)\s*([KMGT]?)B?", str(value or ""), re.I)
    if not found:
        return 0
    return int(float(found.group(1)) * 1024 ** " KMGT".index((found.group(2) or " ").upper()))


def _en_seconds(item):
    value = item.get("runtime") if isinstance(item, dict) else None
    if value is None and isinstance(item, dict):
        value = item.get("14")
    if isinstance(value, (int, float)):
        return value
    found = re.findall(r"(\d+)\s*([hms])", str(value or ""))
    return sum(int(n) * {"h": 3600, "m": 60, "s": 1}[u] for n, u in found) if found else None


def _en_item(raw):
    """(hash, id, name without extension, extension, size, passworded or infected?, seconds) from either answer shape."""
    if isinstance(raw, list):
        if len(raw) < 12:
            return None
        return raw[0], "", raw[10], raw[11], _en_size(raw[4]), False, None
    key = str(raw.get("hash") or raw.get("0") or "")
    extension = str(raw.get("extension") or raw.get("ext") or raw.get("11") or "")
    return key, str(raw.get("id") or ""), str(raw.get("fn") or raw.get("10") or ""), extension, _en_size(raw.get("rawSize") or raw.get("size") or raw.get("4")), \
        bool(raw.get("passwd") or raw.get("virus")), _en_seconds(raw)


def _en_search(query):
    key = "easynews" + query
    answer = util.cache_get(key, KEEP_RESULTS)
    if answer is None:
        params = {"gps": query, "pno": 1, "pby": 250, "u": 1, "safeO": 0, "s1": "relevance", "s1d": "-", "fty[]": "VIDEO", "fly": 2, "sb": 1, "st": "basic", "chxu": 1, "chxgx": 1, "vv": 1}
        answer, _ = util.http(EASYNEWS + "/2.0/search/solr-search/?" + urllib.parse.urlencode(params), headers={"Authorization": _en_header(), "User-Agent": AGENT}, timeout=25)
        util.cache_set(key, answer)
    return answer or {}


EN_SPEED = "easynews_speed.json"        # {"rate": bytes per second of one Easynews download, "at": when measured}
EN_SPEED_DAYS = 7
EN_FIELDS = "easynews_fields.json"      # when the names of Easynews' fields were last written in the log (once a day)


def _en_speed(base, sample_path):
    """Bytes per second this box gets from Easynews, measured once a week with the first 4 MB of one result (one
    connection, as Kodi plays it). 0 when unknown."""
    saved = util.load_json(EN_SPEED, {})
    if saved.get("rate") and time.time() - saved.get("at", 0) < EN_SPEED_DAYS * 86400:
        return saved["rate"]
    if not sample_path:
        return saved.get("rate") or 0
    url, _, extra = ("%s/%s" % (base, sample_path)).partition("|")
    parts = urllib.parse.urlsplit(url)
    head = {"User-Agent": AGENT, "Range": "bytes=0-4194303", "Authorization": _en_header()}
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, parts.query, ""))
    started, got = time.time(), 0
    try:
        with urllib.request.urlopen(urllib.request.Request(clean, headers=head), timeout=8) as response:
            while time.time() - started < 6:
                block = response.read(262144)
                if not block:
                    break
                got += len(block)
    except OSError as error:
        util.log("Easynews speed: not measured (%s)" % type(error).__name__)
        return saved.get("rate") or 0
    seconds = time.time() - started
    if got < 1048576 or seconds <= 0:
        return saved.get("rate") or 0
    rate = got / seconds
    util.save_json(EN_SPEED, {"rate": rate, "at": time.time()})
    util.log("Easynews speed: %.1f MB/s (%.1f MB in %.1f s)" % (rate / 1048576, got / 1048576, seconds))
    return rate


def _en_fields(answer):
    """Once a day: which fields Easynews sends per file (names only, never values), to build on what is really there."""
    rows = [r for r in answer.get("data") or [] if isinstance(r, dict)]
    saved = util.load_json(EN_FIELDS, {})
    if not rows or time.time() - saved.get("at", 0) < 86400:
        return
    util.save_json(EN_FIELDS, {"at": time.time()})
    util.log("Easynews gives: %s; top: %s" % (", ".join(sorted(rows[0])), ", ".join(sorted(k for k in answer if k != "data")[:40])))


def _en_langs(value):
    """Two-letter codes of a list of Easynews languages ('eng', 'dut', 'English' ...), in order, without doubles."""
    out = []
    for item in value if isinstance(value, list) else []:
        text = str(item or "").strip().lower()
        if not text:
            continue
        code = {"dut": "nl", "nld": "nl", "ger": "de", "fre": "fr", "chi": "zh", "cze": "cs", "gre": "el"}.get(text, "")
        if not code:
            try:
                code = (xbmc.convertLanguage(text, xbmc.ISO_639_1) or "").lower()
            except Exception:       # noqa: BLE001 - Kodi does not know it
                code = ""
        code = code if len(code) == 2 else (text if len(text) == 2 else "")
        if code and code not in out:
            out.append(code)
    return out


def _en_preview(answer, raw):
    """The address of Easynews' preview picture of a file (a few frames of the video), or ''."""
    base = str(answer.get("thumbURL") or "")
    key, stem = str(raw.get("0") or raw.get("hash") or ""), str(raw.get("10") or raw.get("fn") or "")
    if not base or len(key) < 3 or not stem:
        return ""
    return "%s%s/pr-%s.jpg/th-%s.jpg" % (base, key[:3], key, urllib.parse.quote(stem))


def preview_file(url):
    """Downloads a preview picture with your Easynews login (in a header, never in a log) to a temporary file."""
    import os
    folder = os.path.join(util.CACHE, "preview")
    os.makedirs(folder, exist_ok=True)
    for name in os.listdir(folder):
        try:
            os.remove(os.path.join(folder, name))
        except OSError:
            pass
    path = os.path.join(folder, "%d.jpg" % int(time.time() * 1000))
    request = urllib.request.Request(url, headers={"Authorization": _en_header(), "User-Agent": AGENT})
    with urllib.request.urlopen(request, timeout=15) as response:
        data = response.read(8 * 1048576)
    if len(data) < 500:
        raise ValueError("no picture")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


def _version(stem, minutes, runtime):
    """('Extended +12 min', keep?) from the length of the file against TMDB's running time (films)."""
    if not minutes or not runtime:
        return "", True
    if minutes < runtime * 0.6:
        return "", False                        # far too short: another video, a part or a fake
    diff = int(round(minutes - runtime))
    if abs(diff) < 6:
        return "", True
    low = stem.lower()
    name = next((label for word, label in (("extended", "Extended"), ("director", "Director's Cut"), ("uncut", "Uncut"),
                                           ("unrated", "Unrated"), ("imax", "IMAX")) if word in low), "")
    if diff > 0:
        return ("%s +%d min" % (name, diff)).strip() if name else util.T("%d min langer", "%d min longer") % diff, True
    return util.T("%d min korter", "%d min shorter") % -diff, True


def easynews(meta):
    if not all(_en_login()):
        return None
    titles = titles_for(meta)
    query = "%s %s" % (titles[0], meta.get("year") or "") if meta["kind"] == "movie" else "%s S%02dE%02d" % (titles[0], int(meta["season"]), int(meta["episode"]))
    answer = _en_search(query.strip())
    base = "%s/%s/%s" % (answer.get("downURL") or EASYNEWS + "/dl", answer.get("dlFarm") or "auto", answer.get("dlPort") or "auto")
    # Login inside the address, not as a header: Kodi hides the first in its log but writes the second out in full.
    parts = urllib.parse.urlsplit(base)
    user, password = _en_login()
    base = "%s://%s:%s@%s%s" % (parts.scheme, urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""), parts.netloc, parts.path)
    agent = urllib.parse.urlencode({"User-Agent": AGENT})
    _en_fields(answer)
    runtime = int(meta.get("runtime") or 0) if meta["kind"] == "movie" else 0
    short = 0
    out = []
    for raw in answer.get("data") or []:
        item = _en_item(raw)
        if not item:
            continue
        key, suffix, stem, extension, size, bad, seconds = item
        extension = "." + extension.lstrip(".")
        low = stem.lower()
        if not key or bad or extension.lower() not in VIDEO or (seconds is not None and seconds < MIN_SECONDS) or "sample" in low[len(low) // 2:]:
            continue
        if not title_matches(stem, titles, meta):
            continue
        if seconds and meta["kind"] != "movie" and meta.get("runtime") and seconds < int(meta["runtime"]) * 60 * 0.5:
            short += 1
            continue                            # an episode far shorter than it should be: a part or another video
        label, keep = _version(stem, seconds / 60.0 if seconds else 0, runtime)
        if not keep:
            short += 1
            continue
        name = stem + extension
        path = "%s%s%s/%s" % (key, suffix, extension, urllib.parse.quote(name))
        source = dict(_source(name, size, "EASYNEWS", url="%s/%s|%s" % (base, path, agent)), always=True, seconds=seconds or 0, _path=path)
        if label:
            source["meta"] += " · " + label
        if isinstance(raw, dict):
            # what is really inside the file (Easynews reads the tracks), not guessed from the name
            sound, subs = _en_langs(raw.get("alangs")), _en_langs(raw.get("slangs"))
            mine = mylang.code()
            if mine in sound and mylang.audio_flag() not in source["flags"]:
                source["flags"].append(mylang.audio_flag())
            if mine in subs and mylang.subs_flag() not in source["flags"]:
                source["flags"].append(mylang.subs_flag())
            if len(sound) > 1:
                source["meta"] += " · " + T("geluid", "audio") + " " + " ".join(c.upper() for c in sound[:4])
            preview = _en_preview(answer, raw)
            if preview:
                source["preview"] = preview
        out.append(source)
    if short:
        util.log("Easynews: %d result(s) left out: far shorter than the film or episode" % short)
    rate = _en_speed(base, out[0]["_path"] if out else "") if out else 0
    heavy = 0
    for source in out:
        source.pop("_path")
        seconds = source.pop("seconds")
        if rate and seconds and source["size"] / seconds > rate * 0.9:
            source["heavy"] = True              # needs more per second than this box gets from Easynews
            source["meta"] += " · " + T("mogelijk te zwaar", "may be too heavy")
            heavy += 1
    if heavy:
        util.log("Easynews: %d of %d result(s) may be too heavy for %.1f MB/s" % (heavy, len(out), rate / 1048576))
    return out


def check_easynews():
    if not all(_en_login()):
        return False, T("Vul eerst je gebruikersnaam en wachtwoord in.", "Enter your username and password first.")
    try:
        answer, _ = util.http(EASYNEWS + "/2.0/search/solr-search/?fly=2&gps=test&pby=1&pno=1&safeO=0&sb=1&st=basic", headers={"Authorization": _en_header(), "User-Agent": AGENT}, timeout=25)
    except util.HttpError as error:
        return False, T("Geen verbinding: %s", "No connection: %s") % error
    return bool(answer is not None), T("Verbonden met Easynews.", "Connected to Easynews.")


# ------------------------------------------------------------------------ TorBox
def _tb_key():
    """The key without anything that cannot be part of it (an invisible character or quotes from pasting)."""
    return re.sub(r"[^0-9A-Za-z_\-]", "", util.setting("torbox_key"))


def tb_key_shape():
    """'36 characters, the usual form' - what the key looks like, never the key itself (for the log)."""
    raw, key = util.setting("torbox_key"), _tb_key()
    form = "the usual form" if re.fullmatch(r"[0-9a-fA-F]{8}-([0-9a-fA-F]{4}-){3}[0-9a-fA-F]{12}", key) else "not the usual form"
    removed = len(raw) - len(key)
    return "%d characters, %s%s" % (len(key), form, ", %d other character(s) left out" % removed if removed else "")


def _tb_head():
    return {"Authorization": "Bearer %s" % _tb_key(), "User-Agent": AGENT}


def tb_reason(error):
    """'HTTP 403 (BAD_TOKEN: ...)': the status plus TorBox's own error code and text, never the key or the address."""
    detail = getattr(error, "detail", None) or {}
    code = str(detail.get("error") or "").strip()
    text = str(detail.get("detail") or "").strip()
    extra = ": ".join(p for p in (code, text) if p)[:160].replace(_tb_key() or "\0", "***")
    return "%s (%s)" % (error, extra) if extra else str(error)


def _tb_search(kind, meta):
    """kind: usenet or torrents. Returns the rows, or None when this search failed."""
    ident = "imdb_id:%s" % meta["imdb"] if meta.get("imdb") else "tmdb_id:%s" % meta["tmdb"]
    params = {"check_cache": "true", "metadata": "false"}
    if meta["kind"] == "episode":
        params.update(season=meta["season"], episode=meta["episode"])
    url = "%s/%s/%s?%s" % (TB_SEARCH, kind, ident, urllib.parse.urlencode(params))
    key = "torboxsearch" + url
    answer = util.cache_get(key, KEEP_RESULTS)
    if answer is None:
        started = time.time()
        try:
            answer, _ = util.http(url, headers=_tb_head(), timeout=25)
        except util.HttpError as error:
            hint = " (the name search-api.torbox.app could not be looked up: DNS)" if str(error) == "HTTP gaierror" else ""
            util.log("TorBox %s search: %s after %.1f s%s" % (kind, tb_reason(error), time.time() - started, hint))
            return None
        if not isinstance(answer, dict) or not isinstance(answer.get("data") or {}, dict):
            util.log("TorBox %s search: unexpected answer (%s)" % (kind, _shape(answer)))
            return None
        util.log("TorBox %s search: answer in %.1f s" % (kind, time.time() - started))
        util.cache_set(key, answer)
    data = (answer or {}).get("data") or {}
    rows = data.get("nzbs" if kind == "usenet" else "torrents") or []
    return rows if isinstance(rows, list) else []


def _shape(answer):
    """What an answer looks like, without any value in it (TorBox answers hold the e-mail address)."""
    if isinstance(answer, dict):
        inner = answer.get("data")
        return "keys %s; data: %s" % (",".join(sorted(answer)[:12]), type(inner).__name__ if not isinstance(inner, dict)
                                     else "keys " + ",".join(sorted(inner)[:20]))
    return type(answer).__name__


def torbox(meta):
    """Usenet and debrid (torrent) files. Only what TorBox already has, so it can stream right away."""
    if not _tb_key():
        return None
    with ThreadPoolExecutor(max_workers=2) as pool:
        usenet_rows, torrent_rows = pool.map(lambda kind: _tb_search(kind, meta), ("usenet", "torrents"))
    if usenet_rows is None and torrent_rows is None:
        raise util.HttpError("TorBox")
    out = []
    for kind, label, rows, link_key in (("usenet", "USENET", usenet_rows, "nzb"), ("torrents", "DEBRID", torrent_rows, "magnet")):
        for row in rows or []:
            if not row.get(link_key) or not row.get("cached"):
                continue
            name = row.get("raw_title") or row.get("title") or ""
            source = _source(name, row.get("size"), "TORBOX %s" % label, resolve={"scraper": "TorBox", "kind": kind, "link": row[link_key], "name": name,
                                                                                  "season": meta.get("season"), "episode": meta.get("episode")})
            out.append(source)
    return out


def _tb_create(job):
    """Puts the release in the TorBox account. Returns its id."""
    boundary = uuid.uuid4().hex
    if job["kind"] == "usenet":
        path, fields, id_key = "/usenet/createusenetdownload", {"link": job["link"], "name": job["name"]}, "usenetdownload_id"
    else:
        path, fields, id_key = "/torrents/createtorrent", {"magnet": job["link"], "name": job["name"], "seed": "3"}, "torrent_id"
    body = "".join('--%s\r\nContent-Disposition: form-data; name="%s"\r\n\r\n%s\r\n' % (boundary, k, v) for k, v in fields.items()) + "--%s--\r\n" % boundary
    answer, _ = util.http(TB_API + path, raw=(body.encode("utf-8"), "multipart/form-data; boundary=%s" % boundary), headers=_tb_head(), timeout=30)
    return ((answer or {}).get("data") or {}).get(id_key)


def _tb_file(item, season, episode):
    files = [f for f in item.get("files") or [] if str(f.get("name") or f.get("short_name") or "").lower().endswith(VIDEO)]
    if season and episode:
        pattern = re.compile(r"s0*%d[ ._-]?e0*%d\b" % (int(season), int(episode)), re.I)
        exact = [f for f in files if pattern.search(str(f.get("name") or ""))]
        files = exact or files
    return max(files, key=lambda f: f.get("size") or 0) if files else None


def torbox_link(job):
    """Adds the release to TorBox (instant when cached), then asks for its web stream. Returns the address, or None."""
    area, id_name = ("usenet", "usenet_id") if job["kind"] == "usenet" else ("torrents", "torrent_id")
    try:
        item_id = _tb_create(job)
        if not item_id:
            return None
        monitor = xbmc.Monitor()
        end = time.time() + 20
        chosen = None
        while time.time() < end and not chosen:
            answer, _ = util.http("%s/%s/mylist?id=%s&bypass_cache=true" % (TB_API, area, item_id), headers=_tb_head(), timeout=20)
            item = (answer or {}).get("data") or {}
            if isinstance(item, list):
                item = item[0] if item else {}
            chosen = _tb_file(item, job.get("season"), job.get("episode"))
            if not chosen and monitor.waitForAbort(1):
                return None
        if not chosen:
            return None
        query = urllib.parse.urlencode({"token": _tb_key(), id_name: item_id, "file_id": chosen["id"], "redirect": "false"})
        answer, _ = util.http("%s/%s/requestdl?%s" % (TB_API, area, query), headers=_tb_head(), timeout=25)
        return (answer or {}).get("data")
    except util.HttpError as error:
        util.log("TorBox link: %s" % error)
        return None


def check_torbox():
    if not _tb_key():
        return False, T("Vul eerst je API-sleutel in.", "Enter your API key first.")
    try:
        answer, _ = util.http(TB_API + "/user/me", headers=_tb_head())
    except util.HttpError as error:
        return False, T("Geen verbinding: %s", "No connection: %s") % tb_reason(error)
    data = answer.get("data") if isinstance(answer, dict) else None
    plan = data.get("plan") if isinstance(data, dict) else None
    return True, T("Verbonden met TorBox (abonnement %s).", "Connected to TorBox (plan %s).") % plan
