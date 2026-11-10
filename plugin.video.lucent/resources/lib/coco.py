# -*- coding: utf-8 -*-
"""Scraper packs from others, as scrapers: CocoScrapers, and its relatives FenomScrapers and OpenScrapers (same
build: sources(specified_folders=...) gives providers with a sources(data, hostDict) method). They find torrents; a
debrid service plays them (see debrid.py).

A pack is only used when it is installed and at least one debrid service has a key. The pack's own settings decide
which of its providers are on.
"""
import re
from concurrent.futures import ThreadPoolExecutor, wait
import os
import sys
import xml.etree.ElementTree as ET

import xbmc
import xbmcaddon

import debrid
import usenet
import util

ADDON = "script.module.cocoscrapers"
WAIT = 25
# name shown in Lucent -> (Kodi add-on, Python package)
PACKS = {"CocoScrapers": (ADDON, "cocoscrapers"),
         "FenomScrapers": ("script.module.fenomscrapers", "fenomscrapers"),
         "OpenScrapers": ("script.module.openscrapers", "openscrapers")}
BTIH = re.compile(r"btih:([0-9a-fA-F]{40}|[A-Za-z2-7]{32})")


def installed(name="CocoScrapers"):
    return xbmc.getCondVisibility("System.HasAddon(%s)" % PACKS[name][0])


def _add_paths(addon_id, done):
    """Kodi only gives an add-on the modules it asks for itself, so add CocoScrapers and what it needs (requests ...)."""
    if addon_id in done or not xbmc.getCondVisibility("System.HasAddon(%s)" % addon_id):
        return
    done.add(addon_id)
    root = xbmcaddon.Addon(addon_id).getAddonInfo("path")
    try:
        manifest = ET.parse(os.path.join(root, "addon.xml")).getroot()
    except (ET.ParseError, OSError):
        return
    for extension in manifest.iter("extension"):
        if extension.get("point") == "xbmc.python.module":
            path = os.path.join(root, extension.get("library") or "lib")
            if path not in sys.path:
                sys.path.append(path)
    for need in manifest.iter("import"):
        if not need.get("addon", "").startswith("xbmc."):
            _add_paths(need.get("addon"), done)


def _providers(name):
    addon_id, package = PACKS[name]
    _add_paths(addon_id, set())
    import importlib
    sources = importlib.import_module(package).sources        # only there when installed
    try:
        return sources(specified_folders=["torrents"])
    except TypeError:                                           # an older pack without folders: all its providers
        return sources()


def _data(meta):
    titles = usenet.titles_for(meta)
    if meta["kind"] == "movie":
        return {"imdb": meta.get("imdb") or "", "title": titles[0], "aliases": titles[1:], "year": str(meta.get("year") or "")}
    return {"imdb": meta.get("imdb") or "", "tvshowtitle": titles[0], "title": meta.get("title") or "", "aliases": titles[1:],
            "year": str(meta.get("year") or ""), "season": str(meta["season"]), "episode": str(meta["episode"]),
            "premiered": meta.get("premiered") or "", "tvdb": meta.get("tvdb") or ""}


def _size_bytes(value):
    try:
        return float(value or 0) * 1024 ** 3                # CocoScrapers gives gigabytes
    except (TypeError, ValueError):
        return 0


def _hash(row):
    if row.get("hash"):
        return str(row["hash"]).lower()
    found = BTIH.search(str(row.get("url") or ""))
    return found.group(1).lower() if found else ""


def fetch(meta, name="CocoScrapers"):
    """Returns None when the pack or a debrid service is missing, else a list of sources."""
    if not installed(name) or not debrid.active() or not meta.get("imdb"):
        return None
    try:
        providers = _providers(name)
    except Exception as error:                              # a broken pack must never stop Lucent
        util.log("%s could not start: %s" % (name, type(error).__name__))
        return None
    data = _data(meta)
    episode = meta["kind"] != "movie"

    def run(entry):
        label, provider = entry
        try:
            scraper = provider()
            if episode and not getattr(scraper, "hasEpisodes", True) or not episode and not getattr(scraper, "hasMovies", True):
                return []
            return scraper.sources(data, []) or []
        except Exception as error:
            util.log("%s %s: %s" % (name, label, type(error).__name__))
            return []

    pool = ThreadPoolExecutor(max_workers=max(1, min(10, len(providers))))
    jobs = [pool.submit(run, entry) for entry in providers]
    wait(jobs, timeout=WAIT)
    pool.shutdown(wait=False)
    found = [row for job in jobs if job.done() and not job.exception() for row in job.result() if _hash(row)]
    by_hash = {}
    for row in found:
        by_hash.setdefault(_hash(row), row)
    owners = debrid.assign(list(by_hash))
    out = []
    for value, row in by_hash.items():
        service = owners.get(value)
        if not service:
            continue
        title = row.get("name") or ""
        source = usenet._source(title, _size_bytes(row.get("size")), "%s · %s" % (debrid.label(service), str(row.get("provider") or "").upper()),
                                resolve={"scraper": name, "service": service, "hash": value, "name": title,
                                         "season": meta.get("season"), "episode": meta.get("episode")})
        out.append(source)
    util.log("%s: %d torrents, %d playable" % (name, len(by_hash), len(out)))
    return out


def link(job):
    return debrid.link(job)
