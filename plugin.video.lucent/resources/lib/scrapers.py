# -*- coding: utf-8 -*-
"""All sources of streams. A new scraper = one entry here, with a fetch(meta) that returns a list of source dicts.

Required in each source: url, name, resolution, flags (may be []), size, cached, service, meta. See streams.convert.
"""
from concurrent.futures import ThreadPoolExecutor, wait

import coco
import ranking
import streams
import usenet
import util

REGISTRY = {"AIOStreams": streams.fetch, "Easynews": usenet.easynews, "TorBox": usenet.torbox}
LINKS = {"TorBox": usenet.torbox_link}
for _pack in coco.PACKS:                     # CocoScrapers, FenomScrapers, OpenScrapers: only used when installed
    REGISTRY[_pack] = (lambda pack: lambda meta: coco.fetch(meta, pack))(_pack)
    LINKS[_pack] = coco.link


# What the 'Finding sources' screen shows while searching: per scraper busy / done (with count) / error, and the
# number per resolution so far. Scrapers that are not set up (answer None) are left out.
LIVE = {"order": [], "rows": {}, "res": {}}


def _reset_live():
    LIVE["order"], LIVE["rows"], LIVE["res"] = [], {}, {}


def _finished(name, job, limit):
    try:
        found = job.result()
    except Exception:          # noqa: BLE001 - shown as an error, reported by _run
        LIVE["rows"][name] = {"state": "error", "count": 0}
        return
    if found is None:
        LIVE["rows"].pop(name, None)
        if name in LIVE["order"]:
            LIVE["order"].remove(name)
        return
    found = found[:limit] if limit else found
    for source in found:
        LIVE["res"][source.get("resolution")] = LIVE["res"].get(source.get("resolution"), 0) + 1
    LIVE["rows"][name] = {"state": "done" if found else "none", "count": len(found)}


def names(profile):
    """Scraper names in the order of the profile; new ones at the end."""
    usable = [n for n in REGISTRY if n not in coco.PACKS or coco.installed(n)]       # packs only when installed
    order = [n for n in profile["scrapers"].get("order") or [] if n in usable]
    return order + [n for n in usable if n not in order]


def _run(selection, profile, meta):
    """Returns (sources in scraper order, did anything answer?, was an address missing everywhere?)."""
    per = profile["scrapers"].get("per") or {}
    pool = ThreadPoolExecutor(max_workers=max(1, len(selection)))
    jobs = {}
    for name in selection:
        LIVE["order"].append(name)
        LIVE["rows"][name] = {"state": "busy", "count": 0}
        jobs[name] = pool.submit(REGISTRY[name], meta)
        jobs[name].add_done_callback(lambda job, name=name: _finished(name, job, int(per.get(name, {}).get("max") or 0)))
    wait(list(jobs.values()), timeout=max(5, int(profile["scrapers"].get("wait") or 30)))
    pool.shutdown(wait=False)
    collected, answered, unset = [], False, 0
    for name in selection:
        job = jobs[name]
        if not job.done():
            util.log("%s: took too long" % name)
            continue
        try:
            found = job.result()
        except Exception as error:      # noqa: BLE001 - one scraper's error must not hide the others (or look like 'not set up')
            util.log("%s: %s: %s" % (name, type(error).__name__, error))
            continue
        if found is None:
            unset += 1
            continue
        answered = True
        limit = int(per.get(name, {}).get("max") or 0)
        for source in found[:limit] if limit else found:
            collected.append(dict(source, scraper=name))
    return collected, answered, unset == len(selection)


def ready(source):
    """Some scrapers only make their link when you press play. True when the source has a link."""
    job = source.get("resolve")
    if source.get("url") or not job:
        return bool(source.get("url"))
    source["url"] = LINKS[job["scraper"]](job) or ""
    return bool(source["url"])


def search(meta):
    """Returns (ranked sources, None) or (None, 'address' or 'failed')."""
    import subcheck
    subtitles = subcheck.start(meta) if not meta.get("no_subcheck") else None
    profile = ranking.active()
    per = profile["scrapers"].get("per") or {}
    order = [n for n in names(profile) if not per.get(n, {}).get("off")]
    _reset_live()
    main = [n for n in order if not per.get(n, {}).get("fallback")]
    spare = [n for n in order if per.get(n, {}).get("fallback")]
    found, answered, unset = _run(main, profile, meta) if main else ([], False, True)
    if not found and spare:
        extra, answered_spare, unset_spare = _run(spare, profile, meta)
        found, answered, unset = extra, answered or answered_spare, unset and unset_spare
    elif spare:
        unset = False                      # the reserve scrapers did not run, so they may well be set up
    if not answered and not found:
        # 'address' (= 'set up a scraper first') only when not one scraper, main or reserve, is set up
        util.log("search: no answer (%s)" % ("nothing set up" if unset else "scrapers did not answer"))
        return None, "address" if unset else "failed"
    import learn
    fitting = subcheck.mark(subtitles, found)          # before ranking, so the order of sources can use NL-SUBS
    ranked = learn.adjust(ranking.apply(found, profile, meta["kind"], order))
    return subcheck.first(ranked, fitting), None
