# -*- coding: utf-8 -*-
"""The local web server that Kodi plays a Usenet release from. It runs in Lucent Video's service (the plugin process
does not live long enough) and only listens on 127.0.0.1, so nothing outside the box can reach it.

    GET /prepare?nzb=<file>&season=&episode=   open a release (the NZB the plugin saved), answer JSON with its key
    GET|HEAD /stream/<key>/<name>              the film, with Range (seeking)
    GET /close/<key>                            close it

One release at a time: opening a new one closes the old one and its connections. A release nobody read from for a few
minutes (the video stopped) is closed by the service as well. The port is in the Home property lucent.video.nzb.port.
"""
import json
import os
import re
import threading
import time
import urllib.parse
import uuid
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

import util

from . import nntp, nzbfile, stats, stream, vfs, yenc

PORT_PROPERTY = "lucent.video.nzb.port"
FOLDER = "nzb"
LOCK_WAIT = 30                  # seconds a new release waits for one still being opened
IDLE = 300                      # seconds without reads before a release is closed
CHUNK = 1048576
_state = {"http": None, "release": None, "key": "", "pool": None, "lock": threading.Lock(), "warm": None}
WARM_KEEP = 20 * 60             # a release opened in advance (the next episode) is kept this long
WARM_CONNS = 4                  # connections used to open it in advance (closed again right after)
_warm_lock = threading.Lock()


def folder():
    path = os.path.join(util.PROFILE, FOLDER)
    os.makedirs(path, exist_ok=True)
    return path


def provider(backup=False):
    """The provider settings, read fresh (never logged). backup=True: the backup provider, or None when it is off."""
    pre = "nzb2_" if backup else "nzb_"
    if backup and not (util.flag("nzb2_on") and util.setting("nzb2_host")):
        return None
    return {"host": util.setting(pre + "host"), "port": util.number(pre + "port", 563), "ssl": util.setting(pre + "ssl").lower() != "false",
            "user": util.setting(pre + "user"), "password": util.setting(pre + "pass"),
            "connections": util.number(pre + "conns", 10 if backup else 20)}


def wanted():
    return util.flag("nzb_on") and bool(util.setting("nzb_host"))


def _close_release():
    release, pool, spare = _state["release"], _state["pool"], _state.get("backup")
    _state["release"], _state["key"], _state["pool"], _state["backup"] = None, "", None, None
    if release:
        stats = release.stats
        util.log("usenet: release closed after %.0f s: %d MB in, %d MB to Kodi, Kodi waited %d time(s), longest %.1f s"
                 % (time.time() - release.opened, stats.get("in", 0) >> 20, stats.get("out", 0) >> 20, stats.get("waits", 0),
                    stats.get("longest", 0.0)))
        release.close()
    for one in (pool, spare):
        if one:
            one.close()


def _sizes(minutes):
    """(ahead, cache) in bytes from the settings; stream.fit_to_film raises them for a heavy film."""
    megabyte = 1024 * 1024
    ahead = max(16, util.number("nzb_ahead", 64)) * megabyte
    cache = max(32, util.number("nzb_cache", 128)) * megabyte
    return ahead, cache


def prepare(name, season=None, episode=None, pack=False, minutes=0):
    """Open the release in nzb/<name>. Returns a dict for the plugin: ok, key, name, size or error."""
    path = os.path.join(folder(), os.path.basename(name))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return {"ok": False, "error": "nzb missing"}
    util.log("usenet: opening release")
    if not _state["lock"].acquire(timeout=LOCK_WAIT):
        util.log("usenet: a previous release is still being opened")
        return {"ok": False, "error": "busy"}
    try:
        _close_release()
        spare_settings = provider(backup=True)
        started = time.time()
        warmed = _take_warm(_warm_key(name, season, episode, pack))
        if warmed:
            release, pool, spare = warmed["release"], warmed["pool"], warmed["spare"]
            pool.limit = max(pool.limit, int(provider().get("connections") or 8))      # now it plays: all connections
            if spare and spare_settings:
                spare.limit = max(spare.limit, int(spare_settings.get("connections") or 8))
            util.log("usenet: release was opened in advance %.0f s ago" % (time.time() - warmed["at"]))
        else:
            drop_warm()                             # another release than the one opened in advance
            pool = nntp.Pool(provider())
            spare = nntp.Pool(spare_settings) if spare_settings else None
            pool.record = True                      # this one plays: its counts go to the statistics
            if spare:
                spare.record = True
        ahead, cache = _sizes(minutes)
        try:
            if not warmed:
                release = stream.open_release(pool, data, season, episode, log=lambda text: util.log("usenet: " + text), pack=pack,
                                              backup=spare, fill=util.flag("nzb_fill"), ahead=ahead, cache=cache, minutes=minutes)
        except nntp.AuthError:
            pool.close()
            if spare:
                spare.close()
            return {"ok": False, "error": "login"}
        except (nntp.NntpError, OSError, yenc.YencError, stream.StreamError, nzbfile.NzbError) as error:
            pool.close()
            if spare:
                spare.close()
            return {"ok": False, "error": "%s: %s" % (type(error).__name__, error)}
        except Exception as error:                  # noqa: BLE001 - never leave the player waiting without a reason
            pool.close()
            if spare:
                spare.close()
            util.log("usenet: opening failed (%s: %s)" % (type(error).__name__, str(error)[:120]))
            return {"ok": False, "error": type(error).__name__}
        util.log("usenet: release ready in %.1f s" % (time.time() - started))
        vfs.clear()                                 # an older plan (with the sign-in) never stays behind
        if vfs.wanted():
            native = vfs.hand_over(release, provider(), spare_settings, fill=util.flag("nzb_fill"))
            if native:
                release.close()                     # vfs.lucent makes its own connections: free these now
                pool.close()
                if spare:
                    spare.close()
                return {"ok": True, "key": "", "native": native, "name": release.name, "size": release.size}
        _state["release"], _state["pool"], _state["key"], _state["backup"] = release, pool, uuid.uuid4().hex[:12], spare
        _warn_slow()
        return {"ok": True, "key": _state["key"], "name": release.name, "size": release.size}
    finally:
        _state["lock"].release()


