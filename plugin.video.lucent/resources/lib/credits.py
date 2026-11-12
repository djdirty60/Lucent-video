# -*- coding: utf-8 -*-
"""At the credits of a film.

- 'Stay seated': TMDB knows which films have a scene during or after the credits (keywords duringcreditsstinger and
  aftercreditsstinger). When the credits begin, a card says so, with 'Keep watching' and 'Stop'.
- Homey: without such a scene Lucent sends the tag 'aftiteling' when the credits begin, so a Flow can bring the
  lights up slowly, as in a cinema. With such a scene the lights stay down until the film ends ('einde').

Where the credits begin comes from TheIntroDB (no key needed). One small lookup at the start of the film; after that
the thread only sleeps until the credits: nothing runs while you watch."""
import xbmc

import dialogs
import tmdb
import util

T = util.T
DURING, AFTER = 179431, 179430                 # TMDB keyword ids
MONTH = 30 * 86400
SHOW_FOR = 15                                  # seconds the card stays


def wanted():
    return util.setting("credits_on").lower() != "false"


def stingers(tmdb_id):
    """(scene during the credits, scene after the credits) from TMDB's keywords."""
    try:
        rows = tmdb.get("movie/%s/keywords" % tmdb_id, ttl=MONTH).get("keywords") or []
    except util.HttpError as error:
        util.log("credits keywords: %s" % error)
        return False, False
    ids = {row.get("id") for row in rows}
    names = {(row.get("name") or "").lower().replace(" ", "") for row in rows}
    return (DURING in ids or "duringcreditsstinger" in names), (AFTER in ids or "aftercreditsstinger" in names)


def credits_start(meta, total):
    """Second at which the credits begin (TheIntroDB), or None."""
    import tidb
    for start, _end in tidb.segments(meta, total).get("credits") or []:
        if total * 0.5 < start < total:                   # a credits block in the second half of this video
            return start
    return None


def _texts(during, after, lights):
    if during and after:
        head = T("Blijf zitten: er komt nog meer", "Stay seated: there is more to come")
        sub = T("Deze film heeft een scène tijdens én na de aftiteling.", "This film has a scene during and after the credits.")
    elif during:
        head = T("Blijf zitten: er komt nog een scène", "Stay seated: there is one more scene")
        sub = T("Deze film heeft een scène tijdens de aftiteling.", "This film has a scene during the credits.")
    else:
        head = T("Blijf zitten: er komt nog een scène", "Stay seated: there is one more scene")
        sub = T("Deze film heeft een scène na de aftiteling.", "This film has a scene after the credits.")
    if lights:
        sub += " " + T("De lampen blijven gedimd tot de film echt is afgelopen.", "The lights stay dimmed until the film really ends.")
    return {"kicker": T("Na de aftiteling", "After the credits"), "head": head, "sub": sub,
            "stay": T("Blijven kijken", "Keep watching"), "stop": T("Stoppen", "Stop")}


class Card(dialogs.Base):
    def onInit(self):
        self.fill(self.args["texts"])
        self.setFocusId(10)

    def onClick(self, control):
        self.result = "stop" if control == 11 else "stay"
        self.close()


def _show(texts):
    dialog = dialogs._open(Card, "lucent_credits.xml", texts=texts)       # noqa: SLF001 - same add-on
    import threading
    timer = threading.Timer(SHOW_FOR, dialog.close)
    timer.start()
    dialog.doModal()
    timer.cancel()
    result = dialog.result
    del dialog
    return result


class Watch:
    """One per film: sleeps until the credits, then does its work once. Stops when the video changes."""

    def __init__(self, player, meta, number):
        self.player, self.meta, self.number = player, meta, number

    def _same(self):
        return self.number == self.player.video_number and self.player.isPlayingVideo()

    def run(self):
        meta = self.meta
        if not meta or meta.get("kind") != "movie" or not meta.get("tmdb"):
            return
        import homey
        if not wanted() and not homey.address():
            return
        monitor = xbmc.Monitor()
        total = 0
        for _ in range(30):
            try:
                total = self.player.getTotalTime() or 0
            except RuntimeError:
                total = 0
            if total or monitor.waitForAbort(1):
                break
        if total < 600:
            return
        during, after = stingers(meta["tmdb"]) if wanted() else (False, False)
        start = credits_start(meta, total)
        if start is None:
            util.log("credits: TheIntroDB does not know where the credits of %s begin" % meta["tmdb"])
            return
        util.log("credits at %ds, scene during %s, after %s" % (start, during, after))
        while True:
            if not self._same():
                return
            try:
                now = self.player.getTime()
            except RuntimeError:
                return
            if now >= start - 1:
                break
            if monitor.waitForAbort(min(60.0, max(1.0, start - now))):
                return
        if now > start + 120:                             # jumped past the credits: not at the very end
            return
        cinema = getattr(self.player, "cinema", False)
        if during or after:
            if wanted():
                if _show(_texts(during, after, cinema)) == "stop" and self._same():
                    self.player.stop()
        elif cinema:
            homey.signal("aftiteling")
            util.log("Homey: aftiteling sent")
