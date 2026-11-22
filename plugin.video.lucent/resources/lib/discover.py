# -*- coding: utf-8 -*-
"""Extra lists from TMDB and Trakt: just out digitally, film series (collections), finished series, seasonal lists,
most anticipated, short and long films, per country, anime."""
import datetime

import tmdb
import util

T = util.T
DAY = tmdb.DAY


def _today():
    return datetime.date.today()


# key -> (TMDB type, Lucent kind, Dutch, English, parameters (a function of today, so dates stay fresh))
PRESETS = {
    "digital": ("movie", "movie", "Net digitaal verschenen", "Just out digitally",
                lambda d: {"with_release_type": "4", "release_date.gte": (d - datetime.timedelta(days=60)).isoformat(),
                           "release_date.lte": d.isoformat(), "sort_by": "popularity.desc", "vote_count.gte": 10}),
    "ended": ("tv", "tvshow", "Afgeronde series", "Finished series",
              lambda d: {"with_status": "3", "vote_average.gte": 7.5, "vote_count.gte": 300, "sort_by": "popularity.desc"}),
    "short": ("movie", "movie", "Korte films (tot 100 min)", "Short films (up to 100 min)",
              lambda d: {"with_runtime.lte": 100, "with_runtime.gte": 60, "vote_count.gte": 1000, "vote_average.gte": 6.5,
                         "sort_by": "popularity.desc"}),
    "long": ("movie", "movie", "Lange zit (vanaf 150 min)", "Long sit (150 min and more)",
             lambda d: {"with_runtime.gte": 150, "vote_count.gte": 1000, "vote_average.gte": 7, "sort_by": "popularity.desc"}),
    "dutch_movie": ("movie", "movie", "Nederlandse films", "Dutch films",
                    lambda d: {"with_origin_country": "NL", "vote_count.gte": 20, "sort_by": "popularity.desc"}),
    "dutch_tv": ("tv", "tvshow", "Nederlandse series", "Dutch series",
                 lambda d: {"with_origin_country": "NL", "vote_count.gte": 5, "sort_by": "popularity.desc"}),
    "korean_tv": ("tv", "tvshow", "Koreaanse series", "Korean series",
                  lambda d: {"with_origin_country": "KR", "with_original_language": "ko", "vote_count.gte": 50,
                             "sort_by": "popularity.desc"}),
    "korean_movie": ("movie", "movie", "Koreaanse films", "Korean films",
                     lambda d: {"with_origin_country": "KR", "with_original_language": "ko", "vote_count.gte": 100,
                                "sort_by": "popularity.desc"}),
    "nordic_tv": ("tv", "tvshow", "Scandinavische crime", "Nordic crime",
                  lambda d: {"with_origin_country": "SE|DK|NO|IS|FI", "with_genres": "80", "vote_count.gte": 20,
                             "sort_by": "popularity.desc"}),
    "nordic_movie": ("movie", "movie", "Scandinavische thrillers", "Nordic thrillers",
                     lambda d: {"with_origin_country": "SE|DK|NO|IS|FI", "with_genres": "53|80", "vote_count.gte": 50,
                                "sort_by": "popularity.desc"}),
    "anime_tv": ("tv", "tvshow", "Anime-series", "Anime series",
                 lambda d: {"with_genres": "16", "with_original_language": "ja", "vote_count.gte": 50, "sort_by": "popularity.desc"}),
    "anime_movie": ("movie", "movie", "Anime-films", "Anime films",
                    lambda d: {"with_genres": "16", "with_original_language": "ja", "vote_count.gte": 100, "sort_by": "popularity.desc"}),
}

# the countries of 'Per land', per kind
COUNTRIES = {"movie": ["dutch_movie", "korean_movie", "nordic_movie"], "tvshow": ["dutch_tv", "korean_tv", "nordic_tv"]}


def label(key):
    if key in SEASONS:
        return T(SEASONS[key][1], SEASONS[key][2])
    entry = PRESETS.get(key)
    return T(entry[2], entry[3]) if entry else key


def titles(key, page=1):
    """(titles, more?) of a preset list."""
    if key in SEASONS:
        return season_titles(key, page)
    api, kind, _nl, _en, params = PRESETS[key]
    return tmdb.listing("discover/%s" % api, kind, page, include_adult="false", **params(_today()))


