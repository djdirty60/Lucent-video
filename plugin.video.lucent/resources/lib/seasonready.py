# -*- coding: utf-8 -*-
"""Season ready: one season pack from Usenet, fetched once, so every episode of the season starts at once.

From the menu of a season ('Seizoen klaarzetten'). Lucent looks for season packs at your indexers, takes the best
one by your profile, fetches its NZB (one NZB for the whole season) and checks which episodes are in it. Each of those
episodes gets the pack as its source, kept for KEEP_DAYS: pressing play skips the search. Only the NZB and the
episode list are fetched beforehand, not the season itself.

For the skin: an episode that is ready has the ListItem property lucent.ready = 1.
"""
import time

import util

T = util.T
FILE = "season_ready.json"
KEEP_DAYS = 3
_ready = {"at": 0.0, "value": None}


def _load():
    now = time.time()
    data = {k: v for k, v in util.load_json(FILE, {}).items() if v.get("until", 0) > now}
    return data


def is_ready(tmdb_id, season, episode):
    """True when this episode is in a season that was put ready (read once a minute at most: it is asked per item)."""
    if _ready["value"] is None or time.time() - _ready["at"] > 60:
        _ready.update(at=time.time(), value=_load())
    entry = _ready["value"].get("%s:%s" % (tmdb_id, season))
    return bool(entry) and int(episode or 0) in entry.get("episodes", [])


def run(tmdb_id, season):
    import xbmcgui
    from nzb import source as nzb_source
    if not nzb_source.configured():
        util.notify(T("Seizoen klaarzetten werkt met Usenet: stel dat eerst in", "Season ready works with Usenet: set that up first"))
        return
    import player
    import prefetch
    import ranking
    season = int(season)
    first = player.title_meta("episode", tmdb_id, season, 1)
    if not first:
        util.notify(T("Dit seizoen is niet gevonden", "This season was not found"))
        return
    heading = T("Seizoen %d klaarzetten", "Season %d ready") % season
    progress = xbmcgui.DialogProgressBG()
    progress.create(heading, T("Zoeken naar een seizoenspak…", "Looking for a season pack…"))
    try:
        found = nzb_source.fetch(first) or []
        packs = [s for s in found if s.get("pack")]
        if packs:
            packs = ranking.heavy_last(ranking.apply(packs, ranking.active(), "episode", ["Usenet"], first.get("lang") or ""))
        if not packs:
            util.notify(T("Geen seizoenspak gevonden voor seizoen %d", "No season pack found for season %d") % season)
            return
        pick = packs[0]
        job = pick["resolve"]
        progress.update(20, heading, T("NZB ophalen…", "Fetching the NZB…"))
        if not nzb_source.ensure_nzb(job, "season ready"):
            util.notify(T("De NZB kon niet worden opgehaald (daglimiet van je indexer?)", "The NZB could not be fetched (your indexer's daily limit?)"))
            return
        episodes = _in_pack(job, season, int(first.get("season_count") or 0))
        if not episodes:
            util.notify(T("In dit seizoenspak zijn geen afleveringen herkend", "No episodes were recognised in this season pack"))
            return
        size = streams_size(pick)
        ready = []
        for number, episode in enumerate(episodes, 1):
            meta = player.title_meta("episode", tmdb_id, season, episode) if episode != 1 else first
            if not meta:
                continue
            source = dict(pick, resolve=dict(job, season=season, episode=episode, pack=True, minutes=int(meta.get("runtime") or 0)))
            prefetch.keep(meta, [source], keep=KEEP_DAYS * 86400)
            ready.append(episode)
            progress.update(20 + int(80 * number / len(episodes)), heading,
                            T("Aflevering %d van %d klaar · 1 NZB (seizoenspak%s)", "Episode %d of %d ready · 1 NZB (season pack%s)")
                            % (number, len(episodes), (", " + size) if size else ""))
        data = _load()
        data["%s:%s" % (tmdb_id, season)] = {"episodes": ready, "until": time.time() + KEEP_DAYS * 86400, "name": pick.get("name")}
        util.save_json(FILE, data)
        _ready["value"] = None
        util.log("season ready: season %d, %d episodes from one pack" % (season, len(ready)))
        util.notify(T("Seizoen %d staat klaar: %d afleveringen starten meteen", "Season %d is ready: %d episodes start at once")
                    % (season, len(ready)))
    finally:
        progress.close()


def streams_size(source):
    """'41 GB' style size of the whole pack, from the line under the name."""
    for part in (source.get("meta") or "").split(" · "):
        if part.endswith(("GB", "MB", "TB")):
            return part
    return ""


def _in_pack(job, season, count):
    """The episode numbers of this season that are in the pack (read from the NZB, nothing fetched)."""
    import os
    from nzb import nzbfile, server, source as nzb_source
    try:
        with open(os.path.join(server.folder(), nzb_source._nzb_name(job)), "rb") as handle:     # noqa: SLF001
            files = nzbfile.parse(handle.read())
    except (OSError, nzbfile.NzbError):
        return []
    out = []
    for episode in range(1, max(count, 1) + 1 if count else 41):
        try:
            nzbfile.pick(files, season, episode, True)
        except nzbfile.NzbError:
            if not count and out:
                break                           # number of episodes unknown: stop after the last one found
            continue
        out.append(episode)
    return out
