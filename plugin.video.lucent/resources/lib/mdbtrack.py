# -*- coding: utf-8 -*-
"""MDBList as the account that remembers what you watched (like Trakt, Simkl and PunchPlay): watched history, where
you stopped (scrobbling), your watchlist and ratings. It uses your own MDBList API key (Settings > Services > MDBList),
the same key the scores and lists use; linking only says 'use MDBList as my account too'.

The answers are turned into Trakt's shape here, so tracker.py and the rest of the add-on need not know."""
from concurrent.futures import ThreadPoolExecutor
import time
import urllib.parse

import mdblist
import tmdb
import util

T = util.T
API = "https://api.mdblist.com/"
FILE = "mdbtrack.json"              # {"user": name}: linked as your account
STATE = "mdbtrack_state.json"       # the watched history, kept between runs
HAS_LISTS = False                   # your MDBList lists are already under MDBList in the menu
CHECK_EVERY = 300                   # watched history again at most every five minutes (or when asked)
PAGE = 1000


# ----------------------------------------------------------------------------------------- account
def account():
    return util.load_json(FILE, {})


def logged_in():
    return bool(account()) and mdblist.available()


def call(path, data=None, method=None, **params):
    """One request. The key goes in the address, as MDBList wants; errors never hold the address (util.http)."""
    params["apikey"] = mdblist.key()
    url = API + path + "?" + urllib.parse.urlencode(params)
    answer, _ = util.http(url, data=data, method=method or ("POST" if data is not None else None), timeout=20)
    return answer


def login():
    """Links MDBList as your account: asks for the key when there is none yet, and checks it."""
    import xbmcgui
    key = mdblist.key()
    if not key:
        key = xbmcgui.Dialog().input(T("Je MDBList API-sleutel (mdblist.com > Preferences > API)", "Your MDBList API key (mdblist.com > Preferences > API)"))
        if not key:
            return False
        util.ADDON.setSetting("mdblist_key", key.strip())
    try:
        me = call("user") or {}
    except util.HttpError as error:
        util.log("MDBList link: %s" % error)
        xbmcgui.Dialog().ok("MDBList", T("Deze sleutel werkt niet. Kijk hem na bij Instellingen > Diensten > MDBList.",
                                         "This key does not work. Check it under Settings > Services > MDBList."))
        return False
    name = me.get("username") or me.get("name") or "MDBList"
    util.save_json(FILE, {"user": name, "linked": int(time.time())})
    forget_lists()
    util.cache_drop("mdbtrackchecked")
    util.notify(T("Gekoppeld als %s", "Linked as %s") % name, "MDBList")
    return True


def logout():
    """Unlinks MDBList as your account. The key stays: scores and lists keep working."""
    util.save_json(FILE, {})
    forget_lists()


def forget_lists():
    for key in ("mdbtrackchecked", "mdbtrackwatchlist", "mdbtrackplayback", "nextup"):
        util.cache_drop(key)


# ----------------------------------------------------------------------------------------- watched history
def _pages(path, **params):
    """Every page of a cursor-paged answer, merged: {'movies': [...], 'episodes': [...], ...}."""
    merged, cursor = {}, None
    for _ in range(50):
        if cursor:
            params["cursor"] = cursor
        data = call(path, limit=PAGE, **params) or {}
        if isinstance(data, list):
            merged.setdefault("items", []).extend(data)
            break
        for key, value in data.items():
            if isinstance(value, list):
                merged.setdefault(key, []).extend(value)
        cursor = (data.get("pagination") or {}).get("next_cursor")
        if not cursor:
            break
    return merged


def _tmdb(ids):
    try:
        return str(int((ids or {}).get("tmdb")))
    except (TypeError, ValueError):
        return ""


