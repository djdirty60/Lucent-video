# -*- coding: utf-8 -*-
"""Marathon planner: you are behind with a series and a new season is coming. Lucent counts what is left (episodes and
time), how many a night you need to be ready in time, and on which day you are done at the pace you choose.

    RunPlugin(plugin://plugin.video.lucent/?action=marathon&tmdb=<id>)   the card for one series
    action=list&name=marathon                                             series you are behind with, new season coming

Only reads (TMDB and your history), when you open it: nothing runs in the background."""
import datetime
import math

import dialogs
import tmdb
import tracker
import util

T = util.T
PACE = "marathon_pace.json"            # {tmdb id: episodes a night}
MONTHS = ("januari", "februari", "maart", "april", "mei", "juni", "juli", "augustus", "september", "oktober", "november", "december")
MONTHS_EN = ("January", "February", "March", "April", "May", "June", "July", "August", "September", "October", "November", "December")


def _day(date):
    if util.NL:
        return "%d %s" % (date.day, MONTHS[date.month - 1])
    return "%s %d" % (MONTHS_EN[date.month - 1], date.day)


def _premiere(raw, today):
    """(season number, date) of the coming new season, or (None, None)."""
    coming = raw.get("next_episode_to_air") or {}
    if coming.get("episode_number") == 1 and (coming.get("air_date") or "") >= today:
        return coming.get("season_number"), coming["air_date"]
    later = [s for s in raw.get("seasons") or [] if (s.get("air_date") or "") > today and s.get("season_number", 0) > 0]
    return (later[0]["season_number"], later[0]["air_date"]) if later else (None, None)


def plan(tmdb_id, pace=None):
    """Everything the card shows, or None when there is nothing to catch up."""
    today = datetime.date.today()
    raw = tmdb.show_here(tmdb_id)
    season_new, date_new = _premiere(raw, today.isoformat())
    watched = tracker.watched_episodes().get(str(tmdb_id), {})
    last = max(watched) if watched else (0, 0)
    default_runtime = (raw.get("episode_run_time") or [45])[0] or 45
    episodes = []
    for entry in raw.get("seasons") or []:
        number = entry.get("season_number", 0)
        if number <= 0 or (season_new and number >= season_new):
            continue
        for row in tmdb.season(tmdb_id, number).get("episodes") or []:
            if tmdb.aired(row.get("air_date")):
                episodes.append((number, row["episode_number"], row.get("runtime") or default_runtime))
    left = [e for e in episodes if (e[0], e[1]) > last]
    if not left:
        return None
    minutes = sum(e[2] for e in left)
    premiere = datetime.date.fromisoformat(date_new) if date_new else None
    nights = (premiere - today).days if premiere else None           # tonight up to the evening before the premiere
    needed = max(1, math.ceil(len(left) / nights)) if nights and nights > 0 else None
    pace = int(pace or util.load_json(PACE, {}).get(str(tmdb_id)) or needed or 2)
    finish = today + datetime.timedelta(days=math.ceil(len(left) / pace) - 1)
    return {"tmdb": str(tmdb_id), "title": raw.get("name") or "", "fanart": tmdb.image(raw.get("backdrop_path"), "w1280"),
            "left": len(left), "minutes": minutes, "done": len(episodes) - len(left), "total": len(episodes),
            "pace": pace, "needed": needed, "next": left[0], "last": last if watched else None,
            "season_new": season_new, "premiere": premiere, "finish": finish,
            "spare": (premiere - finish).days - 1 if premiere else None}


def _hours(minutes):
    return (T("%d u", "%d h") % round(minutes / 60.0)) if minutes >= 90 else ("%d min" % minutes)


