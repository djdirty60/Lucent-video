# -*- coding: utf-8 -*-
"""TheTVDB (API v4) as a spare source of text: only when TMDB has no description of an episode, a season or a
series, in your language or in English. Only with your own TheTVDB key (and PIN, when your key needs one):
Lucent Video has no key of its own. Without a key nothing happens.

    login:            POST /login {"apikey", "pin"}  -> token (valid for a month)
    episodes:         GET  /series/<id>/episodes/default/<lang>?page=<n>
    series text:      GET  /series/<id>/translations/<lang>
"""
import util

API = "https://api4.thetvdb.com/v4"
TOKEN = "tvdbtoken"
LANGS = {"nl": "nld", "en": "eng"}


def available():
    return bool(util.setting("tvdb_key").strip())


def _token(fresh=False):
    token = None if fresh else util.cache_get(TOKEN, 25 * 86400)
    if token:
        return token
    body = {"apikey": util.setting("tvdb_key").strip()}
    pin = util.setting("tvdb_pin").strip()
    if pin:
        body["pin"] = pin
    answer, _ = util.http(API + "/login", data=body, timeout=15)
    token = ((answer or {}).get("data") or {}).get("token") or ""
    if token:
        util.cache_set(TOKEN, token)
    return token


def _get(path):
    for fresh in (False, True):
        token = _token(fresh)
        if not token:
            return {}
        try:
            answer, _ = util.http(API + path, headers={"Authorization": "Bearer " + token}, timeout=15)
            return (answer or {}).get("data") or {}
        except util.HttpError as error:
            if error.status == 401 and not fresh:       # the token ran out: log in once more
                continue
            raise
    return {}


def _cached(path, ttl=86400):
    value = util.cache_get("tvdb" + path, ttl)
    if value is None:
        value = _get(path)
        util.cache_set("tvdb" + path, value)
    return value


def episodes(tvdb_id, season, lang):
    """{episode number: (name, overview)} of one season in one language ('nl' or 'en')."""
    out = {}
    for page in range(0, 10):
        data = _cached("/series/%s/episodes/default/%s?page=%d" % (tvdb_id, LANGS.get(lang, "eng"), page))
        rows = data.get("episodes") or []
        for row in rows:
            if row.get("seasonNumber") == int(season) and row.get("number") is not None:
                out[int(row["number"])] = (row.get("name") or "", row.get("overview") or "")
        if len(rows) < 500:
            break
    return out


def series_text(tvdb_id, lang):
    data = _cached("/series/%s/translations/%s" % (tvdb_id, LANGS.get(lang, "eng")), 7 * 86400)
    return data.get("overview") or ""


def fill_episodes(tvdb_id, season, rows):
    """Gives episodes without a description (and without a real name) the text of TheTVDB: your language first."""
    if not (available() and tvdb_id) or all(row.get("overview") for row in rows):
        return
    for lang in ("nl", "en") if util.NL else ("en",):
        missing = [row for row in rows if not row.get("overview")]
        if not missing:
            return
        try:
            found = episodes(tvdb_id, season, lang)
        except util.HttpError as error:
            util.log("TheTVDB episodes: %s" % error)
            return
        for row in missing:
            name, text = found.get(int(row.get("episode_number") or 0), ("", ""))
            if text:
                row["overview"] = text
                if name and (not row.get("name") or _generic(row["name"])):
                    row["name"] = name


def fill_text(tvdb_id):
    """The description of a series from TheTVDB, your language first, or ''."""
    if not (available() and tvdb_id):
        return ""
    for lang in ("nl", "en") if util.NL else ("en",):
        try:
            text = series_text(tvdb_id, lang)
        except util.HttpError as error:
            util.log("TheTVDB series: %s" % error)
            return ""
        if text:
            return text
    return ""


def check():
    """The test button in the settings: can Lucent log in with your key (and PIN)?"""
    import xbmcgui
    T = util.T
    if not available():
        return xbmcgui.Dialog().ok("TheTVDB", T("Vul eerst je TheTVDB-API-sleutel in.", "Enter your TheTVDB API key first."))
    util.cache_drop(TOKEN)
    try:
        ok = bool(_token(True))
    except util.HttpError as error:
        ok = False
        util.log("TheTVDB login: %s" % error)
    if ok:
        return xbmcgui.Dialog().ok("TheTVDB", T("Het werkt. Lucent gebruikt TheTVDB als TMDB geen tekst heeft.",
                                                "It works. Lucent uses TheTVDB when TMDB has no text."))
    return xbmcgui.Dialog().ok(T("Niet gelukt", "Failed"), T("TheTVDB accepteert de sleutel (of de PIN) niet. Controleer ze, en of je "
                                                              "abonnement nog loopt als je sleutel een PIN vraagt.",
                                                              "TheTVDB does not accept the key (or the PIN). Check them, and whether your "
                                                              "subscription still runs when your key needs a PIN."))


def _generic(name):
    """TMDB's placeholder names such as 'Aflevering 3' or 'Episode 3'."""
    words = name.strip().split()
    return len(words) == 2 and words[0].lower() in ("aflevering", "episode", "episodio", "folge", "épisode") and words[1].isdigit()