def _rows(data):
    """[{'type': 'movie', 'tmdb', 'at'} / {'type': 'episode', 'show', 'season', 'episode', 'at'}] from /sync/watched."""
    rows = []
    for entry in data.get("movies") or []:
        movie = entry.get("movie") or entry
        tmdb_id = _tmdb(movie.get("ids"))
        if tmdb_id:
            rows.append({"type": "movie", "tmdb": tmdb_id, "at": entry.get("last_watched_at") or entry.get("watched_at") or ""})
    for entry in data.get("episodes") or []:
        episode = entry.get("episode") or entry
        show = _tmdb((episode.get("show") or entry.get("show") or {}).get("ids"))
        if show and episode.get("season") is not None and episode.get("number"):
            rows.append({"type": "episode", "show": show, "season": int(episode["season"]), "episode": int(episode["number"]),
                         "at": entry.get("last_watched_at") or entry.get("watched_at") or ""})
    for entry in data.get("shows") or []:        # the grouped form, should MDBList answer that way
        show = _tmdb((entry.get("show") or entry).get("ids"))
        for season in entry.get("seasons") or []:
            for episode in season.get("episodes") or []:
                if show and episode.get("number"):
                    rows.append({"type": "episode", "show": show, "season": int(season.get("number") or 0), "episode": int(episode["number"]),
                                 "at": episode.get("last_watched_at") or episode.get("watched_at") or entry.get("last_watched_at") or ""})
    return rows


def _history(force=False):
    saved = util.load_json(STATE, {})
    if saved.get("rows") is not None and not force and util.cache_get("mdbtrackchecked", CHECK_EVERY) is not None:
        return saved["rows"]
    rows = _rows(_pages("sync/watched"))
    util.save_json(STATE, {"rows": rows, "at": int(time.time())})
    util.cache_set("mdbtrackchecked", 1)
    return rows


def _safe():
    try:
        return _history()
    except util.HttpError as error:
        util.log("MDBList: %s" % error)
        return util.load_json(STATE, {}).get("rows") or []


def refresh():
    try:
        _history(force=True)
        return True
    except util.HttpError as error:
        util.log("MDBList refresh: %s" % error)
        return False


def watched_movies():
    out = {}
    for row in _safe():
        if row["type"] == "movie":
            out[row["tmdb"]] = max(out.get(row["tmdb"], ""), row["at"])
    return out


def watched_episodes():
    out = {}
    for row in _safe():
        if row["type"] == "episode":
            seen = out.setdefault(row["show"], {})
            key = (row["season"], row["episode"])
            seen[key] = max(seen.get(key, ""), row["at"])
    return out


# ----------------------------------------------------------------------------------------- where you stopped
def _playback(ttl=60):
    saved = util.cache_get("mdbtrackplayback", ttl)
    if saved is None:
        saved = call("sync/playback") or []
        saved = saved if isinstance(saved, list) else saved.get("items") or saved.get("playback") or []
        util.cache_set("mdbtrackplayback", saved)
    return saved


def paused():
    """Rows in Trakt's shape."""
    try:
        rows = _playback()
    except util.HttpError as error:
        util.log("MDBList playback: %s" % error)
        return []
    out = []
    for row in rows:
        try:
            percent = float(row.get("progress") or 0)
        except (TypeError, ValueError):
            continue
        if not 0 < percent < 100:
            continue
        common = {"progress": percent, "paused_at": row.get("paused_at") or row.get("updated_at") or ""}
        kind = str(row.get("type") or "").lower()
        if kind == "movie" and _tmdb((row.get("movie") or {}).get("ids")):
            out.append(dict(common, type="movie", movie={"ids": {"tmdb": int(_tmdb(row["movie"]["ids"]))}, "title": row["movie"].get("title")}))
        elif kind == "episode":
            episode = row.get("episode") or {}
            show = row.get("show") or episode.get("show") or {}
            if _tmdb(show.get("ids")) and episode.get("season") is not None and episode.get("number"):
                out.append(dict(common, type="episode", show={"ids": {"tmdb": int(_tmdb(show["ids"]))}, "title": show.get("title")},
                                episode={"season": int(episode["season"]), "number": int(episode["number"])}))
    return out


