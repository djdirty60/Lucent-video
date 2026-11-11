# -*- coding: utf-8 -*-
"""WeTrakr next to your main account: what you watch with Lucent Video also goes to WeTrakr (wetrakr.com). Trakt (or
Simkl) stays the account Lucent reads from; WeTrakr only receives.

Link: the device code of WeTrakr's Kodi API (no key of Lucent's own is needed):
    POST https://api.wetrakr.com/oauth/device/code   {}                 -> user_code, device_code, verification_url
    POST https://api.wetrakr.com/oauth/device/token  {device_code}      -> access_token, username
Events: POST https://api.wetrakr.com/webhooks/kodi/<token> with event playing / paused / scrobble / rating, the same
shape as WeTrakr's own Kodi add-on (script.wetrakr). The token is a secret: it is never written to the log."""
import threading
import time

import util

T = util.T
API = "https://api.wetrakr.com"
FILE = "wetrakr.json"
WATCHED_AT = 80.0


def account():
    return util.load_json(FILE, {})


def linked():
    return bool(account().get("token"))


def _payload(event, meta, percent=0.0, total=0):
    ids = {"tmdb": str(meta["tmdb"])} if meta.get("kind") == "movie" else {}
    if meta.get("kind") == "movie" and meta.get("imdb"):
        ids["imdb"] = meta["imdb"]
    body = {"event": event, "progress": round(float(percent or 0), 1)}
    if total:
        body["runtime"] = int(total)
    if meta.get("kind") == "movie":
        body.update(media_type="movie", title=meta.get("title") or "", year=int(meta.get("year") or 0), ids=ids)
        if meta.get("premiered"):
            body["premiered"] = meta["premiered"][:10]
    else:
        show_ids = {"tmdb": str(meta["tmdb"])}
        for key in ("imdb", "tvdb"):
            if meta.get(key):
                show_ids[key] = str(meta[key])
        body.update(media_type="episode", title=meta.get("title") or "", show_title=meta.get("show") or "", show_ids=show_ids,
                    season=int(meta["season"]), episode=int(meta["episode"]),
                    ids={"tmdb": str(meta["tmdb_episode"])} if meta.get("tmdb_episode") else {})
        if meta.get("premiered"):
            body["aired"] = meta["premiered"][:10]
    return body


def _send(body):
    token = account().get("token")
    if not token:
        return
    try:
        util.http("%s/webhooks/kodi/%s" % (API, token), data=body, timeout=10)
    except util.HttpError as error:
        util.log("WeTrakr %s: %s" % (body.get("event"), error))           # never the address: it holds the token
        if error.status in (401, 403):
            saved = account()
            saved["rejected"] = True
            util.save_json(FILE, saved)


def send(event, meta, percent=0.0, total=0):
    """In the background, once; never in the way of playback. Nothing without a link."""
    if meta and meta.get("kind") in ("movie", "episode") and meta.get("tmdb") and linked():
        threading.Thread(target=_send, args=(_payload(event, meta, percent, total),), daemon=True).start()


def scrobble(state, meta, percent, total=0):
    """Lucent's scrobble moments: start -> playing, pause -> paused, stop -> scrobble (watched) or paused."""
    if state == "start":
        send("playing", meta, percent, total)
    elif state == "pause":
        send("paused", meta, percent, total)
    elif state == "stop":
        send("scrobble" if percent >= WATCHED_AT else "paused", meta, percent, total)


def watched(meta, seen):
    """'Mark as watched' in Lucent: WeTrakr gets it as watched. (Its Kodi API has no 'unwatched'.)"""
    if seen:
        send("scrobble", meta, 100.0)


def rate(kind, tmdb_id, stars10):
    if linked():
        body = {"event": "rating", "media_type": "movie" if kind == "movie" else "show", "ids": {"tmdb": str(tmdb_id)}, "rating": int(stars10)}
        threading.Thread(target=_send, args=(body,), daemon=True).start()


# ------------------------------------------------------------------ linking
def login():
    import xbmc
    import xbmcgui
    import dialogs
    try:
        code, _ = util.http(API + "/oauth/device/code", data={}, timeout=15)
    except util.HttpError:
        util.notify(T("WeTrakr is niet bereikbaar", "WeTrakr cannot be reached"))
        return False
    dialog, picture = dialogs.link_dialog("WeTrakr", code.get("verification_url") or "https://wetrakr.com/activate", code["user_code"])
    state = {"open": True, "linked": False}

    def poll():
        interval, started = int(code.get("interval") or 5), time.time()
        monitor = xbmc.Monitor()
        while state["open"] and time.time() - started < int(code.get("expires_in") or 600):
            if monitor.waitForAbort(interval):
                break
            try:
                answer, _ = util.http(API + "/oauth/device/token", data={"device_code": code["device_code"]}, timeout=10)
            except util.HttpError as error:
                reason = (error.detail or {}).get("error") if isinstance(error.detail, dict) else ""
                if reason == "slow_down":
                    interval += 2
                    continue
                if reason == "authorization_pending" or error.status in (400, 428):
                    continue
                break
            if (answer or {}).get("access_token"):
                util.save_json(FILE, {"token": answer["access_token"], "user": answer.get("username") or ""})
                state["linked"] = True
                break
        if state["open"]:
            state["open"] = False
            dialog.close()

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    dialog.doModal()
    state["open"] = False
    worker.join(timeout=15)
    del dialog
    try:
        import os
        os.remove(picture)
    except (OSError, TypeError):
        pass
    if state["linked"]:
        util.ADDON.setSetting("wetrakr_user", account().get("user") or "WeTrakr")
        xbmcgui.Dialog().ok("WeTrakr", T("Gekoppeld als %s. Wat je met Lucent kijkt, gaat nu ook naar WeTrakr.",
                                         "Linked as %s. What you watch with Lucent now also goes to WeTrakr.") % (account().get("user") or "WeTrakr"))
    return state["linked"]


def logout():
    util.save_json(FILE, {})
    util.ADDON.setSetting("wetrakr_user", "")
    util.notify(T("WeTrakr losgekoppeld", "WeTrakr unlinked"))
