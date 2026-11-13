# -*- coding: utf-8 -*-
"""Lucent Video's own screens in any skin.

The layouts in resources/skins/Default/1080i use the Lucent skin's colour names (text, panel, focus ...) and font names
(font_label, font_heading ...). Other skins do not have those: text and panels would be invisible. So with another skin
Lucent makes, once per version, a copy of its screens in which every colour name is its fixed colour value and every
font is one that (almost) every skin has (font10, font13, ...). With the Lucent skin nothing changes.

    path()        the folder to give to WindowXMLDialog
    color(name)   a colour for a ListItem property: the name in the Lucent skin, the value in another skin"""
import os
import re
import shutil

import xbmc
import xbmcvfs

import util

LUCENT = "skin.lucent"
ADDON_PATH = xbmcvfs.translatePath(util.ADDON.getAddonInfo("path"))
# The Lucent skin's own default colours (colors/defaults.xml), for the colour names Lucent Video's screens use.
COLORS = {"amber": "FFF2B84B", "bg": "FF0B1119", "bg_95": "F20B1119", "card": "14FFFFFF", "card_focus": "FF2C3E4C",
          "dim_strong": "CC000000", "focus": "FFFFFFFF", "glass": "1AFFFFFF", "glass_strong": "24FFFFFF",
          "glow_bold": "B3D6F0FF", "glow_subtle": "33D6F0FF", "green": "FF4CC38A", "hairline": "2EFFFFFF",
          "lavender": "FFC9B8FF", "on_focus": "FF0B1119", "on_focus2": "FF3C4A56", "panel": "E0162532", "red": "FFE5534B",
          "text": "FFFFFFFF", "text2": "FFDCE2E7", "text3": "FFBDC8D1", "text4": "FF8F9BA6", "white": "FFFFFFFF"}
# Fonts nearly every skin has (Kodi itself falls back to font13 when one is missing).
FONTS = {"font_tiny": "font10", "font_small_bold": "font12", "font_caps": "font12", "font_label": "font13",
         "font_label_bold": "font13", "font_body": "font13", "font_plot": "font13", "font_heading_small": "font13",
         "font_heading": "font30", "font_hero": "font37"}
COLOR_TAGS = r"(<(?:textcolor|focusedcolor|disabledcolor|selectedcolor|shadowcolor|invalidcolor|colordiffuse)>)([^<$]+)(<)"
COLOR_ATTRS = r'((?:colordiffuse|textcolor|focusedcolor|color)=")([^"$]+)(")'


def lucent():
    return xbmc.getSkinDir() == LUCENT


def color(name):
    return name if lucent() else COLORS.get(name, name)


def _convert(text):
    text = re.sub(COLOR_TAGS, lambda m: m.group(1) + COLORS.get(m.group(2).strip(), m.group(2)) + m.group(3), text)
    text = re.sub(COLOR_ATTRS, lambda m: m.group(1) + COLORS.get(m.group(2).strip(), m.group(2)) + m.group(3), text)
    return re.sub(r"<font>([^<]+)</font>", lambda m: "<font>%s</font>" % FONTS.get(m.group(1).strip(), m.group(1)), text)


def _build(target):
    """A copy of the screens with fixed colours and common fonts; the pictures are copied as they are."""
    source = os.path.join(ADDON_PATH, "resources", "skins", "Default")
    work = target + ".new"
    shutil.rmtree(work, ignore_errors=True)
    layouts = os.path.join(work, "resources", "skins", "Default", "1080i")
    os.makedirs(layouts)
    for name in os.listdir(os.path.join(source, "1080i")):
        if name.endswith(".xml"):
            with open(os.path.join(source, "1080i", name), encoding="utf-8") as handle:
                text = _convert(handle.read())
            with open(os.path.join(layouts, name), "w", encoding="utf-8") as handle:
                handle.write(text)
    shutil.copytree(os.path.join(source, "media"), os.path.join(work, "resources", "skins", "Default", "media"))
    shutil.rmtree(target, ignore_errors=True)
    os.rename(work, target)


def path():
    """Where the screens are read from: the add-on itself with the Lucent skin, the converted copy with any other."""
    if lucent():
        return ADDON_PATH
    target = os.path.join(util.PROFILE, "anyskin", util.ADDON.getAddonInfo("version"))
    if not os.path.isfile(os.path.join(target, "resources", "skins", "Default", "1080i", "lucent_sources.xml")):
        try:
            shutil.rmtree(os.path.join(util.PROFILE, "anyskin"), ignore_errors=True)      # older versions
            _build(target)
            util.log("screens made for skin %s" % xbmc.getSkinDir())
        except OSError as error:
            util.log("screens for other skins: %s" % error)
            return ADDON_PATH
    return target
