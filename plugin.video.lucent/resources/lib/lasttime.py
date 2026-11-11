# -*- coding: utf-8 -*-
"""'Previous time on…': when you start an episode of a series you have not watched for three weeks or more, a short
summary of the two episodes before it appears at the bottom for 12 seconds (or until you press OK), while Lucent
already looks for sources. Only what came before: no spoilers for what is still to come."""
import datetime
import threading

import xbmc

import dialogs
import tmdb
import tracker
import util

T = util.T
AFTER_DAYS = 21
SECONDS = 12


def wanted():
    return util.setting("lasttime_on").lower() != "false"


def _days_since(when):
    try:
        day = datetime.date.fromisoformat(str(when)[:10])
    except ValueError:
        return 0
    return (datetime.date.today() - day).days


def _short(text, limit=260):
    text = " ".join((text or "").split())
    if len(text) <= limit:
        return text
    cut = text[:limit]
    end = max(cut.rfind(". "), cut.rfind("! "), cut.rfind("? "))
    return cut[:end + 1] if end > 80 else cut.rsplit(" ", 1)[0] + "…"


def _before(tmdb_id, season, episode, count=2):
    """The aired episodes just before season/episode (also from the season before), oldest first."""
    rows = []
    for number in (season - 1, season):
        if number < 1:
            continue
        rows += [r for r in tmdb.season(tmdb_id, number).get("episodes") or [] if tmdb.aired(r.get("air_date"))]
    rows = [r for r in rows if (r["season_number"], r["episode_number"]) < (season, episode)]
    return rows[-count:]


def text_for(params):
    """(kicker, summary, next line) when a recap fits this start, else None."""
    if params.get("type") != "episode" or params.get("autoplay") == "1" or not wanted():
        return None
    try:
        season, episode = int(params["season"]), int(params["episode"])
        watched = tracker.watched_episodes().get(str(params["tmdb"]), {})
        if not watched or (season, episode) <= (1, 1):
            return None
        if tracker.resume_percent("episode", params["tmdb"], season, episode):
            return None                         # half watched: the resume question has its own 'previous time'
        days = _days_since(max(watched.values()))
        if days < AFTER_DAYS:
            return None
        rows = [r for r in _before(params["tmdb"], season, episode) if r.get("overview")]
    except (util.HttpError, ValueError, KeyError) as error:
        util.log("previous time: %s" % type(error).__name__)
        return None
    if not rows:
        return None
    weeks = days // 7
    when = (T("%d weken geleden", "%d weeks ago") % weeks) if weeks < 9 else (T("%d maanden geleden", "%d months ago") % (days // 30))
    summary = "[CR]".join("S%dE%d · %s" % (r["season_number"], r["episode_number"], _short(r["overview"])) for r in rows)
    return T("Vorige keer · %s", "Previous time · %s") % when, summary


class LastTime(dialogs.Base):
    def onInit(self):
        self.fill(self.args["props"])
        self.left = SECONDS
        self._tick()
        threading.Thread(target=self._count, daemon=True).start()

    def _tick(self):
        self.setProperty("count", str(self.left))
        self.setProperty("ring", "lucent/ring_%03d.png" % (int(round(self.left * 20.0 / SECONDS)) * 5))

    def _count(self):
        monitor = xbmc.Monitor()
        while self.left > 0 and not self.result:
            if monitor.waitForAbort(1):
                break
            self.left -= 1
            if not self.result:
                self._tick()
        if not self.result:
            self.result = "time"
            self.close()

    def onClick(self, _control):
        self.result = "now"
        self.close()

    def onAction(self, action):
        if action.getId() in dialogs.BACK:
            self.result = "skip"
            self.close()


def show(params, meta=None):
    """Shows the recap when it fits; returns when it is closed. Never stops playback."""
    found = text_for(params)
    if not found:
        return
    kicker, summary = found
    meta = meta or {}
    head = meta.get("show") or ""
    if not head:
        try:
            head = (tmdb.details("tvshow", params["tmdb"]) or {}).get("title") or ""
        except util.HttpError:
            head = ""
    nxt = "S%sE%s" % (params["season"], params["episode"]) + ((" · " + meta["title"]) if meta.get("title") else "")
    props = {"head": head, "next": T("Volgende: %s", "Next: %s") % nxt, "kicker": kicker, "summary": summary,
             "fanart": meta.get("fanart") or meta.get("thumb") or "", "start": T("Nu starten", "Start now")}
    dialog = dialogs._open(LastTime, "lucent_lasttime.xml", props=props)       # noqa: SLF001 - same add-on
    dialog.doModal()
    del dialog