def _item(meta):
    """The film or episode as MDBList's scrobble wants it."""
    if meta["kind"] == "movie":
        return {"movie": {"ids": {"tmdb": int(meta["tmdb"])}}}
    return {"show": {"ids": {"tmdb": int(meta["tmdb"])}, "season": {"number": int(meta["season"]), "episode": {"number": int(meta["episode"])}}}}


def forget_playback(meta):
    """Kodi's 'Reset resume position': the stopped-here point leaves MDBList too."""
    try:
        call("scrobble/clear", data=_item(meta))
    except util.HttpError as error:
        util.log("MDBList clear: %s" % error)
        return False
    util.cache_drop("mdbtrackplayback")
    return True


def scrobble(state, meta, percent):
    """start / pause / stop, like Trakt: MDBList itself counts a stop from 80% as watched."""
    if meta.get("kind") not in ("movie", "episode") or state not in ("start", "pause", "stop"):
        return
    body = dict(_item(meta), progress=round(max(0.0, min(100.0, float(percent))), 2))
    call("scrobble/%s" % state, data=body)
    if state != "start":
        util.cache_drop("mdbtrackplayback")
    if state == "stop" and percent >= 80.0:
        util.cache_drop("mdbtrackchecked")
        util.cache_drop("nextup")


def next_up():
    """[(show tmdb id, season, episode)], most recent first: the episode after the last one you saw, when it is out."""
    saved = util.cache_get("mdbtracknextup", 300)
    if saved is not None:
        return [tuple(r) for r in saved]

    def step(item):
        show, seen = item
        season, episode = max(seen)
        try:
            following = tmdb.next_episode(show, season, episode)
        except util.HttpError:
            return None
        return (show, following["season"], following["episode"]) if following else None
    recent = sorted(watched_episodes().items(), key=lambda s: max(s[1].values()) if s[1] else "", reverse=True)[:40]
    with ThreadPoolExecutor(max_workers=6) as pool:
        out = [r for r in pool.map(step, recent) if r]
    util.cache_set("mdbtracknextup", out)
    return out


# ----------------------------------------------------------------------------------------- watchlist
def _watchlist():
    saved = util.cache_get("mdbtrackwatchlist", 300)
    if saved is not None:
        return saved
    data = _pages("watchlist/items")
    rows = []
    for kind, key in (("movie", "movies"), ("tvshow", "shows")):
        for entry in data.get(key) or []:
            tmdb_id = _tmdb(entry.get("ids")) or _tmdb({"tmdb": entry.get("tmdb_id") or entry.get("tmdbid") or entry.get("id")})
            if tmdb_id:
                rows.append({"kind": kind, "tmdb": tmdb_id, "title": entry.get("title") or "", "year": str(entry.get("release_year") or entry.get("year") or ""),
                             "added": entry.get("watchlist_at") or entry.get("listed_at") or entry.get("added_at") or ""})
    for entry in data.get("items") or []:
        kind = "movie" if str(entry.get("mediatype") or "").lower() == "movie" else "tvshow"
        tmdb_id = _tmdb(entry.get("ids")) or _tmdb({"tmdb": entry.get("tmdb_id") or entry.get("id")})
        if tmdb_id:
            rows.append({"kind": kind, "tmdb": tmdb_id, "title": entry.get("title") or "", "year": str(entry.get("release_year") or ""),
                         "added": entry.get("watchlist_at") or ""})
    util.cache_set("mdbtrackwatchlist", rows)
    return rows


def pairs(name):
    """[(kind, tmdb id)] for watchlist_movie or watchlist_tv. MDBList has no collection here: that stays empty."""
    if not name.startswith("watchlist_"):
        return []
    wanted = "movie" if name.endswith("movie") else "tvshow"
    try:
        return [(wanted, row["tmdb"]) for row in _watchlist() if row["kind"] == wanted]
    except util.HttpError as error:
        util.log("MDBList watchlist: %s" % error)
        return []


def dates(name):
    if not name.startswith("watchlist_"):
        return {}
    wanted = "movie" if name.endswith("movie") else "tvshow"
    try:
        return {(wanted, row["tmdb"]): row["added"] for row in _watchlist() if row["kind"] == wanted and row.get("added")}
    except util.HttpError:
        return {}


