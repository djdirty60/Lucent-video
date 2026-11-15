# -*- coding: utf-8 -*-
"""A log file testers can attach to a GitHub issue, with every address, key and password taken out."""
import os
import platform
import re
import time

import xbmc
import xbmcgui
import xbmcvfs

import util

T = util.T
RULES = [(re.compile(r"[a-z][a-z0-9+.-]*://[^\s\"'<>]+", re.I), lambda m: m.group(0) if m.group(0).startswith(("plugin://", "special://")) else "[link]"),
         (re.compile(r"(authorization|api[_-]?key|apikey|token|password|passwd|secret)\s*[=:]\s*(?:bearer\s+|basic\s+)?\S+", re.I), r"\1=[hidden]"),
         (re.compile(r"\b(bearer|basic)\s+[A-Za-z0-9+/_.=-]{6,}", re.I), r"\1 [hidden]"),
         # the same inside a link that is written out (plugin://…?item=%7B…api_key%3D…%22): '=' is '%3D' there
         (re.compile(r"(api[_-]?key|apikey|token|password|secret)(%3D|%253D)[^&%\s\"']+", re.I), r"\1\2[hidden]"),
         (re.compile(r"\b[A-Za-z0-9+/_-]{40,}={0,2}"), "[hidden]"),
         (re.compile(r"\b[0-9a-fA-F]{32}\b"), "[hidden]"),
         (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "[e-mail]"),
         (re.compile(r"(/(?:Users|home)/)[^/\s]+"), r"\1[user]")]
MAX_LINES = 1500


# what you filled in yourself and must never leave the box: your Usenet provider and indexers (Kodi itself may log a
# setting's value or a host name without 'http://' in front)
PRIVATE_SETTINGS = ("nzb_host", "nzb_user", "nzb_pass", "nzb2_host", "nzb2_user", "nzb2_pass", "easynews_user", "easynews_pass") \
    + tuple("nzb_ix%d_%s" % (n, part) for n in range(1, 11) for part in ("url", "key"))


def _private():
    found = set()
    for name in PRIVATE_SETTINGS:
        value = (util.setting(name) or "").strip()
        if len(value) < 4:
            continue
        found.add(value)
        host = re.sub(r"^[a-z]+://", "", value, flags=re.I).split("/", 1)[0]
        if len(host) >= 4:
            found.add(host)
    return sorted(found, key=len, reverse=True)


def clean(line, private=()):
    for value in private:
        line = line.replace(value, "[private]")
    for pattern, replacement in RULES:
        line = pattern.sub(replacement, line)
    return line


def _read(path):
    """(lines, note) of a log file: read straight from disk, else through Kodi. The note says what happened."""
    try:
        size = os.path.getsize(path)
    except OSError as error:
        size = None
        missing = type(error).__name__
    else:
        missing = ""
    try:
        with open(path, encoding="utf-8", errors="replace") as handle:
            lines = handle.readlines()[-MAX_LINES:]
        if lines or size == 0:
            return lines, "%d lines, %s bytes" % (len(lines), size)
        way = "read directly: 0 lines"
    except (OSError, ValueError) as error:
        way = "read directly: %s" % type(error).__name__
    try:                                        # the same file through Kodi's own file layer
        handle = xbmcvfs.File(path)
        text = handle.read()
        handle.close()
        lines = text.splitlines(True)[-MAX_LINES:]
        return lines, "%s; through Kodi: %d lines, %s bytes" % (way, len(lines), size)
    except Exception as error:                  # noqa: BLE001 - the report must always come out
        return [], "%s; through Kodi: %s%s" % (way, type(error).__name__, (" (file: %s)" % missing) if missing else "")


def build():
    folder = xbmcvfs.translatePath("special://logpath")
    path = os.path.join(folder, "kodi.log")
    lines, note = _read(path)
    head = ["Lucent Video %s" % util.ADDON.getAddonInfo("version"), "Kodi %s" % xbmc.getInfoLabel("System.BuildVersion"), "Skin %s" % xbmc.getSkinDir(),
            "System %s %s" % (platform.system(), platform.machine()), "Made %s" % time.strftime("%Y-%m-%d %H:%M"),
            "Log kodi.log: %s" % note]
    older = []
    if len(lines) < 200:                        # just after a restart: the end of the previous log is the interesting part
        older, old_note = _read(os.path.join(folder, "kodi.old.log"))
        older = older[-(MAX_LINES - len(lines)):]
        head.append("Log kodi.old.log: %s" % old_note)
    head.append("")
    private = _private()
    body = ""
    if older:
        body += "----- kodi.old.log (the previous run) -----\n" + "".join(clean(line, private) for line in older)
        body += "\n----- kodi.log (this run) -----\n"
    body += "".join(clean(line, private) for line in lines)
    return "\n".join(head) + "\n" + body


def save():
    folder = xbmcgui.Dialog().browse(3, T("Waar wil je het foutrapport bewaren?", "Where do you want to save the report?"), "files")
    if not folder:
        return
    target = os.path.join(folder, "lucent-video-rapport-%s.txt" % time.strftime("%Y%m%d-%H%M"))
    handle = xbmcvfs.File(target, "w")
    handle.write(build())
    handle.close()
    xbmcgui.Dialog().ok("Lucent", T("Het rapport is bewaard. Adressen, sleutels en wachtwoorden zijn eruit gehaald. Voeg het bestand toe aan je melding op GitHub.",
                                    "The report is saved. Addresses, keys and passwords were removed. Attach the file to your issue on GitHub."))
