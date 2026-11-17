"""Zips build/dist/vfs.lucent as vfs.lucent-<version>.zip (the version from its addon.xml), in the current folder:
zip_build.py <dist folder>. The name is the one a Kodi repository expects: <id>-<version>.zip."""
import os
import re
import sys
import zipfile

dist = sys.argv[1]
folder = os.path.join(dist, "vfs.lucent")
text = open(os.path.join(folder, "addon.xml"), encoding="utf-8").read()
version = re.search(r'<addon [^>]*version="([^"]+)"', text).group(1)
name = "vfs.lucent-%s.zip" % version
with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as archive:
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for item in sorted(files):
            full = os.path.join(root, item)
            archive.write(full, os.path.join("vfs.lucent", os.path.relpath(full, folder)).replace(os.sep, "/"))
print(name)
