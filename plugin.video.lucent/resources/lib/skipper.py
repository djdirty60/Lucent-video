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
KINDS = ("intro", "recap", "preview", "credits")
OFF, BUTTON, AUTO = 0, 1, 2
ASKED = "skip_migrated.json"


def _names():
    return {"intro": T("Intro", "Intro"), "recap": T("Terugblik", "Recap"), "preview": T("Preview", "Preview"), "credits": T("Aftiteling", "Credits")}


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


def offset():
    """Seconds added to the end of a part when skipping (TheIntroDB's ends can be a little early or late)."""
    try:
        return max(-5.0, min(5.0, float(util.setting("skip_offset") or 0)))
    except ValueError:
        return 0.0


def show_seconds():
    """How long a button (not 'by itself') stays; 0 = until the part is over."""
    try:
        return max(0, int(util.setting("skip_show") or 0))
    except ValueError:
        return 0


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
        self.result = {10: "skip", 12: "report"}.get(control, "watch")
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
        if "credits" in parts and meta.get("kind") == "movie":
            import credits
            if any(credits.stingers(meta["tmdb"])):
                parts.pop("credits")                        # 'Stay seated' says it there; no skipping past that scene
        self.parts = parts
        self.player.skip_parts = parts                       # for adding a missing intro at a pause (Marker)
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

    def _next_episode(self):
        """(season, episode) of the next aired episode, or None."""
        meta = self.meta
        if meta.get("kind") != "episode":
            return None
        import tmdb
        season, episode = int(meta["season"]), int(meta["episode"])
        for number, after in ((season, episode), (season + 1, 0)):
            try:
                rows = tmdb.season(meta["tmdb"], number).get("episodes") or []
            except Exception:  # noqa: BLE001
                rows = []
            for row in rows:
                if row.get("episode_number", 0) > after and tmdb.aired(row.get("air_date")):
                    return number, row["episode_number"]
        return None

    def _offer(self, kind, start, end, total):
        to_end = end is None or end >= total - 15
        following = self._next_episode() if kind in ("credits", "preview") and to_end else None
        target = (end if end is not None else total - 1) + offset()
        target = min(target, total - 1)
        auto = mode(kind, self.meta) == AUTO
        names = _names()
        skip_label = T("Volgende aflevering", "Next episode") if following else T("Overslaan", "Skip")
        props = {"kicker": "%s · %s" % (names[kind], _clock(target - start)), "skip": skip_label, "watch": T("Kijken", "Watch"),
                 "sub": T("Druk op OK om over te slaan", "Press OK to skip") if not following else
                 T("S%dE%d staat klaar", "S%dE%d is ready") % following,
                 "report": T("Klopt niet", "Wrong") if util.setting("tidb_key") and util.setting("skip_report").lower() != "false" else ""}
        seconds, shown = wait_seconds(), show_seconds()
        if auto:
            props["sub"] = T("Slaat over in %d sec", "Skips in %d sec") % seconds
        card = dialogs._open(Card, "lucent_skip.xml", props=props)        # noqa: SLF001 - same add-on
        state = {"open": True}

        def watch():
            left, gone = seconds, 0
            while state["open"]:
                if self.monitor.waitForAbort(1) or not self._same():
                    break
                gone += 1
                now = self._now()
                if now is None or now >= target - 1 or now < start - 2:   # the part is over, or you went back
                    break
                if auto:
                    left -= 1
                    if left <= 0:
                        card.result = "skip"
                        break
                    card.setProperty("sub", T("Slaat over in %d sec", "Skips in %d sec") % left)
                elif shown and gone >= shown:
                    break
            if state["open"]:
                card.close()

        worker = threading.Thread(target=watch, daemon=True)
        worker.start()
        card.doModal()
        state["open"] = False
        result = card.result
        if result == "report":
            threading.Thread(target=report, args=(self.meta,), daemon=True).start()
            return
        if result != "skip" or not self._same():
            return
        if following:
            self.player.following = {"season": following[0], "episode": following[1]}
            util.log("skip: %s, on to S%dE%d" % (kind, following[0], following[1]))
            self.player.play_next(self.meta)
            return
        try:
            self.player.seekTime(target)
            util.log("skip: %s skipped to %d" % (kind, target))
        except RuntimeError:
            pass


# ------------------------------------------------------------------ report wrong times, add a missing intro
def _ids(meta):
    body = {"type": "movie" if meta.get("kind") == "movie" else "tv", "tmdb_id": int(meta["tmdb"])}
    if meta.get("kind") == "episode":
        body.update(season=int(meta["season"]), episode=int(meta["episode"]))
    return body