def _warm_key(name, season, episode, pack):
    return (os.path.basename(name or ""), str(season or ""), str(episode or ""), bool(pack))


def warm(name, season=None, episode=None, pack=False, minutes=0):
    """While an episode plays: open the release of the next one in advance (NZB read, first articles in), so pressing
    'Next' starts at once. It uses a few connections only while opening; they are closed again right away, so the
    episode that plays now keeps all of them. True when it is ready."""
    key = _warm_key(name, season, episode, pack)
    with _warm_lock:
        ready = _state["warm"]
        if ready and ready["key"] == key:
            return True
    drop_warm()
    try:
        with open(os.path.join(folder(), os.path.basename(name)), "rb") as handle:
            data = handle.read()
    except OSError:
        return False
    started = time.time()
    pool = nntp.Pool(provider(), limit=WARM_CONNS)
    spare_settings = provider(backup=True)
    spare = nntp.Pool(spare_settings, limit=2) if spare_settings else None
    for one in (pool, spare):
        if one:
            one.record = True
    ahead, cache = _sizes(minutes)
    try:
        release = stream.open_release(pool, data, season, episode, log=lambda text: util.log("usenet: next episode: " + text),
                                      pack=pack, backup=spare, fill=util.flag("nzb_fill"), ahead=ahead, cache=cache, minutes=minutes)
    except Exception as error:                  # noqa: BLE001 - only a quicker start; pressing play opens it as before
        util.log("usenet: next episode not opened in advance (%s)" % type(error).__name__)
        for one in (pool, spare):
            if one:
                one.close()
        return False
    for one in (pool, spare):
        if one:
            one.drop_idle()                     # no connections stay open while the current episode plays
    with _warm_lock:
        old, _state["warm"] = _state["warm"], {"key": key, "release": release, "pool": pool, "spare": spare, "at": time.time()}
    if old:
        _close_warm(old)
    util.log("usenet: next episode opened in advance in %.1f s" % (time.time() - started))
    return True


def _take_warm(key):
    with _warm_lock:
        ready = _state["warm"]
        if ready and ready["key"] == key and time.time() - ready["at"] < WARM_KEEP:
            _state["warm"] = None
            return ready
    return None


def _close_warm(ready):
    try:
        ready["release"].close()
    except Exception:                           # noqa: BLE001
        pass
    for one in (ready["pool"], ready["spare"]):
        if one:
            one.close()


def drop_warm(older_than=None):
    """Close the release opened in advance (all of it, or only when it is older than older_than seconds)."""
    with _warm_lock:
        ready = _state["warm"]
        if not ready or (older_than is not None and time.time() - ready["at"] < older_than):
            return
        _state["warm"] = None
    _close_warm(ready)


SLOW_WARNED = "lucent.video.slowunpack"


def _warn_slow():
    """Once per Kodi session: say it plainly when only the slow Python unpacker works here."""
    if yenc.fast() or util.HOME.getProperty(SLOW_WARNED):
        return
    util.HOME.setProperty(SLOW_WARNED, "1")
    util.notify(util.T("Snelle uitpakker werkt niet op dit apparaat: afspelen kan haperen. Stuur graag een foutrapport.",
                       "Fast unpacker does not work on this device: playback may stutter. Please send an error report."))


