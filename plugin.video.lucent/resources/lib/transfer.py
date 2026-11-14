# -*- coding: utf-8 -*-
"""'Copy your library': your watchlist and what you watched, from Trakt to Simkl or from Simkl to Trakt
(action=transfer, a button under Settings > Account). Only copies: nothing is removed anywhere, and what the other
account already has is left out. Sent in parts, so a large history does not hit a service's limits."""
import xbmcgui

import simkl
import trakt
import util

T = util.T
PART = 100                 # films or shows per request
EPISODES_PER_PART = 1000   # episodes per request


def _count(n, one_nl, many_nl, one_en, many_en):
    return "%d %s" % (n, T(one_nl if n == 1 else many_nl, one_en if n == 1 else many_en))


def _name(account):
    return "Trakt" if account is trakt else "Simkl"


def _when(stamp):
    """'2026-11-01 20:00:00' -> '2026-11-01T20:00:00Z' (both services read this); '' stays ''."""
    text = str(stamp or "").strip()
    return text.replace(" ", "T") + "Z" if len(text) == 19 else ""


def read(account):
    """What one account has: {'watchlist': {(kind, tmdb)}, 'movies': {tmdb: when}, 'episodes': {show: {(s, e): when}}}."""
    watchlist = {(kind, str(tmdb_id)) for name in ("watchlist_movie", "watchlist_tv") for kind, tmdb_id in account.pairs(name)}
    return {"watchlist": watchlist, "movies": {str(k): v for k, v in account.watched_movies().items()},
            "episodes": {str(k): dict(v) for k, v in account.watched_episodes().items()}}


def missing(source, target):
    """What the target account does not have yet."""
    watchlist = sorted(source["watchlist"] - target["watchlist"])
    movies = {k: v for k, v in source["movies"].items() if k not in target["movies"]}
    episodes = {}
    for show, seen in source["episodes"].items():
        have = target["episodes"].get(show, {})
        new = {key: when for key, when in seen.items() if key not in have and key[0] > 0}
        if new:
            episodes[show] = new
    return {"watchlist": watchlist, "movies": movies, "episodes": episodes}


def _ids(account, tmdb_id):
    return {"tmdb": int(tmdb_id)} if account is trakt else {"tmdb": str(tmdb_id)}


def watchlist_bodies(account, pairs):
    for start in range(0, len(pairs), PART):
        part = pairs[start:start + PART]
        body = {}
        for kind, tmdb_id in part:
            entry = {"ids": _ids(account, tmdb_id)}
            if account is simkl:
                entry["to"] = "plantowatch"
            body.setdefault("movies" if kind == "movie" else "shows", []).append(entry)
        yield len(part), body


def history_bodies(account, movies, episodes):
    films = sorted(movies.items())
    for start in range(0, len(films), PART):
        part = films[start:start + PART]
        rows = []
        for tmdb_id, when in part:
            row = {"ids": _ids(account, tmdb_id)}
            if _when(when):
                row["watched_at"] = _when(when)
            elif account is trakt:
                row["watched_at"] = "released"       # no date known: Trakt then uses the release date, not today
            rows.append(row)
        yield len(part), {"movies": rows}
    shows, count = [], 0
    for show, seen in sorted(episodes.items()):
        seasons = {}
        for (season, episode), when in sorted(seen.items()):
            entry = {"number": episode}
            if _when(when):
                entry["watched_at"] = _when(when)
            seasons.setdefault(season, []).append(entry)
        shows.append({"ids": _ids(account, show), "seasons": [{"number": s, "episodes": e} for s, e in sorted(seasons.items())]})
        count += len(seen)
        if count >= EPISODES_PER_PART or len(shows) >= PART:
            yield count, {"shows": shows}
            shows, count = [], 0
    if shows:
        yield count, {"shows": shows}


def _post(account, path, body):
    account.call(path, data=body)


def send(target, todo, progress=None):
    """Sends what is missing to the target. Returns (items sent, parts that failed)."""
    jobs = [("sync/watchlist" if target is trakt else "sync/add-to-list", n, body)
            for n, body in watchlist_bodies(target, todo["watchlist"])]
    jobs += [("sync/history", n, body) for n, body in history_bodies(target, todo["movies"], todo["episodes"])]
    total = sum(n for _p, n, _b in jobs) or 1
    sent = failed = 0
    for path, n, body in jobs:
        if progress and progress.iscanceled():
            break
        try:
            _post(target, path, body)
            sent += n
        except util.HttpError as error:
            util.log("transfer to %s: %s" % (_name(target), error))
            failed += 1
        if progress:
            progress.update(int(sent * 100 / total), T("%d van %d verstuurd", "%d of %d sent") % (sent, total))
    target.forget_lists()
    return sent, failed


