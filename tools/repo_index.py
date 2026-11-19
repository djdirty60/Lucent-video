"""Writes repo/addons.xml and repo/addons.xml.md5 from what is in the repository folder:
Lucent Video (plugin.video.lucent/addon.xml) and every vfs.lucent and vfs.lucentcheck build in repo/zips/.

vfs.lucent comes as one zip per platform and Kodi version (0.1.0+android.armv7.k21 ...). They all stand in
addons.xml; Kodi only looks at the ones with its own <platform> whose Kodi interfaces it has, and takes the newest."""
import glob
import hashlib
import os
import re
import zipfile


def _body(xml):
    return re.sub(r'^<\?xml[^>]*\?>\s*', '', xml).rstrip()


def write(dst):
    out = os.path.join(dst, 'repo')
    parts = [_body(open(os.path.join(dst, 'plugin.video.lucent', 'addon.xml'), encoding='utf-8').read())]
    for addon in ('vfs.lucent', 'vfs.lucentcheck'):
        for path in sorted(glob.glob(os.path.join(out, 'zips', addon, '%s-*.zip' % addon))):
            with zipfile.ZipFile(path) as archive:
                parts.append(_body(archive.read('%s/addon.xml' % addon).decode('utf-8')))
    xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + '\n'.join(parts) + '\n</addons>\n'
    open(os.path.join(out, 'addons.xml'), 'w', encoding='utf-8').write(xml)
    open(os.path.join(out, 'addons.xml.md5'), 'w').write(hashlib.md5(xml.encode('utf-8')).hexdigest())
    return len(parts)
