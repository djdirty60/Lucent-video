# -*- coding: utf-8 -*-
"""Turns titles into Kodi list items, with the properties the Lucent skin reads."""
import os
import urllib.parse

import xbmc
import xbmcgui

import util


def _plain(text):
    return str(text or "")


def art_of(meta):
    art = {"poster": meta.get("poster", ""), "fanart": meta.get("fanart", ""), "landscape": meta.get("fanart", ""),
           "clearlogo": meta.get("logo", ""), "thumb": meta.get("thumb") or meta.get("poster", "")}
    if meta["kind"] in ("season", "episode"):
        show_fanart = meta.get("tvshow_fanart") or meta.get("fanart", "")
        art.update({"tvshow.poster": meta.get("poster", ""), "tvshow.fanart": show_fanart, "tvshow.landscape": show_fanart,
                    "tvshow.clearlogo": meta.get("logo", "")})
    return {k: v for k, v in art.items() if v}


def fill_tag(item, meta, playcount=None, resume=None, last_played="", default_id="tmdb"):
    tag = item.getVideoInfoTag()
    kind = meta["kind"]
    tag.setMediaType({"movie": "movie", "tvshow": "tvshow", "season": "season", "episode": "episode"}[kind])
    tag.setTitle(_plain(meta.get("title")))
    if meta.get("original_title") and meta["original_title"] != meta.get("title"):
        tag.setOriginalTitle(meta["original_title"])
    tag.setPlot(_plain(meta.get("plot")))
    tag.setTagLine(_plain(meta.get("reason") or meta.get("tagline")))
    tag.setMpaa(_plain(meta.get("mpaa")))
    tag.setTrailer(_plain(meta.get("trailer")))
    if meta.get("year"):
        tag.setYear(int(meta["year"]))
    if meta.get("premiered"):
        tag.setPremiered(meta["premiered"])
    ratings = {}
    if meta.get("rating"):
        ratings["tmdb"] = (float(meta["rating"]), int(meta.get("votes") or 0))
    if meta.get("trakt_rating"):
        ratings["trakt"] = (float(meta["trakt_rating"]), int(meta.get("trakt_votes") or 0))
    ratings.update(meta.get("scores") or {})
    if ratings:
        tag.setRatings(ratings, "tmdb" if "tmdb" in ratings else "trakt")
    tag.setGenres(list(meta.get("genres") or []))
    tag.setStudios(list(meta.get("studios") or []))
    tag.setDirectors(list(meta.get("directors") or []))
    tag.setWriters(list(meta.get("writers") or []))
    if meta.get("cast"):
        tag.setCast([xbmc.Actor(p["name"], p.get("role") or "", order, p.get("thumb") or "") for order, p in enumerate(meta["cast"])])
    if meta.get("runtime"):
        tag.setDuration(int(meta["runtime"]) * 60)
    ids = {"tmdb": str(meta["tmdb"])}
    if meta.get("imdb"):
        ids["imdb"] = meta["imdb"]
    if meta.get("tvdb"):
        ids["tvdb"] = meta["tvdb"]
    if meta.get("tmdb_episode"):
        ids["tmdb_episode"] = meta["tmdb_episode"]
    tag.setUniqueIDs(ids, default_id if default_id in ids else "tmdb")
    if kind != "movie" and meta.get("status"):
        tag.setTvShowStatus(meta["status"])         # Ended, Continuing, Cancelled or In production
    if kind in ("season", "episode"):
        tag.setTvShowTitle(_plain(meta.get("show")))
        tag.setSeason(int(meta["season"]))
    if kind == "episode":
        tag.setEpisode(int(meta["episode"]))
    if playcount is not None:
        tag.setPlaycount(playcount)
    if resume:
        tag.setResumePoint(float(resume[0]), float(resume[1]))
    if last_played:
        tag.setLastPlayed(last_played)


def play_url(meta, **extra):
    if meta["kind"] == "movie":
        return util.url_for(action="play", type="movie", tmdb=meta["tmdb"], **extra)
    return util.url_for(action="play", type="episode", tmdb=meta["tmdb"], season=meta["season"],
                        episode=meta["episode"], **extra)


def context(meta, watched, resumable=False):
    entries = []

    def run(label, **params):
        entries.append((label, "RunPlugin(%s)" % util.url_for(**params)))
    target = {"type": meta["kind"], "tmdb": meta["tmdb"], "season": meta.get("season"), "episode": meta.get("episode")}
    if meta["kind"] in ("movie", "episode"):
        entries.append((util.T("Bron kiezen", "Choose source"), "PlayMedia(%s)" % play_url(meta, select=1)))
        run(util.T("Klaarzetten voor vanavond", "Get ready for tonight"), action="tonight_add", **target)
        if resumable:                         # Kodi's own 'Reset resume position' cannot reach your account
            run(util.T("Hervatpunt wissen", "Clear resume point"), action="resume_forget", **target)
    run(util.T("Markeer als niet bekeken", "Mark as unwatched") if watched else
        util.T("Markeer als bekeken", "Mark as watched"), action="watched", value=0 if watched else 1, **target)
    if meta["kind"] in ("tvshow", "episode", "season"):
        run(util.T("Intro vanzelf overslaan: aan/uit", "Skip intro by itself: on/off"), action="introskip", tmdb=meta["tmdb"])
    if meta["kind"] in ("movie", "tvshow"):
        run(util.T("Op kijklijst", "Add to watchlist"), action="watchlist", value=1, **target)
        run(util.T("Van kijklijst af", "Remove from watchlist"), action="watchlist", value=0, **target)
        if meta.get("trailer"):
            entries.append((util.T("Trailer bekijken", "Watch trailer"), "PlayMedia(%s)" % meta["trailer"]))
        entries.append((util.T("Acteurs", "Cast"), "RunPlugin(%s)" % util.url_for(action="cast_popup", type=meta["kind"], tmdb=meta["tmdb"])))
        entries.append((util.T("Vergelijkbare titels", "Similar titles"), "Container.Update(%s)" % util.url_for(
            action="list", name="similar", type=meta["kind"], tmdb=meta["tmdb"])))
        if meta["kind"] == "tvshow":
            run(util.T("Marathonplanner", "Marathon planner"), action="marathon", tmdb=meta["tmdb"])
        import library
        if library.has(meta["kind"], meta["tmdb"]):
            run(util.T("Uit je bibliotheek halen", "Remove from your library"), action="library_remove", type=meta["kind"], tmdb=meta["tmdb"])
        else:
            run(util.T("Toevoegen aan je bibliotheek", "Add to your library"), action="library_add", type=meta["kind"], tmdb=meta["tmdb"])
        run(util.T("Beoordelen", "Rate"), action="kompas_rate", type=meta["kind"], tmdb=meta["tmdb"])
    return entries


