# -*- coding: utf-8 -*-
"""Decides which sources are shown and in what order. Knows nothing about Kodi screens.

A scraper only has to give a file name; everything else is read from that name when it is missing.
"""
import copy
import re

import util

T = util.T
FILE = "profiles.json"
MARKS = r"(?:^|[ ._\-\[(])%s(?=$|[ ._\-\])])"
FROM_FLAG = {"DV": "dv", "DV2": "dv2", "HDR": "hdr", "HLG": "hlg", "SDR": "sdr", "3D": "3d", "aio_FEL": "fel", "aio_P7": "p7", "aio_IMAX": "imax", "aio_10BIT": "10bit",
             "REMUX": "remux", "BLURAY": "bluray", "WEB": "web", "HDTV": "hdtv", "DVDRIP": "dvd", "CAM": "cam", "SCR": "cam", "ATMOS": "atmos",
             "TRUEHD": "truehd", "DTS-X": "dtsx", "DTS-HDMA": "dtsma", "DTS-HD": "dtshd", "DTS": "dts", "DD+": "ddplus", "DD": "dd", "AAC": "aac",
             "OPUS": "opus", "7.1": "71", "5.1": "51", "2.0": "20", "aio_NL": "nl", "aio_MULTI": "multi", "aio_NL-SUBS": "nlsubs", "HEVC": "hevc",
             "AVC": "avc", "AV1": "av1"}
BY_NAME = {"remux": "REMUX", "bluray": "(?:BLU-?RAY|BDRIP|BRRIP)", "web": "WEB(?:-?DL|-?RIP)?", "hdtv": "HDTV", "cam": "(?:CAM|HDCAM|HDTS|TS|TELESYNC|TC|SCREENER|SCR)",
           "dv": "(?:DV|DOVI|DOLBY[ ._]?VISION)", "dv2": "DV2", "fel": "FEL", "p7": "(?:DV[ ._-]?)?P7", "hdr10plus": r"HDR10(?:\+|PLUS)", "hdr": r"HDR(?:10)?", "hlg": "HLG",
           "sdr": "SDR", "3d": "3D", "imax": "IMAX", "10bit": r"(?:10[- ]?BIT|HI10P?)", "atmos": "ATMOS", "truehd": "TRUEHD", "dtsx": "DTS[-.:]?X", "dtsma": "DTS[-. ]?HD[-. ]?MA", "dtshd": "DTS[-. ]?HD",
           "dts": "DTS", "ddplus": r"(?:DDP|DD\+|EAC3)", "dd": "(?:DD|AC3)", "aac": "AAC", "opus": "OPUS", "hevc": "(?:HEVC|X265|H[. ]?265)", "avc": "(?:AVC|X264|H[. ]?264)",
           "av1": "AV1", "upscale": "(?:UPSCALED?|REGRADED?|AI[. ]?UPSCALE)", "hardcoded": "(?:HC|HARDCODED|HARDSUBS?)", "nl": "(?:DUTCH|NLD?(?![-. ]?SUBS?))", "71": r"7[. ]1", "51": r"5[. ]1", "20": r"2[. ]0", "multi": "MULTI", "nlsubs": "(?:NL[. ]?SUBS?|DUTCH[. ]?SUBS?)"}
COMPILED = {tag: re.compile(MARKS % pattern, re.I) for tag, pattern in BY_NAME.items()}
GROUP_RE = re.compile(r"-([A-Za-z0-9]{2,20})(?:\.[a-z0-9]{2,4})?$")


def tags_of(source):
    """Every property of a source, as a list of short names."""
    name, tags = source.get("name") or "", set()
    for flag in source.get("flags") or []:
        if flag in FROM_FLAG:
            tags.add(FROM_FLAG[flag])
    for tag, pattern in COMPILED.items():
        if tag not in tags and pattern.search(name):
            tags.add(tag)
    if tags & {"fel", "p7", "dv2"}:
        tags.add("dv")
    if tags & {"fel", "p7"}:
        tags.add("hdr")                     # Dolby Vision profile 7 always has an HDR10 base layer
    if tags & {"hdr10plus"}:
        tags.add("hdr")
    tags.add({"4K": "4k", "1440p": "1440p", "1080p": "1080p", "720p": "720p"}.get(source.get("resolution"), "sd"))
    tags.add("cached" if source.get("cached") else "uncached")
    if 0 < (source.get("size") or 0) < 1073741824:
        tags.add("under1gb")
    return tags


