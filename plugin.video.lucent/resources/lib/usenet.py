# -*- coding: utf-8 -*-
"""Easynews and TorBox: they do the usenet (NNTP) or debrid work on their side and give back a normal web stream.

Passwords and keys are never logged and never written to the cache.
"""
from concurrent.futures import ThreadPoolExecutor
import base64
import re
import time
import urllib.error
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
            elif len(meta.get("premiered") or "") == 10 and " ".join(meta["premiered"].split("-")) in rest:
                return True                     # a daily show: Show 2026 11 20
            elif meta.get("anime") and int(meta["season"]) == 1 and re.match(r"(?:\d{4} )?0*%d\b" % int(meta["episode"]), rest) \
                    and not re.search(r"\bs\d{1,2} ?e\d", rest):
                return True                     # anime numbered on: Show - 05
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


EN_PAGES = 3                            # 3.0 pages (100 results each) asked at the same time
EN_EMPTY_WAIT = 0.5                     # an empty answer means 'too many searches at once': wait this long, then once again


def _en_ask(url, timeout=12):
    """One Easynews search page; an empty answer (Easynews' way of saying 'too many at once') is asked once more."""
    head = {"Authorization": _en_header(), "User-Agent": AGENT, "Accept": "application/json, text/javascript, */*; q=0.9"}
    for attempt in (1, 2):
        answer, _ = util.http(url, headers=head, timeout=timeout)     # short: 2.0 must still fit in the search time
        if isinstance(answer, dict):
            return answer
        if attempt == 1:
            time.sleep(EN_EMPTY_WAIT)
    raise util.HttpError("empty answer")


def _en_search3(query, most=EN_PAGES, kind="VIDEO"):
    """Easynews 3.0 (/3.0/api/search): 100 results a page and up to 10 searches at once, so the first pages are asked
    together. The answer is shaped like 2.0's (data, downURL, dlFarm, dlPort), with named fields per file. (No spam
    filter: in rc1.29 every search with spamf=1 hung until the time-out.)"""
    def url(page):
        # pictures with Easynews' safe filter on: a person's name can bring up anything in the picture groups
        params = [("gps", query), ("pno", page), ("u", 1), ("safeO", 1 if kind == "IMAGE" else 0), ("s1", "relevance"), ("s1d", "-"), ("fty[]", kind)]
        return EASYNEWS + "/3.0/api/search?" + urllib.parse.urlencode(params)
    first = _en_ask(url(1))
    pages = min(most, int(first.get("numPages") or 1))
    rows, seen = [], set()

    def add(answer):
        for raw in answer.get("data") or []:
            key = str((raw.get("hash") or raw.get("0") or "") if isinstance(raw, dict) else (raw[0] if raw else ""))
            if key and key not in seen:
                seen.add(key)
                rows.append(raw)
    add(first)
    if pages > 1:
        with ThreadPoolExecutor(max_workers=pages - 1) as pool:
            for answer in pool.map(lambda page: _en_try(url(page)), range(2, pages + 1)):
                add(answer or {})
    return dict(first, data=rows)


def _en_try(url):
    try:
        return _en_ask(url)
    except util.HttpError as error:
        util.log("Easynews: a page left out (%s)" % error)
        return None


def _en_search2(query):
    params = {"gps": query, "pno": 1, "pby": 250, "u": 1, "safeO": 0, "s1": "relevance", "s1d": "-", "fty[]": "VIDEO", "fly": 2, "sb": 1, "st": "basic", "chxu": 1, "chxgx": 1, "vv": 1,
              }
    return _en_ask(EASYNEWS + "/2.0/search/solr-search/?" + urllib.parse.urlencode(params))


def _en_search(query, pages=EN_PAGES, fallback=True):
    """3.0 first; 2.0 when 3.0 fails (an error, or no answer twice), so Easynews keeps working either way. An extra
    search (fallback=False: another way of writing the title) asks 3.0 only: 2.0 allows just two at a time."""
    query = clean_query(query)
    key = "easynews3-%d-%s" % (pages, query)
    answer = util.cache_get(key, KEEP_RESULTS)
    if answer is None:
        try:
            answer = _en_search3(query, pages)
            version = "3.0"
        except util.HttpError as error:
            if not fallback:
                util.log("Easynews 3.0: extra search left out (%s)" % error)
                return {}
            util.log("Easynews 3.0: %s - asking 2.0" % error)
            answer = _en_search2(query)
            version = "2.0"
        if version == "3.0" and fallback and not answer.get("data"):
            util.log("Easynews 3.0: nothing found - asking 2.0 to be sure")
            try:
                answer = _en_search2(query)
                version = "2.0"
            except util.HttpError as error:
                util.log("Easynews 2.0: %s" % error)
        util.log("Easynews %s: %d result(s) for the search%s" % (version, len(answer.get("data") or []), "" if fallback else " (extra)"))
        util.cache_set(key, answer)
    return answer or {}


