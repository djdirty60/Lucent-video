# -*- coding: utf-8 -*-
"""'Where do I know them from?': when you pause a film or episode for a moment, cards at the bottom show the actors
of what you are watching and in which films and series YOU saw them before (your own history, Trakt / Simkl / this
device). Only actors you know from somewhere are shown; with none there is no window.

Nothing runs while the video plays: the work starts 2 seconds after a pause and stops as soon as it goes on."""
import threading
from concurrent.futures import ThreadPoolExecutor

import xbmc

import dialogs
import tmdb
import tracker
import util

T = util.T
WAIT = 2.0             # seconds paused before the cards come
ACTORS = 12            # the first actors of the credits that are looked up
MONTH = 30 * 86400


def wanted():
    return util.setting("known_on").lower() != "false"


def _cast(meta):
    """[(person id, name, role, picture)] of the title being watched, episode guests first."""
    rows = []
    try:
        if meta.get("kind") == "episode":
            credits = tmdb.get("tv/%s/season/%s/episode/%s/credits" % (meta["tmdb"], meta["season"], meta["episode"]), ttl=MONTH)
            rows = (credits.get("cast") or []) + (credits.get("guest_stars") or [])
        else:
            rows = (tmdb.get("movie/%s/credits" % meta["tmdb"], ttl=MONTH).get("cast") or [])
    except util.HttpError as error:
        util.log("known: %s" % error)
    out, seen = [], set()
    for row in rows:
        if row.get("id") and row["id"] not in seen:
            seen.add(row["id"])
            out.append((row["id"], row.get("name") or "", row.get("character") or "", tmdb.image(row.get("profile_path"), "w185")))
    return out[:ACTORS]


def _seen_in(person_id, films, shows, this):
    """Titles of this person that you watched: [(title, year)], newest first, without the title being watched."""
    try:
        credits = tmdb.get("person/%s/combined_credits" % person_id, ttl=MONTH).get("cast") or []
    except util.HttpError:
        return []
    out, done = [], set()
    for row in credits:
        key = (row.get("media_type"), str(row.get("id")))
        if key in done or key == this:
            continue
        done.add(key)
        if (key[0] == "movie" and key[1] in films) or (key[0] == "tv" and key[1] in shows):
            date = row.get("release_date") or row.get("first_air_date") or ""
            out.append((row.get("title") or row.get("name") or "", date[:4]))
    out.sort(key=lambda t: t[1], reverse=True)
    return out


def find(meta):
    """[{name, role, thumb, titles}] of the actors you know from your own history."""
    try:
        films = {str(k) for k in tracker.watched_movies()}
        shows = {str(k) for k in tracker.watched_episodes()}
    except util.HttpError:
        return []
    this = ("movie" if meta.get("kind") == "movie" else "tv", str(meta.get("tmdb")))
    cast = _cast(meta)
    with ThreadPoolExecutor(max_workers=6) as pool:
        found = list(pool.map(lambda c: _seen_in(c[0], films, shows, this), cast))
    out = []
    for (_pid, name, role, thumb), titles in zip(cast, found):
        if titles:
            out.append({"name": name, "role": role, "thumb": thumb,
                        "titles": "[CR]".join("%s (%s)" % t if t[1] else t[0] for t in titles[:4])})
    return out


class Known(dialogs.Base):
    def onInit(self):
        import xbmcgui
        self.fill({"head": T("Waar ken ik die van?", "Where do I know them from?"),
                   "sub": T("uit jouw kijkgeschiedenis", "from your own history"),
                   "seen": T("Je zag hem of haar in", "You saw them in")})
        rows = []
        for person in self.args["people"]:
            item = xbmcgui.ListItem(person["name"], (T("als %s", "as %s") % person["role"]) if person["role"] else "", offscreen=True)
            item.setArt({"thumb": person["thumb"]})
            item.setProperty("titles", person["titles"])
            item.setProperty("letter", person["name"][:1])
            rows.append(item)
        self.getControl(100).addItems(rows)
        self.setFocusId(100)

    def onAction(self, action):
        # Back, Play or Pause: the cards go and the video goes on
        if action.getId() in dialogs.BACK + (12, 68, 79, 229):
            self.result = "resume"
            self.close()


class Pauses:
    """Kept by the service's player watcher: one pause = at most one window."""

    def __init__(self, player):
        self.player = player
        self.dialog = None
        self.count = 0

    def paused(self, meta):
        if not (meta and wanted() and meta.get("kind") in ("movie", "episode")):
            return
        self.count += 1
        threading.Thread(target=self._run, args=(dict(meta), self.count), daemon=True).start()

    def resumed(self):
        self.count += 1
        dialog, self.dialog = self.dialog, None
        if dialog:
            try:
                dialog.close()
            except RuntimeError:
                pass

    def _still(self, number):
        return number == self.count and xbmc.getCondVisibility("Player.Paused")

    def _run(self, meta, number):
        monitor = xbmc.Monitor()
        if monitor.waitForAbort(WAIT) or not self._still(number):
            return
        # the player menu (videoosd) may still be open: the skin closes it a few seconds after a pause; wait for that,
        # so the cards never lie over its buttons
        for _ in range(40):
            if not xbmc.getCondVisibility("Window.IsActive(videoosd) | Window.IsActive(videoosdsettings) | System.HasActiveModalDialog"):
                break
            if monitor.waitForAbort(0.5) or not self._still(number):
                return
        else:
            return
        people = find(meta)
        if not people or not self._still(number) or xbmc.getCondVisibility("Window.IsActive(videoosd) | System.HasActiveModalDialog"):
            return
        self.dialog = dialogs._open(Known, "lucent_known.xml", people=people)     # noqa: SLF001 - same add-on
        self.dialog.doModal()
        dialog, self.dialog = self.dialog, None
        if dialog and dialog.result == "resume" and xbmc.getCondVisibility("Player.Paused"):
            self.player.pause()
