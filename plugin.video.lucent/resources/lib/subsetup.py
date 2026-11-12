# -*- coding: utf-8 -*-
"""The setup steps 'Subtitles' and 'Skip intro' (taken over from the Lucent skin), as short wizards:
action=subs_setup and action=skip_setup. Each shows what is done and offers the next steps."""
import json

import xbmc
import xbmcaddon
import xbmcgui

import subconf
import util

T = util.T
SUBS = subconf.SUBS_ADDON


def has(addon_id):
    return xbmc.getCondVisibility("System.HasAddon(%s)" % addon_id)


def rpc(method, params):
    answer = json.loads(xbmc.executeJSONRPC(json.dumps({"jsonrpc": "2.0", "id": 1, "method": method, "params": params})))
    return answer.get("result") or {}


def install(addon_id):
    xbmc.executebuiltin("InstallAddon(%s)" % addon_id)


# ------------------------------------------------------------------ subtitles
def subs_default():
    return (rpc("Settings.GetSettingValue", {"setting": "subtitles.movie"}) or {}).get("value") == SUBS


def subs_keys():
    """(OpenSubtitles key + login filled in, SubDL key filled in) of Lucent Ondertitels."""
    try:
        addon = xbmcaddon.Addon(SUBS)
        return (bool(addon.getSetting("os_key").strip() and addon.getSetting("os_user").strip() and addon.getSetting("os_pass")),
                bool(addon.getSetting("subdl_key").strip()))
    except Exception:          # noqa: BLE001 - not installed
        return False, False


def subs_state():
    if not has(SUBS):
        return T("Lucent Ondertitels is niet geïnstalleerd", "Lucent Subtitles is not installed")
    os_ok, subdl_ok = subs_keys()
    parts = [T("Lucent Ondertitels is de standaard", "Lucent Subtitles is the default") if subs_default()
             else T("Lucent Ondertitels is nog niet de standaard", "Lucent Subtitles is not the default yet"),
             "OpenSubtitles ✓" if os_ok else T("OpenSubtitles: geen account", "OpenSubtitles: no account"),
             "SubDL ✓" if subdl_ok else T("SubDL: geen sleutel", "SubDL: no key")]
    parts.append(util.HOME.getProperty("lucent.video.subs.state") or "")
    return "  ·  ".join(p for p in parts if p)


def subs_actions():
    import subs_live
    if not has(SUBS):
        return [(T("Lucent Ondertitels installeren", "Install Lucent Subtitles"), lambda: install(SUBS))]
    out = [(T("Accounts en sleutels", "Accounts and keys"), lambda: xbmc.executebuiltin("Addon.OpenSettings(%s)" % SUBS))]
    if not subs_default():
        import subwatch
        out.append((T("Als standaard instellen", "Make it the default"), subwatch.set_default))
    out.append((T("Automatisch zoeken", "Search automatically"), subs_live.choose_mode))
    spoken = subconf.text("lucent.audio.label") or T("Kodi beslist", "Kodi decides")
    out.append((T("Gesproken taal: %s", "Spoken language: %s") % spoken, subs_live.choose_audio))
    out.append((T("AI-vertaling", "AI translation"), subs_live.enter_key))
    return out


INTRO_SUBS = T("Lucent Ondertitels vraagt OpenSubtitles, SubDL en Podnapisi tegelijk en zet de ondertitels die precies bij je video "
               "passen bovenaan. Maak twee gratis accounts en vul ze in bij 'Accounts en sleutels':[CR]"
               "1. opensubtitles.com: aanmelden, dan Profiel › API consumers › New consumer (de API-sleutel), plus je gebruikersnaam en wachtwoord.[CR]"
               "2. subdl.com: aanmelden, dan Account › API-sleutel.[CR]Optioneel: een AI-sleutel (Gemini is gratis, of OpenRouter of Claude) "
               "vertaalt Engelse ondertitels. Bij 'Gesproken taal' kies je met welk geluid een film start.",
               "Lucent Subtitles asks OpenSubtitles, SubDL and Podnapisi at the same time and puts the subtitles that fit your video "
               "exactly first. Make two free accounts and fill them in under 'Accounts and keys':[CR]"
               "1. opensubtitles.com: sign up, then Profile › API consumers › New consumer (the API key), plus your user name and password.[CR]"
               "2. subdl.com: sign up, then Account › API key.[CR]Optional: an AI key (Gemini is free, or OpenRouter or Claude) translates "
               "English subtitles. Under 'Spoken language' you choose which sound a film starts with.")


# ------------------------------------------------------------------ skip intro (built into Lucent, skipper.py)
def _modes():
    import skipper
    return {k: skipper.mode(k) for k in skipper.KINDS}