def custom_lists(kind, query=None):
    return []


def list_pairs(owner, list_id, page, size):
    return []


def set_watchlist(meta, add):
    body = {"movies" if meta["kind"] == "movie" else "shows": [{"tmdb": int(meta["tmdb"])}]}
    call("watchlist/items/%s" % ("add" if add else "remove"), data=body)
    util.cache_drop("mdbtrackwatchlist")


def library_rows(kind):
    """What the Kodi library follows: your watchlist, and the series you are watching."""
    key = "movie" if kind == "movies" else "show"
    wanted = "movie" if kind == "movies" else "tvshow"
    out, seen = [], set()
    for row in _watchlist():            # a failure reaches the caller: then nothing gets removed
        if row["kind"] == wanted and row["tmdb"] not in seen:
            seen.add(row["tmdb"])
            out.append({key: {"title": row["title"], "year": row["year"], "ids": {"tmdb": int(row["tmdb"])}},
                        "collected_at": row["added"], "last_collected_at": row["added"]})
    if kind == "shows":
        for show, _season, _episode in next_up():
            if str(show) not in seen:
                found = tmdb.details("tvshow", show)
                if found:
                    seen.add(str(show))
                    out.append({key: {"title": found["title"], "year": found.get("year") or "", "ids": {"tmdb": int(show)}}})
    return out


def library_list(meta, add):
    set_watchlist(meta, add)


# ----------------------------------------------------------------------------------------- marking and rating
def _episodes(meta):
    """[(season, episode)] that a 'mark as watched' on this title means."""
    if meta["kind"] == "episode":
        return [(int(meta["season"]), int(meta["episode"]))]
    import tracker
    return tracker._aired(meta)                 # noqa: SLF001 - a season or a whole series: the aired episodes


def set_watched(meta, watched):
    now = time.strftime("%Y-%m-%dT%H:%M:%SZ", time.gmtime())
    if meta["kind"] == "movie":
        entry = {"ids": {"tmdb": int(meta["tmdb"])}}
        if watched:
            entry["watched_at"] = now
        body = {"movies": [entry]}
    else:
        seasons = {}
        for season, episode in _episodes(meta):
            entry = {"number": episode}
            if watched:
                entry["watched_at"] = now
            seasons.setdefault(season, []).append(entry)
        if not seasons:
            return
        body = {"shows": [{"ids": {"tmdb": int(meta["tmdb"])}, "seasons": [{"number": s, "episodes": e} for s, e in sorted(seasons.items())]}]}
    call("sync/watched" if watched else "sync/watched/remove", data=body)
    _local_update(meta, watched, now)


def _local_update(meta, watched, now):
    """The saved history follows at once, so lists show it without waiting for the next check."""
    saved = util.load_json(STATE, {})
    rows = saved.get("rows") or []
    if meta["kind"] == "movie":
        rows = [r for r in rows if not (r["type"] == "movie" and r["tmdb"] == str(meta["tmdb"]))]
        if watched:
            rows.append({"type": "movie", "tmdb": str(meta["tmdb"]), "at": now})
    else:
        keys = set(_episodes(meta))
        show = str(meta["tmdb"])
        rows = [r for r in rows if not (r["type"] == "episode" and r["show"] == show and (r["season"], r["episode"]) in keys)]
        if watched:
            rows += [{"type": "episode", "show": show, "season": s, "episode": e, "at": now} for s, e in sorted(keys)]
    saved["rows"] = rows
    util.save_json(STATE, saved)
    util.cache_drop("mdbtracknextup")
    util.cache_drop("nextup")


def rate(kind, tmdb_id, rating):
    """Your Kompas rating (Great 9, Nice 7, Not for me 3) on MDBList too."""
    call("sync/ratings", data={"movies" if kind == "movie" else "shows": [{"ids": {"tmdb": int(tmdb_id)}, "rating": int(rating)}]})
