# -*- coding: utf-8 -*-
"""More from TMDB: titles about the same subject (keywords), reviews, popular people and the order of episodes
(TMDB 'episode groups': DVD order, absolute numbering for anime and so on)."""
import xbmcgui

import tmdb
import util

T = util.T
DAY = tmdb.DAY
ORDERS = "episode_order.json"          # {show tmdb id: episode group id} - the numbering used to search for releases
GROUP_TYPES = {1: T("op uitzenddatum", "by air date"), 2: T("doorlopend genummerd", "absolute"), 3: "DVD",
               4: T("digitaal", "digital"), 5: T("verhaallijnen", "story arcs"), 6: T("productie", "production"), 7: "TV"}


# ------------------------------------------------------------------ more about the same subject
def keyword_titles(kind, tmdb_id, page=1):
    """Titles that share the most telling keywords of this one ('time travel', 'heist'), best known first. (titles, more?)"""
    api = "movie" if kind == "movie" else "tv"
    raw = tmdb.get("%s/%s/keywords" % (api, tmdb_id), ttl=30 * DAY)
    words = raw.get("keywords") or raw.get("results") or []
    if not words:
        return [], False
    ids = "|".join(str(w["id"]) for w in words[:6])          # '|' = any of them: TMDB puts titles with more matches higher
    data = tmdb.get("discover/%s" % api, ttl=DAY, page=page, with_keywords=ids, sort_by="popularity.desc",
                    include_adult="false", **{"vote_count.gte": 50})
    pairs = [(kind, r["id"]) for r in data.get("results") or [] if str(r.get("id")) != str(tmdb_id)]
    return tmdb.many(pairs), page < min(data.get("total_pages") or 1, 20)


# ------------------------------------------------------------------ reviews
def reviews(kind, tmdb_id):
    """[(author, rating or None, date, text)]: in your language when there are any, else in English."""
    api = "movie" if kind == "movie" else "tv"
    rows = tmdb.get("%s/%s/reviews" % (api, tmdb_id), ttl=DAY).get("results") or []
    if not rows and tmdb.lang() != "en-US":
        rows = tmdb.get("%s/%s/reviews" % (api, tmdb_id), ttl=DAY, language="en-US").get("results") or []
    out = []
    for row in rows[:8]:
        rating = (row.get("author_details") or {}).get("rating")
        out.append((row.get("author") or "?", rating, (row.get("created_at") or "")[:10], (row.get("content") or "").strip()))
    return out


def show_reviews(kind, tmdb_id):
    meta = tmdb.details("movie" if kind == "movie" else "tvshow", tmdb_id) or {}
    found = reviews(kind, tmdb_id)
    if not found:
        return util.notify(T("Nog geen recensies op TMDB", "No reviews on TMDB yet"))
    parts = []
    for author, rating, date, text in found:
        head = "[B]%s[/B]" % author + ("  ★ %s/10" % rating if rating else "") + ("  ·  %s" % date if date else "")
        parts.append("%s\n%s" % (head, text[:1500] + ("…" if len(text) > 1500 else "")))
    xbmcgui.Dialog().textviewer(T("Recensies: %s", "Reviews: %s") % meta.get("title", ""), "\n\n".join(parts))
    return None


# ------------------------------------------------------------------ popular people
def popular_people(page=1):
    """[(id, name, portrait, known for)] and more pages?"""
    data = tmdb.get("person/popular", ttl=DAY, page=page)
    people = []
    for row in data.get("results") or []:
        if row.get("adult") or not row.get("name"):
            continue
        known = ", ".join((t.get("title") or t.get("name") or "") for t in (row.get("known_for") or [])[:2] if t.get("title") or t.get("name"))
        people.append((row["id"], row["name"], tmdb.image(row.get("profile_path"), "w342"), known))
    return people, page < min(data.get("total_pages") or 1, 20)


