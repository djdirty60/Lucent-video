# -*- coding: utf-8 -*-
"""PunchPlay account (punchplay.tv). Like Trakt and Simkl: what you watch, where you stopped, your watchlist and lists.

Signing in uses the device code (a code on the TV, you approve on your phone). Lucent Video's PunchPlay app is a
public client: only the Client ID, never a secret. Access tokens live one hour; the refresh token changes with every
renewal and only the newest one works, so renewing is done by one process at a time (a small lock file).
"""
import os
import threading
import time
import uuid

import xbmc
import xbmcgui

import keys
import util

API = "https://punchplay.tv/api/platform/v1/"
FILE = "punchplay.json"
STATE = "punchplay_state.json"
LOCK = "punchplay.lock"
HAS_LISTS = True
WANTS_TOTAL = True          # tracker.py passes the running time: PunchPlay wants the position in seconds
SCOPES = "profile:read playback:read playback:write history:read history:write lists:read lists:write ratings:read ratings:write"
CHECK_EVERY = 300           # new watches: at most every five minutes (or when you ask)
FULL_EVERY = 6 * 3600       # the whole history again (picks up what was removed on the website)
PAGE = 100


def client():
    return keys.PUNCHPLAY_CLIENT_ID


def account():
    return util.load_json(FILE, {})


def logged_in():
    return bool(account().get("access_token"))


# ----------------------------------------------------------------------------------------- tokens
def _store(token, user=None):
    saved = account()
    token = dict(token)
    token["expires_at"] = time.time() + int(token.get("expires_in") or 3600)
    token["user"] = user if user is not None else saved.get("user", "")
    token.setdefault("refresh_token", saved.get("refresh_token", ""))
    util.save_json(FILE, token)
    return token


