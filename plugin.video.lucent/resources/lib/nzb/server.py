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

from . import nntp, nzbfile, stream, yenc

PORT_PROPERTY = "lucent.video.nzb.port"
FOLDER = "nzb"
IDLE = 300                      # seconds without reads before a release is closed
CHUNK = 1048576
_state = {"http": None, "release": None, "key": "", "pool": None, "lock": threading.Lock()}


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
    return util.lab() and util.flag("nzb_on") and bool(util.setting("nzb_host"))


def _close_release():
    release, pool, spare = _state["release"], _state["pool"], _state.get("backup")
    _state["release"], _state["key"], _state["pool"], _state["backup"] = None, "", None, None
    if release:
        release.close()
    for one in (pool, spare):
        if one:
            one.close()


def prepare(name, season=None, episode=None, pack=False):
    """Open the release in nzb/<name>. Returns a dict for the plugin: ok, key, name, size or error."""
    path = os.path.join(folder(), os.path.basename(name))
    try:
        with open(path, "rb") as handle:
            data = handle.read()
    except OSError:
        return {"ok": False, "error": "nzb missing"}
    with _state["lock"]:
        _close_release()
        pool = nntp.Pool(provider())
        spare_settings = provider(backup=True)
        spare = nntp.Pool(spare_settings) if spare_settings else None
        megabyte = 1024 * 1024
        started = time.time()
        try:
            release = stream.open_release(pool, data, season, episode, log=lambda text: util.log("usenet: " + text), pack=pack,
                                          backup=spare, fill=util.flag("nzb_fill"),
                                          ahead=max(16, util.number("nzb_ahead", 64)) * megabyte,
                                          cache=max(32, util.number("nzb_cache", 128)) * megabyte)
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
        _state["release"], _state["pool"], _state["key"], _state["backup"] = release, pool, uuid.uuid4().hex[:12], spare
        util.log("usenet: release ready in %.1f s" % (time.time() - started))
        _warn_slow()
        return {"ok": True, "key": _state["key"], "name": release.name, "size": release.size}


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
            return self._json(prepare(query.get("nzb", ""), query.get("season"), query.get("episode"), query.get("pack") == "1"))
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
        try:
            while at <= end:
                chunk = release.read(at, min(CHUNK, end - at + 1))
                if not chunk:
                    break
                self.wfile.write(chunk)
                at += len(chunk)
        except (BrokenPipeError, ConnectionResetError):
            pass                            # Kodi closed this request (a seek): normal
        except (stream.StreamError, nntp.NntpError, yenc.YencError, OSError) as error:
            util.log("usenet: reading stopped (%s)" % type(error).__name__)
            self.close_connection = True


def start():
    if _state["http"]:
        return
    try:                                        # Android: a folder inside the app for the fast unpacker (see yenc)
        import xbmcvfs
        inside = os.path.dirname(os.path.dirname(xbmcvfs.translatePath("special://xbmc").rstrip("/\\")))
        yenc.set_copy_dir(os.path.join(inside, "lucent_native"))
    except Exception:                           # noqa: BLE001 - only a faster way, never a reason not to start
        pass
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    _state["http"] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    util.HOME.setProperty(PORT_PROPERTY, str(server.server_address[1]))
    util.log("usenet: local player server on, unpacking: %s" % yenc.mode())


def stop():
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
        util.log("usenet: speed: in %.1f MB/s, to Kodi %.1f MB/s, Kodi waited %d time(s) (%.1f s), processor %d%% of %d core(s), cache %d MB; "
                 "one connection %.1f MB/s, %d connection(s) busy on average, unpacking %d%% of the time"
                 % ((stats["in"] - before["in"]) / seconds / 1e6, (stats["out"] - before["out"]) / seconds / 1e6,
                    stats["waits"] - before["waits"], stats["waited"] - before["waited"],
                    round(100 * (cpu - _speed["cpu"]) / seconds / cores), cores, release.cached // 1000000,
                    raw / net / 1e6 if net else 0, round(net / seconds), round(100 * unpack / seconds)))
        _remember_speed((stats["in"] - before["in"]) / seconds, stats["waits"] - before["waits"])
    elif before is not None and _speed.get("release") is release:
        return
    _speed.update(at=now, cpu=cpu, stats=stats, release=release)


def tick(playing):
    """From the service every few seconds: on or off with the setting; an unread release is closed."""
    if wanted():
        start()
    elif _state["http"]:
        stop()
        util.log("usenet: local player server off")
        return
    release = _state["release"]
    if release and time.time() - release.last_read < SPEED_EVERY:
        _speed_line(release)
    if release and not playing and time.time() - release.last_read > IDLE:
        with _state["lock"]:
            if _state["release"] is release:
                _close_release()
                util.log("usenet: release closed (not read for a while)")
    _tidy()


def _tidy():
    """Keep only the last few NZB files."""
    try:
        names = sorted((os.path.getmtime(os.path.join(folder(), n)), n) for n in os.listdir(folder()) if n.endswith(".nzb"))
    except OSError:
        return
    for _when, name in names[:-10]:
        try:
            os.remove(os.path.join(folder(), name))
        except OSError:
            pass