def _post(path, body):
    import json
    import urllib.error
    import urllib.request
    request = urllib.request.Request(tidb.API.rsplit("/", 1)[0] + path, data=json.dumps(body).encode("utf-8"), method="POST",
                                     headers={"Content-Type": "application/json", "Accept": "application/json", "User-Agent": "Lucent-Video",
                                              "Authorization": "Bearer %s" % util.setting("tidb_key")})
    try:
        with urllib.request.urlopen(request, timeout=10) as response:
            response.read()
            return True, ""
    except urllib.error.HTTPError as error:
        try:
            reason = json.loads(error.read().decode("utf-8")).get("error") or ""
        except Exception:  # noqa: BLE001
            reason = ""
        return False, reason or "HTTP %s" % error.code
    except Exception as error:      # noqa: BLE001
        return False, type(error).__name__


def report(meta):
    """'Wrong': tells TheIntroDB the times of this film or episode are not right (needs your own key)."""
    ok, reason = _post("/flag-vote", _ids(meta))
    if ok or "already" in reason.lower():
        util.notify(T("Gemeld bij TheIntroDB. Dank je!", "Reported to TheIntroDB. Thank you!"))
    else:
        util.notify(T("Melden lukte niet (%s)", "Reporting failed (%s)") % reason)


class Marker:
    """Adding a missing intro to TheIntroDB with a button (action=intro_mark, for the player screen): press it where the
    intro begins and again where it ends; Lucent asks to be sure and sends it. Pausing never does anything here.
    Needs your own TheIntroDB key; only when TheIntroDB has no intro for this video yet."""

    def __init__(self):
        self.start, self.done = None, False

    def reset(self):
        self.start, self.done = None, False

    def press(self, player, meta, parts):
        if not meta or meta.get("kind") not in ("movie", "episode"):
            return util.notify(T("Alleen bij films en afleveringen van Lucent", "Only with films and episodes of Lucent"))
        if not util.setting("tidb_key"):
            return util.notify(T("Vul eerst je TheIntroDB-sleutel in (Intro overslaan)", "Fill in your TheIntroDB key first (Skip intro)"))
        if (parts or {}).get("intro"):
            return util.notify(T("TheIntroDB kent deze intro al. Kloppen de tijden niet? Druk op Klopt niet.",
                                 "TheIntroDB knows this intro already. Times wrong? Press Wrong."))
        if self.done:
            return util.notify(T("De intro van deze video is al aangemeld", "The intro of this video was sent already"))
        try:
            now, total = player.getTime(), player.getTotalTime()
        except RuntimeError:
            return
        if self.start is None or now <= self.start:
            self.start = now
            return util.notify(T("Begin van de intro: %s. Druk nog een keer waar hij eindigt.", "Start of the intro: %s. Press again where it ends.") % _clock(now))
        length = now - self.start
        if not 5 <= length <= 200:
            self.start = None
            return util.notify(T("Een intro duurt 5 tot 200 seconden. Begin opnieuw.", "An intro lasts 5 to 200 seconds. Start again."))
        if not _ask(T("Intro aanmelden", "Add the intro"), T("Van %s tot %s (%d sec) naar TheIntroDB sturen?", "Send %s to %s (%d sec) to TheIntroDB?")
                    % (_clock(self.start), _clock(now), length), T("Versturen", "Send")):
            self.start = None
            return
        body = dict(_ids(meta), segment="intro", start_sec=round(self.start, 1), end_sec=round(now, 1))
        if 300 <= total <= 21600:
            body["video_duration_ms"] = int(total * 1000)
        ok, reason = _post("/submit", body)
        util.notify(T("Intro aangemeld bij TheIntroDB. Dank je!", "Intro sent to TheIntroDB. Thank you!") if ok
                    else T("Aanmelden lukte niet (%s)", "Sending failed (%s)") % reason)
        self.done, self.start = ok, None


def _ask(kicker, text, yes):
    card = dialogs._open(Card, "lucent_skip.xml", props={"kicker": kicker, "sub": text, "skip": yes, "watch": T("Nee", "No"), "report": ""})  # noqa: SLF001
    card.doModal()
    result = card.result
    del card
    return result == "skip"


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
    try:
        own.setSetting("skip_offset", str(max(-5, min(5, int(old.getSetting("skip_offset") or 0)))))
        own.setSetting("skip_show", str(max(0, int(old.getSetting("button_display_seconds") or 0))))
    except ValueError:
        pass
    own.setSetting("skip_report", "false" if (old.getSetting("hide_report_button") or "").lower() == "true" else "true")
    key = (old.getSetting("introdb_api_key") or "").strip()
    if key and not util.setting("tidb_key"):
        own.setSetting("tidb_key", key)
    util.log("skip: settings of the TheIntroDB add-on taken over")
