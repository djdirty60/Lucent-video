# -*- coding: utf-8 -*-
"""Usenet statistics: per day, per provider and per indexer. Only counts (no titles, no addresses with keys, nothing
that says what was watched). One small file in the add-on's own folder; days older than KEEP_DAYS go by themselves and
the whole file stays below MAX_BYTES. The statistics screen (Settings > Usenet) can wipe it.

Providers (the service: Lucent's own reader and vfs.lucent): bytes, articles, missing articles, errors, how long the
provider takes to start answering, and the time something was being fetched (for the speed).
Indexers (the plugin): searches with their answer time, results, NZBs fetched, and whether the NZB could be played.
"""
import os
import threading
import time

import util

FILE = "usenet_stats.json"
KEEP_DAYS = 400
MAX_BYTES = 2 * 1024 * 1024
FLUSH_EVERY = 60

P_FIELDS = ("b", "a", "m", "e", "ln", "ls", "busy")       # bytes, articles, missing, errors, latency count/sum, busy s
I_FIELDS = ("s", "ss", "se", "r", "g", "ok", "bad")       # searches, their seconds, failed searches, results, NZBs,
                                                          # NZB played / could not be played

_lock = threading.Lock()
_pending = {}                      # provider host -> {field: value}, written by flush()
_state = {"flushed": time.time()}


def _day(at=None):
    return time.strftime("%Y-%m-%d", time.localtime(at or time.time()))


def _lock_path():
    return os.path.join(util.PROFILE, FILE + ".lock")


def _update(change):
    """Read, change and write the file, with a lock file: the plugin and the service both write to it."""
    lock = _lock_path()
    end = time.time() + 3
    have = False
    while time.time() < end:
        try:
            os.makedirs(util.PROFILE, exist_ok=True)
            os.close(os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY))
            have = True
            break
        except FileExistsError:
            try:
                if time.time() - os.path.getmtime(lock) > 15:     # left behind by a crash
                    os.remove(lock)
                    continue
            except OSError:
                pass
            time.sleep(0.05)
        except OSError:
            break
    try:
        data = util.load_json(FILE, {})
        if not isinstance(data, dict) or data.get("v") != 1:
            data = {"v": 1, "days": {}}
        change(data["days"])
        _trim(data)
        util.save_json(FILE, data)
    except Exception as error:          # noqa: BLE001 - statistics never break playing
        util.log("usenet: statistics not saved (%s)" % type(error).__name__)
    finally:
        if have:
            try:
                os.remove(lock)
            except OSError:
                pass


def _trim(data):
    days = data["days"]
    oldest = _day(time.time() - KEEP_DAYS * 86400)
    for day in [d for d in days if d < oldest]:
        del days[day]
    import json
    while len(days) > 1 and len(json.dumps(data)) > MAX_BYTES:
        del days[min(days)]


def _add(bucket, name, values):
    entry = bucket.setdefault(name, {})
    for field, value in values.items():
        if value:
            entry[field] = round(entry.get(field, 0) + value, 3)


# ------------------------------------------------------------------ providers (service)
def provider(host, **values):
    """Count for a provider; kept in memory and written once a minute (flush) so playing never waits for the disk."""
    if not host:
        return
    with _lock:
        entry = _pending.setdefault(host, {})
        for field, value in values.items():
            entry[field] = entry.get(field, 0) + value


def flush(force=False):
    if not force and time.time() - _state["flushed"] < FLUSH_EVERY:
        return
    _state["flushed"] = time.time()
    with _lock:
        pending = dict(_pending)
        _pending.clear()
    if not pending:
        return
    day = _day()
    _update(lambda days: [_add(days.setdefault(day, {}).setdefault("p", {}), host, values) for host, values in pending.items()])


# ------------------------------------------------------------------ indexers (plugin)
def indexer(name, **values):
    if not name:
        return
    day = _day()
    _update(lambda days: _add(days.setdefault(day, {}).setdefault("i", {}), name, values))


# ------------------------------------------------------------------ reading
def clear():
    with _lock:
        _pending.clear()
    _update(lambda days: days.clear())


def size():
    try:
        return os.path.getsize(os.path.join(util.PROFILE, FILE))
    except OSError:
        return 0


def summary(days_back=30):
    """{'days': [(day, bytes)] oldest first, 'providers': {host: totals}, 'indexers': {name: totals}} for the last
    days_back days (0: everything that is kept)."""
    flush(force=True)
    days = util.load_json(FILE, {}).get("days") or {}
    if days_back:
        first = _day(time.time() - (days_back - 1) * 86400)
        chosen = sorted(d for d in days if d >= first)
        span = [_day(time.time() - n * 86400) for n in range(days_back - 1, -1, -1)]
    else:
        chosen = sorted(days)
        span = []
        if chosen:
            start = time.mktime(time.strptime(chosen[0], "%Y-%m-%d"))
            n = 0
            while True:
                day = _day(start + n * 86400 + 3600)
                span.append(day)
                if day >= _day() or n > KEEP_DAYS + 2:
                    break
                n += 1
    providers, indexers = {}, {}
    for day in chosen:
        for host, values in (days[day].get("p") or {}).items():
            _add(providers, host, values)
        for name, values in (days[day].get("i") or {}).items():
            _add(indexers, name, values)
    per_day = [(day, sum(v.get("b", 0) for v in (days.get(day, {}).get("p") or {}).values())) for day in span]
    return {"days": per_day, "providers": providers, "indexers": indexers}
