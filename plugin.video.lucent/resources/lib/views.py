# -*- coding: utf-8 -*-
"""Settings > General > Views: which view the Lucent skin uses for films, series, seasons, episodes, sets ...

The Lucent skin already remembers a view per kind of content (Skin.String(lucent.view.videos.<kind>), its script
views.py). Choosing here writes exactly that, so Lucent and the skin never fight over the view. When that skin option
is off (Skin.HasSetting(lucent.view.remember.off)), Lucent sets the chosen view itself in its own lists.

The view numbers and the kinds each view can show come from skin.lucent (Includes_Views*.xml); their names are the
skin's own texts. With another skin this does nothing."""
import xbmc
import xbmcgui

import util

T = util.T
SKIN = "skin.lucent"
KINDS = [("movies", T("Films", "Films")), ("tvshows", T("Series", "Series")), ("seasons", T("Seizoenen", "Seasons")),
         ("episodes", T("Afleveringen", "Episodes")), ("sets", T("Sets en filmreeksen", "Sets and film series")),
         ("videos", T("Video's", "Videos")), ("none", T("Menu's en mappen", "Menus and folders"))]
_ALL = ("movies", "tvshows", "sets", "videos")
# view id: (skin text with its name, the kinds it can show)
VIEWS = {
    50: (31031, ("movies", "tvshows", "episodes", "sets", "videos", "none")),
    51: (31032, ("movies", "tvshows", "sets")),
    52: (31033, ("movies", "tvshows", "sets")),
    57: (31571, ("movies", "tvshows", "episodes", "videos")),
    53: (31034, ("movies", "tvshows", "episodes", "videos")),
    55: (31020, ("episodes",)),
    56: (31567, ("seasons",)),
    70: (31843, _ALL), 71: (31844, _ALL), 72: (31845, _ALL), 73: (31846, _ALL),
    74: (31848, ("tvshows",)), 75: (31849, ("tvshows",)), 78: (31852, ("tvshows",)),
    76: (31850, ("episodes",)), 77: (31851, ("episodes",)), 79: (31853, ("episodes",)), 80: (31854, ("episodes",)),
    86: (31865, ("sets",)),
    87: (31866, ("movies", "tvshows", "episodes", "videos")),
}


def _key(kind):
    return "lucent.view.videos.%s" % kind


def _name(view):
    return xbmc.getLocalizedString(VIEWS[view][0]) or str(view)


def _chosen(kind):
    value = xbmc.getInfoLabel("Skin.String(%s)" % _key(kind))
    return int(value) if value.isdigit() and int(value) in VIEWS else None


def choose():
    if xbmc.getSkinDir() != SKIN:
        xbmcgui.Dialog().ok("Lucent", T("Weergaven kiezen kan alleen met de Lucent-skin.", "Choosing views only works with the Lucent skin."))
        return
    dialog = xbmcgui.Dialog()
    while True:
        rows = ["%s: %s" % (label, _name(_chosen(kind)) if _chosen(kind) else T("zoals je hem laatst koos", "as you last picked it"))
                for kind, label in KINDS]
        pick = dialog.select(T("Weergave per soort", "View per kind"), rows)
        if pick < 0:
            return
        kind, label = KINDS[pick]
        views = [v for v, (_text, kinds) in VIEWS.items() if kind in kinds]
        now = _chosen(kind)
        names = [T("Zoals je hem laatst koos", "As you last picked it")] + [_name(v) for v in views]
        choice = dialog.select(label, names, preselect=views.index(now) + 1 if now in views else 0)
        if choice < 0:
            continue
        if choice == 0:
            xbmc.executebuiltin("Skin.Reset(%s)" % _key(kind))
        else:
            xbmc.executebuiltin("Skin.SetString(%s,%d)" % (_key(kind), views[choice - 1]))
        util.log("views: %s = %s" % (kind, views[choice - 1] if choice else "skin"))


def apply(content):
    """In Lucent's own lists, when the skin does not remember views itself: the chosen view for this kind."""
    if xbmc.getSkinDir() != SKIN or not xbmc.getCondVisibility("Skin.HasSetting(lucent.view.remember.off)"):
        return
    view = _chosen(content or "none")
    if not view:
        return
    monitor = xbmc.Monitor()
    for _ in range(30):                         # the list must be on screen first, or Kodi ignores the change
        if not xbmc.getCondVisibility("Container.IsUpdating") and xbmc.getInfoLabel("Container.FolderPath").startswith("plugin://plugin.video.lucent"):
            break
        if monitor.waitForAbort(0.1):
            return
    xbmc.executebuiltin("Container.SetViewMode(%d)" % view)
