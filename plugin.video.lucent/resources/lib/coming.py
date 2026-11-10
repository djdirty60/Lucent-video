# -*- coding: utf-8 -*-
"""'Coming soon from your watchlist': films and series on your watchlist that you cannot watch yet (see hide.py),
with the day they come out. On that day a message tells you it is there.

    plugin://plugin.video.lucent/?action=list&name=coming_watchlist[&widget=1]"""
import datetime
import time

import hide
import tmdb
import tracker
import util

T = util.T
TOLD = "coming_told.json"          # {"movie:123": day it was told}
CHECK_EVERY = 6 * 3600
MONTHS_NL = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]
_last = {"check": 0.0}


def _day(text):
    try:
        day = datetime.date.fromisoformat(str(text)[:10])
    except ValueError:
        return ""
    months = MONTHS_NL if util.NL else MONTHS_EN
    return "%d %s %d" % (day.day, months[day.month - 1], day.year)


def _watchlist():
    return tmdb.many(tracker.pairs("watchlist_movie") + tracker.pairs("watchlist_tv"))


def titles():
    """Not out yet, soonest first; titles without any date last."""
    out = [m for m in _watchlist() if not hide.can_watch(m)]
    for meta in out:
        when = meta.get("digital")
        meta["reason"] = (T("Te kijken vanaf %s", "Out on %s") % _day(when)) if when else T("Nog geen datum", "No date yet")
    out.sort(key=lambda m: m.get("digital") or "9999")
    return out


def due():
    return time.time() - _last["check"] > CHECK_EVERY


def check():
    """Service, every six hours: a message for each watchlist title that came out since it was on the list."""
    _last["check"] = time.time()
    if util.setting("only_digital").lower() == "false":
        return
    told = util.load_json(TOLD, {})
    today = datetime.date.today().isoformat()
    first = not told                               # the first time: remember what is out already, tell nothing
    new = []
    for meta in _watchlist():
        key = "%s:%s" % (meta["kind"], meta["tmdb"])
        if key in told:
            continue
        if hide.can_watch(meta) and meta.get("digital"):
            told[key] = today
            if not first and meta["digital"] >= (datetime.date.today() - datetime.timedelta(days=14)).isoformat():
                new.append(meta["title"])
        elif not first:
            continue
    if first:
        told["_since"] = today
    util.save_json(TOLD, told)
    for title in new[:3]:
        util.notify(T("Nu te kijken: %s", "Out now: %s") % title)
    if len(new) > 3:
        util.notify(T("En nog %d titels van je kijklijst zijn uit", "And %d more titles from your watchlist are out") % (len(new) - 3))
