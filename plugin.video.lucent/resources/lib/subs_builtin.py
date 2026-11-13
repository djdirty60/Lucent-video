# -*- coding: utf-8 -*-
"""Lucent Video as its own subtitle source. It answers the same addresses a subtitle add-on answers, so the subtitle
list, the automatic search and two-languages-at-once ask it the same way as any other subtitle add-on:

    plugin://plugin.video.lucent/?action=search&languages=Dutch,English&preferredlanguage=Dutch[&video={...}]
    plugin://plugin.video.lucent/?action=manualsearch&searchstring=...&languages=...
    plugin://plugin.video.lucent/?action=subdownload&item={...}[&video={...}]   -> a list holding the local file

A search for films or series (action=search&type=...) never has 'languages', so the two never mix up."""
import json
import urllib.parse

import xbmcgui
import xbmcplugin

import subs_core
import util

T = util.T
ID = "plugin.video.lucent"
NAME = "Lucent Video"


def is_subtitle_call(action, params):
    return action in ("search", "manualsearch") and ("languages" in params or "preferredlanguage" in params)


def languages(params):
    names = [n for n in (params.get("languages") or "").split(",") if n]
    codes = []
    for name in [params.get("preferredlanguage", "")] + names:
        code = subs_core.to_code(name)
        if code and code not in codes:
            codes.append(code)
    return codes or ["en"]


def override(video, params):
    """Lucent can ask for another video than the one playing (the next episode, a video on the wish list): 'video' is
    a JSON object with show / season / episode / title / year / ids. The playing file is not used then."""
    try:
        other = json.loads(params.get("video") or "{}")
    except ValueError:
        other = {}
    if other:
        video.update({k: str(v) for k, v in other.items() if k in ("title", "year", "show", "season", "episode", "imdb",
                                                                     "tmdb", "show_imdb", "show_tmdb", "release")})
        video["file"] = other.get("file", "")
        video["release"] = other.get("release", "")
        video["hash"], video["size"] = "", 0
        video["other"] = True
    return video


def search(handle, params):
    try:
        query = params.get("searchstring", "").strip() if params.get("action") == "manualsearch" else ""
        video = override(subs_core.current_video(), params)
        if query:
            video["query"] = query
        rows = subs_core.search(video, languages(params))
        items = []
        for r in rows:
            label = subs_core.SOURCES[r["src"]][2]
            li = xbmcgui.ListItem(label=subs_core.lang_name(r["lang"]), label2=f"{r['release'] or video.get('title')}  ·  {label}",
                                  offscreen=True)
            li.setArt({"icon": str(r["score"]), "thumb": r["lang"]})
            li.setProperty("sync", "true" if r["sync"] else "false")
            li.setProperty("hearing_imp", "true" if r["hi"] else "false")
            args = {"action": "subdownload", "item": json.dumps({k: r[k] for k in ("src", "id", "lang", "release")})}
            if params.get("video"):
                args["video"] = params["video"]
            items.append((f"plugin://{ID}/?" + urllib.parse.urlencode(args), li, False))
        xbmcplugin.addDirectoryItems(handle, items, len(items))
    except Exception as error:  # noqa: BLE001 - never leave Kodi waiting
        subs_core.log(f"search: {error}")
    xbmcplugin.endOfDirectory(handle)


def download(handle, params):
    try:
        item = json.loads(params.get("item") or "{}")
        path = subs_core.download(item, override(subs_core.current_video(), params))
        xbmcplugin.addDirectoryItem(handle, path, xbmcgui.ListItem(label=path, offscreen=True), False)
    except Exception as error:  # noqa: BLE001
        subs_core.log(f"download: {error}")
        message = T("Daglimiet van OpenSubtitles bereikt", "Daily download limit of OpenSubtitles reached") if str(error) == "limit" \
            else T("Downloaden mislukt", "Download failed")
        xbmcgui.Dialog().notification("Lucent", message, xbmcgui.NOTIFICATION_WARNING, 4000)
    xbmcplugin.endOfDirectory(handle)


def keys():
    """(OpenSubtitles key + login filled in, SubDL key filled in)."""
    return (bool(subs_core.setting("os_key").strip() and subs_core.setting("os_user").strip() and subs_core.setting("os_pass")),
            bool(subs_core.setting("subdl_key").strip()))


def take_over_once():
    """Once: the keys of the separate Lucent Subtitles add-on, when it is installed and Lucent Video has none yet."""
    import xbmc
    import xbmcaddon
    if util.setting("subsrc_taken") == "true" or not xbmc.getCondVisibility("System.HasAddon(service.subtitles.lucent)"):
        return
    try:
        old = xbmcaddon.Addon("service.subtitles.lucent")
    except Exception:  # noqa: BLE001
        return
    own = xbmcaddon.Addon(ID)
    for key in ("os_on", "os_key", "os_user", "os_pass", "subdl_on", "subdl_key", "podnapisi_on", "timeout", "hi_last"):
        value = old.getSetting(key)
        if value and not own.getSetting("subsrc_" + key):
            own.setSetting("subsrc_" + key, value)
    own.setSetting("subsrc_taken", "true")
    util.log("subtitles: keys of Lucent Subtitles taken over")
