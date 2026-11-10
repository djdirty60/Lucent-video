# -*- coding: utf-8 -*-
"""'Library and lists' for any film or series (also from other add-ons): one window with ticks that shows where the
title is now. Tick = add, untick = remove.

    RunPlugin(plugin://plugin.video.lucent/?action=lists&type=movie|tvshow&tmdb=<id>)

Rows: watchlist, Trakt collection, the Kodi library, your own Trakt lists, your Simkl list (one status at a time) and
your own static MDBList lists. Without an account: the watchlist and the library on this device."""
import xbmcgui

import library
import mdblist
import simkl
import tmdb
import tracker
import trakt
import util

T = util.T
SIMKL_STATUS = [("plantowatch", "Simkl: wil ik zien", "Simkl: plan to watch"), ("watching", "Simkl: bezig", "Simkl: watching"),
                ("completed", "Simkl: gezien", "Simkl: completed"), ("hold", "Simkl: even gestopt", "Simkl: on hold"),
                ("dropped", "Simkl: afgehaakt", "Simkl: dropped")]


def _pair(kind, tmdb_id):
    return ("movie" if kind == "movie" else "tvshow", str(tmdb_id))


def _in(pairs, kind, tmdb_id):
    return _pair(kind, tmdb_id) in {(k, str(i)) for k, i in pairs}


# ------------------------------------------------------------------ Trakt
def _trakt_body(kind, tmdb_id):
    return {("movies" if kind == "movie" else "shows"): [{"ids": {"tmdb": int(tmdb_id)}}]}


def _list_key(list_id):
    return "trakt" + "users/me/lists/%s/items/movie,show:all" % list_id


def _trakt_list_items(list_id):
    """Every title on one of your lists: Trakt gives them page by page, so a long list is read to the end."""
    rows = util.cache_get(_list_key(list_id), 300)
    if rows is None:
        rows = trakt.all_pages("users/me/lists/%s/items/movie,show" % list_id, 250)
        util.cache_set(_list_key(list_id), rows)
    return trakt.pairs_from(rows)


def _trakt_rows(kind, tmdb_id):
    rows = [(T("Trakt-collectie", "Trakt collection"), _in(tracker.pairs("collection_movie" if kind == "movie" else "collection_tv"), kind, tmdb_id),
             lambda add: trakt.call("sync/collection" + ("" if add else "/remove"), data=_trakt_body(kind, tmdb_id)))]
    own = []
    for _owner, list_id, name in trakt.custom_lists("mine"):
        def change(add, list_id=list_id):
            trakt.call("users/me/lists/%s/items%s" % (list_id, "" if add else "/remove"), data=_trakt_body(kind, tmdb_id))
            util.cache_drop(_list_key(list_id))
        pairs = _trakt_list_items(list_id)
        # a list that already holds this kind (films for a film, series for a series) or is empty comes first
        other = bool(pairs) and not any(k == _pair(kind, 0)[0] for k, _i in pairs)
        own.append(((other, name.lower()), (T("Lijst: %s", "List: %s") % name, _in(pairs, kind, tmdb_id), change)))
    return rows + [row for _key, row in sorted(own, key=lambda item: item[0])]


# ------------------------------------------------------------------ Simkl (one status at a time)
def _simkl_status(kind, tmdb_id):
    bucket = "movies" if kind == "movie" else "shows"
    for row, entry in simkl._rows(simkl._library(), bucket):        # noqa: SLF001 - same add-on
        if simkl._id(entry) == str(tmdb_id):                         # noqa: SLF001
            return row.get("status")
    return None


def _simkl_rows(kind, tmdb_id):
    now = _simkl_status(kind, tmdb_id)
    meta = {"kind": kind, "tmdb": str(tmdb_id)}
    bucket = "movies" if kind == "movie" else "shows"
    rows = []
    for status, nl, en in SIMKL_STATUS:
        if status == "plantowatch":
            continue                                # that is the watchlist row
        if kind == "movie" and status in ("watching", "hold"):
            continue

        def change(add, status=status):
            if add:
                simkl.call("sync/add-to-list", data={bucket: [{"to": status, "ids": simkl._ids(meta)}]})     # noqa: SLF001
            else:
                simkl.call("sync/history/remove", data={bucket: [{"ids": simkl._ids(meta)}]})
            simkl.forget_lists()
        rows.append((T(nl, en), now == status, change))
    return rows


# ------------------------------------------------------------------ MDBList (own static lists)
def _mdb_static():
    data = util.cache_get("mdblistlists/user", 900)
    if data is None:
        data, _ = util.http(mdblist._url("lists/user"))                # noqa: SLF001
        util.cache_set("mdblistlists/user", data)
    rows = data.get("lists") or [] if isinstance(data, dict) else data or []
    return [(row["id"], row.get("name") or "") for row in rows if row.get("id") and not row.get("dynamic")]


