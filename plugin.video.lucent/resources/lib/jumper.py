"""Open a series where you left off (setting 'series_jump').

Opening a series (in Lucent Video or in the Kodi library) skips the seasons screen: you land in the episode list of
the season you are in, with the cursor on your next episode. One step back brings you to the seasons screen, and from
there it does not jump again. Runs as a light watcher in the service: only while the Videos window is open and nothing
plays, it reads two labels four times a second.
"""
import re
import threading
import urllib.parse

import xbmc
import xbmcgui

import util

VIDEOS = 10025
PLUGIN = "plugin://%s/" % util.ADDON_ID
LIBRARY_SHOW = re.compile(r"^videodb://tvshows/titles/(\d+)/(?:-?\d+/)?(?:\?.*)?$")


def _plugin_params(path):
    if not path.startswith(PLUGIN):
        return {}
    return dict(urllib.parse.parse_qsl(urllib.parse.urlsplit(path).query))


def _where(path):
    """('seasons'|'episodes', show key, season or None) for a series path, else None."""
    params = _plugin_params(path)
    if params.get("action") == "seasons" and params.get("tmdb"):
        return "seasons", "p" + params["tmdb"], None
    if params.get("action") == "episodes" and params.get("tmdb"):
        return "episodes", "p" + params["tmdb"], params.get("season")
    match = LIBRARY_SHOW.match(path)
    if match:
        rest = path[len("videodb://tvshows/titles/%s/" % match.group(1)):].split("?")[0].strip("/")
        if not rest:
            return "seasons", "l" + match.group(1), None
        return "episodes", "l" + match.group(1), rest
    return None


def _after(watched, aired):
    """The first aired (season, episode) after the last one you watched; None if you watched none or all."""
    seen = [key for key in watched if key[0] > 0]
    if not seen:
        return None
    last = max(seen)
    return next((key for key in sorted(aired) if key > last and key not in watched), None)


def _plugin_next(tmdb_id):
    import tmdb
    import tracker
    show = tmdb.details("tvshow", tmdb_id)
    if not show:
        return None
    aired = []
    for season in show.get("seasons") or []:
        number = season.get("season_number") or 0
        count = tmdb.aired_in_season(show, number) if show.get("last_aired") else (season.get("episode_count") or 0)
        aired += [(number, episode) for episode in range(1, count + 1)]
    return _after(set(tracker.watched_episodes().get(str(tmdb_id), {})), aired)


def _library_next(show_id):
    import library
    rows = library.rpc("VideoLibrary.GetEpisodes", {"tvshowid": int(show_id), "properties": ["season", "episode", "playcount"]})
    episodes = rows.get("episodes") or []
    watched = {(e["season"], e["episode"]) for e in episodes if e.get("playcount")}
    return _after(watched, [(e["season"], e["episode"]) for e in episodes])


def _episodes_path(key, season):
    if key.startswith("p"):
        return util.url_for(action="episodes", tmdb=key[1:], season=season)
    return "videodb://tvshows/titles/%s/%d/" % (key[1:], season)


class Jumper:
    def __init__(self):
        self.last = ""
        self.focus = None          # (episodes path, season, episode, tries left)
        self.busy = False

    def tick(self, playing):
        if playing or xbmcgui.getCurrentWindowId() != VIDEOS:
            self.last = ""                  # coming back to the Videos window later is a fresh start, not a step back
            return
        path = xbmc.getInfoLabel("Container.FolderPath")
        if path != self.last:
            before, self.last = self.last, path
            self.changed(before, path)
        if self.focus:
            self.try_focus(path)

    def changed(self, before, path):
        here = _where(path)
        if not here or here[0] != "seasons" or self.busy or not util.flag("series_jump"):
            return
        came_from = _where(before)
        if came_from and came_from[1] == here[1] and came_from[0] == "episodes":
            return                          # one step back from an episode list: stay on the seasons screen
        self.busy = True
        threading.Thread(target=self.jump, args=(path, here[1]), daemon=True).start()

    def jump(self, path, key):
        try:
            found = _plugin_next(key[1:]) if key.startswith("p") else _library_next(key[1:])
            if not found or xbmc.getInfoLabel("Container.FolderPath") != path or xbmc.Player().isPlaying():
                return
            target = _episodes_path(key, found[0])
            self.focus = (target, found[0], found[1], 40)
            util.log("series: opening season %d at episode %d" % found)
            xbmc.executebuiltin("Container.Update(%s)" % target)
        except Exception as error:      # noqa: BLE001 - a jump is a convenience, never a reason to fail
            util.log("series jump: %s" % type(error).__name__)
        finally:
            self.busy = False

    def try_focus(self, path):
        target, season, episode, tries = self.focus
        self.focus = (target, season, episode, tries - 1) if tries > 1 else None
        if _where(path) != _where(target) or xbmc.getCondVisibility("Container.IsUpdating"):
            return
        total = int(xbmc.getInfoLabel("Container.NumAllItems") or xbmc.getInfoLabel("Container.NumItems") or 0)
        for index in range(total + 1):
            label = "Container.ListItemAbsolute(%d)." % index
            if xbmc.getInfoLabel(label + "Season") == str(season) and xbmc.getInfoLabel(label + "Episode") == str(episode):
                control = xbmcgui.Window(VIDEOS).getFocusId()
                xbmc.executebuiltin("SetFocus(%d,%d,absolute)" % (control, index))
                self.focus = None
                return


def start(monitor):
    """Runs the watcher in its own thread, four checks a second, until Kodi stops."""
    jumper, player = Jumper(), xbmc.Player()

    def loop():
        while not monitor.waitForAbort(0.25):
            try:
                jumper.tick(player.isPlaying())
            except Exception as error:      # noqa: BLE001
                util.log("series jump: %s" % type(error).__name__)
    threading.Thread(target=loop, daemon=True).start()