def release_group(source):
    found = GROUP_RE.search(source.get("name") or "")
    return found.group(1) if found else ""


# id -> (label, tags of which one must match)
RULES = {
    "4k": ("4K", ["4k"]), "1440p": ("1440p", ["1440p"]), "1080p": ("1080p", ["1080p"]), "720p": ("720p", ["720p"]), "sd": ("SD", ["sd"]),
    "remux": ("Remux", ["remux"]), "bluray": ("Blu-ray", ["bluray"]), "web": ("WEB-DL", ["web"]), "hdtv": ("HDTV", ["hdtv"]), "dvd": ("DVD", ["dvd"]), "cam": ("CAM / TS", ["cam"]),
    "felp7": ("P7 / FEL", ["fel", "p7"]), "fel": ("FEL", ["fel"]), "p7": ("P7 (MEL)", ["p7"]), "dv2": ("Dolby Vision 2", ["dv2"]), "dvany": ("Dolby Vision", ["dv"]), "dv": ("Dolby Vision", ["dv"]),
    "hdr10plus": ("HDR10+", ["hdr10plus"]), "hdr": ("HDR10", ["hdr"]), "hlg": ("HLG", ["hlg"]), "sdr": ("SDR", ["sdr"]), "3d": ("3D", ["3d"]), "imax": ("IMAX", ["imax"]), "10bit": ("10-bit", ["10bit"]),
    "lossless": ("TrueHD / DTS-HD", ["truehd", "dtsx", "dtsma", "dtshd"]), "atmos": ("Atmos", ["atmos"]), "truehd": ("TrueHD", ["truehd"]), "dtsx": ("DTS:X", ["dtsx"]), "dtsma": ("DTS-HD MA", ["dtsma"]),
    "dtshd": ("DTS-HD", ["dtshd"]), "dts": ("DTS", ["dts"]), "ddplus": ("Dolby Digital Plus", ["ddplus"]), "dd": ("Dolby Digital", ["dd"]), "aac": ("AAC", ["aac"]), "opus": ("Opus", ["opus"]),
    "71": ("7.1", ["71"]), "51": ("5.1", ["51"]), "20": ("Stereo", ["20"]), "hevc": ("HEVC", ["hevc"]), "avc": ("H.264", ["avc"]), "av1": ("AV1", ["av1"]),
    "cached": (T("Direct speelbaar", "Ready to play"), ["cached"]), "uncached": (T("Niet direct speelbaar", "Not ready to play"), ["uncached"]),
    "nl": (T("Nederlandse audio", "Dutch audio"), ["nl"]), "nlsubs": (T("Nederlandse ondertitels", "Dutch subtitles"), ["nlsubs"]), "multi": (T("Meerdere talen", "Multiple languages"), ["multi"]),
    "upscale": (T("Upscales", "Upscales"), ["upscale"]), "hardcoded": (T("Ingebrande ondertitels", "Hardcoded subtitles"), ["hardcoded"]), "under1gb": (T("Kleiner dan 1 GB", "Under 1 GB"), ["under1gb"]),
    "largest": (T("Grootste bestand", "Largest file"), []), "smallest": (T("Kleinste bestand", "Smallest file"), []),
}
GROUPS = [("resolution", T("Resolutie", "Resolution"), ["4k", "1440p", "1080p", "720p", "sd"]), ("source", T("Bron", "Source"), ["remux", "bluray", "web", "hdtv", "dvd", "cam"]),
          ("visual", T("Beeld", "Picture"), ["fel", "p7", "dv2", "dv", "hdr10plus", "hdr", "hlg", "sdr", "imax", "10bit"]), ("audio", T("Geluid", "Sound"), ["atmos", "truehd", "dtsx", "dtsma", "dtshd", "dts", "ddplus", "dd", "aac", "opus"]),
          ("speed", T("Direct speelbaar", "Ready to play"), ["cached", "uncached"]), ("language", T("Taal", "Language"), ["nl", "nlsubs", "multi"]), ("size", T("Grootte", "Size"), ["largest", "smallest"]),
          ("channels", T("Kanalen", "Channels"), ["71", "51", "20"]), ("codec", "Codec", ["hevc", "av1", "avc"]),
          ("scraper", "Scraper", [])]