def _mdb_rows(kind, tmdb_id):
    rows = []
    for list_id, name in _mdb_static():
        found = util.cache_get("mdbitems%s" % list_id, 300)
        if found is None:
            found, _more = mdblist.items(list_id, 1, 1000)
            util.cache_set("mdbitems%s" % list_id, found)

        def change(add, list_id=list_id):
            body = {("movies" if kind == "movie" else "shows"): [{"tmdb": int(tmdb_id)}]}
            util.http(mdblist._url("lists/%s/items/%s" % (list_id, "add" if add else "remove")), data=body)    # noqa: SLF001
            util.cache_drop("mdbitems%s" % list_id)
        rows.append((T("MDBList: %s", "MDBList: %s") % name, _in(found, kind, tmdb_id), change))
    return rows


# ------------------------------------------------------------------ the window
def rows_for(kind, tmdb_id):
    """[(label, on now, change(add))]."""
    meta = {"kind": kind, "tmdb": str(tmdb_id)}
    watch = _in(tracker.pairs("watchlist_movie" if kind == "movie" else "watchlist_tv"), kind, tmdb_id)
    # in this order: your account (watchlist, collection, own lists), other services, then this device
    rows = [(T("Kijklijst", "Watchlist"), watch, lambda add: tracker.set_watchlist(meta, add))]
    account = tracker.active()
    for part in ((_trakt_rows,) if account is trakt else (_simkl_rows,) if account is simkl else ()) + ((_mdb_rows,) if mdblist.available() else ()):
        try:
            rows += part(kind, tmdb_id)
        except util.HttpError as error:
            util.log("lists: %s" % error)
    rows.append((T("Kodi-bibliotheek op dit apparaat", "Kodi library on this device"), library.has(kind, tmdb_id),
                 lambda add: library.add_one(kind, tmdb_id) if add else library.remove_one(kind, tmdb_id, ask=False)))
    return rows


def open_lists(kind, tmdb_id):
    kind = "movie" if kind == "movie" else "tvshow"
    meta = tmdb.details(kind, tmdb_id) if tmdb.key() else None
    title = (meta or {}).get("title") or T("Bibliotheek en lijsten", "Library and lists")
    try:
        rows = rows_for(kind, tmdb_id)
    except util.HttpError as error:
        util.log("lists: %s" % error)
        return util.notify(T("Je account is niet bereikbaar", "Your account cannot be reached"))
    picked = xbmcgui.Dialog().multiselect(title, [label for label, _on, _do in rows], preselect=[n for n, row in enumerate(rows) if row[1]])
    if picked is None:
        return None
    done, failed = [], []
    for number, (label, on, change) in enumerate(rows):
        wanted = number in picked
        if wanted == on:
            continue
        try:
            change(wanted)
            done.append(("+ " if wanted else "- ") + label)
        except util.HttpError as error:
            util.log("lists %s: %s" % (label, error))
            failed.append((label, error.status))
    tracker.forget_lists()
    import widgets
    widgets.mark_old(account_only=True)
    if failed:
        xbmcgui.Dialog().ok(title, "\n".join("%s: %s" % (label, why(status)) for label, status in failed))
    elif done:
        util.notify(", ".join(done))
    return True


def why(status):
    """The reason in plain words, from the answer of Trakt (or MDBList)."""
    if not isinstance(status, int):
        return T("Geen verbinding. Controleer je internet en probeer het opnieuw.", "No connection. Check your internet and try again.")
    if status == 420:
        return T("Trakt staat niet meer titels toe: de grens van je account is bereikt. Een gratis Trakt-account "
                 "heeft een maximum per lijst (ook voor de kijklijst en collectie). Haal titels weg of neem Trakt VIP.",
                 "Trakt does not allow more titles: your account limit is reached. A free Trakt account has a maximum "
                 "per list (also for the watchlist and collection). Remove titles or get Trakt VIP.")
    if status in (401, 403):
        return T("Je account is niet meer gekoppeld. Koppel hem opnieuw in Instellingen › Account.",
                 "Your account is no longer linked. Link it again in Settings › Account.")
    if status == 404:
        return T("Niet gevonden. De lijst of titel bestaat daar misschien niet meer.", "Not found. The list or title may no longer exist there.")
    if status == 429:
        return T("Even te veel tegelijk. Probeer het over een minuut opnieuw.", "Too much at once. Try again in a minute.")
    return T("De dienst gaf foutcode %s. Probeer het later opnieuw.", "The service gave error %s. Try again later.") % status