# ------------------------------------------------------------------ order of episodes
def groups(show):
    """The other orders TMDB knows for this series: [(id, label)]."""
    rows = tmdb.get("tv/%s/episode_groups" % show, ttl=7 * DAY).get("results") or []
    out = []
    for row in rows:
        kind = GROUP_TYPES.get(row.get("type"), "")
        label = row.get("name") or kind or "?"
        extra = ", ".join(p for p in (kind if kind and kind.lower() not in label.lower() else "",
                                       T("%d afl.", "%d ep.") % int(row.get("episode_count") or 0)) if p)
        out.append((row["id"], "%s (%s)" % (label, extra), row.get("type")))
    return out


def _numbers(group_id, kind=None):
    """{(TMDB season, TMDB episode): (season, episode) in this order}. An absolute order counts on through the groups."""
    data = tmdb.get("tv/episode_group/%s" % group_id, ttl=7 * DAY)
    absolute = (kind or data.get("type")) == 2
    table, count = {}, 0
    for part in sorted(data.get("groups") or [], key=lambda g: g.get("order") or 0):
        for row in sorted(part.get("episodes") or [], key=lambda e: e.get("order") or 0):
            key = (int(row.get("season_number") or 0), int(row.get("episode_number") or 0))
            if absolute:
                if key[0] == 0:                 # specials keep their own numbers
                    continue
                count += 1
                table[key] = (1, count)
            else:
                table[key] = (int(part.get("order") or 0), int(row.get("order") or 0) + 1)
    return table


def chosen(show):
    """(group id, group type) of the order you chose for this series, or (None, None)."""
    value = util.load_json(ORDERS, {}).get(str(show))
    if isinstance(value, dict):
        return value.get("id"), value.get("type")
    return value, None


def for_search(meta):
    """The episode with the numbers of the order you chose for this series (only for finding releases: watched,
    progress and your tracker keep TMDB's own numbers). The same meta when there is no other order."""
    if meta.get("kind") != "episode" or not meta.get("season") or not meta.get("episode") or "tmdb_season" in meta:
        return meta                             # not an episode, or already in the chosen order (a second try)
    group, kind = chosen(meta.get("tmdb"))
    if not group:
        return meta
    try:
        found = _numbers(group, kind).get((int(meta["season"]), int(meta["episode"])))
    except (util.HttpError, ValueError, TypeError) as error:
        util.log("episode order: not read (%s)" % error)
        return meta
    if not found or found == (int(meta["season"]), int(meta["episode"])):
        return meta
    util.log("episode order: S%02dE%02d is searched as S%02dE%02d" % (int(meta["season"]), int(meta["episode"]), found[0], found[1]))
    return dict(meta, season=found[0], episode=found[1], tmdb_season=meta["season"], tmdb_episode=meta["episode"])


def choose(show):
    """The menu 'Order of episodes': TMDB's own or one of the other orders, kept for this series."""
    try:
        options = groups(show)
    except util.HttpError:
        return util.notify(T("TMDB is niet bereikbaar", "TMDB cannot be reached"))
    if not options:
        return xbmcgui.Dialog().ok(T("Afleveringsvolgorde", "Order of episodes"),
                                   T("TMDB kent voor deze serie alleen de gewone volgorde.", "TMDB only knows the normal order for this series."))
    now = chosen(show)[0]
    labels = [T("Gewoon (zoals TMDB)", "Normal (as TMDB)")] + [label for _id, label, _type in options]
    current = 0 if not now else next((n + 1 for n, (gid, _l, _t) in enumerate(options) if gid == now), 0)
    labels = [("● " if n == current else "") + label for n, label in enumerate(labels)]
    picked = xbmcgui.Dialog().select(T("Zoeken met welke volgorde?", "Search with which order?"), labels, preselect=current)
    if picked < 0:
        return None
    saved = util.load_json(ORDERS, {})
    if picked == 0:
        saved.pop(str(show), None)
    else:
        saved[str(show)] = {"id": options[picked - 1][0], "type": options[picked - 1][2]}
    util.save_json(ORDERS, saved)
    util.notify(T("Volgorde bewaard: zoeken gebruikt nu deze nummers", "Order saved: searching now uses these numbers") if picked
                else T("Weer de gewone volgorde", "Back to the normal order"))
    return None