def clean_query(query):
    """Words only: 3.0 found nothing for 'Avengers: Infinity War 2018' or 'Spider-Man: No Way Home 2021' (a colon
    or a dash means something to its search). 'Ocean's' becomes 'Oceans', as releases write it."""
    text = query.replace("'", "").replace("\u2019", "").replace("&", " and ")
    return " ".join(re.sub(r"[^\w\s]", " ", text, flags=re.UNICODE).split())


def _show_facts(meta):
    """(year the series started, anime?, daily show?) from TMDB, for the extra ways of searching an episode."""
    raw = tmdb.get("tv/%s" % meta["tmdb"], ttl=7 * tmdb.DAY, language="en-US") or {}
    genres = {str(g.get("name") or "").lower() for g in raw.get("genres") or []}
    anime = "animation" in genres and (raw.get("original_language") == "ja" or "JP" in (raw.get("origin_country") or []))
    daily = bool(genres & {"talk", "news"})
    return str(raw.get("first_air_date") or "")[:4], anime, daily


def queries(meta, titles):
    """The ways Easynews is searched, the main one first (asked with 3 pages, the others with 1): like AIOStreams and
    MediaFusion. Films: the title and year, and the original title. Episodes: S01E05 and 1x05, the season (S01, for
    files from a season pack), the original title, the title with the year the series started (two series with the
    same name), the episode number alone for anime, and the date for daily shows."""
    first = titles[0]
    other = next((t for t in titles[1:] if _words(t) and _words(t) != _words(first) and t.isascii()), "")
    if meta["kind"] == "movie":
        year = meta.get("year") or ""
        out = ["%s %s" % (first, year)]
        if other:
            out.append("%s %s" % (other, year))
        return [q.strip() for q in out]
    season, episode = int(meta["season"]), int(meta["episode"])
    code = "S%02dE%02d" % (season, episode)
    out = ["%s %s" % (first, code), "%s %dx%02d" % (first, season, episode), "%s S%02d" % (first, season)]
    if other:
        out.append("%s %s" % (other, code))
    try:
        started, anime, daily = _show_facts(meta)
    except util.HttpError:
        started, anime, daily = "", False, False
    if started:
        out.append("%s %s %s" % (first, started, code))
    if anime and season == 1:
        out.append("%s %02d" % (first, episode))
    if daily and len(meta.get("premiered") or "") == 10:
        out.append("%s %s" % (first, meta["premiered"].replace("-", " ")))
    return out


def _en_all(meta, titles):
    """The main search and the extra ones at the same time, the files of all of them together (each once)."""
    asked = queries(meta, titles)
    with ThreadPoolExecutor(max_workers=min(4, len(asked))) as pool:
        answers = list(pool.map(lambda pair: _en_search(pair[1], EN_PAGES if pair[0] == 0 else 1, pair[0] == 0), enumerate(asked)))
    main = dict(answers[0])
    rows, seen = [], set()
    for number, answer in enumerate(answers):
        for raw in answer.get("data") or []:
            key = str((raw.get("hash") or raw.get("0") or "") if isinstance(raw, dict) else (raw[0] if raw else ""))
            if key and key not in seen:
                seen.add(key)
                rows.append(raw)
        if number == 0:
            first_count = len(rows)
    if not main.get("downURL"):
        main.update({k: v for answer in answers for k, v in answer.items() if k in ("downURL", "dlFarm", "dlPort", "thumbURL")})
    extra = len(rows) - first_count
    if extra > 0:
        util.log("Easynews: %d more file(s) from the extra searches (%s)" % (extra, ", ".join(q.split(" ", 1)[-1] if len(q) < 80 else "?" for q in asked[1:])))
    main["data"] = rows
    return main


EN_SPEED = "easynews_speed.json"        # {"rate": bytes per second of one Easynews download, "at": when measured}
EN_SPEED_DAYS = 7
EN_FIELDS = "easynews_fields.json"      # when the names of Easynews' fields were last written in the log (once a day)


def port_choice():
    """Your own Easynews port (setting 'Easynews port'), or '' for the one Easynews sends: its Route Selector does
    not always keep a choice (November 2026: back to automatic after a refresh)."""
    return re.sub(r"[^0-9]", "", util.setting("easynews_port"))[:5]


