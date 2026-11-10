# -*- coding: utf-8 -*-
"""Fast rows on Home: every row with &widget=1 keeps a copy per profile. The copy is shown at once; when it is older
than its time (30 minutes for rows of your account, 6 hours for TMDB rows) the row is fetched again right after, in
the same run, and only when something changed the Home property lucent.video.widgets gets a new value.

For the skin: add &reload=$INFO[Window(Home).Property(lucent.video.widgets)] to a row's address, then Kodi fetches
the row again by itself when it changed. RunPlugin(plugin://plugin.video.lucent/?action=widgets_refresh) marks every
copy as old (for 'Refresh all rows')."""
import hashlib
import json
import os
import time

import util

PROPERTY = "lucent.video.widgets"
ACCOUNT = ("continue", "continue_movie", "continue_episode", "nextup", "watchlist_movie", "watchlist_tv", "collection_movie",
           "collection_tv", "traktlist", "floppylist", "mdblist", "calendar", "tonight", "alerts", "new_seasons", "coming_watchlist")
ACCOUNT_AGE = 30 * 60
OTHER_AGE = 6 * 3600
SKIP = ("service_row", "service_catalog", "lib_newseasons", "lib_newepisodes", "kompas")
IGNORE = ("widget", "reload", "page")
MARKS = "widgets_marks.json"      # {"all": time, "account": time}: copies from before these times are old


def _folder():
    return os.path.join(util.PROFILE, "widgets", util.current_user())


def _key(params):
    clean = sorted((k, v) for k, v in params.items() if k not in IGNORE)
    return hashlib.md5(json.dumps(clean).encode("utf-8")).hexdigest()


def wanted(params):
    return params.get("widget") == "1" and params.get("action") == "list" and params.get("name") not in SKIP \
        and not str(params.get("name", "")).startswith(SKIP)


def _slim(meta):
    return {k: v for k, v in meta.items() if k != "gallery"}


def get(params):
    try:
        with open(os.path.join(_folder(), _key(params) + ".json"), encoding="utf-8") as handle:
            return json.load(handle)
    except (OSError, ValueError):
        return None


def put(params, titles, content, size=0):
    folder = _folder()
    os.makedirs(folder, exist_ok=True)
    path = os.path.join(folder, _key(params) + ".json")
    with open(path + ".tmp", "w", encoding="utf-8") as handle:
        json.dump({"at": time.time(), "titles": [_slim(t) for t in titles], "content": content, "size": size,
                   "params": {k: v for k, v in params.items() if k not in ("reload", "page")}, "used": time.time()}, handle)
    os.replace(path + ".tmp", path)


def is_old(params, entry, size=0):
    if size and entry.get("size") != size:
        return True                             # you chose another number of titles per row
    marks = util.load_json(MARKS, {})
    account = params.get("name") in ACCOUNT
    age = ACCOUNT_AGE if account else OTHER_AGE
    at = entry.get("at", 0)
    return time.time() - at > age or at < marks.get("all", 0) or (account and at < marks.get("account", 0))


def same(entry, titles, content):
    def ids(rows):
        return [(t.get("kind"), str(t.get("tmdb")), t.get("season"), t.get("episode")) for t in rows]
    return entry.get("content") == content and ids(entry.get("titles") or []) == ids(titles)


def bump():
    util.HOME.setProperty(PROPERTY, str(int(time.time() * 1000)))


def mark_old(account_only=False):
    marks = util.load_json(MARKS, {})
    marks["account" if account_only else "all"] = time.time()
    util.save_json(MARKS, marks)
    bump()


def warm():
    """Service, shortly after Kodi starts: rows that were on Home this week and are old are fetched now, so Home
    shows fresh rows at once instead of fetching while you look. One new property value at the end."""
    folder = _folder()
    try:
        names = [n for n in os.listdir(folder) if n.endswith(".json")]
    except OSError:
        return
    import router
    changed = False
    for name in names:
        try:
            with open(os.path.join(folder, name), encoding="utf-8") as handle:
                entry = json.load(handle)
        except (OSError, ValueError):
            continue
        params = entry.get("params")
        if not params or time.time() - entry.get("used", entry.get("at", 0)) > 7 * 86400:
            continue
        size = entry.get("size") or 0
        if not is_old(params, entry, size):
            continue
        try:
            titles, content, _more = router.gather_row(params, size)
        except Exception as error:      # noqa: BLE001 - the row is fetched when Home asks for it
            util.log("warm %s: %s" % (params.get("name"), type(error).__name__))
            continue
        if titles:
            changed = changed or not same(entry, titles, content)
            put(params, titles, content, size)
    if changed:
        bump()
    util.log("Home rows ready")


def refresh_all():
    """action=widgets_refresh."""
    mark_old(False)
