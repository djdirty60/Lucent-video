# -*- coding: utf-8 -*-
"""'What did they say?': 10 seconds back with the subtitle on for a moment; after that it goes off again by itself
(only when it was off). For a button on the skin's player screen, or a key you choose in the settings:

    RunPlugin(plugin://plugin.video.lucent/?action=replay)

Setting 'replay_key' writes one small keymap (userdata/keymaps/lucent_replay.xml) for that key in the full-screen
video; 'none' removes it again. Nothing else in your keymaps is touched."""
import os

import xbmc
import xbmcvfs

import util

T = util.T
BACK_SECONDS = 10
SUB_SECONDS = 14
KEYMAP = "special://profile/keymaps/lucent_replay.xml"
COMMAND = "RunPlugin(plugin://plugin.video.lucent/?action=replay)"
# setting value -> (keyboard key, remote key) and longpress or not
KEYS = {"1": ("left", "left", True), "2": ("r", "reverse", True), "3": ("f1", "red", False)}


def run():
    player = xbmc.Player()
    if not player.isPlayingVideo():
        return
    was_on = xbmc.getCondVisibility("VideoPlayer.SubtitlesEnabled")
    has = xbmc.getCondVisibility("VideoPlayer.HasSubtitles")
    try:
        player.seekTime(max(0.0, player.getTime() - BACK_SECONDS))
    except RuntimeError:
        return
    if was_on:
        return
    if not has:
        return util.notify(T("10 seconden terug (deze video heeft geen ondertitel)", "10 seconds back (this video has no subtitle)"))
    player.showSubtitles(True)
    monitor = xbmc.Monitor()
    if monitor.waitForAbort(SUB_SECONDS):
        return
    if player.isPlayingVideo() and xbmc.getCondVisibility("VideoPlayer.SubtitlesEnabled"):
        player.showSubtitles(False)


def _keymap(choice):
    keyboard, remote, long_press = KEYS[choice]
    mod = ' mod="longpress"' if long_press else ""
    return ('<?xml version="1.0" encoding="UTF-8"?>\n<!-- Lucent Video: "Wat zeiden ze?" (setting in Lucent Video). '
            'Remove it there, not here. -->\n<keymap><FullscreenVideo>'
            '<keyboard><%s%s>%s</%s></keyboard><remote><%s%s>%s</%s></remote>'
            '</FullscreenVideo></keymap>\n') % (keyboard, mod, COMMAND, keyboard, remote, mod, COMMAND, remote)


def apply_key():
    """Writes or removes the keymap to match the setting. Returns True when something changed."""
    choice = util.setting("replay_key") or "0"
    path = xbmcvfs.translatePath(KEYMAP)
    wanted = _keymap(choice) if choice in KEYS else None
    try:
        with open(path, encoding="utf-8") as handle:
            now = handle.read()
    except OSError:
        now = None
    if now == wanted:
        return False
    if wanted is None:
        try:
            os.remove(path)
        except OSError:
            pass
    else:
        os.makedirs(os.path.dirname(path), exist_ok=True)
        with open(path, "w", encoding="utf-8") as handle:
            handle.write(wanted)
    xbmc.executebuiltin("Action(reloadkeymaps)")
    util.log("replay key: %s" % ("removed" if wanted is None else "set"))
    return True
