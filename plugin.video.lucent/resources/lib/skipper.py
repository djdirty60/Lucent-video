# -*- coding: utf-8 -*-
"""Skip intro, recap and preview, built into Lucent Video (data from TheIntroDB, see tidb.py).

Per part a setting: off, a button, or by itself. 'By itself' shows the card with a countdown and 'Watch' to stay.
A series can be set to skip by itself in its menu ('Skip intro by itself', introskip.py).

One lookup when a film or episode of Lucent starts; then the thread only sleeps until the next part: nothing runs
while you watch. The separate TheIntroDB add-on (plugin.video.tidb) is not needed any more: while it is switched on,
Lucent's own skipping stays off, so two buttons never lie on top of each other."""
import threading

import xbmc
import xbmcgui

import dialogs
import tidb
import util

T = util.T
OLD = "plugin.video.tidb"
KINDS = ("intro", "recap", "preview")
OFF, BUTTON, AUTO = 0, 1, 2
ASKED = "skip_migrated.json"


def _names():
    return {"intro": T("Intro", "Intro"), "recap": T("Terugblik", "Recap"), "preview": T("Preview", "Preview")}


def old_addon_on():
    return xbmc.getCondVisibility("System.HasAddon(%s) + System.AddonIsEnabled(%s)" % (OLD, OLD))


def mode(kind, meta=None):
    try:
        value = int(util.setting("skip_%s" % kind) or BUTTON)
    except ValueError:
        value = BUTTON
    if value != OFF and meta and meta.get("kind") == "episode":
        import introskip
        if introskip.series_auto(meta["tmdb"]):
            return AUTO
    return value


def wait_seconds():
    try:
        return max(2, min(15, int(util.setting("skip_wait") or 5)))
    except ValueError:
        return 5


