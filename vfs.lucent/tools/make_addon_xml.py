"""Writes addon.xml for one Kodi version and one platform: make_addon_xml.py <21|22> <platform> <out>"""
import sys

KODI = {  # (main, general, filesystem, vfs) as (minversion, version)
    "21": (("2.0.0", "2.0.2"), ("1.0.4", "1.0.5"), ("1.1.7", "1.1.9"), ("3.0.1", "3.0.1")),
    "22": (("2.0.3", "2.0.4"), ("1.0.4", "1.0.6"), ("1.1.11", "1.1.11"), ("3.0.1", "3.0.1")),
}
LIB = {"android": 'library_android="libvfs.lucent.so"', "linux": 'library_linux="libvfs.lucent.so"',
       "windx64": 'library_windows="vfs.lucent.dll"'}

kodi, platform, out = sys.argv[1], sys.argv[2], sys.argv[3]
version = open(__file__.rsplit("/", 2)[0] + "/VERSION").read().strip()
(main, general, fs, vfs) = KODI[kodi]
text = open(__file__.rsplit("/", 2)[0] + "/addon.xml").read()
for name, (low, high) in (("main", main), ("general", general), ("filesystem", fs)):
    start = text.index('<import addon="kodi.binary.global.%s"' % name)
    end = text.index("/>", start) + 2
    text = text[:start] + '<import addon="kodi.binary.global.%s" minversion="%s" version="%s"/>' % (name, low, high) + text[end:]
start = text.index('<import addon="kodi.binary.instance.vfs"')
end = text.index("/>", start) + 2
text = text[:start] + '<import addon="kodi.binary.instance.vfs" minversion="%s" version="%s"/>' % vfs + text[end:]
lib = LIB[platform.split("-")[0]]          # android-armv7 -> android, windx64 -> windx64
start = text.index('library_')
end = text.index('/>', start)
text = text[:start] + lib + text[end:]
start = text.index("<platform>")
end = text.index("</platform>") + len("</platform>")
text = text[:start] + "<platform>%s</platform>" % platform + text[end:]
start = text.index('version="', text.index("<addon ")) + 9
end = text.index('"', start)
text = text[:start] + version + text[end:]
open(out, "w").write(text)
