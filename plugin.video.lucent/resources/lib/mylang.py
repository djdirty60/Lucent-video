# -*- coding: utf-8 -*-
"""Your language for sources: which release names, AIOStreams details and indexer details count as 'audio in your
language' and 'subtitles in your language'.

Lucent began as a Dutch add-on; this was fixed on Dutch. Now it follows the setting 'Language of sources' (sources_lang),
and when that is on automatic: the language of the Kodi interface, or Kodi's preferred audio / subtitle language when
the interface is English. Inside Lucent the tags stay 'nl' and 'nlsubs' (they mean: yours); what they match and how
the label looks comes from here. Every language has its own colour on the source labels (flags aio_XX / aio_XX-SUBS).
"""
import json
import re

import xbmc

import util

# code: (English name, own name, audio pattern, subtitle pattern, words in AIOStreams / indexer details)
# Short codes are only in a pattern where they are rarely anything else in a release name (NL, PL, RO).
LANGS = {
    "nl": ("Dutch", "Nederlands", r"(?:DUTCH|NLD?(?![-. ]?SUBS?))", r"(?:NL[. ]?SUBS?|DUTCH[. ]?SUBS?)",
           ("dutch", "nederlands", "flemish", "vlaams", "nl", "nld", "dut")),
    "de": ("German", "Deutsch", r"(?:GERMAN|DEUTSCH|GER(?![-. ]?SUBS?))", r"(?:GER(?:MAN)?[-. ]?SUBS?)",
           ("german", "deutsch", "de", "deu", "ger")),
    "fr": ("French", "Français", r"(?:FRENCH|TRUEFRENCH|VFF|VFQ|VFI|VF2|VOF)", r"(?:VOSTFR|FR(?:ENCH)?[-. ]?SUBS?)",
           ("french", "français", "francais", "fr", "fra", "fre")),
    "es": ("Spanish", "Español", r"(?:SPANISH|CASTELLANO|LATINO|ESP(?![-. ]?SUBS?))", r"(?:(?:ESP|SPA|SPANISH)[-. ]?SUBS?|VOSE)",
           ("spanish", "español", "espanol", "castellano", "latino", "es", "spa", "esp")),
    "it": ("Italian", "Italiano", r"(?:ITALIAN|ITA(?![-. ]?SUBS?))", r"(?:SUB[. ]?ITA|ITA(?:LIAN)?[-. ]?SUBS?)",
           ("italian", "italiano", "it", "ita")),
    "pt": ("Portuguese", "Português", r"(?:PORTUGUESE|PT[-. ]?BR|DUBLADO)", r"(?:LEGENDADO|PT[-. ]?SUBS?|POR[-. ]?SUBS?)",
           ("portuguese", "português", "portugues", "brazilian", "pt", "por", "pt-br")),
    "ro": ("Romanian", "Română", r"(?:ROMANIAN|ROMANA|RO(?![-. ]?SUBS?))", r"(?:RO(?:M|MANIAN)?[-. ]?SUBS?|SUBBED[. ]RO)",
           ("romanian", "română", "romana", "ro", "ron", "rum")),
    "pl": ("Polish", "Polski", r"(?:POLISH|LEKTOR|DUBBING[. ]PL|PL(?![-. ]?SUBS?))", r"(?:PL[-. ]?SUBS?|NAPISY)",
           ("polish", "polski", "pl", "pol")),
    "en": ("English", "English", r"(?:ENGLISH|ENG(?![-. ]?SUBS?))", r"(?:ENG(?:LISH)?[-. ]?SUBS?)",
           ("english", "en", "eng")),
    "sv": ("Swedish", "Svenska", r"(?:SWEDISH|SWE(?![-. ]?SUBS?))", r"(?:SWE(?:DISH)?[-. ]?SUBS?)",
           ("swedish", "svenska", "sv", "swe")),
    "da": ("Danish", "Dansk", r"(?:DANISH|DANSK)", r"(?:DAN(?:ISH)?[-. ]?SUBS?)",
           ("danish", "dansk", "da", "dan")),
    "no": ("Norwegian", "Norsk", r"(?:NORWEGIAN|NORDIC|NOR(?![-. ]?SUBS?))", r"(?:NOR(?:WEGIAN)?[-. ]?SUBS?)",
           ("norwegian", "norsk", "no", "nor", "nob")),
    "fi": ("Finnish", "Suomi", r"(?:FINNISH|FIN(?![-. ]?SUBS?))", r"(?:FIN(?:NISH)?[-. ]?SUBS?)",
           ("finnish", "suomi", "fi", "fin")),
    "hu": ("Hungarian", "Magyar", r"(?:HUNGARIAN|HUN(?![-. ]?SUBS?))", r"(?:HUN(?:GARIAN)?[-. ]?SUBS?)",
           ("hungarian", "magyar", "hu", "hun")),
    "cs": ("Czech", "Čeština", r"(?:CZECH|CZ(?![-. ]?SUBS?))", r"(?:CZ(?:ECH)?[-. ]?SUBS?|CZ[. ]?TIT)",
           ("czech", "čeština", "cestina", "cs", "cze", "ces")),
    "tr": ("Turkish", "Türkçe", r"(?:TURKISH|TUR(?![-. ]?SUBS?))", r"(?:TUR(?:KISH)?[-. ]?SUBS?)",
           ("turkish", "türkçe", "turkce", "tr", "tur")),
    "ru": ("Russian", "Русский", r"(?:RUSSIAN|RUS(?![-. ]?SUBS?))", r"(?:RUS(?:SIAN)?[-. ]?SUBS?)",
           ("russian", "русский", "ru", "rus")),
}
ORDER = list(LANGS)
NL_NAMES = {"nl": "Nederlands", "de": "Duits", "fr": "Frans", "es": "Spaans", "it": "Italiaans", "pt": "Portugees",
            "ro": "Roemeens", "pl": "Pools", "en": "Engels", "sv": "Zweeds", "da": "Deens", "no": "Noors", "fi": "Fins",
            "hu": "Hongaars", "cs": "Tsjechisch", "tr": "Turks", "ru": "Russisch"}