def _en_bases(answer):
    """(the download base Lucent uses, the one Easynews sent): downURL/farm/port. Your own port replaces Easynews'."""
    sent = "%s/%s/%s" % (answer.get("downURL") or EASYNEWS + "/dl", answer.get("dlFarm") or "auto", answer.get("dlPort") or "auto")
    port = port_choice()
    return ("%s/%s/%s" % (sent.rsplit("/", 2)[0], answer.get("dlFarm") or "auto", port) if port else sent), sent


def _with_login(base):
    """Login inside the address, not as a header: Kodi hides the first in its log but writes the second out in full."""
    parts = urllib.parse.urlsplit(base)
    user, password = _en_login()
    return "%s://%s:%s@%s%s" % (parts.scheme, urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""), parts.netloc, parts.path)


EN_SPEED_SIZE = 64 * 1048576           # at most this much is read for the measurement (6 s at most)
EN_SPEED_V = 2                          # rc1.33: measured after the first byte, with up to 64 MB (was 4 MB from the start)


def _en_speed(base, sample_path, port=""):
    """Bytes per second this box gets from Easynews, measured once a week with one result (one connection, as Kodi
    plays it): the clock starts at the first byte, so signing in and the redirect do not count, and up to 64 MB or
    6 seconds are read. (Before rc1.33 only 4 MB was read with the clock running from the start: '4.0 MB in 0.6 s',
    6.9 MB/s, while Easynews' own test gave about 39 MB/s.) 0 when unknown."""
    saved = util.load_json(EN_SPEED, {})
    same = str(saved.get("port", port)) == str(port) and saved.get("v") == EN_SPEED_V     # another port: measured again
    if saved.get("rate") and same and time.time() - saved.get("at", 0) < EN_SPEED_DAYS * 86400:
        return saved["rate"]
    if not sample_path:
        return saved.get("rate") or 0
    url, _, extra = ("%s/%s" % (base, sample_path)).partition("|")
    parts = urllib.parse.urlsplit(url)
    head = {"User-Agent": AGENT, "Range": "bytes=0-%d" % (EN_SPEED_SIZE - 1), "Authorization": _en_header()}
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[-1], parts.path, parts.query, ""))
    asked, started, got = time.time(), None, 0
    try:
        with urllib.request.urlopen(urllib.request.Request(clean, headers=head), timeout=8) as response:
            while True:
                block = response.read(262144)
                if not block:
                    break
                if started is None:
                    started = time.time()       # the first data is in: from here the speed of the file itself
                else:
                    got += len(block)
                if time.time() - started >= 6:
                    break
    except OSError as error:
        util.log("Easynews speed: not measured (%s)" % type(error).__name__)
        return saved.get("rate") or 0
    seconds = time.time() - (started or asked)
    if got < 4 * 1048576 or seconds <= 0.2:
        util.log("Easynews speed: too little to measure (%.1f MB)" % (got / 1048576))
        return saved.get("rate") or 0
    rate = got / seconds
    util.save_json(EN_SPEED, dict(saved, rate=rate, at=time.time(), port=port, v=EN_SPEED_V))
    util.log("Easynews speed: %.1f MB/s (%.1f MB in %.1f s after the first byte, which took %.1f s; port %s%s)" % (
        rate / 1048576, got / 1048576, seconds, started - asked, port or "?", ", your own" if port_choice() else ""))
    if rate < EN_SLOW and time.time() - saved.get("tipped", 0) > 30 * 86400:
        util.save_json(EN_SPEED, dict(saved, rate=rate, at=time.time(), port=port, v=EN_SPEED_V, tipped=time.time()))
        util.notify(T("Easynews: %.1f MB/s. Een snellere server kiezen? Zie Accountinformatie.",
                      "Easynews: %.1f MB/s. Choose a faster server? See Account information.") % (rate / 1048576))
    return rate


EN_SLOW = 10 * 1048576                  # below this a 4K Remux may stop to buffer: a tip about Easynews' Route Selector
ROUTE = "members.easynews.com/route/"


