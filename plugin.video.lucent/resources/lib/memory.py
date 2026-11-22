# -*- coding: utf-8 -*-
"""Clearing memory (RAM) after watching: what Lucent itself still holds (its Usenet release and buffers) is let go, so
the next film and the menus start with free memory. The log says how much memory was free before and after, measured,
not guessed."""
import gc
import threading
import time

import xbmc

import util

WAIT = 8                    # seconds after the film: Kodi first closes its own player


def _available():
    """MB of memory free for use (MemAvailable), or None where /proc/meminfo is not there (Windows, Android without access)."""
    try:
        with open("/proc/meminfo") as handle:
            for line in handle:
                if line.startswith("MemAvailable:"):
                    return int(line.split()[1]) // 1024
    except (OSError, ValueError):
        pass
    return None


def _usenet():
    """Lucent's own Usenet release and the one opened in advance, closed: their buffers go. Only when nothing reads them."""
    try:
        from nzb import server
    except Exception:       # noqa: BLE001 - no Usenet part
        return False
    closed = False
    server.drop_warm()
    if not server._state["lock"].acquire(timeout=0):       # noqa: SLF001 - a release is being opened right now
        return closed
    try:
        release = server._state["release"]                  # noqa: SLF001
        if release and time.time() - release.last_read > 5:
            server._close_release()                         # noqa: SLF001
            closed = True
    finally:
        server._state["lock"].release()                     # noqa: SLF001
    return closed


def tidy():
    """Some seconds after a film or episode stopped (not when the next one follows by itself)."""
    xbmc.Monitor().waitForAbort(WAIT)
    if xbmc.Player().isPlaying():
        return                                              # something plays again: leave it all
    before = _available()
    done = []
    if _usenet():
        done.append("Usenet release closed")
    gc.collect()
    # rc1.35: no more emptying of the Linux cache or putting memory together: on the Fire TV Cube it freed 5 MB
    # (749 -> 754 MB) and Kodi crashed seconds after a stop with it (the video decoder uses that memory too)
    after = _available()
    if before is not None and after is not None:
        util.log("memory: %d MB free after watching, %d MB now (%s)" % (before, after, ", ".join(done) or "only Python's own memory"))


def after_watching():
    threading.Thread(target=tidy, daemon=True).start()
