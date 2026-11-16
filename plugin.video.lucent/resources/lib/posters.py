# -*- coding: utf-8 -*-
"""Posters from a poster maker (btttr.cc, SpatialPosters, Pictorium, XRDB, ...): the user pastes its poster URL with
placeholders, Lucent fills them in. Empty, or the service is down: the usual TMDB posters."""
import re
import threading
import time
import urllib.request

import util

DOWN = "lucent.video.posters.down"          # set for a while when the poster maker does not answer
CHECK_EVERY = 3600
SAMPLE = {"kind": "movie", "imdb": "tt0133093", "tmdb": "603", "title": "The Matrix"}
NAMES = "imdb_id|tmdb_id|tvdb_id|type|item_type"
PLACEHOLDER = r"\{((?:%s)(?:\s*\|\s*(?:%s))*)\}" % (NAMES, NAMES)       # {tmdb_id} or {tmdb_id|imdb_id}
_cache = {"pattern": None, "shows": True, "round": "", "at": 0.0}
ROUND = "lv"                                # the 'Refresh posters' button counts up; a new address makes Kodi fetch anew


def _round():
    _settings()
    return _cache["round"]


def with_round(url, number=None):
    """Kodi keeps every picture under its address: after 'Refresh posters' the address gets &lv=N, so it is new."""
    number = _round() if number is None else number
    if not url or not number or number == "0":
        return url
    return "%s%s%s=%s" % (url, "&" if "?" in url else "?", ROUND, number)


def _settings():
    if time.time() - _cache["at"] > 5:                     # read the settings at most every few seconds (long lists)
        _cache.update(pattern=util.setting("poster_pattern").strip(), shows=util.flag("poster_shows"),
                      round=util.setting("poster_round"), at=time.time())
    return _cache["pattern"], _cache["shows"]


def url_for(meta, pattern=None):
    """The poster maker's URL for this title, or '' when it cannot be made (no pattern, a missing id)."""
    pattern = pattern if pattern is not None else _settings()[0]
    if not pattern or "://" not in pattern:
        return ""
    kind = meta.get("kind")
    tv = kind in ("tvshow", "season", "episode")
    tmdb_id = str(meta.get("show_tmdb") or meta.get("tmdb") or "") if tv else str(meta.get("tmdb") or "")
    values = {"imdb_id": meta.get("show_imdb") or meta.get("imdb") if tv else meta.get("imdb"), "tmdb_id": tmdb_id,
              "tvdb_id": meta.get("tvdb"), "type": "tv" if tv else "movie", "item_type": "series" if tv else "movie"}
    missing = []

    def fill(match):
        # {tmdb_id|imdb_id}: the first one this title has (the AIOMetadata way of writing a fallback)
        for name in match.group(1).split("|"):
            value = values.get(name.strip())
            if value:
                return str(value)
        missing.append(match.group(1))
        return ""

    url = re.sub(PLACEHOLDER, fill, pattern)
    return "" if missing else with_round(url)


EPISODE_NAMES = ("tmdb_id", "imdb_id", "tvdb_id", "season", "episode", "thumbnail", "blur")
OPTIONAL = ("thumbnail", "blur")            # may stay empty: the poster maker then uses its own picture / no blur


def episode_url(meta, watched=False):
    """The poster maker's episode picture (thumbnail pattern), or '' when not set up or a number is missing."""
    import urllib.parse
    pattern = _episode_pattern()
    if not pattern or "://" not in pattern or meta.get("kind") != "episode" or util.HOME.getProperty(DOWN):
        return ""
    still = meta.get("thumb") or ""
    if still == meta.get("tvshow_fanart") or "image.tmdb.org" not in still:
        still = ""                              # only a real episode still (not the series backdrop as a stand-in)
    values = {"tmdb_id": meta.get("tmdb"), "imdb_id": meta.get("imdb"), "tvdb_id": meta.get("tvdb"),
              "season": meta.get("season"), "episode": meta.get("episode"),
              "thumbnail": urllib.parse.quote(still, safe="") if still else "",
              "blur": "true" if (util.flag("episode_blur") and not watched) else "false"}
    missing = []

    def fill(match):
        for name in match.group(1).split("|"):
            name = name.strip()
            value = values.get(name)
            if value not in (None, ""):
                return str(value)
            if name in OPTIONAL:
                return ""
        missing.append(match.group(1))
        return ""

    url = re.sub(r"\{((?:%s)(?:\s*\|\s*(?:%s))*)\}" % ("|".join(EPISODE_NAMES), "|".join(EPISODE_NAMES)), fill, pattern)
    return "" if missing else with_round(url)


