# -*- coding: utf-8 -*-
"""Your own TMDB account: link it once (approve on your phone), then your TMDB watchlist, favourites and ratings are
lists in Lucent, and a film or series can be put on them or rated from Lucent. The session stays on this box only."""
import os
import threading
import time

import xbmc
import xbmcgui

import tmdb
import util

T = util.T
FILE = "tmdb_account.json"             # {"session": ..., "id": ..., "user": ...} - per Kodi user, never in a log or report
LISTS = {"tmdb_watchlist_movie": ("watchlist/movies", "movie"), "tmdb_watchlist_tv": ("watchlist/tv", "tvshow"),
         "tmdb_favorite_movie": ("favorite/movies", "movie"), "tmdb_favorite_tv": ("favorite/tv", "tvshow"),
         "tmdb_rated_movie": ("rated/movies", "movie"), "tmdb_rated_tv": ("rated/tv", "tvshow")}
LABELS = {"tmdb_watchlist_movie": T("Kijklijst films", "Watchlist movies"), "tmdb_watchlist_tv": T("Kijklijst series", "Watchlist shows"),
          "tmdb_favorite_movie": T("Favoriete films", "Favourite movies"), "tmdb_favorite_tv": T("Favoriete series", "Favourite shows"),
          "tmdb_rated_movie": T("Beoordeelde films", "Rated movies"), "tmdb_rated_tv": T("Beoordeelde series", "Rated shows")}


def account():
    return util.load_json(FILE, {})


def linked():
    return bool(account().get("session"))


def _call(path, method=None, data=None, **params):
    params.update(api_key=tmdb.key())
    if linked():
        params["session_id"] = account()["session"]
    import urllib.parse
    url = tmdb.API + path + "?" + urllib.parse.urlencode(params)
    return util.http(url, data=data, method=method)[0] or {}


# ------------------------------------------------------------------ linking
def login():
    import dialogs
    if not tmdb.key():
        return util.notify(T("Geen TMDB-sleutel", "No TMDB key"))
    try:
        token = _call("authentication/token/new").get("request_token")
    except util.HttpError as error:
        util.log("TMDB link: %s" % error)
        return util.notify(T("TMDB is niet bereikbaar", "TMDB cannot be reached"))
    if not token:
        return util.notify(T("TMDB is niet bereikbaar", "TMDB cannot be reached"))
    url = "https://www.themoviedb.org/authenticate/%s" % token
    dialog, picture = dialogs.link_dialog("TMDB", "https://www.themoviedb.org/authenticate", T("Goedkeuren", "Approve"), url)
    state = {"open": True, "linked": False}

    def poll():
        monitor, started = xbmc.Monitor(), time.time()
        while state["open"] and time.time() - started < 900:          # a request token lasts 60 minutes; 15 is plenty
            if monitor.waitForAbort(3):
                break
            try:
                answer = _call("authentication/session/new", method="POST", data={"request_token": token})
            except util.HttpError:
                continue                        # not approved yet (TMDB answers 401 until you did)
            if answer.get("session_id"):
                util.save_json(FILE, {"session": answer["session_id"]})
                state["linked"] = True
                break
        if state["open"]:
            state["open"] = False
            dialog.close()

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    dialog.doModal()
    state["open"] = False
    worker.join(timeout=10)
    try:
        os.remove(picture)
    except OSError:
        pass
    if not state["linked"]:
        return False
    try:
        info = _call("account")
        util.save_json(FILE, dict(account(), id=info.get("id"), user=info.get("username") or ""))
    except util.HttpError as error:
        util.log("TMDB account: %s" % error)
    util.log("TMDB account linked")
    import accountinfo
    accountinfo.publish_names()
    util.notify(T("TMDB is gekoppeld", "TMDB is linked"))
    return True


def logout():
    if linked():
        try:
            _call("authentication/session", method="DELETE", data={"session_id": account()["session"]})
        except util.HttpError as error:
            util.log("TMDB unlink: %s" % error)
    util.save_json(FILE, {})
    import accountinfo
    accountinfo.publish_names()
    util.notify(T("TMDB is ontkoppeld", "TMDB is unlinked"))


def _account_id():
    saved = account()
    if saved.get("id"):
        return saved["id"]
    info = _call("account")
    util.save_json(FILE, dict(saved, id=info.get("id"), user=info.get("username") or ""))
    return info.get("id")


# ------------------------------------------------------------------ the lists
def titles(name, page=1):
    """(titles, more pages?) of one of your TMDB lists, newest first."""
    path, kind = LISTS[name]
    data = _call("account/%s/%s" % (_account_id(), path), page=page, sort_by="created_at.desc", language=tmdb.lang())
    rows = data.get("results") or []
    found = tmdb.many([(kind, r["id"]) for r in rows])
    if name.startswith("tmdb_rated"):
        mine = {str(r["id"]): r.get("rating") for r in rows}
        for meta in found:
            if mine.get(str(meta.get("tmdb"))):
                meta["userrating"] = int(round(float(mine[str(meta["tmdb"])])))
    return found, page < (data.get("total_pages") or 1)


# ------------------------------------------------------------------ from a title
def actions(kind, tmdb_id):
    """Watchlist, favourite and rating on your TMDB account for one film or series."""
    if not linked():
        return util.notify(T("Koppel eerst je TMDB-account in de instellingen", "Link your TMDB account in the settings first"))
    api, media = ("movie", "movie") if kind == "movie" else ("tv", "tv")
    try:
        state = _call("%s/%s/account_states" % (api, tmdb_id))
    except util.HttpError as error:
        util.log("TMDB states: %s" % error)
        return util.notify(T("TMDB is niet bereikbaar", "TMDB cannot be reached"))
    rated = (state.get("rated") or {}).get("value") if isinstance(state.get("rated"), dict) else None
    entries = [(T("Van TMDB-kijklijst af", "Remove from TMDB watchlist") if state.get("watchlist") else T("Op TMDB-kijklijst", "Add to TMDB watchlist"), "watchlist"),
               (T("Geen favoriet meer", "Remove from favourites") if state.get("favorite") else T("Favoriet maken", "Mark as favourite"), "favorite"),
               ((T("Beoordelen (nu %s/10)", "Rate (now %s/10)") % ("%g" % rated)) if rated else T("Beoordelen", "Rate"), "rate")]
    if rated:
        entries.append((T("Beoordeling wissen", "Remove rating"), "unrate"))
    picked = xbmcgui.Dialog().contextmenu([label for label, _what in entries])
    if picked < 0:
        return None
    what = entries[picked][1]
    try:
        if what in ("watchlist", "favorite"):
            _call("account/%s/%s" % (_account_id(), what), method="POST",
                  data={"media_type": media, "media_id": int(tmdb_id), what: not state.get(what)})
        elif what == "rate":
            numbers = [str(n) for n in range(10, 0, -1)]
            choice = xbmcgui.Dialog().select(T("Hoeveel punten?", "How many points?"), numbers)
            if choice < 0:
                return None
            _call("%s/%s/rating" % (api, tmdb_id), method="POST", data={"value": float(numbers[choice])})
        else:
            _call("%s/%s/rating" % (api, tmdb_id), method="DELETE")
    except util.HttpError as error:
        util.log("TMDB %s: %s" % (what, error))
        return util.notify(T("TMDB is niet bereikbaar, er is niets gewijzigd", "TMDB cannot be reached, nothing was changed"))
    util.notify(T("Bijgewerkt op TMDB", "Updated on TMDB"))
    return None