GROUP_NAMES = {g[0]: g[1] for g in GROUPS}
HIDE_CHOICES = ["cam", "upscale", "3d", "av1", "uncached", "hardcoded"]
STATE_NAMES = {"pref": T("Voorkeur", "Preferred"), "need": T("Verplicht", "Required"), "hide": T("Verbergen", "Hide"), "off": T("Uit", "Off")}


def _preset(name, about, rules, hide=None, tags=None, first=None, lead=None, **limits):
    filters = {"hide": hide or [], "size_movie": [0, 0], "size_episode": [0, 0], "min_res": "", "max_per_res": 0, "prefer_groups": [], "hide_groups": []}
    filters.update(limits)
    groups = default_groups(rules)
    groups.sort(key=lambda g: 0 if g["id"] == first else 1)
    if lead:                                    # these groups first, in this order (e.g. picture before source)
        groups.sort(key=lambda g: lead.index(g["id"]) if g["id"] in lead else len(lead))
    return {"name": name, "about": about, "tags": tags or [], "filters": filters, "groups": groups, "scrapers": {"order": [], "per": {}, "dedupe": True, "wait": 30}}


def default_groups(rules=None):
    """Groups in the usual order; rules that belong to a group move to the front of that group."""
    wanted = [r for r in rules or []]
    out = []
    for group, _label, inner in GROUPS:
        out.append({"id": group, "order": [r for r in wanted if r in inner] + [r for r in inner if r not in wanted]})
    return out


def presets():
    return {
        "aio": dict(_preset(T("Zoals AIOStreams", "As AIOStreams"), T("Niets veranderen aan de volgorde.", "Leave the order alone."), []), as_is=True),
        "fel": _preset(T("FEL eerst", "FEL first"), T("Voor een speler die FEL aankan.", "For a player that handles FEL."), ["4k", "remux", "fel", "p7", "atmos", "truehd", "cached"], ["cam"],
                       tags=["4K", "Remux", "P7 / FEL", "Atmos"], lead=["resolution", "visual", "source"]),
        "best": _preset(T("Beste kwaliteit", "Best quality"), T("Grootste en mooiste bestand.", "Largest and best file."), ["4k", "remux", "dv", "truehd", "largest"], ["cam", "upscale"],
                        tags=["4K", "Remux", "DV", T("Lossless geluid", "Lossless sound")], lead=["resolution", "visual", "source"]),
        "small": _preset(T("Snel en zuinig", "Fast and small"), T("Kleine bestanden, start snel.", "Small files, quick to start."), ["1080p", "web", "cached", "smallest"], ["cam"],
                         tags=["1080p", "WEB-DL", T("Onder 10 GB", "Under 10 GB")], size_movie=[0, 10], size_episode=[0, 4]),
        "dutch": _preset(T("Alleen Nederlands", "Dutch first"), T("Nederlandse audio of ondertitels.", "Dutch audio or subtitles."), ["nl", "nlsubs", "4k", "1080p"], ["cam"],
                         tags=[T("NL-audio", "Dutch audio"), T("NL-subs", "Dutch subs"), "4K"], first="language"),
        "anime": _preset("Anime", T("Meerdere talen en ondertitels.", "Multiple languages and subtitles."), ["multi", "1080p", "cached"], ["cam"],
                         tags=["Dual-audio", "1080p", "Subs"], first="language"),
    }


def _store():
    return util.load_json(FILE, {"active": "aio", "profiles": {}})


def _complete(profile):
    """A saved profile from an older version: new choices are added at the end of their group and new groups at the end,
    so what you set up keeps its order and nothing jumps ahead of it."""
    groups = profile.get("groups")
    if not isinstance(groups, list):
        return profile
    known = {g.get("id"): g for g in groups}
    for group, _label, inner in GROUPS:
        if group not in known:
            groups.append({"id": group, "order": list(inner)})
            continue
        order = known[group].setdefault("order", [])
        if group not in ("size", "scraper"):
            order += [r for r in inner if r not in order]
    return profile


OLD_GROUP_ORDER = ["resolution", "source", "visual", "audio", "speed", "language", "size", "scraper"]