def _lock(name=LOCK, stale=30):
    """True when this one may go ahead (renew, or fetch the history). A lock older than `stale` seconds is left over and
    taken over. Widgets, the service and lists run at the same moment: only one asks PunchPlay at a time."""
    path = util._path(name)            # noqa: SLF001 - same add-on
    os.makedirs(os.path.dirname(path), exist_ok=True)
    for _ in range(2):
        try:
            os.close(os.open(path, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            return True
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(path) > stale:
                    os.remove(path)
                    continue
            except OSError:
                continue
            return False
        except OSError:
            return False
    return False


def _unlock(name=LOCK):
    try:
        os.remove(util._path(name))    # noqa: SLF001
    except OSError:
        pass


def _refresh(seen_token=None):
    """New tokens. Another process may have renewed just before: then its tokens are used."""
    for _ in range(20):
        saved = account()
        if not saved.get("refresh_token"):
            return None
        if seen_token and saved.get("access_token") != seen_token:
            return saved                          # someone else renewed meanwhile
        if _lock():
            break
        time.sleep(0.5)
    else:
        return account() if account().get("access_token") != seen_token else None
    try:
        saved = account()
        if seen_token and saved.get("access_token") != seen_token:
            return saved
        try:
            token, _ = util.http(API + "auth/refresh", data={"client_id": client(), "refresh_token": saved["refresh_token"]})
        except util.HttpError as error:
            util.log("PunchPlay renewal failed: %s" % error)
            if error.status in (400, 401):
                util.save_json(FILE, {})             # the link is gone: sign in again
                util.notify(util.T("Koppel PunchPlay opnieuw", "Link PunchPlay again"))
            return None
        return _store(token)
    finally:
        _unlock()


def _access():
    saved = account()
    if saved.get("refresh_token") and saved.get("expires_at", 0) - time.time() < 300:
        return (_refresh(saved.get("access_token")) or saved).get("access_token")
    return saved.get("access_token")


def call(path, data=None, method=None, idempotent=False, retry=True):
    """One request. Returns the answer (or None). A 429 waits as long as PunchPlay asks (at most 10 s) once."""
    token = _access()
    if not token:
        raise util.HttpError(401)
    head = {"Authorization": "Bearer %s" % token}
    if idempotent:
        head["Idempotency-Key"] = uuid.uuid4().hex
    try:
        return util.http(API + path, data=data, headers=head, method=method)[0]
    except util.HttpError as error:
        if error.status == 401 and retry and _refresh(token):
            return call(path, data, method, idempotent, False)
        if error.status == 429 and retry:
            xbmc.Monitor().waitForAbort(5)
            return call(path, data, method, idempotent, False)
        raise


# ----------------------------------------------------------------------------------------- linking
def login():
    import dialogs
    if not client():
        xbmcgui.Dialog().ok("Lucent", util.T("PunchPlay is nog niet beschikbaar in deze versie.", "PunchPlay is not available in this version yet."))
        return False
    try:
        code, _ = util.http(API + "auth/device/code", data={"client_id": client(), "scope": SCOPES})
    except util.HttpError as error:
        util.log("PunchPlay device code: %s" % error)
        util.notify(util.T("PunchPlay is niet bereikbaar", "PunchPlay cannot be reached"))
        return False
    dialog, picture = dialogs.link_dialog("PunchPlay", code["verification_uri"], code["user_code"], code.get("verification_uri_complete"))
    state = {"open": True, "linked": False}

    def poll():
        interval, started = 5, time.time()
        monitor = xbmc.Monitor()
        while state["open"] and time.time() - started < int(code.get("expires_in") or 900):
            if monitor.waitForAbort(interval):
                break
            try:
                token, _ = util.http(API + "auth/device/token", data={"client_id": client(), "device_code": code["device_code"],
                                                                      "device_name": "Kodi (Lucent Video)"})
            except util.HttpError as error:
                reason = (error.detail or {}).get("error")
                if reason == "authorization_pending":
                    continue
                if error.status == 429 or reason == "slow_down":
                    interval += 5
                    continue
                util.log("PunchPlay linking stopped: %s %s" % (error.status, reason or ""))
                break
            _store(token, user="")
            state["linked"] = True
            break
        if state["open"]:
            state["open"] = False
            dialog.close()

    worker = threading.Thread(target=poll, daemon=True)
    worker.start()
    dialog.doModal()
    state["open"] = False
    worker.join(timeout=20)
    del dialog
    try:
        os.remove(picture)
    except OSError:
        pass
    if state["linked"]:
        try:
            me = call("me") or {}
            saved = account()
            saved["user"] = me.get("username") or (me.get("profile") or {}).get("displayName") or ""
            util.save_json(FILE, saved)
        except util.HttpError:
            pass
        util.save_json(STATE, {})
        util.cache_clear()
        util.notify(util.T("PunchPlay is gekoppeld", "PunchPlay is linked"))
    return state["linked"]


def logout():
    saved = account()
    for kind in ("refresh_token", "access_token"):
        if saved.get(kind):
            try:
                util.http(API + "oauth/revoke", form={"token": saved[kind], "client_id": client()})
            except util.HttpError:
                pass
    util.save_json(FILE, {})
    util.save_json(STATE, {})
    util.cache_clear()
    util.notify(util.T("PunchPlay is losgekoppeld", "PunchPlay is unlinked"))


# ----------------------------------------------------------------------------------------- history
def _number(row, plain, source):
    value = row.get(source)
    return value if value is not None else row.get(plain)


def _entry(row):
    """A history row in short: what Lucent needs, with TMDB season and episode numbers."""
    out = {"id": row.get("id"), "type": row.get("type"), "at": str(row.get("watchedAt") or "").replace("T", " ")[:19]}
    if row.get("type") == "episode":
        out.update(show=row.get("showTmdbId"), season=_number(row, "season", "sourceSeason"), episode=_number(row, "episode", "sourceEpisode"))
    else:
        out["tmdb"] = row.get("tmdbId")
    return out


def _fetch(known_ids=None):
    """History pages, newest first. With known_ids: stops at the first page that holds a known entry."""
    rows, cursor = [], None
    for _ in range(200):
        path = "me/history?limit=%d" % PAGE + ("&cursor=%s" % cursor if cursor else "")
        answer = call(path) or {}
        page = [_entry(r) for r in answer.get("items") or []]
        rows += page
        cursor = answer.get("nextCursor")
        if not cursor or (known_ids and any(r["id"] in known_ids for r in page)):
            break
    return rows


HISTORY_LOCK = "punchplay_history.lock"


def _history(force=False):
    saved = util.load_json(STATE, {})
    rows = saved.get("rows")
    if rows is not None and not force and util.cache_get("punchplaychecked", CHECK_EVERY) is not None:
        return rows
    if not _lock(HISTORY_LOCK, 120):
        # another widget or the service is fetching it right now: wait for that answer instead of asking again
        monitor = xbmc.Monitor()
        for _ in range(60):
            if monitor.waitForAbort(0.5) or not os.path.exists(util._path(HISTORY_LOCK)):     # noqa: SLF001
                break
        rows = util.load_json(STATE, {}).get("rows")
        if rows is not None:
            return rows
        if not _lock(HISTORY_LOCK, 120):
            raise util.HttpError("PunchPlay busy")
    try:
        return _fetch_history(force)
    finally:
        _unlock(HISTORY_LOCK)


def _fetch_history(force):
    saved = util.load_json(STATE, {})
    rows = saved.get("rows")
    now = time.time()
    if rows is not None and not force and util.cache_get("punchplaychecked", CHECK_EVERY) is not None:
        return rows                       # fetched by another one while this one waited
    if rows is None or now - saved.get("full", 0) > FULL_EVERY:
        rows, full = _fetch(), now
    else:
        known = {r["id"] for r in rows}
        new = [r for r in _fetch(known) if r["id"] not in known]
        rows, full = new + rows, saved.get("full", now)
    util.save_json(STATE, dict(saved, rows=rows, full=full))
    util.cache_set("punchplaychecked", 1)
    return rows


def _safe():
    try:
        return _history()
    except util.HttpError as error:
        util.log("PunchPlay: %s" % error)
        return util.load_json(STATE, {}).get("rows") or []


def refresh():
    try:
        _history(force=True)
        return True
    except util.HttpError as error:
        util.log("PunchPlay refresh: %s" % error)
        return False


def forget_lists():
    for key in ("punchplaychecked", "punchplaylists", "punchplaywatchlist", "punchplayinprogress", "punchplaycontinue"):
        util.cache_drop(key)


def watched_movies():
    out = {}
    for row in _safe():
        if row["type"] == "movie" and row.get("tmdb"):
            key = str(row["tmdb"])
            out[key] = max(out.get(key, ""), row["at"])
    return out


def watched_episodes():
    out = {}
    for row in _safe():
        if row["type"] == "episode" and row.get("show") and row.get("season") is not None and row.get("episode"):
            seen = out.setdefault(str(row["show"]), {})
            key = (int(row["season"]), int(row["episode"]))
            seen[key] = max(seen.get(key, ""), row["at"])
    return out


# ----------------------------------------------------------------------------------------- where you stopped
def _in_progress(ttl=60):
    saved = util.cache_get("punchplayinprogress", ttl)
    if saved is None:
        saved = call("playback/in-progress") or []
        util.cache_set("punchplayinprogress", saved)
    return saved


def paused():
    """Rows in Trakt's shape (tracker.py and the library read that shape)."""
    try:
        rows = _in_progress()
    except util.HttpError:
        return []
    out = []
    for row in rows:
        percent = float(row.get("progressPercent") or 0)
        if percent <= 0:
            continue
        common = {"id": row.get("id"), "progress": percent, "paused_at": row.get("updatedAt") or ""}
        if row.get("type") == "movie" and row.get("tmdbId"):
            out.append(dict(common, type="movie", movie={"ids": {"tmdb": int(row["tmdbId"])}, "title": row.get("title")}))
        elif row.get("type") == "episode" and row.get("showTmdbId"):
            season, episode = _number(row, "season", "sourceSeason"), _number(row, "episode", "sourceEpisode")
            if season is None or not episode:
                continue
            out.append(dict(common, type="episode", show={"ids": {"tmdb": int(row["showTmdbId"])}, "title": row.get("showTitle")},
                            episode={"season": int(season), "number": int(episode)}))
    return out


def forget_playback(meta):
    """Kodi's 'Reset resume position': the stopped-here point leaves PunchPlay too."""
    removed = False
    for row in paused():
        same = (meta["kind"] == "movie" and row["type"] == "movie" and str(row["movie"]["ids"]["tmdb"]) == str(meta["tmdb"])) or \
               (meta["kind"] == "episode" and row["type"] == "episode" and str(row["show"]["ids"]["tmdb"]) == str(meta["tmdb"])
                and row["episode"]["season"] == int(meta["season"]) and row["episode"]["number"] == int(meta["episode"]))
        if same and row.get("id"):
            call("playback/in-progress/%s" % row["id"], method="DELETE")
            removed = True
    util.cache_drop("punchplayinprogress")
    return removed


def next_up():
    """[(show tmdb id, season, episode)], most recent first: PunchPlay's own 'next episode' per show."""
    rows = util.cache_get("punchplaycontinue", 300)
    if rows is None:
        try:
            rows = call("me/continue-watching?status=watching") or []
        except util.HttpError as error:
            util.log("PunchPlay next up: %s" % error)
            return []
        util.cache_set("punchplaycontinue", rows)
    out = []
    for row in rows:
        season, episode = _number(row, "nextSeason", "nextSourceSeason"), _number(row, "nextEpisode", "nextSourceEpisode")
        if row.get("showTmdbId") and season is not None and episode:
            out.append((row["showTmdbId"], int(season), int(episode)))
    return out


# ----------------------------------------------------------------------------------------- lists
def _lists():
    saved = util.cache_get("punchplaylists", 900)
    if saved is None:
        saved, cursor = [], None
        for _ in range(20):
            answer = call("me/lists?limit=100" + ("&cursor=%s" % cursor if cursor else "")) or {}
            saved += [{"id": r.get("id"), "name": r.get("name"), "watchlist": bool(r.get("isWatchlist"))} for r in answer.get("items") or []]
            cursor = answer.get("nextCursor")
            if not cursor:
                break
        util.cache_set("punchplaylists", saved)
    return saved


def _items(list_id, ttl=900):
    key = "punchplaylist%s" % list_id
    saved = util.cache_get(key, ttl)
    if saved is None:
        detail = call("lists/%s" % list_id) or {}
        saved = [{"tmdb": r.get("tmdbId"), "type": r.get("type"), "title": r.get("title"), "added": r.get("addedAt"),
                  "year": str(r.get("releaseDate") or "")[:4]} for r in detail.get("items") or [] if r.get("tmdbId")]
        util.cache_set(key, saved)
    return saved


def _kind(value):
    return "movie" if str(value).lower() == "movie" else "tvshow"


def _watchlist():
    saved = util.cache_get("punchplaywatchlist", 300)
    if saved is not None:
        return saved
    found = next((entry for entry in _lists() if entry["watchlist"]), None)
    saved = _items(found["id"], 0) if found else []
    util.cache_set("punchplaywatchlist", saved)
    return saved


def pairs(name):
    """[(kind, tmdb id)] for watchlist_movie or watchlist_tv. PunchPlay has no collection here: that stays empty."""
    if not name.startswith("watchlist_"):
        return []
    wanted = "movie" if name.endswith("movie") else "tvshow"
    try:
        return [(wanted, row["tmdb"]) for row in _watchlist() if _kind(row["type"]) == wanted]
    except util.HttpError as error:
        util.log("PunchPlay watchlist: %s" % error)
        return []


def dates(name):
    """{(kind, tmdb id): when it was added to your PunchPlay watchlist}."""
    if not name.startswith("watchlist_"):
        return {}
    wanted = "movie" if name.endswith("movie") else "tvshow"
    try:
        return {(wanted, row["tmdb"]): row.get("added") for row in _watchlist() if _kind(row["type"]) == wanted and row.get("added")}
    except util.HttpError:
        return {}


def custom_lists(kind, query=None):
    """[(owner, id, name)] of your own lists (not the watchlist). PunchPlay has no list search here."""
    if kind != "mine":
        return []
    try:
        return [("me", entry["id"], entry["name"]) for entry in _lists() if not entry["watchlist"]]
    except util.HttpError:
        return []


def list_pairs(owner, list_id, page, size):
    try:
        rows = _items(list_id)
    except util.HttpError:
        return []
    start = (int(page) - 1) * int(size)
    return [(_kind(row["type"]), row["tmdb"]) for row in rows[start:start + int(size)]]


def _watchlist_body(meta, add):
    return {"items": [{"kind": "movie" if meta["kind"] == "movie" else "show", "tmdb_id": int(meta["tmdb"]), "remove": not add}]}


def set_watchlist(meta, add):
    call("sync/watchlist", data=_watchlist_body(meta, add), idempotent=True)
    util.cache_drop("punchplaywatchlist")
    util.cache_drop("punchplaylists")


def library_rows(kind):
    """What the Kodi library follows: your watchlist, and the series you are watching."""
    key = "movie" if kind == "movies" else "show"
    wanted = "movie" if kind == "movies" else "tvshow"
    out, seen = [], set()
    for row in _watchlist():            # a failure reaches the caller: then nothing gets removed
        if _kind(row["type"]) == wanted and row["tmdb"] not in seen:
            seen.add(row["tmdb"])
            out.append({key: {"title": row.get("title"), "year": row.get("year"), "ids": {"tmdb": int(row["tmdb"])}},
                        "collected_at": row.get("added"), "last_collected_at": row.get("added")})
    if kind == "shows":
        next_up()                                   # fills the saved answer
        for row in util.cache_get("punchplaycontinue", 86400) or []:
            show = row.get("showTmdbId")
            if show and show not in seen and row.get("title"):
                seen.add(show)
                out.append({key: {"title": row["title"], "year": row.get("year") or "", "ids": {"tmdb": int(show)}}})
    return out


def library_list(meta, add):
    set_watchlist(meta, add)


# ----------------------------------------------------------------------------------------- watching
_SESSIONS = {}          # one playback session per film or episode, while it plays (the service runs all of it)


def _key(meta):
    """One key per playback: 'started' (set when Lucent hands the video to Kodi) makes playing it again a new session."""
    return "%s:%s:%s:%s:%s" % (meta.get("kind"), meta.get("tmdb"), meta.get("season"), meta.get("episode"), meta.get("started", ""))


def _action(meta, state):
    """(PunchPlay action or None, session id). Playing on after a sent pause is 'resume' in the same session; playing on
    when no pause was sent (a short pause is not passed on) needs nothing."""
    key = _key(meta)
    if state == "start" and key in _SESSIONS:
        session, last = _SESSIONS[key]
        _SESSIONS[key] = (session, "resume" if last == "pause" else last)
        return ("resume" if last == "pause" else None), session
    if state == "start" or key not in _SESSIONS:
        _SESSIONS[key] = (uuid.uuid4().hex, state)
    session = _SESSIONS[key][0]
    _SESSIONS[key] = (session, state)
    return state, session


def scrobble(state, meta, percent, total=0):
    """start, pause or stop. tracker.py already decided: a stop here means watched."""
    episode = meta.get("kind") != "movie"
    body = {"event_id": uuid.uuid4().hex, "event_created_at": int(time.time() * 1000),
            "media_type": "episode" if episode else "movie", "title": (meta.get("show") if episode else meta.get("title")) or meta.get("title") or "",
            "tmdb_id": int(meta["tmdb"]), "client_version": "lucent-video"}
    action, body["playback_session_id"] = _action(meta, state)
    if action is None:
        return
    if meta.get("imdb"):
        body["imdb_id"] = meta["imdb"]
    if episode:
        body.update(season=int(meta["season"]), episode=int(meta["episode"]))
        if meta.get("title"):
            body["episode_title"] = meta["title"]
    elif meta.get("year"):
        try:
            body["year"] = int(meta["year"])
        except (TypeError, ValueError):
            pass
    ratio = max(0.0, min(1.0, float(percent) / 100.0))
    total = int(total or 0) or int(float(meta.get("runtime") or 0) * (60 if float(meta.get("runtime") or 0) < 600 else 1))
    if total:
        body.update(progress=round(ratio, 4), duration_seconds=total, position_seconds=int(total * ratio))
    elif state != "start":
        body["progress"] = round(ratio, 4)
    if state == "stop":
        body["watched"] = True
    try:
        call("playback/" + action, data=body)
    except util.HttpError as error:
        if error.status == 409 and action != "start":       # PunchPlay does not know this session (any more): start a new one
            try:
                _SESSIONS.pop(_key(meta), None)
                _start, session = _action(meta, "start")
                call("playback/start", data=dict(body, event_id=uuid.uuid4().hex, playback_session_id=session))
                if action != "resume":
                    call("playback/" + action, data=dict(body, event_id=uuid.uuid4().hex, playback_session_id=session))
                return
            except util.HttpError as again:
                error = again
        util.log("PunchPlay %s: %s %s" % (action, error, (error.detail or {}).get("error", "")))
    finally:
        if state != "start":
            util.cache_drop("punchplayinprogress")
        if state == "stop":
            _SESSIONS.pop(_key(meta), None)
            util.cache_drop("punchplaychecked")
            util.cache_drop("punchplaycontinue")


# ----------------------------------------------------------------------------------------- watched by hand
def _aired_pairs(meta):
    import tracker
    return tracker._aired(meta)         # noqa: SLF001 - same add-on: the aired episodes of a show or season


def _log(items):
    for start in range(0, len(items), 100):
        call("sync/history", data={"items": items[start:start + 100]}, idempotent=True)


def set_watched(meta, watched):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    tmdb_id = int(meta["tmdb"])
    if watched:
        if meta["kind"] == "movie":
            items = [{"client_item_id": "lv-movie-%d" % tmdb_id, "kind": "movie", "tmdb_id": tmdb_id, "watched_at": now}]
        else:
            pairs_ = [(int(meta["season"]), int(meta["episode"]))] if meta["kind"] == "episode" else _aired_pairs(meta)
            items = [{"client_item_id": "lv-ep-%d-%d-%d-%d" % (tmdb_id, s, e, int(time.time())), "kind": "episode",
                      "tmdb_id": tmdb_id, "season": s, "episode": e, "watched_at": now} for s, e in pairs_]
        if items:
            _log(items)
    else:
        if meta["kind"] == "movie":
            call("title/movie/%d/history" % tmdb_id, method="DELETE")
        elif meta["kind"] == "tvshow":
            call("title/show/%d/history" % tmdb_id, method="DELETE")
        elif meta["kind"] == "season":
            call("title/show/%d/season/%d/watch" % (tmdb_id, int(meta["season"])), method="DELETE")
        else:
            season, episode = int(meta["season"]), int(meta["episode"])
            found = [r["id"] for r in _safe() if r["type"] == "episode" and str(r.get("show")) == str(tmdb_id)
                     and r.get("season") == season and r.get("episode") == episode and r.get("id")]
            for entry in found:
                call("watch-history/%s" % entry, method="DELETE")
    _local_update(meta, watched)
    util.cache_drop("punchplaycontinue")


def _local_update(meta, watched):
    """The saved history follows at once, so lists show the change without waiting for the next check."""
    saved = util.load_json(STATE, {})
    rows = saved.get("rows")
    if rows is None:
        return
    tmdb_id = int(meta["tmdb"])
    if watched:
        stamp = time.strftime("%Y-%m-%d %H:%M:%S", time.gmtime())
        if meta["kind"] == "movie":
            rows.insert(0, {"id": None, "type": "movie", "tmdb": tmdb_id, "at": stamp})
        else:
            pairs_ = [(int(meta["season"]), int(meta["episode"]))] if meta["kind"] == "episode" else _aired_pairs(meta)
            rows[:0] = [{"id": None, "type": "episode", "show": tmdb_id, "season": s, "episode": e, "at": stamp} for s, e in pairs_]
    else:
        def gone(row):
            if meta["kind"] == "movie":
                return row["type"] == "movie" and row.get("tmdb") == tmdb_id
            if row["type"] != "episode" or row.get("show") != tmdb_id:
                return False
            if meta["kind"] == "tvshow":
                return True
            if meta["kind"] == "season":
                return row.get("season") == int(meta["season"])
            return row.get("season") == int(meta["season"]) and row.get("episode") == int(meta["episode"])
        rows = [row for row in rows if not gone(row)]
    util.save_json(STATE, dict(saved, rows=rows))
    util.cache_drop("punchplaychecked")


def rate(kind, tmdb_id, rating):
    """Your Kompas rating (Great 9, Nice 7, Not for me 3) on PunchPlay too."""
    call("title/%s/%s/interact" % ("movie" if kind == "movie" else "show", int(tmdb_id)), data={"rating": rating}, method="PATCH")
