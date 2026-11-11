# -*- coding: utf-8 -*-
"""The Kodi library and your account stay the same, both ways.

- Account -> library: what you saw and where you stopped is put on the library titles every half hour and right after
  a video ends (library.sync_state).
- Library -> account: when you mark a Lucent Video title in the Kodi library as watched or unwatched yourself, that goes
  to Trakt, Simkl or this device too. Watching from the library already goes through Lucent Video, which scrobbles.
"""
import json
import threading
import time
import urllib.parse

import xbmc
import xbmcvfs

import util

QUIET = "lucent.video.libsync"      # Home property: Lucent itself is changing the library right now
SYNC_EVERY = 30 * 60
_last = {"sync": 0.0}
_sent = {}
_recent = {}                         # title key -> time a Lucent Video playback ended (Kodi then updates playcount itself)


def quiet(seconds=30):
    util.HOME.setProperty(QUIET, str(time.time() + seconds))


def _is_quiet():
    try:
        return float(util.HOME.getProperty(QUIET) or 0) > time.time()
    except ValueError:
        return False


def played(meta):
    """Called by the player service when a video ends: Kodi's own playcount change for it is not sent again."""
    _recent["%s:%s:%s" % (meta.get("tmdb"), meta.get("season") or "", meta.get("episode") or "")] = time.time()


def sync_soon(delay=20):
    """Puts what the account knows on the library, a moment after a video ended (the scrobble needs to arrive first)."""
    if not util.flag("library_on"):
        return

    def run():
        if not xbmc.Monitor().waitForAbort(delay):
            import library
            library.sync_state()
            _last["sync"] = time.time()
    threading.Thread(target=run, daemon=True).start()


def sync_due():
    return util.flag("library_on") and time.time() - _last["sync"] > SYNC_EVERY


def periodic():
    import library
    _last["sync"] = time.time()
    library.sync_state()


def _target(kind, item_id):
    method, key, id_name = ("VideoLibrary.GetMovieDetails", "moviedetails", "movieid") if kind == "movie" else \
        ("VideoLibrary.GetEpisodeDetails", "episodedetails", "episodeid")
    answer = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                        "params": {id_name: item_id, "properties": ["file", "playcount"]}})))
    details = (answer.get("result") or {}).get(key) or {}
    path = details.get("file") or ""
    if util.ADDON_ID not in path or not path.endswith(".strm"):
        return None, None
    handle = xbmcvfs.File(path)
    try:
        text = handle.read().strip()
    finally:
        handle.close()
    query = dict(urllib.parse.parse_qsl(text.split("?", 1)[1])) if "?" in text else {}
    if not query.get("tmdb"):
        return None, None
    meta = {"kind": query.get("type"), "tmdb": query["tmdb"], "season": query.get("season"), "episode": query.get("episode")}
    return meta, int(details.get("playcount") or 0)


def _resume_of(kind, item_id):
    method, key, id_name = ("VideoLibrary.GetMovieDetails", "moviedetails", "movieid") if kind == "movie" else \
        ("VideoLibrary.GetEpisodeDetails", "episodedetails", "episodeid")
    answer = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method,
                                                        "params": {id_name: item_id, "properties": ["resume"]}})))
    return (((answer.get("result") or {}).get(key) or {}).get("resume") or {}).get("position") or 0


def _maybe_reset(item):
    """Kodi's 'Reset resume position': the title has no resume point in the library any more, while your account
    (or this device) still has one. Forget it there too, else the next library update puts it back."""
    import tracker
    if xbmc.Monitor().waitForAbort(3) or xbmc.Player().isPlayingVideo():
        return
    try:
        meta, _count = _target(item["type"], item["id"])
        if not meta or _resume_of(item["type"], item["id"]):
            return
        key = "%s:%s:%s" % (meta["tmdb"], meta.get("season") or "", meta.get("episode") or "")
        if time.time() - _recent.get(key, 0) < 60:
            return                                   # Kodi tidying up right after a Lucent playback
        import player
        local = player.progress_key(meta) in util.load_json(player.LOCAL, {})
        if not local and not tracker.resume_percent(meta["kind"], meta["tmdb"], meta.get("season"), meta.get("episode")):
            return                                   # nothing to forget
        import users
        util.OVERRIDE_USER = users.MAIN
        try:
            tracker.forget_resume(meta)
            util.log("library -> account: resume point of %s removed" % key)
        finally:
            util.OVERRIDE_USER = None
    except (KeyError, RuntimeError, ValueError, util.HttpError) as error:
        util.log("resume reset: %s" % type(error).__name__)


def on_notification(method, data):
    """Kodi's VideoLibrary.OnUpdate: a title's playcount changed."""
    if method != "VideoLibrary.OnUpdate" or _is_quiet():
        return
    try:
        info = json.loads(data or "{}")
    except ValueError:
        return
    item = info.get("item") or {}
    if item.get("type") not in ("movie", "episode"):
        return
    if "playcount" not in info:
        if not info.get("added"):
            threading.Thread(target=_maybe_reset, args=(item,), daemon=True).start()
        return
    def send():
        import tracker
        if xbmc.Monitor().waitForAbort(6):              # Kodi may tell this before the player service is done
            return
        try:
            meta, count = _target(item["type"], item["id"])     # read again: several quick changes end in the last one
        except (KeyError, RuntimeError, ValueError):
            return
        if not meta or xbmc.Player().isPlayingVideo():
            return
        key = "%s:%s:%s" % (meta["tmdb"], meta.get("season") or "", meta.get("episode") or "")
        if time.time() - _recent.get(key, 0) < 600 or _sent.get(key) == (count > 0, int(time.time() // 30)):
            return                                       # just played with Lucent, or this very change was already sent
        _sent[key] = (count > 0, int(time.time() // 30))
        import users
        util.OVERRIDE_USER = users.MAIN           # the library follows the first profile, so its account gets it
        try:
            tracker.set_watched(meta, count > 0)
            util.log("library -> account: %s %s" % (key, "watched" if count > 0 else "unwatched"))
        except util.HttpError as error:
            util.log("library -> account failed: %s" % error)
        finally:
            util.OVERRIDE_USER = None
    threading.Thread(target=send, daemon=True).start()
