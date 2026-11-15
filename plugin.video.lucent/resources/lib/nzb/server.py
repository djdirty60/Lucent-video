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


def provider():
    """The provider settings, read fresh (never logged)."""
    return {"host": util.setting("nzb_host"), "port": util.number("nzb_port", 563), "ssl": util.setting("nzb_ssl").lower() != "false",
            "user": util.setting("nzb_user"), "password": util.setting("nzb_pass"), "connections": util.number("nzb_conns", 20)}


def wanted():
    return util.lab() and util.flag("nzb_on") and bool(util.setting("nzb_host"))


def _close_release():
    release, pool = _state["release"], _state["pool"]
    _state["release"], _state["key"], _state["pool"] = None, "", None
    if release:
        release.close()
    if pool:
        pool.close()


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
        started = time.time()
        try:
            release = stream.open_release(pool, data, season, episode, log=lambda text: util.log("usenet: " + text), pack=pack)
        except nntp.AuthError:
            pool.close()
            return {"ok": False, "error": "login"}
        except (nntp.NntpError, OSError, yenc.YencError, stream.StreamError, nzbfile.NzbError) as error:
            pool.close()
            return {"ok": False, "error": "%s: %s" % (type(error).__name__, error)}
        _state["release"], _state["pool"], _state["key"] = release, pool, uuid.uuid4().hex[:12]
        util.log("usenet: release ready in %.1f s" % (time.time() - started))
        return {"ok": True, "key": _state["key"], "name": release.name, "size": release.size}


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
    server = ThreadingHTTPServer(("127.0.0.1", 0), _Handler)
    server.daemon_threads = True
    _state["http"] = server
    threading.Thread(target=server.serve_forever, daemon=True).start()
    util.HOME.setProperty(PORT_PROPERTY, str(server.server_address[1]))
    util.log("usenet: local player server on")


def stop():
    with _state["lock"]:
        _close_release()
    if _state["http"]:
        _state["http"].shutdown()
        _state["http"] = None
    util.HOME.clearProperty(PORT_PROPERTY)


def tick(playing):
    """From the service every few seconds: on or off with the setting; an unread release is closed."""
    if wanted():
        start()
    elif _state["http"]:
        stop()
        util.log("usenet: local player server off")
        return
    release = _state["release"]
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
