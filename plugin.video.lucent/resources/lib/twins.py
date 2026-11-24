# -*- coding: utf-8 -*-
"""Two series with the same name and the same year ("twins"): The Stranger (2020) is a British Netflix series and an
American Quibi series. Releases only say "The Stranger S01E01", so both series' releases come back for either one.

When the series you look for has a twin on TMDB, a release whose name carries a streaming service tag (NF, AMZN, DSNP,
QIBI ...) that belongs to the twin's network and not to your series' own network is left out: it is the other series.
Releases without such a tag are kept (they could be either). Films are not touched."""
import re

import tmdb
import util

# release tag -> words that appear in the network's name on TMDB
TAGS = {"NF": ("netflix",), "NETFLIX": ("netflix",), "AMZN": ("amazon", "prime video"), "AMAZON": ("amazon", "prime video"),
        "DSNP": ("disney",), "DISNEY": ("disney",), "HMAX": ("hbo", "max"), "MAX": ("max", "hbo"), "HBO": ("hbo", "max"),
        "ATVP": ("apple",), "HULU": ("hulu",), "PCOK": ("peacock",), "PEACOCK": ("peacock",), "PMTP": ("paramount",),
        "PARAMOUNT": ("paramount",), "QIBI": ("quibi",), "QUIBI": ("quibi",), "IP": ("bbc",), "ROKU": ("roku",),
        "STAN": ("stan",), "CRAV": ("crave",)}
_TAG = re.compile(r"(?<![A-Za-z0-9])(%s)(?![A-Za-z0-9])" % "|".join(TAGS), re.I)


def _networks(tmdb_id):
    try:
        data = tmdb.get("tv/%s" % tmdb_id, ttl=7 * tmdb.DAY)
    except util.HttpError:
        return None
    return data, [str(n.get("name") or "").lower() for n in data.get("networks") or []]


def _services(networks):
    return {tag for tag, words in TAGS.items() if any(word in name for name in networks for word in words)}


def _lengths(data):
    """A series' episode lengths in minutes; TMDB often leaves episode_run_time empty, then the last aired episode."""
    found = [int(m) for m in data.get("episode_run_time") or [] if m]
    if not found:
        for key in ("last_episode_to_air", "next_episode_to_air"):
            runtime = int((data.get(key) or {}).get("runtime") or 0)
            if runtime:
                found.append(runtime)
    return found


def foreign_tags(meta):
    """The service tags that point to a twin of this series and not to the series itself (empty: no twin)."""
    if meta.get("kind") not in ("episode", "tvshow"):
        return set()
    key = "twins3%s" % meta.get("tmdb")      # 3: lengths also from the last aired episode
    saved = util.cache_get(key, 7 * 86400)
    if saved is not None:
        return set(saved)
    mine = _networks(meta.get("tmdb"))
    if not mine:
        return set()
    data, my_networks = mine
    name, year = str(data.get("name") or "").strip(), str(data.get("first_air_date") or "")[:4]
    foreign = set()
    if name and year:
        try:
            found = tmdb.get("search/tv", ttl=7 * tmdb.DAY, query=name, first_air_date_year=year).get("results") or []
        except util.HttpError:
            found = []
        others = [r for r in found if str(r.get("id")) != str(meta.get("tmdb"))
                  and str(r.get("name") or "").strip().lower() == name.lower() and str(r.get("first_air_date") or "")[:4] == year]
        mine_tags = _services(my_networks)
        lengths = []
        for other in others[:3]:
            theirs = _networks(other["id"])
            if theirs:
                foreign |= _services(theirs[1]) - mine_tags
                lengths += _lengths(theirs[0])
        if others:
            util.cache_set("twinlengths%s" % meta.get("tmdb"), lengths)
        if others:
            util.log("twins: %s (%s) has %d other series with the same name and year; their service tags: %s"
                     % (name, year, len(others), ", ".join(sorted(foreign)) or "none"))
    util.cache_set(key, sorted(foreign))
    return foreign


_MARK = re.compile(r"(?<![A-Za-z0-9])(S\d{1,2}(E\d{1,3})?|\d{1,2}x\d{2}|(19|20)\d{2})(?![A-Za-z0-9])", re.I)


def _after_title(name):
    """The part after the title (from S01E01, 1x01 or the year on): a title like 'Mad Max' is no service tag."""
    found = _MARK.search(name)
    return name[found.start():] if found else ""


def drop(sources, meta):
    """The sources without the releases of a twin series."""
    foreign = foreign_tags(meta)
    if not foreign:
        return sources
    lengths = util.cache_get("twinlengths%s" % meta.get("tmdb"), 7 * 86400) or []
    kept = []
    for source in sources:
        tags = {t.upper() for t in _TAG.findall(_after_title(str(source.get("name") or "")))}
        if tags & foreign:
            continue
        source["twin"] = lengths or True    # its length is checked too once the file is read (nzb/source.py other_series)
        kept.append(source)
    if len(kept) < len(sources):
        util.log("twins: %d release(s) left out that belong to the other series with the same name (%s)"
                 % (len(sources) - len(kept), ", ".join(sorted(foreign))))
    return kept
