# -*- coding: utf-8 -*-
"""Back after a long pause: when a film or episode was paused for 5 minutes or longer and you press play, the video
stays paused for a moment and the last lines that were spoken appear in the middle of the screen (from the subtitle
file), with 'Go on' and '30 sec back'. Without a subtitle file the screen shows where you were.

Only at that moment: nothing runs while the video plays or while it is paused."""
import os
import re
import time

import xbmc
import xbmcvfs

import dialogs
import util

T = util.T
AFTER = 300            # seconds paused before the recap is shown
LINES = 3
BACK = 30


def wanted():
    return util.setting("pauserecap_on").lower() != "false"


def _subtitle_file(started):
    """The subtitle file of this video: the one Lucent loaded, else the newest .srt Kodi saved since the start."""
    path = util.HOME.getProperty("lucent.subs.file")
    if path and os.path.isfile(path) and path.lower().endswith(".srt"):
        return path
    best, best_time = "", started
    for root in ("special://temp/", "special://temp/subtitles/", "special://temp/lucent_subs/"):
        folder = xbmcvfs.translatePath(root)
        try:
            names = os.listdir(folder)
        except OSError:
            continue
        for name in names:
            full = os.path.join(folder, name)
            if name.lower().endswith(".srt"):
                try:
                    changed = os.path.getmtime(full)
                except OSError:
                    continue
                if changed >= best_time:
                    best, best_time = full, changed
    return best


def last_lines(position, started):
    """The last lines spoken before `position` (seconds), oldest first; [] without a usable subtitle."""
    path = _subtitle_file(started)
    if not path:
        return []
    import dualsubs
    try:
        cues = dualsubs.parse(dualsubs._read(path))      # noqa: SLF001 - same add-on
    except OSError:
        return []
    offset = 0.0
    if path == util.HOME.getProperty("lucent.subs.file"):
        try:
            import subs_extra
            offset = subs_extra.get_offset()
        except Exception:  # noqa: BLE001
            offset = 0.0
    now = (position - offset) * 1000
    said = [text for start, _end, text in cues if start <= now]
    out = []
    for text in said[-LINES:]:
        clean = " ".join(re.sub(r"<[^>]+>|\{[^}]*\}", "", text).split())
        if clean:
            out.append(clean)
    return out


def _clock(seconds):
    seconds = int(seconds)
    return "%d:%02d:%02d" % (seconds // 3600, seconds // 60 % 60, seconds % 60) if seconds >= 3600 else "%d:%02d" % (seconds // 60, seconds % 60)


def _ago(seconds):
    minutes = int(seconds // 60)
    if minutes >= 120:
        return T("%d uur geleden", "%d hours ago") % (minutes // 60)
    if minutes >= 60:
        return T("een uur geleden", "an hour ago")
    return T("%d minuten geleden", "%d minutes ago") % minutes


def props(meta, position, paused_for, lines):
    if meta and meta.get("kind") == "episode":
        where = "%s · S%dE%d · %s" % (meta.get("show") or "", int(meta["season"]), int(meta["episode"]), _clock(position))
    else:
        where = "%s · %s" % ((meta or {}).get("title") or xbmc.getInfoLabel("VideoPlayer.Title"), _clock(position))
    out = {"kicker": T("Gepauzeerd · %s", "Paused · %s") % _ago(paused_for), "head": T("Je was hier gebleven", "This is where you were"),
           "where": where, "go": T("Verder", "Go on"), "back": T("%d sec terug", "%d sec back") % BACK,
           "fanart": (meta or {}).get("fanart") or ""}
    padded = [""] * (LINES - len(lines)) + ["“%s”" % line for line in lines]
    for number, line in enumerate(padded, start=1):
        out["line%d" % number] = line
    out["nolines"] = "" if lines else "1"
    return out


class Recap(dialogs.Base):
    def onInit(self):
        self.fill(self.args["props"])
        self.setFocusId(10)

    def onClick(self, control):
        self.result = "back" if control == 11 else "go"
        self.close()

    def onAction(self, action):
        if action.getId() in dialogs.BACK + (12, 68, 79, 229):           # Back or Play: go on
            self.result = "go"
            self.close()


def show(player, meta, paused_for, started):
    """Called when play is pressed after a long pause, with the video paused again. Returns when it plays again."""
    try:
        position = player.getTime()
    except RuntimeError:
        return
    dialog = dialogs._open(Recap, "lucent_pauserecap.xml", props=props(meta, position, paused_for, last_lines(position, started)))  # noqa: SLF001
    dialog.doModal()
    result = dialog.result
    del dialog
    try:
        if result == "back":
            player.seekTime(max(0.0, position - BACK))
        if xbmc.getCondVisibility("Player.Paused"):
            player.pause()
    except RuntimeError:
        pass
    util.log("pause recap: %s" % (result or "go"))
    return time.time()
