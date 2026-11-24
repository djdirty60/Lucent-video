# -*- coding: utf-8 -*-
"""Usenet as a scraper: your indexers give the sources, your provider plays them (via the service's local server).

fetch(meta)  -> source dicts for the list (None when Usenet is not set up), like the other scrapers
link(job)    -> when you press play: fetch the NZB, let the service open the release, give Kodi the local address
"""
import datetime
import email.utils
import hashlib
import json
import os
import re
import threading
import time
import urllib.parse
import urllib.request
from concurrent.futures import ThreadPoolExecutor

import mylang
import ranking
import streams
import usenet
import util

from . import newznab, server

SERVICE = "USENET"


def configured():
    return util.flag("nzb_on") and bool(util.setting("nzb_host")) and newznab.configured()


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


_indexer_name = newznab.name_of


EARLY_DAYS = 2      # a release may come out this many days before TMDB's air date (time zones, a day-later shift)


def _aired(meta):
    """The day an episode was first on TV, as a date, or None (films, or no date on TMDB)."""
    if meta.get("kind") != "episode":
        return None
    try:
        return datetime.date.fromisoformat(str(meta.get("premiered") or "")[:10])
    except ValueError:
        return None


def _posted_before(row, aired):
    """True when the indexer says this release was posted clearly before the episode was on TV. Then it cannot be
    this episode: it is a series with the same name (The Perfect Lie 2026: a Korean and a Dutch series)."""
    try:
        posted = email.utils.parsedate_to_datetime(row.get("posted") or "").date()
    except (TypeError, ValueError):
        return False
    return posted < aired - datetime.timedelta(days=EARLY_DAYS)


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
                why = newznab.pause_text(base) or ("background search (getting things ready): Lucent's own stop at half "
                                                   "the limit, or %d a day when the indexer does not tell its limit"
                                                   % newznab.UNKNOWN_BACKGROUND if meta.get("background")
                                                   else "Lucent's own stop at 90% of the limit")
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
    from . import vfs
    most = server.most_per_second("vfs" if vfs.wanted() else "python")
    show_badges = util.flag("nzb_badges")
    seconds = int(meta.get("runtime") or 0) * 60
    out, seen = [], set()
    dead, skipped, locked, early = _dead_now(), 0, 0, 0
    aired = _aired(meta)
    for rows in answers:
        for row in rows or []:
            name = row["title"]
            key = name.lower()
            pack = meta["kind"] != "movie" and is_pack(name, titles, meta)
            if key in seen or not fits(name):
                continue
            if aired and _posted_before(row, aired):
                early += 1                      # posted before this episode was on TV: another series with the same name
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
            job["indexer"] = where                  # for learning from the statistics (learn.py)
            badges = _badges(row) if show_badges else ""
            # needs more per second than this box ever got from Usenet: say so (it may stop to buffer)
            heavy = bool(most and seconds and row["size"] / seconds > most * 0.95)
            warn = util.T("mogelijk te zwaar", "may be too heavy") if heavy else ""
            if not pack:
                source = usenet._source(name, row["size"], SERVICE, resolve=job)          # noqa: SLF001 - same add-on
                _add_info(source, row)
                try:
                    source["posted"] = int(email.utils.parsedate_to_datetime(row.get("posted") or "").timestamp())
                except (TypeError, ValueError, IndexError):
                    pass                            # newer first among equal sources of the same size (ranking.py)
                if not _from_file(source, job):
                    locked += 1                     # found locked or packed before: never playable, not shown
                    continue
                source["meta"] = " · ".join(p for p in (SERVICE, where, streams.size_text(row["size"]), _group(row["title"]), badges, warn) if p)
                source["heavy"] = heavy
                source["always"] = True
                out.append(source)
                continue
            # a pack: rank it by the size of one episode, show the size of the whole season
            job["pack"] = True
            episodes = int(meta.get("season_count") or 0) or 10
            source = usenet._source(name, row["size"] // episodes, SERVICE, resolve=job)    # noqa: SLF001
            _add_info(source, row)
            heavy = bool(most and seconds and row["size"] / episodes / seconds > most * 0.95)
            warn = util.T("mogelijk te zwaar", "may be too heavy") if heavy else ""
            source["meta"] = " · ".join(p for p in (SERVICE, where, util.T("Seizoenspakket", "Season pack"), streams.size_text(row["size"]), _group(row["title"]), badges, warn) if p)
            source["pack"] = True
            source["heavy"] = heavy
            source["always"] = True
            out.append(source)
    if early:
        util.log("usenet: %d release(s) left out that were posted before this episode was on TV (%s): another series with the same name" % (early, aired.isoformat()))
    if skipped:
        util.log("usenet: %d release(s) left out that were found taken down before" % skipped)
    if locked:
        util.log("usenet: %d release(s) left out that were found locked or packed before" % locked)
    return out


AUDIO_INFO = (("atmos", "ATMOS"), ("truehd", "TRUEHD"), ("dts:x", "DTS-X"), ("dts-x", "DTS-X"), ("dts-hd ma", "DTS-HDMA"), ("dts-hd", "DTS-HD"),
              ("e-ac-3", "DD+"), ("eac3", "DD+"), ("dd+", "DD+"), ("ac-3", "DD"), ("ac3", "DD"), ("aac", "AAC"), ("opus", "OPUS"))
VIDEO_INFO = (("hevc", "HEVC"), ("h.265", "HEVC"), ("x265", "HEVC"), ("avc", "AVC"), ("h.264", "AVC"), ("x264", "AVC"), ("av1", "AV1"))


def _info_flags(info):
    """Labels from what the indexer itself says is inside (Newznab extended attributes), next to those from the name."""
    out = []
    languages = [w.strip().lower() for w in re.split(r"[,/|;-]", info.get("language") or "") if w.strip()]
    if any(mylang.is_mine(w) for w in languages):
        out.append(mylang.audio_flag())
    if len(set(languages)) > 1:
        out.append("aio_MULTI")
    subs = [w.strip().lower() for w in re.split(r"[,/|;\s-]+", info.get("subs") or "") if w.strip()]
    if any(mylang.is_mine(w) for w in subs):
        out.append(mylang.subs_flag())
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
NZB_FRESH = 7 * 86400       # an NZB never changes: one that was fetched is used again for a week (indexers warn
                            # and can close an account when the same NZB is fetched over and over)


def _from_file(source, job):
    """What an earlier check read in the file itself: the labels set right (sound, HDR, language, FEL), the real
    resolution, so the order of sources uses the truth. False for an archive that can never be streamed."""
    found = tracks_known(job)
    if not found:
        return True
    if found.get("locked"):
        return False
    source["flags"] = file_flags(source, found)
    real = _real_resolution(found)
    if real and not job.get("pack"):
        source["resolution"] = real
    return True


def _nzb_name(job):
    return hashlib.sha1(job["link"].encode("utf-8")).hexdigest()[:16] + ".nzb"


def _saved_nzb(name):
    path = os.path.join(server.folder(), name)
    try:
        return os.path.getsize(path) > 0 and time.time() - os.path.getmtime(path) < NZB_FRESH
    except OSError:
        return False


def _save_nzb(name, data):
    """Kept packed (gzip): a Remux NZB of 15 MB becomes a few MB, so many more fit in the space for saved NZBs and the
    same NZB is not fetched from the indexer again (before rc1.14 only about a dozen Remux NZBs fitted, and the
    log showed 'the same NZB is fetched again within a day'). nzbfile.parse unpacks it."""
    import gzip
    if data[:2] != b"\x1f\x8b":
        data = gzip.compress(data, 6)
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


def mark_gone(job):
    """Every tested piece of this release is gone (taken down): it is not shown in the next searches."""
    _mark_dead(job)
    util.log("usenet: release taken down (every tested piece gone): left out of the next searches")


def _dead_now():
    return util.load_json(DEAD, {})


def ensure_nzb(job, why):
    """The NZB of this release saved beforehand (for 'why': the next episode, ready for tonight). Only when there is
    room at the indexer (at most half of a known daily NZB limit). True when it is there."""
    name = _nzb_name(job)
    if _saved_nzb(name):
        return True
    base = next((b for slot, b, _key in newznab.indexers() if slot == job.get("slot")), None)
    if not base or not newznab.grab_room(base):
        util.log("usenet: %s: no room for an extra NZB at the indexer today" % why)
        return False
    try:
        _save_nzb(name, newznab.nzb(job))
    except newznab.IndexerError as error:
        util.log("usenet: %s: NZB not fetched beforehand (%s)" % (why, error))
        return False
    return True


def warm(job):
    """While an episode plays (from the service): fetch the NZB of the next episode's source and open its release in
    advance, so 'Next' starts at once. Only when there is room for an NZB at the indexer (at most half of a known
    daily limit, like the check while searching). True when it is ready."""
    name = _nzb_name(job)
    if not ensure_nzb(job, "next episode"):
        return False
    season, episode = (job.get("season"), job.get("episode")) if job.get("season") and job.get("episode") else (None, None)
    return server.warm(name, season, episode, bool(job.get("pack")), int(job.get("minutes") or 0))


HEALTH_HOURS = 6                # a full check that found articles missing is remembered this long
HEALTH_HOURS_COMPLETE = 24      # one that found everything there: this long (a takedown shows when it plays)
HEALTH_PART = 400               # articles per STAT batch over one connection
HEALTH_CONNS = 16               # connections the full check uses at most (4 always stay free for playing)
HEALTH_CONNS_LIST = 18          # in the source list nothing plays: the 4 that read the files beside it and 2 for the
#                                 rest of Lucent stay free (over the provider's limit it refuses or slows down everything)
HEALTH_SECONDS = 90             # vfs.lucentcheck stops a full check after this long (no badge then)


def health_known(job):
    """The remembered full check of this release: (present, total) or None."""
    key = "nzbhealth" + _dead_key(job["link"])
    saved = util.cache_get(key, HEALTH_HOURS_COMPLETE * 3600)
    if saved and saved[0] < saved[1]:                  # articles missing: asked again sooner
        saved = util.cache_get(key, HEALTH_HOURS * 3600)
    return tuple(saved) if saved else None


def can_check(job):
    """A full check needs the NZB itself: only for releases whose NZB Lucent already has (no extra indexer grab)."""
    return bool(_saved_nzb(_nzb_name(job)))


def health(job, stop=lambda: False, beside=False, again=False):
    """(present, total) articles of the files that would be played, over the provider and the backup provider; or
    None when it cannot be told. beside: in the source list, where nothing plays and the file of another release is
    read at the same time over 4 connections: the check takes the rest but 2, up to HEALTH_CONNS_LIST. Only STAT questions: no data is downloaded and nothing is fetched at the indexer."""
    from . import nntp, nzbfile, vfs
    known = None if again else health_known(job)        # again: asked from the menu, every article once more
    if known:
        return known
    try:
        with open(os.path.join(server.folder(), _nzb_name(job)), "rb") as handle:
            files = nzbfile.parse(handle.read())
        _kind, chosen = nzbfile.pick(files, job.get("season"), job.get("episode"), job.get("pack"))
    except Exception:                           # noqa: BLE001 - hidden names, an odd NZB: no badge
        return None
    ids = list(dict.fromkeys(seg[2] for posted in chosen for seg in posted.segments))
    if not ids:
        return None
    provider = server.provider()
    allowed = int(provider.get("connections") or 8)
    most = HEALTH_CONNS_LIST if beside else HEALTH_CONNS
    spare_settings = server.provider(backup=True)
    if vfs.check_available():                   # the separate C++ add-on: faster, Python stays free for the list
        try:
            native = vfs.check_articles(ids, provider, spare_settings, max(4, min(most, allowed - (6 if beside else 4))),
                                        HEALTH_SECONDS, stop)
        except (InterruptedError, TimeoutError):   # a film started, or far too slow: no badge, no second try
            return None
        if native:
            util.cache_set("nzbhealth" + _dead_key(job["link"]), list(native))
            return native
    pool = nntp.Pool(provider, limit=max(4, min(most, allowed - (6 if beside else 4))))
    spare = nntp.Pool(spare_settings, limit=min(4, int(spare_settings.get("connections") or 4))) if spare_settings else None
    parts = [ids[at:at + HEALTH_PART] for at in range(0, len(ids), HEALTH_PART)]

    def one(part):
        if stop():
            raise InterruptedError
        found = pool.stat_many(part)
        lost = [i for i in part if not found.get(i)]
        if lost and spare:
            extra = spare.stat_many(lost)
            lost = [i for i in lost if not extra.get(i)]
        return len(part) - len(lost)

    started = time.time()
    try:
        with ThreadPoolExecutor(max_workers=pool.limit) as run:
            present = sum(run.map(one, parts))
    except InterruptedError:
        return None
    except Exception as error:                  # noqa: BLE001 - no badge, never an error on screen
        util.log("usenet: full check failed (%s)" % type(error).__name__)
        return None
    finally:
        pool.close()
        if spare:
            spare.close()
    util.log("usenet: full check: %d of %d articles there (%.1f s)" % (present, len(ids), time.time() - started))
    util.cache_set("nzbhealth" + _dead_key(job["link"]), [present, len(ids)])
    return present, len(ids)


def health_text(result):
    """The text after the line under the name: ' · ✓ compleet' in green, ' · 97% compleet' in amber or red."""
    if not result:
        return ""
    present, total = result
    if present >= total:
        return "  ·  [COLOR FF4CC38A]✓ %s[/COLOR]" % util.T("compleet", "complete")
    share = int(present * 1000 // max(1, total)) / 10.0         # never rounded up: 99.97% is not 100%
    colour = "FFF2B84B" if share >= 95 else "FFE5534B"
    text = ("%.1f" % share).rstrip("0").rstrip(".")
    if util.NL:
        text = text.replace(".", ",")
    return "  ·  [COLOR %s]%s%% %s[/COLOR]" % (colour, text, util.T("compleet", "complete"))


CHECKING = "  ·  [COLOR FF8D99AA]%s[/COLOR]"
TRACKS_DAYS = 7
DOLBY_BYTES = 12 * 1024 * 1024          # FEL or MEL: the first frames, when they are not in the first 3 MB
TRACKS_BYTES = 3 * 1024 * 1024          # the track list of an MKV is in its first few hundred KB; 3 MB to be safe


FILE_FORMAT = 3                         # what a saved file check holds; older ones are read once more (3: FEL/MEL from the RPU)


def tracks_known(job):
    saved = util.cache_get("nzbtracks" + _dead_key(job["link"]), TRACKS_DAYS * 86400)
    if saved and saved.get("f") != FILE_FORMAT:
        return None                             # read before Lucent looked at sound, picture and running time: once more
    return saved or None


def dovi_flag(found):
    """The source label for what the file itself says: FEL, MEL, or P7 for profile 7 when it could not be read."""
    kind = (found or {}).get("dovi")
    return {"fel": "aio_FEL", "mel": "aio_MEL", "p7": "aio_P7"}.get(kind, "")


def tracks(job, stop=lambda: False):
    """What the MKV of this release really holds (read from its first megabytes), or None: languages, sound, picture,
    running time, chapters, Dolby Vision. {'locked': 'encrypted' / 'compressed'} for an archive that can never be streamed.
    Only for releases whose NZB Lucent already has; a few articles are read, nothing at the indexer."""
    from . import nntp, stream, tracks as mkv
    known = tracks_known(job)
    if known:
        return known
    if stop():
        return None
    try:
        with open(os.path.join(server.folder(), _nzb_name(job)), "rb") as handle:
            data = handle.read()
    except OSError:
        return None
    provider = server.provider()
    pool = nntp.Pool(provider, limit=4)
    spare_settings = server.provider(backup=True)
    spare = nntp.Pool(spare_settings, limit=2) if spare_settings else None
    release = None
    started = time.time()
    found = None
    try:
        release = stream.open_release(pool, data, job.get("season"), job.get("episode"), pack=bool(job.get("pack")), backup=spare,
                                      ahead=4 * 1024 * 1024, cache=16 * 1024 * 1024)
        if not (release.name or "").lower().endswith(".mkv"):
            return None
        head = release.read(0, min(TRACKS_BYTES, int(release.size)))
        found = mkv.read(head)
        if found and found.get("dv") == 7 and found.get("video"):
            # Dolby Vision profile 7 (UHD Blu-ray): FEL or MEL, from the size of the enhancement layer in the first frames
            number, nal_size = found["video"]
            kind = mkv.dolby(head, number, nal_size, found["after"])
            if not kind and int(release.size) > len(head) and not stop():
                head = head + release.read(len(head), min(DOLBY_BYTES, int(release.size)) - len(head))
                kind = mkv.dolby(head, number, nal_size, found["after"])
            found["dovi"] = kind or "p7"
    except Exception as error:                  # noqa: BLE001 - no track info, never an error on screen
        text = str(error).lower()
        locked = "encrypted" if "encrypted" in text else ("compressed" if "compressed" in text else "")
        if locked:
            util.log("usenet: release can never be streamed: archive %s" % locked)
            found = {"f": FILE_FORMAT, "locked": locked}
            util.cache_set("nzbtracks" + _dead_key(job["link"]), found)
            return found
        from .stream import StreamError
        # a StreamError says why (never with a login in it): which article or file could not be read
        util.log("usenet: tracks not read (%s%s)" % (type(error).__name__,
                                                    ": %s" % str(error)[:200] if isinstance(error, StreamError) else ""))
        sample = re.match(r"(\d+) of (\d+) checked articles are gone$", str(error)) if isinstance(error, StreamError) else None
        if sample:
            # the sample over the release (and the backup) already says far too much is gone, mostly a release that was
            # taken down: the full check is not needed. It would wait on the provider, which answers a gone article slowly.
            # Remembered as taken down, so the next search leaves it out (what the old check before the list did).
            _mark_dead(job)
            return {"gone": (int(sample.group(1)), int(sample.group(2)))}
        return None
    finally:
        if release:
            release.close()
        pool.close()
        if spare:
            spare.close()
    if found is None:
        return None
    keep = ("audio", "subs", "forced", "sound", "seconds", "width", "height", "bits", "transfer", "chapters")
    found = dict({k: found.get(k) for k in keep}, f=FILE_FORMAT,
                 dovi=found.get("dovi") or ("dv%d" % found["dv"] if found.get("dv") else ""))
    util.log("usenet: tracks read in %.1f s: audio %s, subtitles %s%s; %s" % (
        time.time() - started, ",".join(found["audio"]) or "-", ",".join(found["subs"]) or "-",
        (" (forced only: %s)" % ",".join(found["forced"])) if found["forced"] else "",
        ", ".join(p for p in ("Dolby Vision %s" % found["dovi"] if found["dovi"] else "",
                              "%dx%d" % (found["width"], found["height"]) if found["height"] else "",
                              "transfer %d" % found["transfer"] if found["transfer"] else "",
                              "%d min" % (found["seconds"] // 60) if found["seconds"] else "",
                              "%d chapters" % found["chapters"] if found["chapters"] else "",
                              "sound " + " ".join("%s:%s/%d%s" % (l, k or "?", c, "+atmos" if a else "") for l, k, c, a in found["sound"][:4]))
                    if p)))
    util.cache_set("nzbtracks" + _dead_key(job["link"]), found)
    return found


# ------------------------------------------------------------------ labels and text from what the file says
SOUND_FLAGS = ("TRUEHD", "DD+", "DD", "AAC", "OPUS")
DTS_FLAGS = ("DTS-X", "DTS-HDMA", "DTS-HD", "DTS")
CHANNEL_FLAGS = {8: "7.1", 7: "5.1", 6: "5.1", 2: "2.0"}


def _real_resolution(found):
    width, height = found.get("width") or 0, found.get("height") or 0
    if not height:
        return ""
    if width >= 3200 or height >= 1700:
        return "4K"
    if width >= 1700 or height >= 950:
        return "1080p"
    if width >= 1100 or height >= 650:
        return "720p"
    return "SD"


def file_flags(source, found):
    """The source's labels set right by what the file says: sound, channels, HDR, your language, Dolby Vision. A label
    the file cannot confirm or deny (DTS-HD MA vs DTS, an Atmos the track name does not mention) stays as the name had it."""
    import mylang
    flags = list(source.get("flags") or [])
    if not found or found.get("locked"):
        return flags
    sound = found.get("sound") or []
    kinds = {k for _l, k, _c, _a in sound if k}
    if kinds:
        for flag in SOUND_FLAGS:
            if flag in kinds and flag not in flags:
                flags.append(flag)
            elif flag not in kinds and flag in flags:
                flags.remove(flag)
        dts = [k for k in kinds if k in DTS_FLAGS]
        if not dts:
            flags = [f for f in flags if f not in DTS_FLAGS]
        elif not any(f in DTS_FLAGS for f in flags):
            flags.append(sorted(dts, key=DTS_FLAGS.index)[0])
        if any(a for _l, _k, _c, a in sound) and "ATMOS" not in flags:
            flags.append("ATMOS")
        elif "ATMOS" in flags and not kinds & {"TRUEHD", "DD+"}:
            flags.remove("ATMOS")                   # Atmos only comes in TrueHD or DD+
        most = max((c for _l, _k, c, _a in sound), default=0)
        channel = CHANNEL_FLAGS.get(most) or ("7.1" if most > 8 else "")
        if channel:
            flags = [f for f in flags if f not in CHANNEL_FLAGS.values()] + [channel]
    transfer = found.get("transfer") or 0
    if transfer in (16, 18):                    # PQ or HLG: HDR (also the base layer under Dolby Vision)
        flags = [f for f in flags if f != "SDR"] + ([] if "HDR" in flags else ["HDR"])
    elif transfer and not found.get("dovi"):
        if transfer in (1, 6, 14, 15):          # BT.709 / BT.601 / BT.2020 SDR: no HDR in this file
            flags = [f for f in flags if f not in ("HDR",)] + ([] if "SDR" in flags else ["SDR"])
    audio_flag, subs_flag = mylang.flags()
    mine = mylang.code()
    if found.get("audio"):
        if mine in found["audio"] and audio_flag not in flags:
            flags.append(audio_flag)
        elif mine not in found["audio"] and audio_flag in flags:
            flags.remove(audio_flag)
    if mine in (found.get("subs") or []):
        if subs_flag not in flags:
            flags.append(subs_flag)
    elif subs_flag in flags and not source.get("subs_online") and (found.get("audio") or found.get("subs") or found.get("forced")):
        flags.remove(subs_flag)                 # the name said so, the file has no full subtitles in your language
    dovi = dovi_flag(found)
    if dovi in ("aio_FEL", "aio_MEL"):               # the file says it: that one label, the others go
        flags = [f for f in flags if f not in ("aio_FEL", "aio_MEL", "aio_P7")] + [dovi]
    elif dovi == "aio_P7" and not {"aio_FEL", "aio_MEL", "aio_P7"} & set(flags):
        flags.append("aio_P7")
    return flags


def other_series(source, found):
    """For a series with a twin (same name and year, twins.py): True when the file's running time is far from this
    series' episodes (The Stranger 2020: the American episodes last about 8 minutes, the British ones 50)."""
    if not source.get("twin") or not found or source.get("pack"):
        return False
    minutes = int((source.get("resolve") or {}).get("minutes") or 0)
    seconds = int(found.get("seconds") or 0)
    if not minutes or not seconds:
        return False
    length = seconds / 60.0
    theirs = [m for m in source["twin"] if m] if isinstance(source["twin"], list) else []
    if abs(length - minutes) <= max(5, minutes * 0.3):
        return False                            # close to this series' length
    if theirs:
        far = min(abs(length - m) for m in theirs) < abs(length - minutes)      # nearer to the other series' episodes
    else:
        far = length > minutes * 2 + 5          # the other series' length unknown: only a file far too long
    if far:
        util.log("twins: %s lasts %d min, this series' episodes %d min: the other series" % (source.get("name", "")[:70], int(length), minutes))
    return far


OTHER_SERIES = util.T("lengte past bij de andere serie met deze naam", "its length fits the other series with this name")


def tracks_text(found, job=None, source=None):
    """Only for a release that can never play (locked, packed or gone): what the file says otherwise shows in its labels."""
    if found and found.get("gone"):
        lost, asked = found["gone"]
        return "  ·  [COLOR FFE5534B]✗ %s[/COLOR]" % (util.T("weg: %d van %d geteste stukjes", "gone: %d of %d tested pieces") % (lost, asked))
    if not found or not found.get("locked"):
        return ""
    return "  ·  [COLOR FFE5A15A]%s[/COLOR]" % (util.T("op slot met een wachtwoord, speelt niet", "locked with a password, cannot play")
                                                if found["locked"] == "encrypted" else
                                                util.T("ingepakt, kan niet gestreamd worden", "packed, cannot be streamed"))


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
            if "daily limit" not in str(error):
                newznab.outcome(job, False)
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
        if answer.get("error") not in ("login", "busy", "nzb missing"):
            newznab.outcome(job, False)
        if answer.get("error") == "login":
            util.notify(util.T("Usenet: inloggen bij je provider lukt niet", "Usenet: signing in to your provider fails"))
        return None
    newznab.outcome(job, True)
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
        return util.T("Snelle lezer: niet geïnstalleerd (films lopen via Lucent's eigen webserver). Sneller spoelen? "
                      "Installeer Lucent Video VFS uit de Lucent-repository (Virtuele bestandsystemen).",
                      "Fast reader: not installed (films go through Lucent's own web server). Faster seeking? "
                      "Install Lucent Video VFS from the Lucent repository (Virtual filesystems).")
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
        state = (util.T("werkt", "works") if good else text) + " · " + util.T("vandaag %s", "today %s") % used
        if newznab.is_free(base):
            lines.append("NZBIndex (%s): %s" % (util.T("gratis", "free"), state))
        else:
            lines.append(util.T("Indexer %d (%s): %s", "Indexer %d (%s): %s") % (slot, host, state))
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
