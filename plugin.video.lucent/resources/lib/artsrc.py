# -*- coding: utf-8 -*-
"""Extra pictures for the Kodi library, next to TMDB's: the special fan-made sets of MediUX and the logos, clearart,
discs and banners of fanart.tv. Each needs your own key (Settings > General > Posters); without one nothing changes.

What comes back is per kind of picture, best first: {'poster': [...], 'fanart': [...], 'clearlogo': [...], ...} and,
for series, {'season': {1: [...]}}. library.py puts these before TMDB's in the .nfo files, so Kodi's 'Choose art'
offers them all and uses the first. Collections (Kodi's movie sets) get theirs the same way, as set.poster etc.

ThePosterDB is not here on purpose: its terms do not allow automated fetching and it has no API."""
import time
import urllib.parse

import util

FANART = "https://webservice.fanart.tv/v3/"
MEDIUX_API = "https://images.mediux.io/graphql"
MEDIUX_IMG = "https://api.mediux.pro/assets/"          # the public address of a MediUX picture (no key needed)
KEEP = 7 * 86400
FAILED_KEEP = 3600
_public = {}

# fanart.tv field -> Kodi art type
FANART_MOVIE = {"movieposter": "poster", "moviebackground": "fanart", "hdmovielogo": "clearlogo", "movielogo": "clearlogo",
                "hdmovieclearart": "clearart", "movieart": "clearart", "moviedisc": "discart", "moviebanner": "banner",
                "moviethumb": "landscape"}
FANART_TV = {"tvposter": "poster", "showbackground": "fanart", "hdtvlogo": "clearlogo", "clearlogo": "clearlogo",
             "hdclearart": "clearart", "clearart": "clearart", "tvbanner": "banner", "tvthumb": "landscape"}


def fanart_key():
    return util.setting("fanart_key").strip()


def mediux_key():
    return util.setting("mediux_key").strip()


def _lang_rank(lang):
    mine = "nl" if util.NL else "en"
    return {mine: 0, "en": 1, "00": 2, "": 2}.get(lang or "", 3)


def _cached(name, fetch):
    """fetch() once a week per title (a failure: try again after an hour). Never the key in the cache name."""
    hit = util.cache_get("art:" + name, KEEP)
    if hit is not None:
        if hit.get("failed") and time.time() - hit["failed"] < FAILED_KEEP:
            return {}
        if not hit.get("failed"):
            return hit
    try:
        found = fetch()
    except util.HttpError as error:
        if error.status != 404:                                     # 404: this title has no pictures there
            return _failed(name, error)
        found = {}
    except Exception as error:      # noqa: BLE001 - extra pictures are never a reason to stop the library
        return _failed(name, error)
    util.cache_set("art:" + name, found)
    return found


def _failed(name, error):
    util.log("pictures: %s did not answer (%s)" % (name.split(":")[0], getattr(error, "status", type(error).__name__)))
    util.cache_set("art:" + name, {"failed": time.time()})
    return {}


def fanart(kind, ident):
    """kind 'movie' (TMDB id), 'set' (TMDB collection id: fanart.tv keeps collections with the movies) or 'tv' (TVDB id)."""
    key = fanart_key()
    if not key or not ident:
        return {}

    def fetch():
        path = ("tv/%s" if kind == "tv" else "movies/%s") % urllib.parse.quote(str(ident))
        data, _ = util.http(FANART + path + "?" + urllib.parse.urlencode({"api_key": key}), timeout=15)
        data = data or {}
        out = {}
        fields = FANART_TV if kind == "tv" else FANART_MOVIE
        for field, art in fields.items():
            rows = [r for r in data.get(field) or [] if str(r.get("url", "")).startswith("http")]
            rows.sort(key=lambda r: (_lang_rank(r.get("lang")), -int(r.get("likes") or 0)))
            out.setdefault(art, [])
            out[art] += [r["url"] for r in rows if r["url"] not in out[art]]
        if kind == "tv":
            seasons = {}
            rows = sorted(data.get("seasonposter") or [], key=lambda r: (_lang_rank(r.get("lang")), -int(r.get("likes") or 0)))
            for row in rows:
                if str(row.get("season", "")).isdigit() and str(row.get("url", "")).startswith("http"):
                    seasons.setdefault(str(int(row["season"])), []).append(row["url"])
            out["season"] = seasons
        return {k: v for k, v in out.items() if v}
    return _cached("fanart:%s:%s" % (kind, ident), fetch)


