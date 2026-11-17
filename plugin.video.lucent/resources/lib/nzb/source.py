# -*- coding: utf-8 -*-
"""Usenet as a scraper: your indexers give the sources, your provider plays them (via the service's local server).

fetch(meta)  -> source dicts for the list (None when Usenet is not set up), like the other scrapers
link(job)    -> when you press play: fetch the NZB, let the service open the release, give Kodi the local address
"""
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import ranking
import streams
import usenet
import util

from . import newznab, server

SERVICE = "USENET"


def configured():
    return util.lab() and util.flag("nzb_on") and bool(util.setting("nzb_host")) and newznab.configured()


def is_pack(name, titles, meta):
    """'Show.S02.1080p...' or 'Show Season 2': the whole season (not an episode, not several seasons)."""
    words = usenet._words(name)                      # noqa: SLF001 - same add-on
    season = int(meta["season"])
    for title in titles:
        wanted = usenet._words(title)                # noqa: SLF001
        if not wanted or words[:len(wanted)] != wanted:
            continue
        rest = " ".join(words[len(wanted):])
        if re.search(r"\bs\d{1,2} ?e\d{1,3}\b|\b\d{1,2}x\d{2,3}\b|\bs\d{1,2} ?s\d{1,2}\b|\bcomplete series\b", rest):
            return False
        if re.search(r"\bs0*%d\b|\bseason 0*%d\b" % (season, season), rest):
            return True
    return False