class _Handler(BaseHTTPRequestHandler):
    protocol_version = "HTTP/1.1"

    def log_message(self, *_args):          # never log what is asked
        pass

    def _json(self, value, status=200):
        body = json.dumps(value).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        self._serve(True)

    def do_HEAD(self):
        self._serve(False)

    def _serve(self, with_body):
        parts = urllib.parse.urlsplit(self.path)
        query = dict(urllib.parse.parse_qsl(parts.query))
        if parts.path == "/prepare":
            return self._json(prepare(query.get("nzb", ""), query.get("season"), query.get("episode"), query.get("pack") == "1",
                                      int(query.get("minutes") or 0) if (query.get("minutes") or "").isdigit() else 0))
        match = re.match(r"^/(stream|close)/([0-9a-f]+)(?:/.*)?$", parts.path)
        release = _state["release"]
        if not match or not release or match.group(2) != _state["key"]:
            self.send_error(404)
            return None
        if match.group(1) == "close":
            with _state["lock"]:
                _close_release()
            return self._json({"ok": True})
        return self._stream(release, with_body)

    def _stream(self, release, with_body):
        size = release.size
        start, end = 0, size - 1
        ranged = re.match(r"bytes=(\d*)-(\d*)", self.headers.get("Range") or "")
        if ranged and (ranged.group(1) or ranged.group(2)):
            if ranged.group(1):
                start = int(ranged.group(1))
                if ranged.group(2):
                    end = min(int(ranged.group(2)), size - 1)
            else:
                start = max(0, size - int(ranged.group(2)))
            if start >= size:
                self.send_response(416)
                self.send_header("Content-Range", "bytes */%d" % size)
                self.send_header("Content-Length", "0")
                self.end_headers()
                return
        length = end - start + 1
        self.send_response(206 if ranged else 200)
        self.send_header("Content-Type", "video/x-matroska" if release.name.lower().endswith(".mkv") else "video/mp4")
        self.send_header("Accept-Ranges", "bytes")
        self.send_header("Content-Length", str(length))
        if ranged:
            self.send_header("Content-Range", "bytes %d-%d/%d" % (start, end, size))
        self.end_headers()
        if not with_body:
            return
        at = start
        asked = time.time()
        first = None
        try:
            while at <= end:
                chunk = release.read(at, min(CHUNK, end - at + 1))
                if not chunk:
                    break
                if first is None:
                    first = time.time() - asked
                    _tell_request(release, start, end, first)
                written = time.time()
                self.wfile.write(chunk)
                at += len(chunk)
                _tell_progress(release, at - start, time.time() - asked, time.time() - written)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # Kodi closed this request (a seek): normal
        except Exception as error:          # noqa: BLE001 - whatever it is, it must reach the log
            util.log("usenet: reading stopped at %.1f%% after %.1f s (%s: %s)" % (100.0 * at / max(1, release.size), time.time() - asked,
                                                                              type(error).__name__, str(error)[:120]))
            self.close_connection = True


TELL_REQUESTS = 12                  # the first requests of a release are logged (where Kodi reads, how fast it got data)


def _tell_request(release, start, end, first):
    told = release.stats.get("told", 0)
    if told >= TELL_REQUESTS and first < 2:
        return
    release.stats["told"] = told + 1
    util.log("usenet: Kodi reads from %.1f%%%s: first data after %.1f s"
             % (100.0 * start / max(1, release.size), "" if end >= release.size - 1 else " (%d MB)" % ((end - start + 1) >> 20), first))


def _tell_progress(release, sent, seconds, write_wait):
    """The first 40 seconds of a film, every 5 seconds: how much Kodi took. A stall right after the start shows here."""
    stats = release.stats
    age = time.time() - release.opened
    if age > 40 or time.time() - stats.get("progress_at", 0) < 5:
        return
    stats["progress_at"] = time.time()
    util.log("usenet: %.0f s after opening: this request sent %d MB in %.1f s; in %d MB, to Kodi %d MB, Kodi waited %d time(s), longest %.1f s"
             % (age, sent >> 20, seconds, stats.get("in", 0) >> 20, stats.get("out", 0) >> 20, stats.get("waits", 0), stats.get("longest", 0.0)))


def native_setup():
    """Android: a folder inside the app for the fast unpacker (see yenc). Also for the settings' test button, which
    runs in another process than the service."""
    try:
        import xbmcvfs
        inside = os.path.dirname(os.path.dirname(xbmcvfs.translatePath("special://xbmc").rstrip("/\\")))
        yenc.set_copy_dir(os.path.join(inside, "lucent_native"))
    except Exception:                           # noqa: BLE001 - only a faster way, never a reason not to start
        pass


def start():
    if _state["http"]:
        return
    native_setup()
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    _state["http"] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    util.HOME.setProperty(PORT_PROPERTY, str(server.server_address[1]))
    util.log("usenet: local player server on, unpacking: %s" % yenc.mode())


