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


RUN = "run_state.json"
CRASH_LINES = 600                   # the end of the previous log when Kodi did not stop cleanly
CRASH_PROPERTY = "lucent.video.lastcrash"


def mark_start():
    """At service start: a 'running' mark still standing means the previous Kodi run ended without stopping cleanly
    (a crash, a freeze, the power). Remembered for this run, so the report adds the end of that log by itself."""
    state = util.load_json(RUN, {})
    if state.get("running"):
        util.HOME.setProperty(CRASH_PROPERTY, time.strftime("%Y-%m-%d %H:%M", time.localtime(state.get("since") or time.time())))
        util.log("the previous Kodi run did not stop cleanly: its log end goes into an error report")
    util.save_json(RUN, {"running": True, "since": time.time()})


def mark_stop():
    util.save_json(RUN, {"running": False, "since": time.time()})


def _crash_files(folder):
    """Kodi's own crash logs (kodi_crashlog*.log / kodi_crash.log), newest first, only from the last two days."""
    found = []
    for place in (folder, os.path.expanduser("~")):        # CoreELEC: the log folder; desktop Linux: the home folder
        try:
            names = [n for n in os.listdir(place) if n.startswith("kodi_crash") and n.endswith(".log")]
        except OSError:
            continue
        found += [(os.path.getmtime(os.path.join(place, n)), os.path.join(place, n)) for n in names]
    return [path for when, path in sorted(set(found), reverse=True) if time.time() - when < 2 * 86400]


def build():
    folder = xbmcvfs.translatePath("special://logpath")
    path = os.path.join(folder, "kodi.log")
    lines, note = _read(path)
    head = ["Lucent Video %s" % util.ADDON.getAddonInfo("version"), "Kodi %s" % xbmc.getInfoLabel("System.BuildVersion"), "Skin %s" % xbmc.getSkinDir(),
            "System %s %s" % (platform.system(), platform.machine()), "Made %s" % time.strftime("%Y-%m-%d %H:%M"),
            "Log kodi.log: %s" % note]
    older = []
    crashed = util.HOME.getProperty(CRASH_PROPERTY)
    if crashed:
        head.append("The previous Kodi run (started %s) did not stop cleanly: a crash, a freeze or the power" % crashed)
    if len(lines) < 200 or crashed:             # after a restart or a crash: the end of the previous log is the interesting part
        older, old_note = _read(os.path.join(folder, "kodi.old.log"))
        older = older[-(max(CRASH_LINES, MAX_LINES - len(lines))):]
        head.append("Log kodi.old.log: %s" % old_note)
    crash_text = []
    if crashed:
        for crash in _crash_files(folder)[:1]:
            crash_lines, crash_note = _read(crash)
            crash_text = crash_lines[-300:]
            head.append("Crash log %s: %s" % (os.path.basename(crash), crash_note))
    head.append("")
    private = _private()
    body = ""
    if crash_text:
        body += "----- Kodi crash log -----\n" + "".join(clean(line, private) for line in crash_text) + "\n"
    if older:
        body += "----- kodi.old.log (the previous run) -----\n" + "".join(clean(line, private) for line in older)
        body += "\n----- kodi.log (this run) -----\n"
    body += "".join(clean(line, private) for line in lines)
    return "\n".join(head) + "\n" + body


PASTE = "https://paste.kodi.tv"


def save():
    choice = xbmcgui.Dialog().select(T("Foutrapport", "Error report"), [
        T("Delen via een link (paste.kodi.tv)", "Share with a link (paste.kodi.tv)"),
        T("Bewaren als bestand", "Save as a file")])
    if choice == 0:
        return share()
    if choice == 1:
        return save_file()
    return None


def share():
    """Put the cleaned report on Kodi's own paste site, after asking, and show the link with a QR code."""
    if not xbmcgui.Dialog().yesno(T("Foutrapport delen", "Share the error report"), T(
            "Het rapport komt op paste.kodi.tv, de plakplek van Kodi zelf. Iedereen met de link kan het lezen. Adressen, sleutels en "
            "wachtwoorden zijn er al uitgehaald.\n\nDoorgaan?",
            "The report goes to paste.kodi.tv, Kodi's own paste site. Anyone with the link can read it. Addresses, keys and passwords "
            "were already removed.\n\nContinue?")):
        return None
    text = build().lstrip("\ufeff")
    try:
        answer, _headers = util.http(PASTE + "/documents", raw=(text.encode("utf-8"), "text/plain; charset=utf-8"), method="POST",
                                     timeout=30)
        link = "%s/%s" % (PASTE, (answer or {})["key"])
    except Exception as error:                  # noqa: BLE001 - offline, site down, an odd answer: fall back to a file
        util.log("report: sharing failed (%s)" % type(error).__name__)
        if xbmcgui.Dialog().yesno("Lucent", T("Delen lukt nu niet. Het rapport als bestand bewaren?",
                                              "Sharing does not work right now. Save the report as a file instead?")):
            return save_file()
        return None
    import dialogs
    dialog, _picture = dialogs.link_dialog("paste", link, link.rsplit("/", 1)[-1])
    dialog.args["props"].update(heading=T("Foutrapport gedeeld", "Error report shared"), button=T("Klaar", "Done"),
                                line=T("Scan de code, of stuur deze link door: %s", "Scan the code, or send this link: %s") % link)
    dialog.doModal()
    del dialog
    return link


def save_file():
    folder = xbmcgui.Dialog().browse(3, T("Waar wil je het foutrapport bewaren?", "Where do you want to save the report?"), "files")
    if not folder:
        return
    target = os.path.join(folder, "lucent-video-rapport-%s.txt" % time.strftime("%Y%m%d-%H%M"))
    handle = xbmcvfs.File(target, "w")
    handle.write(build())
    handle.close()
    xbmcgui.Dialog().ok("Lucent", T("Het rapport is bewaard. Adressen, sleutels en wachtwoorden zijn eruit gehaald. Voeg het bestand toe aan je melding op GitHub.",
                                    "The report is saved. Addresses, keys and passwords were removed. Attach the file to your issue on GitHub."))