def _picture_first(profile_id, profile):
    """'FEL first' and 'Best quality' put Picture before Source since 0.38.9 (a Blu-ray encode with only HDR no longer
    beats a WEB-DL with Dolby Vision). A saved copy whose groups are still in the old built-in order gets the same;
    a copy whose groups you moved yourself is left alone."""
    if profile_id not in ("fel", "best"):
        return profile
    ids = [g.get("id") for g in profile.get("groups") or [] if g.get("id") not in ("channels", "codec")]
    if ids == OLD_GROUP_ORDER:
        groups = profile["groups"]
        source = next(g for g in groups if g.get("id") == "source")
        visual = next(g for g in groups if g.get("id") == "visual")
        a, b = groups.index(source), groups.index(visual)
        groups[a], groups[b] = visual, source
    return profile


def all_profiles():
    """Built-in ones first, your edits on top. {id: profile}"""
    out = presets()
    out.update({k: _picture_first(k, _complete(v)) for k, v in _store()["profiles"].items()})
    return out


def active_id():
    store = _store()
    return store["active"] if store["active"] in all_profiles() else "aio"


def active():
    return copy.deepcopy(all_profiles()[active_id()])


def set_active(profile_id):
    store = _store()
    store["active"] = profile_id
    util.save_json(FILE, store)


def save(profile_id, profile):
    store = _store()
    store["profiles"][profile_id] = profile
    util.save_json(FILE, store)


def delete(profile_id):
    """A built-in profile goes back to how it was; your own disappears."""
    store = _store()
    store["profiles"].pop(profile_id, None)
    if store["active"] == profile_id and profile_id not in presets():
        store["active"] = "aio"
    util.save_json(FILE, store)


def new_id():
    number = 1
    while "own%d" % number in all_profiles():
        number += 1
    return "own%d" % number


def _match(rule_id, tags):
    if rule_id not in RULES:
        return False
    return bool(set(RULES[rule_id][1]) & tags)


def _passes(source, tags, profile, kind):
    filters = profile["filters"]
    if set(filters.get("hide") or []) & tags:
        return False
    for rule_id in filters.get("hide") or []:
        if _match(rule_id, tags):
            return False
    group = release_group(source).lower()
    if group and group in [g.lower() for g in filters.get("hide_groups") or []]:
        return False
    low, high = filters.get("size_movie" if kind == "movie" else "size_episode") or [0, 0]
    gigabytes = (source.get("size") or 0) / 1073741824.0
    if gigabytes and ((low and gigabytes < low) or (high and gigabytes > high)):
        return False
    ladder = ["4k", "1440p", "1080p", "720p", "sd"]
    if filters.get("min_res"):
        floor = ladder.index(filters["min_res"])
        if ladder.index(next(t for t in ladder if t in tags)) > floor:
            return False
    return True


def _size_key(rule_id, source):
    return -(source.get("size") or 0) if rule_id == "largest" else (source.get("size") or 0)


# Points per choice, from the research (TRaSH Guides / Vidhin's scores, see 'Bronnen rangschikken van goed naar slecht').
# Picture adds up (HDR + Dolby Vision + IMAX ...); every other group counts only its best match.
VALUES = {
    "visual": {"fel": 200, "p7": 0, "dv2": 1000, "dv": 1000, "hdr10plus": 100, "hdr": 500, "hlg": 0, "sdr": 0, "imax": 300, "10bit": 100},
    "source": {"remux": 1900, "bluray": 1750, "web": 1650, "hdtv": 500, "dvd": 300, "cam": 0},
    "audio": {"atmos": 3000, "truehd": 2750, "dtsx": 4500, "dtsma": 2500, "dtshd": 2000, "dts": 1250, "ddplus": 1750, "dd": 750,
              "aac": 1000, "opus": 250},
    "speed": {"cached": 1000, "uncached": 0},
    "language": {"nl": 1000, "nlsubs": 800, "multi": 500},
    "channels": {"71": 100, "51": 100, "20": 0},
    "codec": {"hevc": 100, "av1": 0, "avc": 50},
}
SCALE = {"visual": 1500, "source": 1900, "audio": 5000, "speed": 1000, "language": 1000, "channels": 100, "codec": 100}
STACKS = ("visual",)
TRUEHD_ATMOS = 5000
# Red flags: always below every other source (or hidden with the filters).
RED_GENERATED = re.compile(r"(?:^|[ ._\-\[(])(?:BiTOR|VECTOR|VisionXpert)(?=$|[ ._\-\])])", re.I)


