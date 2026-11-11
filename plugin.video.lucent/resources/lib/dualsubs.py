# -*- coding: utf-8 -*-
"""Two languages at once: your own subtitle with the English one under it (in italics), as one subtitle file. Kodi
can show only one subtitle, so the two are put together: every line of yours keeps its own time and gets the English
lines that are on screen at that moment.

Used by the automatic subtitle (setting 'Two subtitles at once') and by
    RunPlugin(plugin://plugin.video.lucent/?action=dual_subs)     (for a button on the skin's player screen)
Only SRT files can be put together; otherwise your own subtitle is shown as it is."""
import os
import re
import urllib.parse

import xbmcvfs

import util

T = util.T
SECOND = "en"
TIME = re.compile(r"(\d+):(\d+):(\d+)[,.](\d+)\s*-->\s*(\d+):(\d+):(\d+)[,.](\d+)")


def wanted():
    return util.flag("subs_dual")


def _read(path):
    with open(path, "rb") as handle:
        raw = handle.read()
    for encoding in ("utf-8-sig", "cp1252", "latin-1"):
        try:
            return raw.decode(encoding)
        except UnicodeDecodeError:
            continue
    return ""


def parse(text):
    """[(start ms, end ms, text)] of an SRT file; [] when it is not SRT."""
    cues = []
    for block in re.split(r"\r?\n\s*\r?\n", text.strip()):
        lines = block.strip().splitlines()
        for n, line in enumerate(lines[:3]):
            found = TIME.search(line)
            if found:
                g = [int(x) for x in found.groups()]
                start = ((g[0] * 60 + g[1]) * 60 + g[2]) * 1000 + g[3]
                end = ((g[4] * 60 + g[5]) * 60 + g[6]) * 1000 + g[7]
                body = "\n".join(lines[n + 1:]).strip()
                if body:
                    cues.append((start, end, body))
                break
    return cues


def _stamp(ms):
    return "%02d:%02d:%02d,%03d" % (ms // 3600000, ms // 60000 % 60, ms // 1000 % 60, ms % 1000)


def merge(mine, other):
    """One SRT text: each of your lines with the other language's lines of that moment under it, in italics."""
    out = []
    for number, (start, end, text) in enumerate(mine, start=1):
        extra = [t for s, e, t in other if s < end and e > start]
        plain = " ".join(re.sub(r"<[^>]+>", "", t).replace("\n", " ") for t in extra).strip()
        body = text + ("\n<i>%s</i>" % plain if plain else "")
        out.append("%d\n%s --> %s\n%s\n" % (number, _stamp(start), _stamp(end), body))
    return "\n".join(out)


def _second(mine_code):
    """Downloads the best subtitle in the second language for the video that plays; '' when there is none."""
    import subs_extra
    import subs_live
    sources = subs_live.subtitle_addons()
    name = subs_live.LANGS.get(SECOND, ("English", ""))[0]
    query = urllib.parse.urlencode({"action": "search", "languages": name, "preferredlanguage": name})
    for aid, _label in sources:
        try:
            files = subs_live.rpc("Files.GetDirectory", {"directory": "plugin://%s/?%s" % (aid, query), "media": "files",
                                                          "properties": ["thumbnail", "art"], "sort": {"method": "none"}}).get("files") or []
        except Exception as error:  # noqa: BLE001
            util.log("two subtitles %s: %s" % (aid, type(error).__name__))
            continue
        pick = subs_extra.best_of(files, SECOND)
        if pick:
            return subs_live.download(pick["file"])
    return ""


def combine(local, mine_code):
    """Path of a subtitle with both languages, or the own one (local) when that is not possible."""
    if mine_code == SECOND or not local.lower().endswith(".srt"):
        return local
    try:
        second = _second(mine_code)
        if not second or not second.lower().endswith(".srt"):
            util.log("two subtitles: no English SRT")
            return local
        mine, other = parse(_read(local)), parse(_read(second))
        if not mine or not other:
            return local
        folder = xbmcvfs.translatePath("special://temp/lucent_subs")
        os.makedirs(folder, exist_ok=True)
        path = os.path.join(folder, os.path.splitext(os.path.basename(local))[0] + ".dual.srt")
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(merge(mine, other))
        util.log("two subtitles: %d lines" % len(mine))
        return path
    except (OSError, ValueError) as error:
        util.log("two subtitles: %s" % type(error).__name__)
        return local


def run_now():
    """The button: search your own language and English for the video that plays and show them together."""
    import xbmc
    import subs_extra
    import subs_live
    if not xbmc.Player().isPlayingVideo():
        return
    mine = subs_live.my_code()
    if mine == SECOND:
        return util.notify(T("Je eigen taal is al Engels", "Your own language is English already"))
    util.notify(T("Twee talen: ondertitels zoeken…", "Two languages: looking for subtitles…"))
    languages, mine_name = subs_live.search_languages()
    query = urllib.parse.urlencode({"action": "search", "languages": mine_name, "preferredlanguage": mine_name})
    local = ""
    for aid, _label in subs_live.subtitle_addons():
        try:
            files = subs_live.rpc("Files.GetDirectory", {"directory": "plugin://%s/?%s" % (aid, query), "media": "files",
                                                          "properties": ["thumbnail", "art"], "sort": {"method": "none"}}).get("files") or []
        except Exception:  # noqa: BLE001
            continue
        pick = subs_extra.best_of(files, mine, subs_extra.preference())
        if pick:
            local = subs_live.download(pick["file"])
            break
    if not local:
        return util.notify(T("Geen ondertitel in je eigen taal gevonden", "No subtitle in your own language found"))
    both = combine(local, mine)
    if both == local:
        util.notify(T("Geen Engelse ondertitel om erbij te zetten", "No English subtitle to add"))
    subs_live.activate(both, mine)