def _badges(row):
    """'120 d · 340×': how many days ago it was posted and how often it was downloaded (when the indexer says)."""
    out = []
    try:
        import email.utils
        import time as _time
        when = email.utils.parsedate_to_datetime(row.get("posted") or "")
        days = int((_time.time() - when.timestamp()) // 86400)
        if days >= 0:
            out.append(util.T("%d d", "%d d") % days)
    except (TypeError, ValueError, OverflowError):
        pass
    if row.get("grabs"):
        out.append("%d×" % int(row["grabs"]))
    return " · ".join(out)


def _group(name):
    """The release group (the end of the name, often cut off on screen) for the line under it."""
    group = ranking.release_group({"name": name})
    return group if group and not group.isdigit() else ""


def _indexer_name(base):
    """'NZBGEEK' for https://api.nzbgeek.info/api: the site's own name, for the source list."""
    host = (urllib.parse.urlsplit(base).hostname or "").lower()
    if re.fullmatch(r"[\d.:]+", host):
        return host                        # an address of your own (a local indexer)
    parts = [p for p in host.split(".") if p not in ("www", "api", "feed", "feeds")]
    if len(parts) >= 2:
        parts = parts[:-1]                 # without .info / .com / .net
    if len(parts) >= 2 and parts[-1] in ("co", "com", "net", "org", "ac", "gov", "or", "ne"):
        parts = parts[:-1]                 # without the .co of .co.za / .co.uk (althub.co.za is ALTHUB, not CO)
    name = parts[-1] if parts else host
    if len(name) <= 3 and len(host.split(".")) >= 2:      # nzb.life, nzb.su: 'NZB' alone says nothing, keep the end
        name = ".".join(p for p in host.split(".") if p not in ("www", "api", "feed", "feeds"))
    return name.upper()[:20]


def fetch(meta):
    if not configured():
        return None
    ixs = newznab.indexers()
    titles = usenet.titles_for(meta)

    def ask(entry):
        slot, base, key = entry
        try:
            return newznab.search(slot, base, key, meta, titles, background=bool(meta.get("background")))
        except newznab.IndexerError as error:
            if str(error) == "daily limit":
                why = newznab.pause_text(base) or "Lucent's own stop at 90% of the limit"
                util.log("usenet: indexer %d: not asked, %s; %s today" % (slot, why, newznab.usage(base, log=True)))
            else:
                util.log("usenet: indexer %d: %s%s" % (slot, error, " (the API key is not accepted)" if str(error) in ("HTTP 401", "HTTP 403") else ""))
            return None

    def fits(name):
        return (meta["kind"] != "movie" and is_pack(name, titles, meta)) or usenet.title_matches(name, titles, meta)

    if util.number("nzb_search", 0) == 1 or len(ixs) == 1:
        with ThreadPoolExecutor(max_workers=len(ixs)) as pool:
            answers = list(pool.map(ask, ixs))
    else:
        # one at a time: stop as soon as there are enough sources, so a free indexer's daily quota lasts
        enough = max(1, util.number("nzb_enough", 10))
        answers, names_found = [], set()
        for entry in ixs:
            rows = ask(entry)
            answers.append(rows)
            names_found.update(r["title"].lower() for r in rows or [] if fits(r["title"]))
            if len(names_found) >= enough:
                skipped = len(ixs) - len(answers)
                if skipped:
                    util.log("usenet: %d sources after %d indexer(s), the other %d not asked" % (len(names_found), len(answers), skipped))
                break
    if all(a is None for a in answers):
        raise util.HttpError("indexers")
    names = {slot: _indexer_name(base) for slot, base, _key in ixs}
    most = server.most_per_second()
    show_badges = util.flag("nzb_badges")
    seconds = int(meta.get("runtime") or 0) * 60
    out, seen = [], set()
    dead, skipped = _dead_now(), 0
    for rows in answers:
        for row in rows or []:
            name = row["title"]
            key = name.lower()
            pack = meta["kind"] != "movie" and is_pack(name, titles, meta)
            if key in seen or not fits(name):
                continue
            if _dead_key(row["link"]) in dead:
                skipped += 1                    # found to be taken down before: not shown again
                continue
            low = name.lower()
            if any(word in low for word in (".iso", " iso ", "sample")):
                continue
            seen.add(key)
            job = {"scraper": "Usenet", "slot": row["slot"], "link": row["link"], "param": row["param"], "name": name,
                   "season": meta.get("season"), "episode": meta.get("episode"), "minutes": int(meta.get("runtime") or 0)}
            where = names.get(row["slot"]) or ""
            badges = _badges(row) if show_badges else ""
            # needs more per second than this box ever got from Usenet: say so (it may stop to buffer)
            heavy = bool(most and seconds and row["size"] / seconds > most * 0.95)
            warn = util.T("mogelijk te zwaar", "may be too heavy") if heavy else ""
            if not pack:
                source = usenet._source(name, row["size"], SERVICE, resolve=job)          # noqa: SLF001 - same add-on
                _add_info(source, row)
                source["meta"] = " · ".join(p for p in (SERVICE, where, streams.size_text(row["size"]), _group(row["title"]), badges, warn) if p)
                source["always"] = True
                out.append(source)
                continue
            # a pack: rank it by the size of one episode, show the size of the whole season
            job["pack"] = True
            episodes = int(meta.get("season_count") or 0) or 10
            source = usenet._source(name, row["size"] // episodes, SERVICE, resolve=job)    # noqa: SLF001
            _add_info(source, row)
            if most and seconds and row["size"] / episodes / seconds > most * 0.95:
                warn = util.T("mogelijk te zwaar", "may be too heavy")
            else:
                warn = ""
            source["meta"] = " · ".join(p for p in (SERVICE, where, util.T("Seizoenspakket", "Season pack"), streams.size_text(row["size"]), _group(row["title"]), badges, warn) if p)
            source["pack"] = True
            source["always"] = True
            out.append(source)
    if skipped:
        util.log("usenet: %d release(s) left out that were found taken down before" % skipped)
    return out


DUTCH = ("dutch", "nederlands", "flemish", "vlaams", "nl", "nld", "dut")
AUDIO_INFO = (("atmos", "ATMOS"), ("truehd", "TRUEHD"), ("dts:x", "DTS-X"), ("dts-x", "DTS-X"), ("dts-hd ma", "DTS-HDMA"), ("dts-hd", "DTS-HD"),
              ("e-ac-3", "DD+"), ("eac3", "DD+"), ("dd+", "DD+"), ("ac-3", "DD"), ("ac3", "DD"), ("aac", "AAC"), ("opus", "OPUS"))
VIDEO_INFO = (("hevc", "HEVC"), ("h.265", "HEVC"), ("x265", "HEVC"), ("avc", "AVC"), ("h.264", "AVC"), ("x264", "AVC"), ("av1", "AV1"))


def _info_flags(info):
    """Labels from what the indexer itself says is inside (Newznab extended attributes), next to those from the name."""
    out = []
    languages = [w.strip().lower() for w in re.split(r"[,/|;-]", info.get("language") or "") if w.strip()]
    if any(w in DUTCH for w in languages):
        out.append("aio_NL")
    if len(set(languages)) > 1:
        out.append("aio_MULTI")
    subs = [w.strip().lower() for w in re.split(r"[,/|;\s-]+", info.get("subs") or "") if w.strip()]
    if any(w in DUTCH for w in subs):
        out.append("aio_NL-SUBS")
    audio = (info.get("audio") or "").lower()
    for mark, flag in AUDIO_INFO:
        if mark in audio and flag not in out:
            if flag == "DD" and "DD+" in out or flag == "DTS-HD" and "DTS-HDMA" in out:
                continue
            out.append(flag)
    for mark, flag in (("7.1", "7.1"), ("5.1", "5.1"), ("2.0", "2.0")):
        if mark in audio:
            out.append(flag)
            break
    video = (info.get("video") or "").lower()
    for mark, flag in VIDEO_INFO:
        if mark in video:
            out.append(flag)
            break
    return out


AUDIO_FLAGS = {"ATMOS", "TRUEHD", "DTS-X", "DTS-HDMA", "DTS-HD", "DTS", "DD+", "DD", "AAC", "OPUS"}
CHANNEL_FLAGS = {"7.1", "5.1", "2.0"}
VIDEO_FLAGS = {"HEVC", "AVC", "AV1"}


def _add_info(source, row):
    info = row.get("info") or {}
    if not info:
        return
    have = set(source["flags"])
    for flag in _info_flags(info):
        # sound, channels and codec from the indexer only when the release name says nothing about them: otherwise a
        # second audio track (an extra AC-3, a stereo commentary) gives badges that do not match the name
        for group in (AUDIO_FLAGS, CHANNEL_FLAGS, VIDEO_FLAGS):
            if flag in group and have & group:
                break
        else:
            if flag not in source["flags"]:
                source["flags"].append(flag)
    if source.get("resolution") == "SD":
        found = streams.resolution({}, info.get("resolution") or "")
        if found != "SD":
            source["resolution"] = found


# ------------------------------------------------------------------ releases that are gone (taken down)
DEAD = "nzb_dead.json"
DEAD_DAYS = 60
CHECK_TOP = 8               # look among this many best sources
CHECK_MOST = 4              # check at most this many Usenet releases there
CHECK_SAMPLE = 8            # articles per release
CHECK_SECONDS = 8           # the whole check never takes longer than this
NZB_FRESH = 7 * 86400       # an NZB never changes: one that was fetched is used again for a week (indexers warn
                            # and can close an account when the same NZB is fetched over and over)
ALIVE = "nzb_alive.json"
ALIVE_HOURS = 24            # a release found complete is not checked (nor its NZB fetched) again for this long


def _nzb_name(job):
    return hashlib.sha1(job["link"].encode("utf-8")).hexdigest()[:16] + ".nzb"


def _saved_nzb(name):
    path = os.path.join(server.folder(), name)
    try:
        return os.path.getsize(path) > 0 and time.time() - os.path.getmtime(path) < NZB_FRESH
    except OSError:
        return False


def _save_nzb(name, data):
    path = os.path.join(server.folder(), name)
    with open(path + ".tmp", "wb") as handle:
        handle.write(data)
    os.replace(path + ".tmp", path)


def _dead_key(link):
    return hashlib.sha1(link.encode("utf-8")).hexdigest()[:16]


def _mark_dead(job):
    dead = util.load_json(DEAD, {})
    now = time.time()
    dead = {k: v for k, v in dead.items() if now - v < DEAD_DAYS * 86400}
    dead[_dead_key(job["link"])] = now
    util.save_json(DEAD, dead)


def _alive_recent():
    now = time.time()
    return {k for k, v in util.load_json(ALIVE, {}).items() if now - v < ALIVE_HOURS * 3600}


def _mark_alive(job):
    alive = util.load_json(ALIVE, {})
    now = time.time()
    alive = {k: v for k, v in alive.items() if now - v < ALIVE_HOURS * 3600}
    alive[_dead_key(job["link"])] = now
    util.save_json(ALIVE, alive)


def _dead_now():
    return util.load_json(DEAD, {})


def _sample_ids(chosen):
    """A few article ids spread over the release: the first, the last and some in between. Only the files they come
    from are read (a 100 GB release has 120,000 articles; reading them all takes a slow box seconds)."""
    if not chosen:
        return []
    if len(chosen) == 1:
        flat = [seg[2] for seg in chosen[0].segments]
        if not flat:
            return []
        step = max(1, len(flat) // CHECK_SAMPLE)
        return list(dict.fromkeys([flat[0], flat[-1]] + flat[::step]))[:CHECK_SAMPLE + 2]
    step = max(1, len(chosen) // CHECK_SAMPLE)
    picked = list(dict.fromkeys(chosen[::step] + [chosen[-1]]))
    ids = []
    for number, posted in enumerate(picked):
        segments = posted.segments
        if not segments:
            continue
        if number == 0:
            ids.append(segments[0][2])
        ids.append(segments[len(segments) // 2][2])
        if posted is chosen[-1]:
            ids.append(segments[-1][2])
    return list(dict.fromkeys(ids))[:CHECK_SAMPLE + 2]


def _alive(job, pool, spare):
    """True / False (taken down) / None (could not tell) for one release, from a few articles spread over it."""
    from . import nzbfile, stream
    name = _nzb_name(job)
    try:
        if not _saved_nzb(name):
            match = next((base for slot, base, _key in newznab.indexers() if slot == job["slot"]), None)
            if not match or not newznab.grab_room(match):
                return None
            _save_nzb(name, newznab.nzb(job))
        with open(os.path.join(server.folder(), name), "rb") as handle:
            files = nzbfile.parse(handle.read())
        _kind, chosen = nzbfile.pick(files, job.get("season"), job.get("episode"), job.get("pack"))
    except Exception:                           # noqa: BLE001 - hidden names, no room, an odd NZB: simply not checked
        return None
    ids = _sample_ids(chosen)
    if not ids:
        return None
    try:
        answer = stream.Release._stat_spread(pool, ids)                    # noqa: SLF001 - same package
        missing = [i for i, ok in answer.items() if not ok]
        if missing and spare:
            extra = stream.Release._stat_spread(spare, missing)            # noqa: SLF001
            missing = [i for i in missing if not extra.get(i)]
    except Exception:                           # noqa: BLE001
        return None
    return len(missing) <= len(ids) // 2


def prune(ranked, meta):
    """Before the list is shown (and before automatic start picks one): check the best Usenet releases and leave out
    the ones that were taken down. Within CHECK_SECONDS; what could not be checked in time simply stays."""
    from concurrent.futures import wait
    from . import nntp
    if not configured() or meta.get("background") or not ranked:
        return ranked
    jobs = [s for s in ranked[:CHECK_TOP] if (s.get("resolve") or {}).get("scraper") == "Usenet"][:CHECK_MOST]
    alive_before = _alive_recent()
    jobs = [s for s in jobs if _dead_key(s["resolve"]["link"]) not in alive_before]
    if not jobs:
        return ranked
    started = time.time()
    pool = nntp.Pool(server.provider(), limit=8)
    spare_settings = server.provider(backup=True)
    spare = nntp.Pool(spare_settings, limit=4) if spare_settings else None
    run = ThreadPoolExecutor(max_workers=len(jobs))
    futures = {run.submit(_alive, s["resolve"], pool, spare): s for s in jobs}
    done, _late = wait(futures, timeout=CHECK_SECONDS)
    for future in futures:
        future.cancel()
    run.shutdown(wait=False)
    gone = []
    for future in done:
        try:
            alive = future.result()
        except Exception:                       # noqa: BLE001
            alive = None
        if alive is False:
            gone.append(futures[future])
            _mark_dead(futures[future]["resolve"])
        elif alive:
            _mark_alive(futures[future]["resolve"])
    pool.close()
    if spare:
        spare.close()
    util.log("usenet: checked %d of the best releases in %.1f s: %d taken down, left out" % (len(done), time.time() - started, len(gone)))
    return [s for s in ranked if not any(s is g for g in gone)]


def _port():
    return util.HOME.getProperty(server.PORT_PROPERTY)


def link(job):
    """The local address Kodi plays, or None (the player then tries the next source)."""
    port = _port()
    if not port:
        util.log("usenet: the service's player server is not running")
        return None
    name = _nzb_name(job)
    if not _saved_nzb(name):                    # already fetched for the check while searching: no second grab
        try:
            _save_nzb(name, newznab.nzb(job))
        except newznab.IndexerError as error:
            host = next((urllib.parse.urlsplit(base).hostname for slot, base, _k in newznab.indexers() if slot == job.get("slot")), "?")
            util.log("usenet: indexer %s (%s): %s" % (job.get("slot"), host, error))
            return None
    query = {"nzb": name}
    if job.get("season") and job.get("episode"):
        query.update(season=job["season"], episode=job["episode"])
    if job.get("pack"):
        query["pack"] = "1"
    if job.get("minutes"):
        query["minutes"] = str(job["minutes"])
    try:
        with urllib.request.urlopen("http://127.0.0.1:%s/prepare?%s" % (port, urllib.parse.urlencode(query)), timeout=90) as response:
            answer = json.loads(response.read().decode("utf-8"))
    except (OSError, ValueError) as error:
        util.log("usenet: service did not answer (%s)" % type(error).__name__)
        return None
    if not answer.get("ok"):
        util.log("usenet: release cannot be played (%s)" % answer.get("error"))
        if any(word in str(answer.get("error")) for word in ("are gone", "encrypted", "password")):
            _mark_dead(job)                     # taken down or locked with a password: never shown again
        if answer.get("error") == "login":
            util.notify(util.T("Usenet: inloggen bij je provider lukt niet", "Usenet: signing in to your provider fails"))
        return None
    if answer.get("native"):
        return answer["native"]                 # vfs.lucent reads it (lucentv://)
    return "http://127.0.0.1:%s/stream/%s/%s" % (port, answer["key"], urllib.parse.quote(answer.get("name") or "film.mkv"))


def _reader_line():
    """Which way films are read: the fast reader (vfs.lucent, with its version) or Lucent's own web server."""
    from . import vfs
    try:
        import xbmc
        answer = json.loads(xbmc.executeJSONRPC(json.dumps({
            "jsonrpc": "2.0", "id": 1, "method": "Addons.GetAddonDetails",
            "params": {"addonid": vfs.ADDON_ID, "properties": ["version"]}})))     # also finds it when switched off
        addon = (answer.get("result") or {}).get("addon") or {}
        installed, version = bool(addon), addon.get("version") or ""
    except Exception:                           # noqa: BLE001 - outside Kodi
        installed, version = False, ""
    if not installed:
        return util.T("Snelle lezer: niet geïnstalleerd (films lopen via Lucent's eigen webserver).",
                      "Fast reader: not installed (films go through Lucent's own web server).")
    if not vfs.available():
        return util.T("Snelle lezer: vfs.lucent %s is uitgeschakeld bij Kodi's add-ons (zet hem daar weer aan).",
                      "Fast reader: vfs.lucent %s is disabled in Kodi's add-ons (enable it there again).") % version
    if not util.flag("nzb_native"):
        return util.T("Snelle lezer: vfs.lucent %s geïnstalleerd, maar uitgezet in deze instellingen.",
                      "Fast reader: vfs.lucent %s installed, but switched off in these settings.") % version
    return util.T("Snelle lezer: aan (vfs.lucent %s, %d verzoeken tegelijk per verbinding).",
                  "Fast reader: on (vfs.lucent %s, %d requests at once per connection).") % (version, vfs.pipeline())


def test():
    """(ok, lines) for the settings' test button: the provider login, then each indexer."""
    from . import nntp
    server.native_setup()                       # Android: the fast unpacker may only load from inside the app
    lines, ok = [], True
    p = server.provider()
    if not p["host"]:
        return False, [util.T("Vul eerst de server van je provider in.", "Enter your provider's server first.")]
    for p, who in ((p, util.T("Provider", "Provider")), (server.provider(backup=True), util.T("Reserve-provider", "Backup provider"))):
        if not p:
            continue
        try:
            connection = nntp.Connection(p["host"], p["port"], p["ssl"], p["user"], p["password"])
            connection.close()
            lines.append(util.T("%s: ingelogd.", "%s: signed in.") % who)
        except nntp.AuthError:
            ok = False
            lines.append(util.T("%s: gebruikersnaam of wachtwoord klopt niet.", "%s: username or password is wrong.") % who)
        except nntp.TooMany:
            lines.append(util.T("%s: bereikbaar, maar al je verbindingen zijn in gebruik.", "%s: reachable, but all your connections are in use.") % who)
        except (nntp.NntpError, OSError) as error:
            ok = False
            lines.append(util.T("%s: geen verbinding (%s).", "%s: no connection (%s).") % (who, type(error).__name__))
    from . import yenc
    lines.append(util.T("Uitpakken: snel (C).", "Unpacking: fast (C).") if yenc.fast() else
                 util.T("Uitpakken: langzaam (Python), afspelen kan haperen. Stuur graag een foutrapport.",
                        "Unpacking: slow (Python), playback may stutter. Please send an error report."))
    lines.append(_reader_line())
    ixs = newznab.indexers()
    if not ixs:
        ok = False
        lines.append(util.T("Geen indexer ingevuld.", "No indexer filled in."))
    for slot, base, key in ixs:
        good, text = newznab.test(slot, base, key)
        ok = ok and good
        host = urllib.parse.urlsplit(base).hostname or "?"
        if not good and text.startswith("API key not accepted"):
            reason = text.split(": ", 1)[1] if ": " in text else ""
            text = util.T("de API-sleutel wordt niet geaccepteerd", "the API key is not accepted") + ((" – %s" % reason) if reason else "")
        elif not good and text == "answer is a web page":
            text = util.T("de indexer geeft een webpagina terug in plaats van een antwoord (adres fout, of geblokkeerd)",
                          "the indexer answers with a web page instead (wrong address, or blocked)")
        elif not good and text in ("answer is not XML", "answer is not readable", "answer is not a Newznab answer"):
            text = util.T("het antwoord van de indexer is onleesbaar (zie het foutrapport)", "the indexer's answer cannot be read (see the error report)")
        elif not good and text == "limit reached":
            text = util.T("het daglimiet is bereikt", "the daily limit is reached")
        used = newznab.usage(base)
        lines.append(util.T("Indexer %d (%s): %s", "Indexer %d (%s): %s") % (slot, host, (util.T("werkt", "works") if good else text)
                                                                             + " · " + util.T("vandaag %s", "today %s") % used))
    if ok:
        lines.append(speed_test())
    return ok, lines


SPEED_SECONDS = 10                  # the speed test never takes longer than this
SPEED_ARTICLES = 200                # at most this many articles (about 140 MB)
NEEDS = ((12.0, "4K REMUX"), (5.0, "4K WEB"), (1.5, "1080p"))     # MB/s a film of that kind needs, about


def speed_test():
    """One line for the test button: how fast the provider delivers, measured on a recent film the indexer has, over
    the connections set in the settings. Costs one search and one NZB at one indexer."""
    from concurrent.futures import ThreadPoolExecutor, wait
    from . import nntp, nzbfile
    T = util.T
    try:
        rows, room = [], False
        for slot, base, key in newznab.indexers():
            if newznab.grab_room(base):
                room = True
                try:
                    rows = [r for r in newznab.latest(slot, base, key) if r["size"] > 2e9]
                except newznab.IndexerError:
                    continue
                if rows:
                    break
        if not rows:
            return (T("Snelheid: niet gemeten (geen geschikte testfilm gevonden).", "Speed: not measured (no suitable test film found).") if room else
                    T("Snelheid: niet gemeten (geen indexer met ruimte voor een NZB).", "Speed: not measured (no indexer with room for an NZB)."))
        row = rows[0]
        files = nzbfile.parse(newznab.nzb(dict(row, scraper="Usenet")))
        biggest = max(files, key=lambda f: f.encoded)
        ids = [seg[2] for seg in biggest.segments[len(biggest.segments) // 3:][:SPEED_ARTICLES]]
    except Exception as error:              # noqa: BLE001 - a test line, never a crash
        util.log("usenet: speed test: %s: %s" % (type(error).__name__, str(error)[:100]))
        return T("Snelheid: niet gemeten (%s).", "Speed: not measured (%s).") % type(error).__name__
    pool = nntp.Pool(server.provider())
    got = {"bytes": 0, "missing": 0, "failed": 0}
    counting = threading.Lock()
    stop = time.time() + SPEED_SECONDS
    answers_before = (nntp.ANSWERS["count"], nntp.ANSWERS["seconds"])

    def one(message_id):
        if time.time() > stop:
            return
        try:
            size = len(pool.body(message_id))           # first fetch, then add: '+=' would read the total before waiting
            with counting:
                got["bytes"] += size
        except nntp.Missing:
            got["missing"] += 1
        except (nntp.NntpError, OSError) as error:
            got["failed"] += 1
            got["why"] = "%s: %s" % (type(error).__name__, str(error)[:60])

    started = time.time()
    run = ThreadPoolExecutor(max_workers=pool.limit)
    jobs = [run.submit(one, i) for i in ids]
    wait(jobs, timeout=SPEED_SECONDS + 25)
    for job in jobs:
        job.cancel()
    run.shutdown(wait=True)                 # every thread done before the test button's script ends
    seconds = max(0.1, time.time() - started)
    pool.close()
    per_second = got["bytes"] / seconds / 1e6
    count = nntp.ANSWERS["count"] - answers_before[0]
    answer = (nntp.ANSWERS["seconds"] - answers_before[1]) / count * 1000 if count else 0
    util.log("usenet: speed test: %.1f MB/s over %d connection(s), %d MB in %.1f s, provider answers in %d ms, %d gone, %d failed%s"
             % (per_second, pool.limit, got["bytes"] >> 20, seconds, answer, got["missing"], got["failed"],
                (" (%s)" % got["why"]) if got.get("why") else ""))
    if not got["bytes"]:
        return T("Snelheid: niet gemeten (de testfilm is niet bij je provider).", "Speed: not measured (the test film is not at your provider).")
    enough = next((label for need, label in NEEDS if per_second >= need * 1.3), None)
    if enough:
        return T("Snelheid: %.0f MB/s met %d verbindingen, genoeg voor %s.", "Speed: %.0f MB/s with %d connections, enough for %s.") % (
            per_second, pool.limit, enough)
    return T("Snelheid: %.1f MB/s met %d verbindingen, krap voor 1080p. Meer verbindingen kunnen helpen.",
             "Speed: %.1f MB/s with %d connections, tight for 1080p. More connections may help.") % (per_second, pool.limit)