def _episode_pattern():
    if time.time() - _cache.get("eat", 0) > 5:
        _cache.update(episode=util.setting("episode_pattern").strip(), eat=time.time())
    return _cache.get("episode") or ""


def poster(meta, fallback):
    """What goes in the poster slot: the poster maker's picture when set up and reachable, else the usual one."""
    pattern, shows = _settings()
    if not pattern or util.HOME.getProperty(DOWN):
        return fallback
    if meta.get("kind") != "movie" and not shows:
        return fallback
    return url_for(meta, pattern) or fallback


def reachable(pattern=None, timeout=8):
    """(ok, text): does the poster maker give a picture for a known film?"""
    url = url_for(SAMPLE, pattern)
    if not url:
        return False, "the pattern has no {imdb_id} or {tmdb_id} Lucent can fill in"
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Lucent-Video"})
        with urllib.request.urlopen(request, timeout=timeout) as answer:
            kind = answer.headers.get("Content-Type") or ""
            head = answer.read(16)
    except Exception as error:                  # noqa: BLE001
        return False, type(error).__name__
    if kind.startswith("image/") or head[:3] == b"\xff\xd8\xff" or head[:8] == b"\x89PNG\r\n\x1a\n" or head[:4] == b"RIFF":
        return True, "ok"
    return False, "the answer is not a picture (%s)" % (kind.split(";")[0] or "?")


# For the skin (library titles and other add-ons): the pattern split around the one id it needs, per type, so the
# skin can build the address in its XML without Python:
#   $INFO[Window(home).Property(lucent.video.poster.movie.prefix)]$INFO[ListItem.UniqueID(imdb)]$INFO[...movie.suffix]
SKIN = "lucent.video.poster"
SKIN_ON = "lucent.video.posters"


def skin_parts(pattern):
    """{'id': 'imdb'|'tmdb', 'movie': (prefix, suffix), 'tv': (prefix, suffix)} or None when the skin cannot use it
    (no id placeholder, or more than one id in it)."""
    groups = [g for g in re.findall(PLACEHOLDER, pattern or "") if g not in ("type", "item_type")]
    if len(groups) != 1 or "://" not in pattern:
        return None
    usable = [n.strip() for n in groups[0].split("|") if n.strip() in ("imdb_id", "tmdb_id")]
    if not usable:
        return None                             # only {tvdb_id}: the skin has no simple way to that number
    out = {"id": usable[0].split("_")[0]}       # {tmdb_id|imdb_id}: the skin uses the first
    for kind, words in (("movie", ("movie", "movie")), ("tv", ("tv", "series"))):
        filled = pattern.replace("{type}", words[0]).replace("{item_type}", words[1])
        prefix, _sep, suffix = filled.partition("{%s}" % groups[0])
        out[kind] = (prefix, with_round(prefix + "\x00" + suffix, util.setting("poster_round")).split("\x00", 1)[1])
    return out


def publish(ok):
    """Home properties for the skin: on only while a pattern is set and the poster maker answers."""
    home = util.HOME
    pattern, shows = util.setting("poster_pattern").strip(), util.flag("poster_shows")
    parts = skin_parts(pattern) if ok else None
    if not parts:
        for name in (SKIN_ON, SKIN + ".id", SKIN + ".shows") + tuple("%s.%s.%s" % (SKIN, k, e) for k in ("movie", "tv")
                                                                     for e in ("prefix", "suffix")):
            home.clearProperty(name)
        return
    home.setProperty(SKIN + ".id", parts["id"])
    for kind in ("movie", "tv"):
        home.setProperty("%s.%s.prefix" % (SKIN, kind), parts[kind][0])
        home.setProperty("%s.%s.suffix" % (SKIN, kind), parts[kind][1])
    home.setProperty(SKIN + ".shows", "1" if shows else "")
    home.setProperty(SKIN_ON, "1")


# Episode pictures for the skin (library episodes): the episode pattern cut at its placeholders, so the skin can glue
# it back together in XML, whatever order the placeholders are in:
#   text0 + value(slot1) + text1 + value(slot2) + ... + text<count>
# slotN is the placeholder's name (tmdb_id, season, episode, thumbnail, blur, ...); with a fallback like
# {tmdb_id|imdb_id} it is the first name. blur says whether unwatched episodes should be blurred.
EPISODE_SKIN = "lucent.video.episode"
EPISODE_SKIN_ON = "lucent.video.episodes"
EPISODE_SLOTS = 8