def stop():
    vfs.clear()
    drop_warm()
    stats.flush(force=True)
    with _state["lock"]:
        _close_release()
    if _state["http"]:
        _state["http"].shutdown()
        _state["http"] = None
    util.HOME.clearProperty(PORT_PROPERTY)


SPEED_EVERY = 30
SPEED_FILE = "nzb_speed.json"               # the most this box got from Usenet while Kodi was waiting for more


def _remember_speed(per_second, waits):
    """Only a half minute where Kodi kept waiting says what the box can do at most (otherwise Lucent simply did not
    need more). The best such half minute is kept; the source list uses it to warn about releases that are too heavy."""
    if waits < 10 or per_second <= 0:
        return
    saved = util.load_json(SPEED_FILE, {})
    if per_second > float(saved.get("max") or 0):
        util.save_json(SPEED_FILE, {"max": round(per_second), "at": int(time.time())})


def most_per_second():
    """Bytes per second this box got from Usenet at most, or 0 when never measured."""
    return float(util.load_json(SPEED_FILE, {}).get("max") or 0)
_speed = {"at": 0.0, "cpu": 0.0, "stats": None}


def _speed_line(release):
    """Every 30 seconds while a release is read: how fast it comes in, how fast Kodi takes it, whether Kodi had to wait,
    and how busy the processor is (all of Kodi, so picture decoding counts too). For testing on small boxes."""
    now, cpu = time.time(), time.process_time()
    stats = dict(release.stats)
    before = _speed["stats"]
    if before is not None and _speed.get("release") is release and now - _speed["at"] >= SPEED_EVERY:
        seconds = now - _speed["at"]
        cores = os.cpu_count() or 1
        net = stats.get("net", 0.0) - before.get("net", 0.0)
        raw = stats.get("raw", 0) - before.get("raw", 0)
        unpack = stats.get("decode", 0.0) - before.get("decode", 0.0)
        answers = nntp.ANSWERS["count"] - _speed.get("answers", (0, 0.0))[0]
        answer_time = nntp.ANSWERS["seconds"] - _speed.get("answers", (0, 0.0))[1]
        util.log("usenet: speed: in %.1f MB/s, to Kodi %.1f MB/s, Kodi waited %d time(s) (%.1f s), processor %d%% of %d core(s), cache %d MB; "
                 "one connection %.1f MB/s, %d connection(s) busy on average, unpacking %d%% of the time; "
                 "provider answers in %d ms, %d pair(s), reading %d MB ahead"
                 % ((stats["in"] - before["in"]) / seconds / 1e6, (stats["out"] - before["out"]) / seconds / 1e6,
                    stats["waits"] - before["waits"], stats["waited"] - before["waited"],
                    round(100 * (cpu - _speed["cpu"]) / seconds / cores), cores, release.cached // 1000000,
                    raw / net / 1e6 if net else 0, round(net / seconds), round(100 * unpack / seconds),
                    round(1000 * answer_time / answers) if answers else 0, stats.get("pairs", 0) - before.get("pairs", 0),
                    release.ahead >> 20))
        _remember_speed((stats["in"] - before["in"]) / seconds, stats["waits"] - before["waits"])
    elif before is not None and _speed.get("release") is release:
        return
    _speed.update(at=now, cpu=cpu, stats=stats, release=release, answers=(nntp.ANSWERS["count"], nntp.ANSWERS["seconds"]))


def tick(playing):
    """From the service every few seconds: on or off with the setting; an unread release is closed."""
    stats.flush()
    if wanted():
        start()
    elif _state["http"]:
        stop()
        util.log("usenet: local player server off")
        return
    vfs.tick(playing)
    drop_warm(older_than=WARM_KEEP)
    release = _state["release"]
    if release and time.time() - release.last_read < SPEED_EVERY:
        _speed_line(release)
    if release and not playing and time.time() - release.last_read > IDLE:
        with _state["lock"]:
            if _state["release"] is release:
                _close_release()
                util.log("usenet: release closed (not read for a while)")
    _tidy()


NZB_KEEP_DAYS = 7
NZB_KEEP_BYTES = 200 * 1024 * 1024      # the saved NZBs together at most this much (the oldest go first)


def _tidy():
    """Saved NZBs are kept for a week (up to NZB_KEEP_BYTES), so the same NZB is not fetched from the indexer again
    and again: indexers warn about that and can close the account."""
    try:
        base = folder()
        names = []
        for name in os.listdir(base):
            if name.endswith(".nzb"):
                path = os.path.join(base, name)
                names.append((os.path.getmtime(path), os.path.getsize(path), path))
    except OSError:
        return
    names.sort(reverse=True)                     # newest first
    total, now = 0, time.time()
    for when, size, path in names:
        total += size
        if now - when > NZB_KEEP_DAYS * 86400 or total > NZB_KEEP_BYTES:
            try:
                os.remove(path)
            except OSError:
                pass
