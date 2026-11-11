# -*- coding: utf-8 -*-
"""'Coming soon from your watchlist': films and series on your watchlist that you cannot watch yet (see hide.py),
with the day they come out.

    plugin://plugin.video.lucent/?action=list&name=coming_watchlist[&widget=1]"""
import datetime

import hide
import tmdb
import tracker
import util

T = util.T
MONTHS_NL = ["jan", "feb", "mrt", "apr", "mei", "jun", "jul", "aug", "sep", "okt", "nov", "dec"]
MONTHS_EN = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


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