def episode_skin_parts(pattern):
    """(texts, slots) or None when the skin cannot use the pattern (no address, no season/episode, too many slots)."""
    if not pattern or "://" not in pattern:
        return None
    regex = r"\{((?:%s)(?:\s*\|\s*(?:%s))*)\}" % ("|".join(EPISODE_NAMES), "|".join(EPISODE_NAMES))
    pieces = re.split(regex, pattern)
    texts, slots = pieces[0::2], [g.split("|")[0].strip() for g in pieces[1::2]]
    texts[-1] = with_round("".join(texts) + "\x00" + texts[-1], util.setting("poster_round")).split("\x00", 1)[1]
    if not slots or len(slots) > EPISODE_SLOTS or "season" not in slots or "episode" not in slots:
        return None
    return texts, slots


def publish_episodes():
    home = util.HOME
    parts = episode_skin_parts(util.setting("episode_pattern").strip())
    for i in range(EPISODE_SLOTS + 1):
        home.clearProperty("%s.text%d" % (EPISODE_SKIN, i))
        home.clearProperty("%s.slot%d" % (EPISODE_SKIN, i))
    if not parts:
        for name in (EPISODE_SKIN_ON, EPISODE_SKIN + ".count", EPISODE_SKIN + ".blur"):
            home.clearProperty(name)
        return
    texts, slots = parts
    for i, text in enumerate(texts):
        home.setProperty("%s.text%d" % (EPISODE_SKIN, i), text)
    for i, slot in enumerate(slots, 1):
        home.setProperty("%s.slot%d" % (EPISODE_SKIN, i), slot)
    home.setProperty(EPISODE_SKIN + ".count", str(len(slots)))
    home.setProperty(EPISODE_SKIN + ".blur", "1" if util.flag("episode_blur") else "")
    home.setProperty(EPISODE_SKIN_ON, "1")


def refresh():
    """The settings button 'Refresh posters': new addresses, so Kodi fetches every picture again."""
    import xbmc
    T = util.T
    try:
        number = int(util.setting("poster_round") or 0) + 1
    except ValueError:
        number = 1
    util.ADDON.setSetting("poster_round", str(number))      # the service sees the change and tells the skin
    _cache["at"] = _cache["eat"] = 0
    xbmc.executebuiltin("Container.Refresh")
    util.notify(T("Posters en afleveringsbeelden worden opnieuw opgehaald",
                  "Posters and episode pictures are fetched again"))


def check():
    """From the service, now and then: while the poster maker is down, the usual posters are used (no empty tiles)."""
    _cache["eat"] = 0                           # settings may have changed: read the episode pattern again
    publish_episodes()
    pattern = util.setting("poster_pattern").strip()
    if not pattern:
        util.HOME.clearProperty(DOWN)
        publish(False)
        return
    ok, why = reachable(pattern)
    if ok:
        if util.HOME.getProperty(DOWN):
            util.log("posters: the poster maker answers again")
        util.HOME.clearProperty(DOWN)
    else:
        if not util.HOME.getProperty(DOWN):
            util.log("posters: the poster maker does not answer (%s): the usual posters for now" % why)
        util.HOME.setProperty(DOWN, "1")
    publish(ok)


def check_soon():
    threading.Thread(target=check, daemon=True).start()


def test():
    """The settings button: a sample poster from the pattern, or what is wrong."""
    import xbmcgui
    import dialogs
    T = util.T
    pattern = util.setting("poster_pattern").strip()
    ok, why = reachable(pattern)
    if not ok:
        xbmcgui.Dialog().ok("Lucent", T("Het patroon werkt niet: %s.", "The pattern does not work: %s.") % why)
        return
    util.HOME.clearProperty(DOWN)
    dialog = dialogs._open(dialogs.TraktLink, "lucent_trakt.xml", props={          # noqa: SLF001 - same add-on
        "qr": url_for(SAMPLE, pattern), "code": SAMPLE["title"], "heading": T("Voorbeeldposter", "Sample poster"),
        "line": T("Zo zien je posters eruit (The Matrix, 1999).", "This is how your posters look (The Matrix, 1999)."),
        "button": T("Klaar", "Done")})
    dialog.doModal()
    del dialog
