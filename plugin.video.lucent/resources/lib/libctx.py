# -*- coding: utf-8 -*-
"""Get ready for tonight, Marathon planner and Get season ready on the titles Lucent Video put in the Kodi library.
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
    if what == "tonight" and clean.get("type") in ("movie", "episode"):
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="tonight_add", **clean))
    elif what == "marathon":
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="marathon", tmdb=clean["tmdb"]))
    elif what == "seasonready" and str(clean.get("season", "")).isdigit():
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="season_ready", tmdb=clean["tmdb"], season=clean["season"]))
