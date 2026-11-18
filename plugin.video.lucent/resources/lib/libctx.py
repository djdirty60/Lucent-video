# -*- coding: utf-8 -*-
"""Lucent Video's options on the titles it put in the Kodi library (Get ready for tonight, Marathon planner, Get season
ready, Choose source, watchlist, trailer, cast, similar, rate ...).
Used by context.py (Kodi's add-on menu items, below Kodi's own) and by the Lucent skin's own buttons at the top of
the context menu (action=library_ctx), which read the focused library item from its info labels."""
import os
import re
import urllib.parse

import xbmc
import xbmcvfs

import util

USENET = "lucent.video.usenet"          # Home property: Usenet is set up (the skin shows 'Get season ready' only then)


def publish():
    try:
        from nzb import source as nzb_source
        on = nzb_source.configured()
    except Exception:                   # noqa: BLE001 - a menu item is never a reason for the service to stop
        on = False
    if on:
        util.HOME.setProperty(USENET, "1")
    else:
        util.HOME.clearProperty(USENET)


def _read(path):
    handle = xbmcvfs.File(path)
    try:
        return handle.read()
    finally:
        handle.close()


def from_labels():
    """The focused library item, as context.target() makes it, from ListItem info labels."""
    label = xbmc.getInfoLabel
    kind = label("ListItem.DBTYPE")
    if kind in ("movie", "episode"):
        path = label("ListItem.FileNameAndPath")
        text = _read(path).strip() if path.endswith(".strm") else ""
        query = dict(urllib.parse.parse_qsl(text.split("?", 1)[1])) if "?" in text else {}
        if not query.get("tmdb"):
            return None
        return {"type": query["type"], "tmdb": query["tmdb"], "season": query.get("season"), "episode": query.get("episode")}
    if kind not in ("tvshow", "season"):
        return None
    tmdb_id = label("ListItem.UniqueID(tmdb)")
    if not tmdb_id:
        folder = label("ListItem.Path").rstrip("/\\")
        for place in (folder, os.path.dirname(folder)) if kind == "season" else (folder,):   # a season's path is the show's folder
            found = re.search(r'<uniqueid type="tmdb"[^>]*>(\d+)<', _read(place + "/tvshow.nfo"))
            if found:
                tmdb_id = found.group(1)
                break
    if not tmdb_id:
        return None
    meta = {"type": "tvshow", "tmdb": tmdb_id}
    if kind == "season":
        meta["season"] = label("ListItem.Season")
    return meta


def run(what, meta):
    if not meta:
        util.notify(util.T("Deze titel komt niet van Lucent Video", "This title does not come from Lucent Video"))
        return
    clean = {k: v for k, v in meta.items() if v not in (None, "")}
    kind = "movie" if clean.get("type") == "movie" else "tvshow"
    if what == "source":
        xbmc.executebuiltin("PlayMedia(%s)" % util.url_for(action="play", select=1, **clean))
    elif what == "watched":
        seen = (xbmc.getInfoLabel("ListItem.PlayCount") or "0") not in ("", "0")
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="watched", value=0 if seen else 1, **clean))
    elif what in ("watchlist_add", "watchlist_remove"):
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="watchlist", value=1 if what == "watchlist_add" else 0, **clean))
    elif what == "resume":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="resume_forget", **clean))
    elif what == "trailer":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="trailer", type=kind, tmdb=clean["tmdb"]))
    elif what == "cast":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="cast_popup", type=kind, tmdb=clean["tmdb"]))
    elif what == "similar":
        xbmc.executebuiltin("ActivateWindow(Videos,%s,return)" % util.url_for(action="list", name="similar", type=kind, tmdb=clean["tmdb"]))
    elif what == "removelib":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="library_remove", type=kind, tmdb=clean["tmdb"]))
    elif what == "rate":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="kompas_rate", type=kind, tmdb=clean["tmdb"]))
    elif what == "skiplib" and clean.get("season"):
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="library_skip", tmdb=clean["tmdb"], season=clean["season"],
                                                         episode=clean.get("episode")))
    elif what == "tonight" and clean.get("type") in ("movie", "episode"):
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="tonight_add", **clean))
    elif what == "marathon":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="marathon", tmdb=clean["tmdb"]))
    elif what == "seasonready" and str(clean.get("season", "")).isdigit():
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="season_ready", tmdb=clean["tmdb"], season=clean["season"]))