# ----------------------------------------------------------------------------- seasonal lists
# key -> (TMDB keyword to look up, Dutch, English, (from month, day), (to month, day))
SEASONS = {
    "christmas": ("christmas", "Kerstfilms", "Christmas films", (11, 15), (12, 31)),
    "halloween": ("halloween", "Halloween", "Halloween", (10, 1), (11, 2)),
    "valentine": ("valentine's day", "Valentijn", "Valentine's", (2, 1), (2, 15)),
    "summer": ("summer vacation", "Zomerfilms", "Summer films", (6, 20), (8, 31)),
}


def season_now(today=None):
    """The seasonal list of today ('christmas' from mid November ...), or ''."""
    today = today or _today()
    for key, (_word, _nl, _en, start, end) in SEASONS.items():
        if start <= (today.month, today.day) <= end:
            return key
    return ""


def _keyword(word):
    """TMDB's keyword id for a word (looked up once a month)."""
    rows = tmdb.get("search/keyword", ttl=30 * DAY, query=word).get("results") or []
    exact = [r for r in rows if (r.get("name") or "").lower() == word]
    return (exact or rows or [{}])[0].get("id")


def season_titles(key, page=1):
    number = _keyword(SEASONS[key][0])
    if not number:
        return [], False
    return tmdb.listing("discover/movie", "movie", page, with_keywords=number, include_adult="false", sort_by="popularity.desc",
                        **{"vote_count.gte": 100})


# ----------------------------------------------------------------------------- film series (TMDB collections)
SERIES = ["Harry Potter", "The Lord of the Rings", "The Hobbit", "Star Wars", "The Avengers", "Iron Man", "Captain America",
          "Thor", "Guardians of the Galaxy", "Spider-Man (Avengers)", "The Dark Knight", "X-Men", "Mission: Impossible",
          "James Bond", "John Wick", "Fast & Furious", "Jurassic Park", "Indiana Jones", "Back to the Future", "The Matrix",
          "Alien", "Terminator", "Rocky", "Creed", "The Godfather", "Die Hard", "Bourne", "The Hunger Games", "Dune",
          "Avatar", "Pirates of the Caribbean", "Toy Story", "Shrek", "Despicable Me", "Kung Fu Panda", "Ice Age",
          "How to Train Your Dragon", "Planet of the Apes", "Transformers", "Twilight", "Ocean's", "Kill Bill", "Scream",
          "The Conjuring", "Insidious", "Saw", "Halloween", "Men in Black", "Ghostbusters", "Top Gun", "Mad Max",
          "Paddington", "Despicable Me", "Frozen", "Cars", "Finding Nemo", "Incredibles", "Deadpool", "Venom"]


def collection(name):
    """(id, name, poster) of a TMDB collection by its name (looked up once a month), or None."""
    rows = tmdb.get("search/collection", ttl=30 * DAY, query=name).get("results") or []
    if not rows:
        return None
    best = next((r for r in rows if (r.get("name") or "").lower().startswith(name.lower())), rows[0])
    return best["id"], best.get("name") or name, tmdb.image(best.get("poster_path"), "w500")


def collections():
    """The film series of the list, each once, in this order."""
    from concurrent.futures import ThreadPoolExecutor
    with ThreadPoolExecutor(max_workers=8) as pool:
        found = list(pool.map(lambda n: _safe(collection, n), dict.fromkeys(SERIES)))
    seen, out = set(), []
    for row in found:
        if row and row[0] not in seen:
            seen.add(row[0])
            out.append(row)
    return out


def _safe(function, *args):
    try:
        return function(*args)
    except util.HttpError:
        return None


def collection_titles(number):
    """The films of a series in the order they came out (the ones still to come at the end)."""
    data = tmdb.get("collection/%s" % number, ttl=7 * DAY)
    parts = sorted(data.get("parts") or [], key=lambda p: p.get("release_date") or "9999")
    return tmdb.many([("movie", p["id"]) for p in parts])


# ----------------------------------------------------------------------------- most anticipated (Trakt)
def anticipated(kind, page=1, size=20):
    """What most Trakt users look forward to (films or series): (titles, more?)."""
    import trakt
    path = "%s/anticipated?page=%d&limit=%d" % ("movies" if kind == "movie" else "shows", page, size)
    data = util.cache_get("trakt" + path, 6 * 3600)
    if data is None:
        data, _ = trakt.call(path, auth=False)
        util.cache_set("trakt" + path, data)
    pairs = trakt.pairs_from([dict(row, type="movie" if kind == "movie" else "show") for row in data or []])
    return tmdb.many(pairs), len(data or []) >= size