def preview(url):
    """A small version for Kodi's choice list (fanart.tv has one for every picture)."""
    if url.startswith("https://assets.fanart.tv/fanart/"):
        return url.replace("/fanart/", "/preview/", 1)
    return url


QUERY_MOVIE = """query($id: ID!) { movies_by_id(id: $id) {
  movie_sets { popularity movie_poster { id } movie_backdrop { id } } } }"""
QUERY_SET = """query($id: ID!) { collections_by_id(id: $id) {
  posters { id collection_set { popularity } } backdrops { id collection_set { popularity } } } }"""
QUERY_SHOW = """query($id: ID!) { shows_by_id(id: $id) {
  show_sets { popularity show_poster { id } show_backdrop { id } season_posters { id season { season_number } } } } }"""


def _many(value):
    return value if isinstance(value, list) else ([value] if isinstance(value, dict) else [])


def _ids(rows):
    return [r["id"] for r in rows if isinstance(r, dict) and r.get("id")]


def mediux(kind, tmdb_id):
    """kind 'movie', 'set' (TMDB collection id) or 'tv' (TMDB id). Most popular sets first."""
    key = mediux_key()
    if not key or not tmdb_id or _public.get("ok") is False:
        return {}

    def fetch():
        query = {"movie": QUERY_MOVIE, "set": QUERY_SET, "tv": QUERY_SHOW}[kind]
        data, _ = util.http(MEDIUX_API, data={"query": query, "variables": {"id": str(tmdb_id)}}, timeout=15,
                            headers={"Authorization": "Bearer " + key})
        root = (data or {}).get("data") or {}
        out = {"poster": [], "fanart": []}
        seasons = {}
        if kind == "set":
            item = root.get("collections_by_id") or {}
            for art, field in (("poster", "posters"), ("fanart", "backdrops")):
                rows = sorted(_many(item.get(field)), key=lambda r: -float(((r or {}).get("collection_set") or {}).get("popularity") or 0))
                out[art] = _ids(rows)
        else:
            item = root.get("shows_by_id" if kind == "tv" else "movies_by_id") or {}
            sets = sorted(_many(item.get("show_sets" if kind == "tv" else "movie_sets")), key=lambda s: -float(s.get("popularity") or 0))
            for one in sets:
                out["poster"] += _ids(_many(one.get("show_poster" if kind == "tv" else "movie_poster")))
                out["fanart"] += _ids(_many(one.get("show_backdrop" if kind == "tv" else "movie_backdrop")))
                for row in _many(one.get("season_posters")):
                    number = ((row or {}).get("season") or {}).get("season_number")
                    if row.get("id") and number is not None:
                        seasons.setdefault(str(int(number)), []).append(MEDIUX_IMG + row["id"])
        result = {k: [MEDIUX_IMG + i for i in dict.fromkeys(v)] for k, v in out.items() if v}
        if seasons:
            result["season"] = seasons
        return result
    found = _cached("mediux:%s:%s" % (kind, tmdb_id), fetch)
    first = next((urls[0] for art, urls in found.items() if art != "season" and urls), None)
    if first and not _reachable(first):
        return {}
    return found


def _reachable(url):
    """Kodi fetches the pictures itself, without a key (a key never goes in an .nfo or Kodi's log). Checked once a day
    with a real picture: when MediUX wants a key for its pictures, MediUX is left out and the log says so."""
    if "ok" in _public:
        return _public["ok"]
    hit = util.cache_get("art:mediux-public", 86400)
    if hit is None:
        try:
            util.http(url, timeout=10, method="HEAD", headers={"Accept": "image/*"})
            hit = {"ok": True}
        except Exception as error:      # noqa: BLE001
            hit = {"ok": False}
            util.log("pictures: MediUX pictures are not public (%s): MediUX left out" % type(error).__name__)
        util.cache_set("art:mediux-public", hit)
    _public["ok"] = bool(hit.get("ok"))
    return _public["ok"]


def order(art, tmdb_urls, from_mediux, from_fanart):
    """One kind of picture, best first: MediUX's special sets lead (so with a MediUX key they are used), then TMDB's
    (the usual first choice), then fanart.tv's; for logos and the kinds TMDB lacks fanart.tv simply adds its own."""
    urls = (from_mediux.get(art) or []) + list(tmdb_urls or []) + (from_fanart.get(art) or [])
    return list(dict.fromkeys(u for u in urls if u))