def props(p):
    if p["premiere"]:
        head = T("Op tijd voor seizoen %d", "In time for season %d") % p["season_new"]
        sub = T("%s · seizoen %d begint op %s", "%s · season %d starts on %s") % (p["title"], p["season_new"], _day(p["premiere"]))
    else:
        head = T("Bijkomen met %s", "Catching up with %s") % p["title"]
        sub = T("Nog geen datum voor een nieuw seizoen", "No date for a new season yet")
    where = (T("Je bent bij S%dE%d", "You are at S%dE%d") % p["last"]) if p["last"] else T("Je bent nog niet begonnen", "You have not started yet")
    if p["premiere"] and p["spare"] is not None and p["spare"] >= 0:
        end = T("Klaar op %s · %d dagen speling", "Done on %s · %d days to spare") % (_day(p["finish"]), p["spare"])
        late = ""
    elif p["premiere"]:
        end = T("Klaar op %s", "Done on %s") % _day(p["finish"])
        late = "1"
    else:
        end = T("Klaar op %s", "Done on %s") % _day(p["finish"])
        late = ""
    return {"kicker": T("Marathonplanner", "Marathon planner"), "head": head, "sub": sub, "fanart": p["fanart"],
            "left": str(p["left"]), "left_label": T("afleveringen te gaan", "episodes to go"),
            "time": _hours(p["minutes"]), "time_label": T("kijktijd", "watching time"),
            "pace": str(p["pace"]), "pace_label": T("per avond", "a night"),
            "where": where, "end": end, "late": late,
            "next": T("Volgende: S%dE%d", "Next: S%dE%d") % p["next"][:2],
            "p1": T("1 per avond", "1 a night"), "p2": T("2 per avond", "2 a night"), "p3": T("3 per avond", "3 a night"),
            "close": T("Sluiten", "Close")}


class Card(dialogs.Base):
    def onInit(self):
        self.show_plan(self.args["plan"])
        self.setFocusId(10)

    def show_plan(self, p):
        self.plan = p
        self.fill(props(p))
        for control, pace in ((11, 1), (12, 2), (13, 3)):
            self.setProperty("on%d" % pace, "1" if p["pace"] == pace else "")
        self.getControl(50).setPercent(p["done"] * 100.0 / p["total"] if p["total"] else 0)

    def onClick(self, control):
        if control == 10:
            self.result = "play"
            self.close()
        elif control in (11, 12, 13):
            pace = control - 10
            saved = util.load_json(PACE, {})
            saved[self.plan["tmdb"]] = pace
            util.save_json(PACE, saved)
            fresh = plan(self.plan["tmdb"], pace)
            if fresh:
                self.show_plan(fresh)
        else:
            self.close()


def open_card(tmdb_id):
    try:
        p = plan(tmdb_id)
    except util.HttpError as error:
        util.log("marathon: %s" % error)
        return util.notify(T("De gegevens kunnen nu niet worden opgehaald", "The details cannot be loaded now"))
    if not p:
        return util.notify(T("Je bent helemaal bij met deze serie", "You are all caught up with this series"))
    dialog = dialogs._open(Card, "lucent_marathon.xml", plan=p)          # noqa: SLF001 - same add-on
    dialog.doModal()
    result = dialog.result
    del dialog
    if result == "play":
        import xbmc
        season, episode = p["next"][:2]
        xbmc.executebuiltin("RunPlugin(%s)" % util.url_for(action="play", type="episode", tmdb=p["tmdb"], season=season, episode=episode))


def titles():
    """Series with an announced new season that you are behind with (from 'New season announced')."""
    import newseason
    out = []
    items = util.load_json(newseason.FILE, {}).get("items") or []
    metas = {m["tmdb"]: m for m in tmdb.many([("tvshow", e["show"]) for e in items])}
    for entry in items:
        meta = metas.get(str(entry["show"]))
        if not meta:
            continue
        try:
            p = plan(entry["show"])
        except util.HttpError:
            continue
        if p:
            out.append(dict(meta, plot=props(p)["head"] + ". " + T("Nog %d afleveringen, %s per avond: %s.", "%d episodes to go, %s a night: %s.")
                            % (p["left"], p["pace"], props(p)["end"].lower())))
    return out