def _mode_name(value):
    return (T("uit", "off"), T("knop", "button"), T("vanzelf", "by itself"))[value]


def skip_auto():
    return all(value == 2 for value in _modes().values())


def toggle_skip_auto():
    """All three parts by itself, or all three back to a button."""
    value = "1" if skip_auto() else "2"
    for kind in ("intro", "recap", "preview", "credits"):
        xbmcaddon.Addon(util.ADDON_ID).setSetting("skip_%s" % kind, value)


def install_skip():
    util.notify(T("Intro overslaan zit nu in Lucent Video zelf", "Skip intro is now built into Lucent Video"))


def skip_state():
    import skipper
    if skipper.old_addon_on():
        return T("De losse TheIntroDB-add-on staat nog aan; zolang blijft Lucent's eigen knop uit",
                 "The separate TheIntroDB add-on is still on; until it is off, Lucent's own button stays off")
    names = {"intro": T("Intro", "Intro"), "recap": T("Terugblik", "Recap"), "preview": T("Preview", "Preview"), "credits": T("Aftiteling", "Credits")}
    return "  ·  ".join("%s: %s" % (names[k], _mode_name(v)) for k, v in _modes().items())


def skip_short():
    """For the skin: 'Button', 'Skips by itself', 'Off' or 'Mixed'."""
    values = set(_modes().values())
    if values == {2}:
        return T("Vanzelf overslaan", "Skips by itself")
    if values == {0}:
        return T("Uit", "Off")
    if values == {1}:
        return T("Knop", "Button")
    return T("Gemengd", "Mixed")


def _cycle(kind):
    import skipper
    value = (skipper.mode(kind) + 1) % 3
    xbmcaddon.Addon(util.ADDON_ID).setSetting("skip_%s" % kind, str(value))


def _switch_old_off():
    import skipper
    skipper.take_over()
    rpc("Addons.SetAddonEnabled", {"addonid": skipper.OLD, "enabled": False})


def skip_actions():
    import skipper
    out = []
    if skipper.old_addon_on():
        out.append((T("Losse TheIntroDB-add-on uitzetten (instellingen overnemen)", "Switch off the separate TheIntroDB add-on (take over its settings)"),
                    _switch_old_off))
    names = {"intro": T("Intro", "Intro"), "recap": T("Terugblik ('eerder in...')", "Recap ('previously on...')"), "preview": T("Preview", "Preview"),
             "credits": T("Aftiteling", "Credits")}
    for kind, value in _modes().items():
        out.append(("%s: %s" % (names[kind], _mode_name(value)), lambda k=kind: _cycle(k)))
    out.append((T("Alles in de instellingen", "Everything in the settings"),
                lambda: xbmc.executebuiltin("RunPlugin(plugin://plugin.video.lucent/?action=settings&section=skip)")))
    return out


INTRO_SKIP = T("Lucent Video weet via TheIntroDB waar de intro, de terugblik ('eerder in...'), de aftiteling en de preview van een aflevering of "
               "film beginnen en eindigen. Komt er een langs, dan verschijnt rechtsonder een kaartje met 'Overslaan' en 'Kijken'. Per stuk kies je: uit, "
               "een knop, of vanzelf (dan telt het kaartje af). Per serie kan het ook: kies 'Intro vanzelf overslaan' in het menu van een serie.[CR]"
               "Een aparte add-on of een API-sleutel is niet nodig.",
               "Through TheIntroDB, Lucent Video knows where the intro, the recap ('previously on...'), the credits and the preview of an "
               "episode or film start and end. When one comes along, a card with 'Skip' and 'Watch' appears at the bottom right. Per part you choose: off, a "
               "button, or by itself (the card then counts down). It can also be done per series: choose 'Skip intro by itself' in the "
               "menu of a series.[CR]No separate add-on or API key is needed.")


# ------------------------------------------------------------------ the wizard
def wizard(kind):
    """kind: subs or skip. A list with the state on top; choosing an action does it and shows the list again."""
    heading = T("Ondertitels", "Subtitles") if kind == "subs" else T("Intro overslaan", "Skip intro")
    if not util.HOME.getProperty("lucent.video.%s.explained" % kind):
        util.HOME.setProperty("lucent.video.%s.explained" % kind, "1")
        xbmcgui.Dialog().textviewer(heading, INTRO_SUBS if kind == "subs" else INTRO_SKIP)
    while True:
        state, actions = (subs_state(), subs_actions()) if kind == "subs" else (skip_state(), skip_actions())
        rows = [label for label, _do in actions] + [T("Klaar", "Done")]
        picked = xbmcgui.Dialog().select("%s  ·  %s" % (heading, state), rows)
        if picked < 0 or picked >= len(actions):
            break
        actions[picked][1]()
        xbmc.sleep(300)
    subconf.publish()