def _factor(position):
    """Your order inside a group still counts: the top choice in full, each step lower 5 % less (at least 55 %)."""
    return max(0.55, 1.0 - 0.05 * position)


def _points(group_id, order, tags):
    """(points 0..~14, the choice that counted most) of one group."""
    values = VALUES.get(group_id)
    if values is None:                                      # a group without values: by place, as before
        n = len(order)
        found = [(10.0 * (n - pos) / n, rule) for pos, rule in enumerate(order) if _match(rule, tags)]
        return (max(found)[0], max(found)[1]) if found else (0.0, None)
    found = [(values.get(rule, 0) * _factor(pos), rule) for pos, rule in enumerate(order) if _match(rule, tags)]
    if group_id == "audio" and "atmos" in tags:
        place = order.index("atmos") if "atmos" in order else len(order)
        if tags & {"truehd"}:
            found.append((TRUEHD_ATMOS * _factor(place), "atmos"))
    if not found:
        return 0.0, None
    best = max(found)
    total = sum(v for v, _r in found) if group_id in STACKS else best[0]
    return 10.0 * total / SCALE[group_id], best[1]


def red_flag(source, tags, kind="movie"):
    """True for what is never the better choice: CAM/TS, upscales, 3D, burned-in subtitles, home-made Dolby Vision or
    HDR10+, H.264 10-bit, and a 4K or 1080p file far too small for what it says it is."""
    if tags & {"cam", "upscale", "3d", "hardcoded"}:
        return True
    name = source.get("name") or ""
    if RED_GENERATED.search(name) and tags & {"dv", "hdr10plus"}:
        return True
    if "avc" in tags and "10bit" in tags:
        return True
    size = source.get("size") or 0
    if size:
        floor = {("movie", "4k"): 4.0, ("movie", "1080p"): 1.3, ("episode", "4k"): 1.2, ("episode", "1080p"): 0.35}
        limit = floor.get(("movie" if kind == "movie" else "episode", "4k" if "4k" in tags else "1080p" if "1080p" in tags else ""))
        if limit and size < limit * 1073741824:
            return True
    return False


def _order_key(source, tags, profile, scraper_order, kind="movie"):
    """Smaller sorts first.
    1. Red flags always go below everything else.
    2. The group on top decides on its own (usually resolution: 4K above 1080p; 4K in SDR counts as 1080p).
    3. Every other group gives points from the research values; a group higher in your list counts more, and inside
       a group your own order still counts. Picture adds up (Dolby Vision + HDR + IMAX), sound counts its best track
       (TrueHD Atmos above TrueHD above DD+). So Dolby Vision + IMAX + Atmos beat 'only Blu-ray'.
    4. Size and scraper only decide between equal points."""
    flagged = red_flag(source, tags, kind)
    key, why, total, tie = [1 if flagged else 0], [], 0.0, []
    if flagged:
        why.append((T("Rode vlag", "Red flag"), T("Onderaan", "At the bottom")))
    groups = profile["groups"]
    scored = [g for g in groups[1:] if g["id"] not in ("size", "scraper")]
    for number, group in enumerate(groups, 1):
        if group["id"] == "scraper":
            tie.append(scraper_order.index(source.get("scraper")) if source.get("scraper") in scraper_order else 99)
            continue
        if group["id"] == "size":
            if group["order"]:
                tie.append(_size_key(group["order"][0], source))
            continue
        if number == 1:
            looked = tags
            if group["id"] == "resolution" and "4k" in tags and "sdr" in tags and not tags & {"hdr", "dv"}:
                looked = (tags - {"4k"}) | {"1080p"}               # 4K without HDR: no better than good 1080p
            position = next((n for n, r in enumerate(group["order"]) if _match(r, looked)), len(group["order"]))
            key.append(position)
            if position < len(group["order"]):
                why.append((RULES[group["order"][position]][0], T("Regel %d", "Rule %d") % number))
            continue
        weight = len(scored) - scored.index(group)
        points, best = _points(group["id"], group["order"], tags)
        total += weight * points
        if best:
            why.append((RULES[best][0], T("Regel %d", "Rule %d") % number))
    key.append(-round(total, 3))
    key += tie
    wanted = [g.lower() for g in profile["filters"].get("prefer_groups") or []]
    group = release_group(source)
    key.append(0 if group and group.lower() in wanted else 1)
    if group and group.lower() in wanted:
        why.append((group, T("Release-groep", "Release group")))
    return key, why