_cache = {}


def _kodi_setting(name):
    query = {"jsonrpc": "2.0", "id": 1, "method": "Settings.GetSettingValue", "params": {"setting": name}}
    try:
        return json.loads(xbmc.executeJSONRPC(json.dumps(query))).get("result", {}).get("value")
    except (ValueError, TypeError):
        return None


def _two(value):
    import subs
    return subs._code(value if isinstance(value, str) else "")


def _auto():
    """Automatic: the language of the Kodi interface; with Kodi in English, Kodi's preferred audio or subtitle language."""
    if "auto" in _cache:
        return _cache["auto"]
    ui = _two(xbmc.getLanguage(xbmc.ISO_639_1))
    found = ui if ui in LANGS and ui != "en" else ""
    if not found:
        for name in ("locale.audiolanguage", "locale.subtitlelanguage"):
            value = _kodi_setting(name)
            if isinstance(value, str) and value not in ("default", "original", "mediadefault", "none", "forced_only"):
                two = _two(value)
                if two in LANGS:
                    found = two
                    break
    _cache["auto"] = found or (ui if ui in LANGS else "en")
    return _cache["auto"]


CODE_KEEP = 10                  # seconds the setting is remembered: one search asks it hundreds of times (per source)


def code():
    """The language of sources: the setting 'Language of sources', or automatic. Read again after CODE_KEEP seconds
    (reading a setting makes a new Addon object: a few ms each on a slow box, times hundreds per search)."""
    import time
    saved = _cache.get("code")
    if saved and time.time() - saved[1] < CODE_KEEP:
        return saved[0]
    chosen = (util.setting("sources_lang") or "").strip().lower()
    value = chosen if chosen in LANGS else _auto()
    _cache["code"] = (value, time.time())
    return value


def name():
    """'Romanian' / 'Roemeens' style: the language's English name, its own name in brackets when different."""
    english, own = LANGS[code()][:2]
    return english if english == own else "%s (%s)" % (english, own)


def label():
    """The language's name in the language of Kodi (Dutch or English): 'Roemeens' / 'Romanian'."""
    return NL_NAMES[code()] if util.NL else LANGS[code()][0]


def audio_label():
    """'Nederlandse audio' stays as it was for Dutch; other languages: 'Audio: Roemeens' / 'Romanian audio'."""
    if code() == "nl":
        return util.T("Nederlandse audio", "Dutch audio")
    return util.T("Audio: %s" % label(), "%s audio" % label())


def subs_label():
    if code() == "nl":
        return util.T("Nederlandse ondertitels", "Dutch subtitles")
    return util.T("Ondertitels: %s" % label(), "%s subtitles" % label())


def short():
    return code().upper()


def audio_flag():
    return "aio_%s" % short()


def subs_flag():
    return "aio_%s-SUBS" % short()


def flags():
    return audio_flag(), subs_flag()


def audio_pattern():
    return LANGS[code()][2]


def subs_pattern():
    return LANGS[code()][3]


def words():
    return LANGS[code()][4]


def is_mine(word):
    """'Romanian', 'ro', 'ron' in AIOStreams or indexer details: is this your language?"""
    return (word or "").strip().lower() in words()


def options():
    """(label, value) for the setting: automatic first, then every language."""
    return [("auto", "")] + [(LANGS[c][0], c) for c in ORDER]


def compiled(marks):
    """The two patterns, inside the release-name marks of ranking.py."""
    key = (code(), marks)
    if key not in _cache:
        _cache[key] = (re.compile(marks % audio_pattern(), re.I), re.compile(marks % subs_pattern(), re.I))
    return _cache[key]


def choose():
    """The setting 'Language of sources': automatic, or one of the languages."""
    import xbmcaddon
    import xbmcgui
    current = (util.setting("sources_lang") or "").strip().lower()
    automatic = _auto()
    names = [util.T("Automatisch (nu: %s)" % NL_NAMES[automatic], "Automatic (now: %s)" % LANGS[automatic][0])]
    names += [NL_NAMES[c] if util.NL else LANGS[c][0] for c in ORDER]
    preselect = ORDER.index(current) + 1 if current in LANGS else 0
    picked = xbmcgui.Dialog().select(util.T("Taal van bronnen", "Language of sources"), names, preselect=preselect)
    if picked < 0:
        return
    xbmcaddon.Addon(util.ADDON_ID).setSetting("sources_lang", "" if picked == 0 else ORDER[picked - 1])
    _cache.pop("code", None)                        # the new choice counts right away
    util.notify(util.T("Taal van bronnen: %s" % label(), "Language of sources: %s" % label()))