def run():
    if not (trakt.logged_in() and simkl.logged_in()):
        return xbmcgui.Dialog().ok("Lucent", T("Log eerst in bij Trakt én bij Simkl (Instellingen > Account). Daarna kun je je bibliotheek van de een naar de ander kopiëren.",
                                               "Sign in to both Trakt and Simkl first (Settings > Account). Then you can copy your library from one to the other."))
    dialog = xbmcgui.Dialog()
    way = dialog.select(T("Bibliotheek kopiëren", "Copy your library"),
                        [T("Van Trakt naar Simkl", "From Trakt to Simkl"), T("Van Simkl naar Trakt", "From Simkl to Trakt")])
    if way < 0:
        return None
    source, target = (trakt, simkl) if way == 0 else (simkl, trakt)
    parts = dialog.multiselect(T("Wat wil je kopiëren?", "What do you want to copy?"),
                               [T("Kijklijst (films en series)", "Watchlist (films and shows)"), T("Wat je gezien hebt (films en afleveringen)", "What you watched (films and episodes)")],
                               preselect=[0, 1])
    if not parts:
        return None
    progress = xbmcgui.DialogProgress()
    progress.create("Lucent", T("Je bibliotheek bij %s en %s lezen…", "Reading your library at %s and %s…") % (_name(source), _name(target)))
    try:
        if not (source.refresh() and target.refresh()):      # never copy on half an answer: Trakt would count plays twice
            raise util.HttpError("refresh")
        todo = missing(read(source), read(target))
    except util.HttpError as error:
        progress.close()
        util.log("transfer: %s" % error)
        return dialog.ok("Lucent", T("%s of %s is niet bereikbaar. Er is niets gekopieerd.", "%s or %s cannot be reached. Nothing was copied.") % (_name(source), _name(target)))
    progress.close()
    if 0 not in parts:
        todo["watchlist"] = []
    if 1 not in parts:
        todo["movies"], todo["episodes"] = {}, {}
    films = sum(1 for kind, _t in todo["watchlist"] if kind == "movie")
    episodes = sum(len(v) for v in todo["episodes"].values())
    if not (todo["watchlist"] or todo["movies"] or episodes):
        return dialog.ok("Lucent", T("%s heeft alles al. Er hoeft niets gekopieerd te worden.", "%s already has everything. Nothing needs copying.") % _name(target))
    lines = []
    if todo["watchlist"]:
        lines.append(T("Kijklijst: %s en %s", "Watchlist: %s and %s") % (_count(films, "film", "films", "film", "films"),
                                                                          _count(len(todo["watchlist"]) - films, "serie", "series", "show", "shows")))
    if todo["movies"] or episodes:
        lines.append(T("Gezien: %s en %s", "Watched: %s and %s") % (_count(len(todo["movies"]), "film", "films", "film", "films"),
                                                                    _count(episodes, "aflevering", "afleveringen", "episode", "episodes")))
    if not dialog.yesno(T("Kopiëren naar %s", "Copy to %s") % _name(target),
                        "\n".join(lines) + "\n" + T("Wat %s al heeft, slaat Lucent over. Er wordt niets verwijderd.", "What %s already has is skipped. Nothing is removed.") % _name(target),
                        nolabel=T("Annuleren", "Cancel"), yeslabel=T("Kopiëren", "Copy")):
        return None
    progress = xbmcgui.DialogProgress()
    progress.create(T("Kopiëren naar %s", "Copy to %s") % _name(target), "")
    try:
        sent, failed = send(target, todo, progress)
    finally:
        progress.close()
    util.log("transfer %s -> %s: %d sent, %d parts failed" % (_name(source), _name(target), sent, failed))
    if failed:
        return dialog.ok("Lucent", T("%d gekopieerd naar %s. %d delen lukten niet; probeer het later nog eens (wat er al staat, slaat Lucent dan over).",
                                     "%d copied to %s. %d parts failed; try again later (what is there already is skipped then).") % (sent, _name(target), failed))
    import widgets
    widgets.mark_old(account_only=True)
    return dialog.ok("Lucent", T("Klaar: %d gekopieerd naar %s.", "Done: %d copied to %s.") % (sent, _name(target)))