def speed_lines():
    """For Account information: the measured speed, and when it is low, where to choose another Easynews server."""
    saved = util.load_json(EN_SPEED, {})
    if not saved.get("rate"):
        return []
    if saved.get("v") != EN_SPEED_V:            # a measurement of an older version: done again at the next search
        return [T("Snelheid: wordt opnieuw gemeten bij de volgende zoekopdracht met Easynews",
                  "Speed: measured again at the next search with Easynews")]
    days = int((time.time() - saved.get("at", 0)) // 86400)
    when = T("vandaag gemeten", "measured today") if days < 1 else T("%d dag(en) geleden gemeten", "measured %d day(s) ago") % days
    lines = [T("Snelheid: %.1f MB/s (%s)", "Speed: %.1f MB/s (%s)") % (saved["rate"] / 1048576, when)]
    if saved["rate"] < EN_SLOW:
        lines.append(T("Tip: kies op %s (Route Selector) een andere regio of poort, vaak gaat het dan sneller. "
                       "Lucent volgt je keuze vanzelf en meet binnen een week opnieuw.",
                       "Tip: on %s (Route Selector) choose another region or port, it is often faster then. "
                       "Lucent follows your choice by itself and measures again within a week.") % ROUTE)
    return lines


def _en_fields(answer):
    """Once a day: which fields Easynews sends per file (names only, never values), to build on what is really there."""
    rows = [r for r in answer.get("data") or [] if isinstance(r, dict)]
    saved = util.load_json(EN_FIELDS, {})
    if not rows or (time.time() - saved.get("at", 0) < 86400 and saved.get("v") == 3):
        return
    util.save_json(EN_FIELDS, {"at": time.time(), "v": 3})
    util.log("Easynews gives: %s; top: %s" % (", ".join(sorted(rows[0])), ", ".join(sorted(k for k in answer if k != "data")[:40])))
    # what some fields look like (file facts only, never an address or the login), to build on them
    sample = rows[0]
    shown = {k: str(sample.get(k))[:60] for k in ("acodec", "vcodec", "xres", "yres", "height", "width", "hz", "bps", "fps", "fullres", "meta",
                                                   "disposition") if k in sample}
    util.log("Easynews sample: %s; dlFarm %s, dlPort %s" % (shown, str(answer.get("dlFarm"))[:20], str(answer.get("dlPort"))[:10]))
    spare = str(sample.get("fallbackURL") or "")
    if spare:                                   # its shape only: the host and how many parts the path has, never the address
        parts = urllib.parse.urlsplit(spare)
        util.log("Easynews fallbackURL: %s, host %s, %d path part(s), %s" % (
            "absolute" if parts.scheme else "relative", parts.hostname or "-", len([p for p in parts.path.split("/") if p]),
            "with a query" if parts.query else "no query"))


def _en_langs(value):
    """Two-letter codes of a list of Easynews languages ('eng', 'dut', 'English' ...), in order, without doubles."""
    out = []
    if isinstance(value, str):
        value = [part for part in value.split(",")]
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
    base = str(answer.get("thumbURL") or "https://th.easynews.com/thumbnails-")
    key = str(raw.get("hash") or raw.get("0") or "")
    if len(key) < 3:
        return ""
    return "%s%s/pr-%s.jpg" % (base, key[:3], key)        # the short form Fen and POV use: no file name, no login


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
    data, tried = b"", []
    # the pictures are public for players such as Stremio (no login); with the login as a second try
    for head in ({"User-Agent": "Mozilla/5.0"}, {"User-Agent": "Mozilla/5.0", "Referer": "https://members.easynews.com/"},
                 {"Authorization": _en_header(), "User-Agent": AGENT}):
        try:
            with urllib.request.urlopen(urllib.request.Request(url, headers=head), timeout=15) as response:
                data = response.read(8 * 1048576)
                tried.append("%s %d bytes %s" % (response.status, len(data), response.headers.get("Content-Type") or "?"))
        except urllib.error.HTTPError as error:
            tried.append("HTTP %s" % error.code)
            continue
        except OSError as error:
            tried.append(type(error).__name__)
            continue
        if len(data) >= 500:
            break
    host = urllib.parse.urlsplit(url).hostname or "?"
    util.log("preview from %s: %s" % (host, "; ".join(tried)))      # never the address: it holds the file name only, but keep it short
    if len(data) < 500:
        raise ValueError("no picture")
    with open(path, "wb") as handle:
        handle.write(data)
    return path


VIDEO_CODECS = (("HEVC", "HEVC"), ("H265", "HEVC"), ("H.265", "HEVC"), ("X265", "HEVC"), ("AV1", "AV1"), ("AVC", "AVC"), ("H264", "AVC"),
                ("H.264", "AVC"), ("X264", "AVC"))
AUDIO_CODECS = (("TRUEHD", "TRUEHD"), ("MLP", "TRUEHD"), ("DTS-X", "DTS-X"), ("DTS:X", "DTS-X"), ("DTS-HD MA", "DTS-HDMA"),
                ("DTS-HD", "DTS-HD"), ("DTSHD", "DTS-HD"), ("DTS", "DTS"), ("E-AC-3", "DD+"), ("EAC3", "DD+"), ("DD+", "DD+"), ("AC-3", "DD"),
                ("AC3", "DD"), ("FLAC", "FLAC"), ("OPUS", "OPUS"), ("AAC", "AAC"))


def _en_number(value):
    try:
        return int(float(str(value).strip()))
    except (TypeError, ValueError):
        return 0


def _en_tech(raw, source):
    """What Easynews read from the file itself: the video and sound codec, and the real size of the picture. Adds
    labels the name does not have; a '4K' that is not 4K (1920 wide) is put under 1080p, with a note."""
    flags = source["flags"]
    video = str(raw.get("vcodec") or raw.get("12") or "").upper()
    if video and not set(flags) & {"HEVC", "AVC", "AV1"}:
        found = next((flag for word, flag in VIDEO_CODECS if word in video), "")
        if found:
            flags.append(found)
    sound = str(raw.get("acodec") or raw.get("18") or "").upper()
    if "ATMOS" in sound and "ATMOS" not in flags:
        flags.append("ATMOS")                   # on top of the codec it rides on (TrueHD or DD+)
    for word, flag in AUDIO_CODECS:
        if word in sound:
            if flag not in flags and not (flag == "DTS" and set(flags) & {"DTS-HD", "DTS-HDMA", "DTS-X"}) \
                    and not (flag == "DD" and "DD+" in flags) and not (flag == "TRUEHD" and "TRUEHD" in flags):
                flags.append(flag)
            break                               # the first (best) match only: 'DTS-HD MA' is not also 'DTS'
    width = _en_number(raw.get("xres") or raw.get("width"))
    height = _en_number(raw.get("yres") or raw.get("height"))
    if not width and "x" in str(raw.get("fullres") or ""):
        width, height = (_en_number(part) for part in str(raw["fullres"]).split("x", 1))
    if not width:
        return
    real = "4K" if width >= 3200 else "1080p" if width >= 1800 else "720p" if width >= 1200 else "SD"
    order = ["4K", "1080p", "720p", "SD"]
    named = source.get("resolution") or "SD"
    if named in order and order.index(real) > order.index(named):
        source["resolution"] = real             # the name promises more than the picture has
        source["meta"] += " · " + T("geen echte %s (%d×%d)", "not real %s (%d×%d)") % (named, width, height)
    elif named == "SD" and real != "SD":
        source["resolution"] = real             # the name says nothing: the picture tells


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
    if meta["kind"] != "movie":
        try:
            meta = dict(meta, anime=_show_facts(meta)[1])
        except util.HttpError:
            pass
    reading = _check_data()                 # beside the search, finished before Easynews' results are returned
    answer = _en_all(meta, titles)
    own, sent = _en_bases(answer)
    base = _with_login(own)
    original = _with_login(sent) if sent != own else ""     # Easynews' own port: tried when yours fails
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
        source = dict(_source(name, size, "EASYNEWS", url="%s/%s|%s" % (base, path, agent)), always=True, seconds=seconds or 0, _path=path,
                      en_key=path.split("/", 1)[0])
        if original:
            source["en_original"] = "%s/%s|%s" % (original, path, agent)
        if label:
            source["meta"] += " · " + label
        if seconds and size:
            source["meta"] += " · %.0f Mbps" % (size * 8.0 / seconds / 1e6)      # the average bitrate of the whole file
        spare = str(raw.get("fallbackURL") or "") if isinstance(raw, dict) else ""
        if spare:
            source["en_fallback"] = spare       # Easynews' second address for this file: tried when the link fails
        sig = str(raw.get("sig") or "") if isinstance(raw, dict) else ""
        source["en_nzb"] = {"hash": key, "fn": stem, "ext": extension.lstrip("."), "sig": sig, "season": meta.get("season"),
                            "episode": meta.get("episode"), "minutes": int(meta.get("runtime") or 0)}
        if isinstance(raw, dict):
            # what is really inside the file (Easynews reads the tracks), not guessed from the name
            sound = _en_langs(raw.get("audio_tracks") or raw.get("alangs") or raw.get("alang"))
            subs = _en_langs(raw.get("subtitle_tracks") or raw.get("slangs") or raw.get("slang"))
            mine = mylang.code()
            if mine in sound and mylang.audio_flag() not in source["flags"]:
                source["flags"].append(mylang.audio_flag())
            if mine in subs and mylang.subs_flag() not in source["flags"]:
                source["flags"].append(mylang.subs_flag())
            if len(sound) > 1 or (sound and sound[0] != "en"):
                # the sound tracks as badges (DE, EN ...), like the language badges of other sources
                for code in sound[:4]:
                    flag = "aio_%s" % code.upper()
                    if code in mylang.LANGS and flag not in source["flags"]:
                        source["flags"].append(flag)
            _en_tech(raw, source)
            preview = _en_preview(answer, raw)
            if preview:
                source["preview"] = preview
        out.append(source)
    if short:
        util.log("Easynews: %d result(s) left out: far shorter than the film or episode" % short)
    rate = _en_speed(base, out[0]["_path"] if out else "", own.rsplit("/", 2)[-1]) if out else 0
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
    if reading:
        reading.join(5)                     # no thread left running when Kodi ends the add-on
    return out


def _cells(html, start, stop="</"):
    """The texts of the <td> cells after a marker in a page (no HTML library on every box)."""
    at = html.find(start)
    if at < 0:
        return []
    part = html[at:]
    cells = re.findall(r"<td[^>]*>(.*?)</td>", part, re.S | re.I)
    return [re.sub(r"\s+", " ", re.sub(r"<[^>]+>", " ", c)).strip() for c in cells]


def easynews_account():
    """(user, plan, expires, status, used, left, loyalty) from Easynews' own account pages, the way Fen and Umbrella
    read them (Easynews has no account API). Empty strings for what could not be read."""
    import urllib.request as request
    def page(url, marker):
        with request.urlopen(request.Request(url, headers={"Authorization": _en_header(), "User-Agent": "Mozilla/5.0"}), timeout=15) as answer:
            html = answer.read(500000).decode("utf-8", "replace")
            where = urllib.parse.urlsplit(answer.geturl())
        cells = _cells(html, marker)
        # what came back (never a value): to see whether Easynews sent its login page or changed its layout
        util.log("Easynews account page %s: HTTP %s from %s%s, %d KB, marker %s, %d cell(s)" % (
            url.rsplit("/", 1)[-1], answer.status, where.hostname, where.path, len(html) // 1024,
            "found" if marker in html else "missing", len(cells)))
        return cells
    info = page("https://account.easynews.com/editinfo.php", 'id="accountForm"')[0:11][1::3]
    usage = page("https://account.easynews.com/usageview.php", 'class="table-responsive"')[0:11][1::3]
    info += [""] * (4 - len(info))
    usage += [""] * (3 - len(usage))
    return info[0], info[1], info[2], info[3], usage[0].replace("Gigs", "GB"), usage[1].replace("Gigs", "GB"), usage[2].replace("Gigs", "GB")


EN_DATA = "easynews_data.json"          # {"at": when read, "left": GB left, "warned": when the warning was shown}
EN_DATA_LOW = 10.0                      # GB: below this a warning that your Easynews data is almost used up


def _gigabytes(text):
    found = re.search(r"([\d.,]+)\s*([KMGT])?", str(text or ""), re.I)
    if not found:
        return None
    number = float(found.group(1).replace(",", ""))
    return number * {"K": 1e-6, "M": 1e-3, "G": 1.0, "T": 1e3}.get((found.group(2) or "G").upper(), 1.0)


def _check_data():
    """Once a day, beside a search (never in its way): how much data is left at Easynews; a warning below 10 GB,
    at most once a day."""
    saved = util.load_json(EN_DATA, {})
    if time.time() - saved.get("at", 0) < 86400:
        return None

    def run():
        try:
            left = _gigabytes(easynews_account()[5])
        except Exception as error:      # noqa: BLE001 - only a warning
            util.log("Easynews data left: not read (%s)" % type(error).__name__)
            util.save_json(EN_DATA, dict(saved, at=time.time()))
            return
        now = dict(saved, at=time.time(), left=left)
        if left is not None and left < EN_DATA_LOW and time.time() - saved.get("warned", 0) > 86400:
            now["warned"] = time.time()
            util.notify(T("Easynews: nog %.1f GB over", "Easynews: %.1f GB left") % left)
        util.log("Easynews data left: %s" % ("%.1f GB" % left if left is not None else "unknown"))
        util.save_json(EN_DATA, now)
    import threading
    job = threading.Thread(target=run, daemon=True)
    job.start()
    return job


def direct(url):
    """Where an Easynews link really points (its CDN), followed once like Fen, Umbrella and AIOStreams do: that address
    needs no login and Kodi can jump through the file without sending the login on every jump. The Easynews link
    itself when the CDN does not answer as a file (206)."""
    address, _, extra = url.partition("|")
    parts = urllib.parse.urlsplit(address)
    if not parts.username:
        return url
    clean = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[1], parts.path, parts.query, ""))

    class Stop(urllib.request.HTTPRedirectHandler):
        def redirect_request(self, *args, **kwargs):
            return None
    head = {"Authorization": _en_header(), "User-Agent": AGENT, "Range": "bytes=0-0"}
    try:
        urllib.request.build_opener(Stop).open(urllib.request.Request(clean, headers=head), timeout=10).close()
        util.log("Easynews: no redirect, the link itself is played")
        return url
    except urllib.error.HTTPError as error:
        target = error.headers.get("Location") if error.code in (301, 302, 303, 307, 308) else ""
    except OSError as error:
        util.log("Easynews: redirect not read (%s)" % type(error).__name__)
        return url
    if not target:
        return url
    target = urllib.parse.urljoin(clean, target)
    try:
        with urllib.request.urlopen(urllib.request.Request(target, headers={"User-Agent": AGENT, "Range": "bytes=0-0"}), timeout=10) as answer:
            works = answer.status == 206
    except OSError as error:
        util.log("Easynews: CDN did not answer (%s), the link itself is played" % type(error).__name__)
        return url
    host = urllib.parse.urlsplit(target).hostname or "?"
    if not works:
        util.log("Easynews: CDN %s does not jump through files, the link itself is played" % host)
        return url
    util.log("Easynews: playing from %s (followed once, no login needed there)" % host)
    return target + ("|" + extra if extra else "")