def season_props(item, meta):
    """For the skin's posters: how many episodes this season has, how many come after this one, and whether this is the
    season finale. Nothing when the number of episodes is not known (specials have none)."""
    item.setProperty("tvshow.tmdb_id", str(meta["tmdb"]))
    count = int(meta.get("season_count") or 0)
    number = int(meta.get("episode") or 0)
    if not count or not number or not int(meta.get("season") or 0) or number > count:
        return
    item.setProperty("lucent.season.count", str(count))
    aired = meta.get("season_aired")
    left = (aired if aired is not None else count) - number          # only episodes that were on TV already
    if number < count and left > 0:
        item.setProperty("lucent.season.left", util.T("nog %d", "%d left") % left)
    elif number == count:
        item.setProperty("lucent.season.finale", "1")


def title_item(meta, watched=False, resume=None, last_played="", counts=None):
    """Returns (url, ListItem, is_folder). The label is the plain title: the skin does the rest."""
    kind = meta["kind"]
    item = xbmcgui.ListItem(meta["title"], offscreen=True)
    item.setArt(art_of(meta))
    fill_tag(item, meta, 1 if watched else 0, resume, last_played)
    item.setProperty("tmdb_id", str(meta["tmdb"]))
    item.setProperty("lucent_video", "1")
    if kind in ("movie", "tvshow"):
        import library
        if library.has(kind, meta["tmdb"]):
            item.setProperty("lucent_inlibrary", "1")
    if meta.get("reason"):
        item.setProperty("kompas_reason", meta["reason"])
    if meta.get("trakt_id"):
        item.setProperty("trakt_id", meta["trakt_id"])
    if meta.get("tmdb_episode"):
        item.setProperty("tmdb_episode_id", meta["tmdb_episode"])
    if kind == "episode":
        season_props(item, meta)
    if meta.get("mdb_average"):
        item.setProperty("mdblist_score", str(int(meta["mdb_average"])))
    if counts:
        item.setProperty("TotalEpisodes", str(counts[0]))
        item.setProperty("WatchedEpisodes", str(counts[1]))
        item.setProperty("UnWatchedEpisodes", str(max(0, counts[0] - counts[1])))
    item.addContextMenuItems(context(meta, watched, bool(resume and resume[0])))
    if kind in ("movie", "episode"):
        item.setProperty("IsPlayable", "true")
        return play_url(meta), item, False
    if kind == "tvshow":
        item.setProperty("TotalSeasons", str(len(meta.get("seasons") or [])))
        return util.url_for(action="seasons", tmdb=meta["tmdb"]), item, True
    return util.url_for(action="episodes", tmdb=meta["tmdb"], season=meta["season"]), item, True


def folder(label, url, icon="DefaultFolder.png"):
    item = xbmcgui.ListItem(label, offscreen=True)
    item.setArt({"icon": icon, "thumb": icon})
    menu_look(item, url)
    return url, item, True


_MEDIA = {}


def _menu_media():
    if "path" not in _MEDIA:
        import xbmcvfs
        _MEDIA["path"] = os.path.join(xbmcvfs.translatePath(util.ADDON.getAddonInfo("path")), "resources", "media", "menu")
    return _MEDIA["path"]


def menu_key(url):
    """'list:continue', 'menu:movie', 'tools' ... from a plugin address (see menudata.py)."""
    query = dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(url).query))
    action = query.get("action", "")
    if action == "search" and query.get("query"):
        return "search"
    if action == "list" and query.get("page"):
        return "page"
    extra = query.get({"list": "name", "menu": "type", "search": "type", "traktlists": "kind", "mdblists": "kind"}.get(action, ""), "")
    return "%s:%s" % (action, extra) if extra else action


def menu_look(item, url):
    """A card in the style of Lucent's menus (icon, colour, accent line) and a short explanation for the skin."""
    import menudata
    key = menu_key(url)
    entry = menudata.MENU.get(key)
    if entry:
        icon, tint, nl, en = entry
    elif key.split(":")[0] in menudata.FALLBACK:
        (icon, tint), nl, en = menudata.FALLBACK[key.split(":")[0]], "", ""
    else:
        return
    name = menudata.card_name(icon, tint)
    folder_path = _menu_media()
    wide, square, poster = (os.path.join(folder_path, name + end) for end in (".jpg", "_sq.jpg", "_poster.jpg"))
    item.setArt({"thumb": square, "icon": square, "poster": poster, "landscape": wide,
                 "fanart": os.path.join(folder_path, "fanart.jpg")})
    text = util.T(nl, en) if nl else ""
    if text:
        item.getVideoInfoTag().setPlot(text)
        item.setProperty("lucent.menu.info", text)


def clock(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds % 3600 // 60, seconds % 60)

