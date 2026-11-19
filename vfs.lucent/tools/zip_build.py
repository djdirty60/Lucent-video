"""Zips build/dist/vfs.lucent as vfs.lucent-<version>.zip (the version from its addon.xml), in the current folder:
zip_build.py <dist folder> [add-on id, default vfs.lucent]. The name is the one a Kodi repository expects: <id>-<version>.zip."""
import os
import re
import sys
import zipfile

dist = sys.argv[1]
addon_id = sys.argv[2] if len(sys.argv) > 2 else "vfs.lucent"
folder = os.path.join(dist, addon_id)
text = open(os.path.join(folder, "addon.xml"), encoding="utf-8").read()
version = re.search(r'<addon [^>]*version="([^"]+)"', text).group(1)
name = "%s-%s.zip" % (addon_id, version)
with zipfile.ZipFile(name, "w", zipfile.ZIP_DEFLATED) as archive:
    for root, dirs, files in os.walk(folder):
        dirs.sort()
        for item in sorted(files):
            full = os.path.join(root, item)
            archive.write(full, os.path.join(addon_id, os.path.relpath(full, folder)).replace(os.sep, "/"))
print(name)
