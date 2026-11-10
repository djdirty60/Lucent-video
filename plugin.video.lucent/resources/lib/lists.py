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
    def collect(add):
        trakt.call("sync/collection" + ("" if add else "/remove"), data=_trakt_body(kind, tmdb_id))
    collect.trakt_limit = "collection"
    rows = [(T("Trakt-collectie", "Trakt collection"), _in(tracker.pairs("collection_movie" if kind == "movie" else "collection_tv"), kind, tmdb_id), collect)]
    own = []
    for _owner, list_id, name in trakt.custom_lists("mine"):
        def change(add, list_id=list_id):
            trakt.call("users/me/lists/%s/items%s" % (list_id, "" if add else "/remove"), data=_trakt_body(kind, tmdb_id))
            util.cache_drop(_list_key(list_id))
        change.trakt_list = (list_id, name)
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
    def watchlist(add):
        tracker.set_watchlist(meta, add)
    watchlist.trakt_limit = "watchlist"
    rows = [(T("Kijklijst", "Watchlist"), watch, watchlist)]
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
            failed.append((label, error.status, change))
    tracker.forget_lists()
    import widgets
    widgets.mark_old(account_only=True)
    for label, status, change in [entry for entry in failed if entry[1] == 420 and tracker.active() is trakt]:
        failed.remove((label, status, change))
        if not _full(title, kind, tmdb_id, change):
            failed.append((label, status, change))
    if failed:
        xbmcgui.Dialog().ok(title, "\n".join("%s: %s" % (label, why(status)) for label, status, _change in failed))
    elif done:
        util.notify(", ".join(done))
    return True


def _limits():
    """Your own limits as Trakt gives them (they differ per account and change over time, also for VIP)."""
    data = trakt.call("users/settings")[0] or {}
    return bool((data.get("user") or {}).get("vip")), data.get("limits") or {}


def _number(value):
    return value if isinstance(value, int) else None


def _full(title, kind, tmdb_id, change):
    """Trakt said 420 (account limit). Say which limit, with the numbers, and for a full list offer a new one.
    Returns False when it cannot tell (then the general reason is shown)."""
    try:
        vip, limits = _limits()
    except util.HttpError as error:
        util.log("lists limits: %s" % error)
        return False
    who = T("je VIP-account", "your VIP account") if vip else T("je gratis account", "your free account")
    tip = "" if vip else T(" Met Trakt VIP mag er meer in.", " Trakt VIP allows more.")
    kind_limit = getattr(change, "trakt_limit", None)
    if kind_limit:                               # watchlist or collection
        most = _number((limits.get(kind_limit) or {}).get("item_count"))
        name = T("Je kijklijst", "Your watchlist") if kind_limit == "watchlist" else T("Je Trakt-collectie", "Your Trakt collection")
        xbmcgui.Dialog().ok(title, T("%s is vol%s. Dat is de grens van %s bij Trakt. Haal er titels af om ruimte te maken.%s",
                                     "%s is full%s. That is the limit of %s at Trakt. Remove titles to make room.%s")
                            % (name, (T(": hooguit %d titels", ": at most %d titles") % most) if most else "", who, tip))
        return True
    if not getattr(change, "trakt_list", None):
        return False
    list_id, list_name = change.trakt_list
    lists = trakt.call("users/me/lists")[0] or []
    entry = next((row for row in lists if (row.get("ids") or {}).get("trakt") == list_id), {})
    count = _number(entry.get("item_count"))
    most = _number((limits.get("list") or {}).get("item_count"))
    if not (count and most and count >= most):
        total = sum(_number(row.get("item_count")) or 0 for row in lists)
        xbmcgui.Dialog().ok(title, T("Lijst \"%s\": Trakt staat niet meer titels toe (grens van %s). De lijst heeft %s titels, "
                                     "al je lijsten samen %d. Haal titels weg of zet hem in een andere lijst.%s",
                                     "List \"%s\": Trakt does not allow more titles (limit of %s). The list holds %s titles, "
                                     "all your lists together %d. Remove titles or use another list.%s")
                            % (list_name, who, count if count is not None else "?", total, tip))
        return True
    new_name = _next_name(list_name, [row.get("name") or "" for row in lists])
    most_lists = _number((limits.get("list") or {}).get("count"))
    room = most_lists is None or len(lists) < most_lists
    text = T("Lijst \"%s\" is vol: %d van de %d titels. Dat is de grens per lijst van %s bij Trakt.%s",
             "List \"%s\" is full: %d of %d titles. That is the limit per list of %s at Trakt.%s") % (list_name, count, most, who, tip)
    if not room:
        xbmcgui.Dialog().ok(title, text + T(" Je hebt ook al het hoogste aantal lijsten (%d). Haal titels weg om ruimte te maken.",
                                            " You also have the most lists allowed (%d). Remove titles to make room.") % most_lists)
        return True
    if not xbmcgui.Dialog().yesno(title, text + T("\n\nEen nieuwe lijst \"%s\" maken en de titel daarin zetten?",
                                                  "\n\nMake a new list \"%s\" and put the title in it?") % new_name):
        return True
    try:
        made = trakt.call("users/me/lists", data={"name": new_name, "privacy": entry.get("privacy") or "private"})[0] or {}
        new_id = (made.get("ids") or {}).get("trakt")
        trakt.call("users/me/lists/%s/items" % new_id, data=_trakt_body(kind, tmdb_id))
    except util.HttpError as error:
        util.log("lists new list: %s" % error)
        xbmcgui.Dialog().ok(title, T("De nieuwe lijst maken lukte niet: %s", "Making the new list did not work: %s") % why(error.status))
        return True
    util.cache_drop("trakt" + "users/me/lists")
    util.notify(T("Gezet in de nieuwe lijst \"%s\"", "Put in the new list \"%s\"") % new_name)
    return True


def _next_name(name, taken):
    """'Movie collection' -> 'Movie collection 2' (or 3, ... when that exists)."""
    base, number = name, 2
    parts = name.rsplit(" ", 1)
    if len(parts) == 2 and parts[1].isdigit():
        base, number = parts[0], int(parts[1]) + 1
    while "%s %d" % (base, number) in taken:
        number += 1
    return "%s %d" % (base, number)


def why(status):
    """The reason in plain words, from the answer of Trakt (or MDBList)."""
    if not isinstance(status, int):
        return T("Geen verbinding. Controleer je internet en probeer het opnieuw.", "No connection. Check your internet and try again.")
    if status == 420:
        return T("De grens van je account is bereikt (Trakt geeft ook VIP-accounts een maximum per lijst). Haal titels weg "
                 "of gebruik een andere lijst.", "Your account limit is reached (Trakt also gives VIP accounts a maximum per "
                 "list). Remove titles or use another list.")
    if status in (401, 403):
        return T("Je account is niet meer gekoppeld. Koppel hem opnieuw in Instellingen › Account.",
                 "Your account is no longer linked. Link it again in Settings › Account.")
    if status == 404:
        return T("Niet gevonden. De lijst of titel bestaat daar misschien niet meer.", "Not found. The list or title may no longer exist there.")
    if status == 429:
        return T("Even te veel tegelijk. Probeer het over een minuut opnieuw.", "Too much at once. Try again in a minute.")
    return T("De dienst gaf foutcode %s. Probeer het later opnieuw.", "The service gave error %s. Try again later.") % status