def _clock(seconds):
    seconds = int(max(0, seconds))
    return "%d:%02d" % (seconds // 60, seconds % 60)


class Card(dialogs.Base):
    """Bottom right: what it is and how long, 'Skip' and 'Watch'. With 'by itself' a countdown runs."""

    def onInit(self):
        self.fill(self.args["props"])
        self.setFocusId(10)

    def onClick(self, control):
        self.result = "skip" if control == 10 else "watch"
        self.close()

    def onAction(self, action):
        if action.getId() in dialogs.BACK:
            self.result = "watch"
            self.close()


class Watch:
    """One per video: sleeps until each part, then shows the card once for it."""

    def __init__(self, player, meta, number):
        self.player, self.meta, self.number = player, meta, number
        self.monitor = xbmc.Monitor()

    def _same(self):
        return self.number == self.player.video_number and self.player.isPlayingVideo()

    def _now(self):
        try:
            return self.player.getTime()
        except RuntimeError:
            return None

    def run(self):
        meta = self.meta
        if not meta or meta.get("kind") not in ("movie", "episode") or old_addon_on():
            return
        wanted = [k for k in KINDS if mode(k, meta) != OFF]
        if not wanted:
            return
        total = 0
        for _ in range(30):
            try:
                total = self.player.getTotalTime() or 0
            except RuntimeError:
                total = 0
            if total or self.monitor.waitForAbort(1):
                break
        if not total:
            return
        parts = tidb.segments(meta, total)
        todo = sorted((start, end, kind) for kind in wanted for start, end in parts.get(kind) or [])
        if not todo:
            return
        util.log("skip: %s" % ", ".join("%s %d-%s" % (k, s, int(e) if e else "end") for s, e, k in todo))
        for start, end, kind in todo:
            stop_at = end if end is not None else total
            while True:
                if not self._same():
                    return
                now = self._now()
                if now is None:
                    return
                if now >= stop_at - 3:
                    break                                   # already past this part
                if now >= start - 0.5:
                    self._offer(kind, start, end, total)
                    break
                if self.monitor.waitForAbort(min(30.0, max(0.5, start - now - 0.3))):
                    return

    def _offer(self, kind, start, end, total):
        target = end if end is not None else max(0.0, total - 1)
        auto = mode(kind, self.meta) == AUTO
        names = _names()
        props = {"kicker": "%s · %s" % (names[kind], _clock(target - start)), "skip": T("Overslaan", "Skip"), "watch": T("Kijken", "Watch"),
                 "sub": T("Druk op OK om over te slaan", "Press OK to skip")}
        seconds = wait_seconds()
        if auto:
            props["sub"] = T("Slaat over in %d sec", "Skips in %d sec") % seconds
        card = dialogs._open(Card, "lucent_skip.xml", props=props)        # noqa: SLF001 - same add-on
        state = {"open": True}

        def watch():
            left = seconds
            while state["open"]:
                if self.monitor.waitForAbort(1) or not self._same():
                    break
                now = self._now()
                if now is None or now >= target - 1 or now < start - 2:   # the part is over, or you went back
                    break
                if auto:
                    left -= 1
                    if left <= 0:
                        card.result = "skip"
                        break
                    card.setProperty("sub", T("Slaat over in %d sec", "Skips in %d sec") % left)
            if state["open"]:
                card.close()

        worker = threading.Thread(target=watch, daemon=True)
        worker.start()
        card.doModal()
        state["open"] = False
        result = card.result
        del card
        if result == "skip" and self._same():
            try:
                self.player.seekTime(target)
                util.log("skip: %s skipped to %d" % (kind, target))
            except RuntimeError:
                pass


# ------------------------------------------------------------------ from the separate TheIntroDB add-on
def migrate_once():
    """Once: when the TheIntroDB add-on is still on, ask to switch it off and take over its settings."""
    if not old_addon_on() or util.load_json(ASKED, {}).get("asked"):
        return
    if xbmc.getCondVisibility("Player.HasMedia | System.HasActiveModalDialog") or not xbmc.getCondVisibility("Window.IsVisible(home)"):
        return                                          # later, when nothing else is going on
    util.save_json(ASKED, {"asked": True})
    yes = xbmcgui.Dialog().yesno(T("Intro overslaan", "Skip intro"),
                                 T("Lucent Video slaat intro, terugblik en preview nu zelf over. Mag Lucent de losse TheIntroDB-add-on "
                                   "uitzetten? Je instellingen ervan neemt Lucent over. Zonder dat blijft Lucent's eigen knop uit, "
                                   "zodat er geen twee knoppen over elkaar komen.",
                                   "Lucent Video now skips intro, recap and preview by itself. May Lucent switch off the separate "
                                   "TheIntroDB add-on? Lucent takes over its settings. Without that, Lucent's own button stays off, so "
                                   "two buttons never lie on top of each other."),
                                 nolabel=T("Nee", "No"), yeslabel=T("Ja, uitzetten", "Yes, switch off"))
    if not yes:
        util.log("skip: the user keeps the TheIntroDB add-on")
        return
    take_over()
    import subs_live
    try:
        subs_live.rpc("Addons.SetAddonEnabled", {"addonid": OLD, "enabled": False})
        util.notify(T("TheIntroDB-add-on uitgezet; Lucent slaat nu zelf over", "TheIntroDB add-on switched off; Lucent skips by itself now"))
    except Exception as error:      # noqa: BLE001
        util.log("skip: could not switch off %s: %s" % (OLD, type(error).__name__))


def take_over():
    """The TheIntroDB add-on's choices as Lucent's own: per part off / button / by itself, and its key."""
    import xbmcaddon
    try:
        old = xbmcaddon.Addon(OLD)
    except Exception:  # noqa: BLE001
        return
    own = xbmcaddon.Addon(util.ADDON_ID)
    # Lucent switched the add-on's 'by itself' per series before; its own choice was kept in intro_skip.json
    default = util.load_json("intro_skip.json", {}).get("default") or {}
    for kind in KINDS:
        auto = default.get("auto_skip_%s" % kind, old.getSetting("auto_skip_%s" % kind)) or ""
        if (old.getSetting("enable_%s" % kind) or "true").lower() == "false":
            value = OFF
        elif auto.lower() == "true":
            value = AUTO
        else:
            value = BUTTON
        own.setSetting("skip_%s" % kind, str(value))
    key = (old.getSetting("introdb_api_key") or "").strip()
    if key and not util.setting("tidb_key"):
        own.setSetting("tidb_key", key)
    util.log("skip: settings of the TheIntroDB add-on taken over")
