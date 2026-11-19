"""Puts a new set of vfs.lucent and vfs.lucentcheck builds in the Kodi repository folder: add_vfs.py <folder with the zips> <checkout>.

The zips come from the vfs-lucent-build branch (vfs.lucent-out/, built by GitHub Actions). The previous set
stays next to the new one (a Kodi with an older addons.xml must still find its zip); older sets go."""
import glob
import os
import re
import shutil
import sys
import zipfile

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_index  # noqa: E402

src, dst = sys.argv[1], sys.argv[2]
ADDONS = ('vfs.lucent', 'vfs.lucentcheck')     # the player's reader and the separate article check, built together


def _order(version):
    return [int(n) for n in re.findall(r'\d+', version)]


for addon in ADDONS:
    zd = os.path.join(dst, 'repo', 'zips', addon)
    new = sorted(glob.glob(os.path.join(src, '%s-*.zip' % addon)))
    if not new:
        sys.exit('no %s zips in %s' % (addon, src))
    os.makedirs(zd, exist_ok=True)

    def base(name):
        """0.1.0 for vfs.lucent-0.1.0+android.armv7.k21.zip"""
        return re.match(r'%s-([^+]+)\+' % re.escape(addon), os.path.basename(name)).group(1)

    for path in new:
        with zipfile.ZipFile(path) as archive:
            text = archive.read('%s/addon.xml' % addon).decode('utf-8')
        version = re.search(r'<addon [^>]*version="([^"]+)"', text).group(1)
        if os.path.basename(path) != '%s-%s.zip' % (addon, version):
            sys.exit('%s does not match its version %s' % (path, version))
        shutil.copy(path, zd)
    with zipfile.ZipFile(new[0]) as archive:
        open(os.path.join(zd, 'icon.png'), 'wb').write(archive.read('%s/icon.png' % addon))
    sets = sorted({base(n) for n in glob.glob(os.path.join(zd, '%s-*.zip' % addon))}, key=_order)
    for old in sets[:-2]:                               # keep the new set and the one before it
        for path in glob.glob(os.path.join(zd, '%s-%s+*.zip' % (addon, old))):
            os.remove(path)
print('%d entries in addons.xml' % repo_index.write(dst))