def _read_head(url, size):
    """The first `size` bytes of an Easynews file (login from the address, in a header; never in the log)."""
    address, _, _extra = url.partition("|")
    parts = urllib.parse.urlsplit(address)
    head = {"User-Agent": AGENT, "Range": "bytes=0-%d" % (size - 1)}
    if parts.username:
        login = "%s:%s" % (urllib.parse.unquote(parts.username), urllib.parse.unquote(parts.password or ""))
        head["Authorization"] = "Basic " + base64.b64encode(login.encode("utf-8")).decode("ascii")
        address = urllib.parse.urlunsplit((parts.scheme, parts.netloc.rsplit("@", 1)[1], parts.path, parts.query, ""))
    with urllib.request.urlopen(urllib.request.Request(address, headers=head), timeout=15) as answer:
        return answer.read(size)


def dovi_kind(source):
    """'fel', 'mel', 'p7' (profile 7, not read) or '' for an Easynews MKV, from its first megabytes, the same way as for
    Usenet releases (nzb/tracks.py: the Dolby Vision metadata). Kept for two weeks."""
    from nzb import tracks
    import hashlib
    key = "endovi" + hashlib.sha1((source.get("en_key") or source.get("name") or "").encode("utf-8")).hexdigest()[:16]
    known = util.cache_get(key, 14 * 86400)
    if known is not None:
        return known
    url = source.get("en_original") or source.get("url") or ""
    data = _read_head(url, 3 * 1048576)
    found = tracks.read(data) or {}
    kind = ""
    if found.get("dv") == 7 and found.get("video"):
        number, nal_size = found["video"]
        kind = tracks.dolby(data, number, nal_size, found["after"])
        if not kind:
            data = _read_head(url, 12 * 1048576)
            kind = tracks.dolby(data, number, nal_size, found["after"])
        kind = kind or "p7"
    util.cache_set(key, kind)
    return kind