def _is_plain(profile):
    return profile.get("as_is")


def apply(sources, profile, kind="movie", scraper_order=None):
    """The sources that stay, best first. Each gets 'why': [(what, which rule)]."""
    scraper_order = scraper_order or []
    per = profile["scrapers"].get("per") or {}
    kept, seen = [], set()
    for position, source in enumerate(sources):
        tags = tags_of(source)
        if not _passes(source, tags, profile, kind):
            continue
        fingerprint = ((source.get("name") or "").lower(), source.get("size") or 0)
        if profile["scrapers"].get("dedupe", True) and fingerprint in seen:
            continue
        seen.add(fingerprint)
        key, why = _order_key(source, tags, profile, scraper_order, kind)
        kept.append((key, position, dict(source, why=why)))
    if profile.get("as_is"):
        kept = [([], row[1], dict(row[2], why=[])) for row in kept]
    kept.sort(key=lambda row: (row[0], row[1]))
    ranked = [row[2] for row in kept]
    if _is_plain(profile) or not any(per.get(s.get("scraper"), {}).get("own") for s in ranked):
        result = [s for s in ranked]
    else:
        result = []
        names = scraper_order + [s.get("scraper") for s in ranked if s.get("scraper") not in scraper_order]
        for name in dict.fromkeys(names):
            own = per.get(name, {}).get("own")
            block = [row[2] for row in sorted(kept, key=lambda r: r[1]) if row[2].get("scraper") == name] if own else [s for s in ranked if s.get("scraper") == name]
            result += block
    limit = int(profile["filters"].get("max_per_res") or 0)
    if limit:
        counts, trimmed = {}, []
        for source in result:
            counts[source.get("resolution")] = counts.get(source.get("resolution"), 0) + 1
            if counts[source.get("resolution")] <= limit:
                trimmed.append(source)
        result = trimmed
    return result


SAMPLES = [
    {"name": "Demo.2160p.BluRay.REMUX.DV.FEL.TrueHD.Atmos-GROUP.mkv", "resolution": "4K", "flags": ["DV", "aio_FEL", "REMUX", "ATMOS", "TRUEHD"], "size": 62e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.2160p.BluRay.REMUX.DV.P7.DTS-HD.MA-GROUP.mkv", "resolution": "4K", "flags": ["DV", "aio_P7", "REMUX", "DTS-HDMA"], "size": 55e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.2160p.BluRay.REMUX.HDR.TrueHD-GROUP.mkv", "resolution": "4K", "flags": ["HDR", "REMUX", "TRUEHD"], "size": 48e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.2160p.WEB-DL.DV.DDP5.1-GROUP.mkv", "resolution": "4K", "flags": ["DV", "WEB", "DD+", "5.1"], "size": 16e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.2160p.WEB-DL.HDR.NL-SUBS.DDP5.1-GROUP.mkv", "resolution": "4K", "flags": ["HDR", "WEB", "DD+", "aio_NL-SUBS"], "size": 14e9, "cached": False, "scraper": "AIOStreams"},
    {"name": "Demo.1080p.BluRay.REMUX.DTS-HD.MA-GROUP.mkv", "resolution": "1080p", "flags": ["AVC", "REMUX", "DTS-HDMA"], "size": 28e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.1080p.WEB-DL.DUTCH.DDP5.1-GROUP.mkv", "resolution": "1080p", "flags": ["AVC", "WEB", "DD+", "aio_NL"], "size": 5e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.720p.HDTV.AAC-GROUP.mkv", "resolution": "720p", "flags": ["AVC", "HDTV", "AAC"], "size": 0.9e9, "cached": True, "scraper": "AIOStreams"},
    {"name": "Demo.2160p.HDCAM-GROUP.mkv", "resolution": "4K", "flags": ["CAM"], "size": 4e9, "cached": True, "scraper": "AIOStreams"},
]
