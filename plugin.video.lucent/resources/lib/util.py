# -*- coding: utf-8 -*-
"""Kodi helpers, HTTP and a small file cache. Secrets (the AIOStreams address, tokens) are never logged."""
import gzip
import hashlib
import http.client as httpclient
import importlib
import json
import os
import ssl
import threading
import time
import types
import urllib.error
import urllib.parse
import urllib.request
import uuid

import xbmc
import xbmcaddon
import xbmcgui
import xbmcvfs

ADDON_ID = "plugin.video.lucent"
BASE = "plugin://%s/" % ADDON_ID
ADDON = xbmcaddon.Addon(ADDON_ID)
NL = xbmc.getLanguage(xbmc.ISO_639_1) == "nl"
HOME = xbmcgui.Window(10000)
PROFILE = xbmcvfs.translatePath("special://profile/addon_data/%s/" % ADDON_ID)
CACHE = os.path.join(xbmcvfs.translatePath("special://temp/"), "lucent_video")


def T(nl, en):
    return nl if NL else en


def log(text, level=xbmc.LOGINFO):
    xbmc.log("[lucent.video] %s" % text, level)


def setting(name):
    return (xbmcaddon.Addon(ADDON_ID).getSetting(name) or "").strip()     # a fresh object: the service runs for days


def flag(name):
    return setting(name).lower() == "true"


# Only a fingerprint of the testers' code is kept here (this file is public): the code itself is never in the code.
# one code per tester (only their sha256 is here, never the codes): remove a line to take a code back
LAB_HASHES = {
    "bb3837535d91cf65f5c7f1a679c31adc7fcc791e9923ad8c9a6d546a349ae3eb",     # Wilfried
    "3de7c85ab486bd35bc87810e543f167cf0385178228c6abf082191bb42367536",     # tester 2
}
LAB_PROPERTY = "lucent.video.lab"


def lab():
    """Functions still being tested are only on with the test code (Settings > General, advanced level)."""
    code = setting("lab_code").strip().upper()
    return bool(code) and hashlib.sha256(code.encode("utf-8")).hexdigest() in LAB_HASHES


def publish_lab():
    """The settings show the test blocks only when the code is right (they look at this Home property)."""
    if lab():
        HOME.setProperty(LAB_PROPERTY, "1")
    else:
        HOME.clearProperty(LAB_PROPERTY)


def watched_at():
    """Percent from which a film or episode counts as watched (setting, 50-100; 90 when not set)."""
    try:
        value = float(setting("watched_at") or 90)
    except ValueError:
        value = 90.0
    return min(100.0, max(50.0, value))


def number(name, default):
    try:
        return int(setting(name))
    except ValueError:
        return default


def notify(text, heading="Lucent"):
    xbmcgui.Dialog().notification(heading, text, xbmcgui.NOTIFICATION_INFO, 4000)


def url_for(**params):
    clean = {k: v for k, v in params.items() if v not in (None, "")}
    return BASE + "?" + urllib.parse.urlencode(sorted(clean.items()))


class HttpError(Exception):
    def __init__(self, status, detail=None):
        Exception.__init__(self, "HTTP %s" % status)
        self.status = status
        self.detail = detail or {}


def _unpack(raw):
    if raw[:2] == b"\x1f\x8b":                          # packed (gzip): smaller over the network
        raw = gzip.decompress(raw)
    return json.loads(raw.decode("utf-8")) if raw else None


def _detail(raw):
    try:
        detail = _unpack(raw)
    except (ValueError, OSError, EOFError):
        return {}
    return detail if isinstance(detail, dict) else {}


_open = threading.local()       # per thread: connections that stay open, so the next question skips the handshake
_tls = []
REDIRECTS = (301, 302, 303, 307, 308)
STALE = (httpclient.RemoteDisconnected, httpclient.BadStatusLine, ConnectionResetError, BrokenPipeError,
         ConnectionAbortedError, httpclient.CannotSendRequest, httpclient.ResponseNotReady)


class _Fallback(Exception):
    """This question goes the ordinary way (a redirect, a proxy, ...)."""


def _connection(scheme, host, port, timeout):
    pool = getattr(_open, "pool", None)
    if pool is None:
        pool = _open.pool = {}
    key = (scheme, host, port)
    conn = pool.get(key)
    reused = conn is not None
    if conn is None:
        if scheme == "https":
            if not _tls:
                _tls.append(ssl.create_default_context())
            conn = httpclient.HTTPSConnection(host, port, timeout=timeout, context=_tls[0])
        else:
            conn = httpclient.HTTPConnection(host, port, timeout=timeout)
        pool[key] = conn
    conn.timeout = timeout
    if conn.sock is not None:
        conn.sock.settimeout(timeout)
    return conn, reused


def _drop(scheme, host, port):
    conn = (getattr(_open, "pool", None) or {}).pop((scheme, host, port), None)
    if conn is not None:
        conn.close()


def _keepalive(url, body, head, timeout, method):
    """One question over a connection that stays open. Raises _Fallback when it should go the ordinary way."""
    parts = urllib.parse.urlsplit(url)
    scheme, host = parts.scheme, parts.hostname
    if scheme not in ("http", "https") or not host or parts.username or parts.password:
        raise _Fallback()
    proxies = urllib.request.getproxies()
    if proxies.get(scheme) and not urllib.request.proxy_bypass(host):
        raise _Fallback()                                  # a proxy is set up on this device: leave it to urllib
    port = parts.port or (443 if scheme == "https" else 80)
    path = parts.path or "/"
    if parts.query:
        path += "?" + parts.query
    method = method or ("POST" if body is not None else "GET")
    if method != "GET":
        _drop(scheme, host, port)              # sending something: always over a fresh connection, so it is never sent twice
    for attempt in (1, 2):
        conn, reused = _connection(scheme, host, port, timeout)
        try:
            conn.request(method, path, body=body, headers=head)
            response = conn.getresponse()
            raw = response.read()
        except STALE:
            _drop(scheme, host, port)
            if reused and attempt == 1:                    # the server had closed it while it waited: once more, fresh
                continue
            raise
        except BaseException:
            _drop(scheme, host, port)
            raise
        if response.will_close:
            _drop(scheme, host, port)
        if response.status in REDIRECTS:
            raise _Fallback()
        if not 200 <= response.status < 300:
            raise HttpError(response.status, _detail(raw))
        return _unpack(raw), dict(response.msg)
    raise _Fallback()


