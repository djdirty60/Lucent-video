# -*- coding: utf-8 -*-
"""Clearing memory (RAM) after watching: what Lucent itself still holds is let go, and on boxes where Kodi may do it
(CoreELEC, LibreELEC: Kodi runs as root) the Linux cache is emptied, so the next film and the menus start with free memory.
The log says how much memory was free before and after, measured, not guessed."""
import gc
import os
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


def _write(path, value):
    try:
        with open(path, "w") as handle:
            handle.write(value)
        return True
    except OSError:
        return False


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
    if hasattr(os, "sync"):
        os.sync()
    if _write("/proc/sys/vm/drop_caches", "1"):
        done.append("Linux cache emptied")
    if _write("/proc/sys/vm/compact_memory", "1"):
        done.append("memory put together")
    after = _available()
    if before is not None and after is not None:
        util.log("memory: %d MB free after watching, %d MB now (%s)" % (before, after, ", ".join(done) or "only Python's own memory"))


def after_watching():
    threading.Thread(target=tidy, daemon=True).start()
