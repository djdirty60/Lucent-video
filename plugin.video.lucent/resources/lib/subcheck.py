# -*- coding: utf-8 -*-
"""Subtitles sure beforehand: while the sources are searched, Lucent Video's own subtitle search (subs_core.py) is asked
which subtitles in your language exist for this film or episode. A source whose release fits one of them gets the
NL-SUBS mark (so the order of sources can use it), and with 'Sources with subtitles first' it moves up within its
own resolution. Uses the subtitle search built into Lucent Video, with its own keys."""
import threading


import util

FLAG = "aio_NL-SUBS"
FIT = 0.85


def available():
    return util.flag("subs_check")


def _core():
    """The subtitle search built into Lucent Video."""
    import subs_core
    return subs_core


def start(meta):
    """Starts the search next to the scrapers. Returns a holder to pass to apply(), or None."""
    if not available():
        return None
    import subs
    langs = sorted(subs.mine()) or ["nl"]
    episode = meta["kind"] == "episode"
    video = {"title": meta.get("original_title") or meta.get("show") or meta["title"], "year": str(meta.get("year") or ""),
             "show": meta.get("show") or "" if episode else "", "season": str(meta.get("season") or "") if episode else "",
             "episode": str(meta.get("episode") or "") if episode else "", "imdb": "" if episode else meta.get("imdb") or "",
             "tmdb": "" if episode else str(meta.get("tmdb") or ""), "show_imdb": meta.get("imdb") or "" if episode else "",
             "show_tmdb": str(meta.get("tmdb") or "") if episode else "", "file": "", "release": "", "other": True}
    holder = {"rows": None}

    def run():
        try:
            holder["rows"] = [r for r in _core().search(video, langs) if r.get("lang") in langs]
        except Exception as error:          # never let subtitles stop the search for sources
            util.log("subtitle check: %s" % type(error).__name__)
            holder["rows"] = []
    holder["thread"] = threading.Thread(target=run, daemon=True)
    holder["thread"].start()
    return holder


def mark(holder, sources, wait=8):
    """Marks the sources that have a fitting subtitle. Returns the ids of those sources."""
    if not holder or not sources:
        return set()
    holder["thread"].join(wait)
    rows = holder.get("rows") or []
    if not rows:
        return set()
    core = _core()
    releases = [r for r in [r.get("release") or "" for r in rows] + [o for r in rows for o in (r.get("others") or [])[:10]] if r]
    fitting = set()
    for source in sources:
        video = {"release": core.release_name(source.get("name") or "")}
        if any(core.release_match(release, video) >= FIT for release in releases):
            fitting.add(id(source))
            if FLAG not in (source.get("flags") or []):
                source["flags"] = list(source.get("flags") or []) + [FLAG]
    util.log("subtitle check: %d subtitles, %d of %d sources fit" % (len(rows), len(fitting), len(sources)))
    return fitting


def first(sources, fitting):
    """With 'Sources with subtitles first' on: those go first within their own resolution."""
    if not fitting or not util.flag("subs_first"):
        return sources
    groups = {}
    for source in sources:
        groups.setdefault(source.get("resolution"), len(groups))
    return [s for _n, s in sorted(enumerate(sources), key=lambda p: (groups[p[1].get("resolution")], id(p[1]) not in fitting, p[0]))]