def fallback_link(spare):
    """The playable address of Easynews' fallbackURL (its second address for a file): absolute or under members, the
    login inside the address for Easynews' own hosts (Kodi hides it in its log)."""
    address = urllib.parse.urljoin(EASYNEWS + "/", spare)
    parts = urllib.parse.urlsplit(address)
    if not parts.scheme.startswith("http"):
        return ""
    if (parts.hostname or "").endswith("easynews.com") and not parts.username:
        user, password = _en_login()
        login = "%s:%s@" % (urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""))
        address = urllib.parse.urlunsplit((parts.scheme, login + parts.netloc, parts.path, parts.query, ""))
    return address + "|" + urllib.parse.urlencode({"User-Agent": AGENT})


def nzb_link(job):
    """An Easynews file played over Usenet with your own provider and vfs.lucent: Easynews makes the NZB
    (POST /2.0/api/dl-nzb, like AIOStreams and UsenetStreamer), Lucent plays it like any other NZB. A second way
    when the web link of Easynews fails."""
    from nzb import source as nzb_source
    plain = lambda text: base64.b64encode(text.encode("utf-8")).decode("ascii").replace("=", "")      # noqa: E731
    token = "%s|%s:%s" % (job["hash"], plain(job["fn"]), plain(job["ext"]))
    form = urllib.parse.urlencode([("autoNZB", "1"), ("0&sig=%s" % job["sig"] if job.get("sig") else "0", token),
                                   ("nameZipQ0", job["fn"][:120])]).encode("ascii")
    head = {"Authorization": _en_header(), "User-Agent": "Mozilla/5.0", "Content-Type": "application/x-www-form-urlencoded",
            "Accept": "application/x-nzb, */*"}
    link = "easynews:%s" % job["hash"]
    play = {"scraper": "Usenet", "link": link, "name": job["fn"] + "." + job["ext"], "season": job.get("season"),
            "episode": job.get("episode"), "minutes": job.get("minutes") or 0, "indexer": "Easynews"}
    name = nzb_source._nzb_name(play)                                       # noqa: SLF001 - same add-on
    if not nzb_source._saved_nzb(name):                                     # noqa: SLF001
        try:
            with urllib.request.urlopen(urllib.request.Request(EASYNEWS + "/2.0/api/dl-nzb", data=form, headers=head), timeout=30) as answer:
                data = answer.read(64 * 1048576)
        except OSError as error:
            util.log("Easynews NZB: not made (%s)" % (getattr(error, "code", "") or type(error).__name__))
            return None
        if b"<segment" not in data:
            util.log("Easynews NZB: empty (no articles)")
            return None
        util.log("Easynews NZB: %d KB" % (len(data) // 1024))
        nzb_source._save_nzb(name, data)                                    # noqa: SLF001
    return nzb_source.link(play)


def can_nzb():
    """Is there a Usenet provider filled in, so an Easynews NZB can be played by vfs.lucent?"""
    return bool(util.flag("nzb_on") and util.setting("nzb_host"))


def free_search(query, kind="VIDEO", pages=1):
    """Easynews searched for your own words (no title from TMDB): for files that do not match, like WALL·E."""
    return _en_search3(clean_query(query), pages, kind)


def free_sources(query):
    """Playable sources for your own words: the same checks as the normal search, without the title check."""
    try:
        answer = free_search(query, pages=2)
    except util.HttpError as error:
        util.log("Easynews search: %s - asking 2.0" % error)
        answer = _en_search2(query)
    base = _en_bases(answer)[0]
    out = []
    for raw in answer.get("data") or []:
        item = _en_item(raw)
        if not item:
            continue
        key, suffix, stem, extension, size, bad, seconds = item
        extension = "." + extension.lstrip(".")
        if not key or bad or extension.lower() not in VIDEO:
            continue
        name = stem + extension
        source = _source(name, size, "EASYNEWS")
        source.update(path="%s%s%s/%s" % (key, suffix, extension, urllib.parse.quote(name)), base=base, seconds=seconds or 0)
        if seconds:
            source["meta"] += " · %d min" % (seconds // 60)
        if isinstance(raw, dict):
            _en_tech(raw, source)
            source["preview"] = _en_preview(answer, raw)
        out.append(source)
    util.log("Easynews search: %d file(s) for your own words" % len(out))
    return out


def play_url(base, path):
    """The Easynews link of a file found with your own words (the login inside the address: Kodi hides it in its log)."""
    parts = urllib.parse.urlsplit(base)
    user, password = _en_login()
    login = "%s:%s@" % (urllib.parse.quote(user, safe=""), urllib.parse.quote(password, safe=""))
    return "%s://%s%s%s/%s|%s" % (parts.scheme, login, parts.netloc, parts.path, path, urllib.parse.urlencode({"User-Agent": AGENT}))


def pictures(name):
    """Easynews' photos of a person (pictures in the newsgroups), like Fen shows them: (small picture, large picture)."""
    answer = free_search(name, "IMAGE", 1)
    out = []
    for raw in answer.get("data") or []:
        if not isinstance(raw, dict):
            continue
        key = str(raw.get("hash") or "")
        if len(key) < 3:
            continue
        out.append(("https://th.easynews.com/thumbnails-%s/sm-%s.jpg" % (key[:3], key), "https://th.easynews.com/thumbnails-%s/pr-%s.jpg" % (key[:3], key)))
    return out[:60]


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