def http(url, data=None, headers=None, timeout=20, method=None, form=None, raw=None):
    """Returns (parsed json or None, response headers). The error never holds the URL."""
    head = {"User-Agent": "Lucent-Video/0.1", "Accept": "application/json", "Accept-Encoding": "gzip"}
    head.update(headers or {})
    body = None
    if data is not None:
        body = json.dumps(data).encode("utf-8")
        head["Content-Type"] = "application/json"
    elif raw is not None:
        body, head["Content-Type"] = raw
    elif form is not None:
        body = urllib.parse.urlencode(form).encode("utf-8")
        head["Content-Type"] = "application/x-www-form-urlencoded"
    try:
        return _keepalive(url, body, head, timeout, method)
    except _Fallback:
        pass
    except HttpError:
        raise
    except (OSError, ValueError, EOFError, httpclient.HTTPException) as error:
        raise HttpError(type(error).__name__) from None
    request = urllib.request.Request(url, data=body, headers=head, method=method)
    try:
        with urllib.request.urlopen(request, timeout=timeout) as response:
            return _unpack(response.read()), dict(response.headers)
    except urllib.error.HTTPError as error:
        raise HttpError(error.code, _detail(error.read())) from None
    except (urllib.error.URLError, OSError, ValueError, EOFError, httpclient.HTTPException) as error:
        raise HttpError(type(error).__name__) from None


def _cache_path(key):
    return os.path.join(CACHE, hashlib.sha1(_user_key(key).encode("utf-8")).hexdigest() + ".json")


def cache_get(key, ttl):
    path = _cache_path(key)
    try:
        if time.time() - os.path.getmtime(path) > ttl:
            return None
        with open(path, encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def write_text(path, text):
    """Write a file in one go: a temporary file of its own (widgets, the service and lists can write the same file at
    the same moment; on Android they are all threads of one process), then put in place."""
    tmp = "%s.%s.tmp" % (path, uuid.uuid4().hex[:10])
    try:
        with open(tmp, "w", encoding="utf-8") as handle:
            handle.write(text)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.remove(tmp)
        except OSError:
            pass
        raise


def cache_set(key, value):
    try:
        os.makedirs(CACHE, exist_ok=True)
        write_text(_cache_path(key), json.dumps(value))
    except (OSError, TypeError, ValueError):
        pass


def cache_drop(key):
    try:
        os.remove(_cache_path(key))
    except OSError:
        pass


def cache_prune(days=31):
    """Throw away saved answers nobody can use any more (the longest anything is kept is 30 days)."""
    limit = time.time() - days * 86400
    try:
        names = os.listdir(CACHE)
    except OSError:
        return 0
    gone = 0
    for name in names:
        path = os.path.join(CACHE, name)
        try:
            if os.path.getmtime(path) < limit:
                os.remove(path)
                gone += 1
        except OSError:
            pass
    return gone


def cache_clear():
    try:
        for name in os.listdir(CACHE):
            os.remove(os.path.join(CACHE, name))
    except OSError:
        pass


USERS = "users.json"
PER_USER = {"punchplay_history.lock", "kompas_profile.json", "kompas_ratings.json", "kompas_rows.json", "kompas_shown.json", "kompas_saved.json", "search_history.json", "trakt.json", "simkl.json", "wetrakr.json", "simkl_state.json", "punchplay.json", "punchplay_state.json", "punchplay.lock", "watched.json", "watchlist.json", "progress.json", "last_release.json"}


OVERRIDE_USER = None      # set for a moment while marking a title for someone who watched along


def current_user():
    if OVERRIDE_USER:
        return OVERRIDE_USER
    try:
        with open(os.path.join(PROFILE, USERS), encoding="utf-8") as handle:
            return json.load(handle).get("current") or "main"
    except (OSError, ValueError):
        return "main"


def _path(name):
    """The first profile keeps its files where they always were; the others get their own folder."""
    user = current_user()
    if name in PER_USER and user != "main":
        return os.path.join(PROFILE, "users", user, name)
    return os.path.join(PROFILE, name)


def _user_key(key):
    """Account answers are cached per person; TMDB answers are shared."""
    user = current_user()
    return "%s|%s" % (user, key) if user != "main" and key.startswith(("trakt", "simkl", "punchplay", "nextup")) \
        and not key.startswith("traktsearch/") else key


def load_json(name, default):
    try:
        with open(_path(name), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return default


def save_json(name, value):
    path = _path(name)
    os.makedirs(os.path.dirname(path), exist_ok=True)
    write_text(path, json.dumps(value))


def video_playing():
    """A video is on (any video, also from outside Lucent): background work waits, so the box has its full power for
    decoding (Dolby Vision FEL needs it)."""
    try:
        return xbmc.getCondVisibility("Player.HasVideo")
    except Exception:          # noqa: BLE001
        return False


class _Lazy(types.ModuleType):
    """A module that is only loaded the first time it is used: opening a menu does not load the player and library code."""

    def __getattr__(self, attr):
        return getattr(importlib.import_module(self.__name__), attr)


def lazy(name):
    return _Lazy(name)
