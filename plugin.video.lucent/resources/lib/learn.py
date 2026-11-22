# -*- coding: utf-8 -*-
"""Self-learning choice of sources: this device remembers which releases played well and which failed.

A 'release' is its group, resolution and service (for example FLUX · 4K · TORBOX). After a few tries a release that
keeps failing moves down and one that keeps playing well moves up, but only within its own resolution: a 1080p that
plays well never jumps above 4K. Kept per device (not per profile), because it is about what this device can play.
"""
import time

import util

ranking = util.lazy("ranking")

FILE = "learned.json"
GOOD_AFTER = 120          # seconds of playing that count as 'played well'
KEEP = 400                # releases remembered at most


def key(source):
    group = (ranking.release_group(source) or "").lower()
    if not group:
        return ""
    return "%s|%s|%s" % (group, source.get("resolution") or "", (source.get("scraper") or source.get("service") or "").lower())


def _save(data):
    if len(data) > KEEP:
        data = dict(sorted(data.items(), key=lambda kv: kv[1].get("at", 0))[-KEEP:])
    util.save_json(FILE, data)


def record(source_key, good):
    if not source_key:
        return
    data = util.load_json(FILE, {})
    entry = data.setdefault(source_key, {"good": 0, "bad": 0})
    entry["good" if good else "bad"] += 1
    entry["at"] = time.time()
    _save(data)


def score(source_key, data):
    entry = data.get(source_key)
    if not entry:
        return 0
    return entry.get("good", 0) - 2 * entry.get("bad", 0)


def adjust(sources):
    """Same order of resolutions as the ranking gave; inside each, good releases first and failing ones last."""
    if not util.flag("learn") or not sources:
        return sources
    data = util.load_json(FILE, {})
    rates, health = _usenet_hints(sources)
    if not data and not rates and not health:
        return sources
    groups = {}
    for source in sources:
        groups.setdefault(source.get("resolution"), len(groups))

    weak = set()

    def bucket(source):
        value = score(key(source), data)
        place = 0 if value >= 2 else (2 if value <= -2 else 1)
        job = source.get("resolve") or {}
        if job.get("scraper") == "Usenet":
            known = health.get(id(source))
            if known and known[0] < known[1] * GAPPY:
                place = 2                       # this release itself is known to miss articles
            elif not known and rates.get(job.get("indexer")) is not None and rates[job["indexer"]] < WEAK_INDEXER:
                weak.add(id(source))            # only its indexer often failed here: a small step down, see below
        return place
    places = {id(s): bucket(s) for s in sources}
    # failing releases go to the end of their resolution; good ones move up only within their own quality class
    # (same picture and source), so a release that played well never jumps over a better one from your profile
    order = {id(s): n for n, s in enumerate(sources)}
    kept = sorted(sources, key=lambda s: (groups[s.get("resolution")], places[id(s)] == 2, order[id(s)]))
    kept = ranking.within_class(kept, lambda s: places[id(s)] == 0, apart=lambda s: places[id(s)] == 2)
    if not weak:
        return kept
    # an indexer whose NZBs often failed here (taken down, locked) only loses from equal sources elsewhere: never a whole
    # indexer under the rest of the list (before 1.0.0~rc1.17 all its releases went below even the red-flagged ones)
    return ranking.within_class(kept, lambda s: id(s) not in weak, apart=lambda s: places[id(s)] == 2)


WEAK_INDEXER = 0.80     # an indexer whose NZBs played less often than this (at least 5 times) moves its releases down
GAPPY = 0.98            # a release known (full check) to miss more than 2% of its articles goes to the end of its resolution


def _usenet_hints(sources):
    """({indexer: share played}, {id(source): (present, total)}) for the Usenet sources; empty when there are none."""
    if not any((s.get("resolve") or {}).get("scraper") == "Usenet" for s in sources):
        return {}, {}
    try:
        from nzb import source as nzb_source, stats
        rates = stats.indexer_rates()
        health = {}
        for source in sources:
            job = source.get("resolve") or {}
            if job.get("scraper") == "Usenet" and job.get("link"):
                known = nzb_source.health_known(job)
                if known:
                    health[id(source)] = known
        return rates, health
    except Exception as error:          # noqa: BLE001 - learning is extra; the plain order always stays
        util.log("learn: Usenet hints not read (%s)" % type(error).__name__)
        return {}, {}


def forget():
    util.save_json(FILE, {})
    util.notify(util.T("Geleerde bronnen gewist", "Learned sources cleared"))
