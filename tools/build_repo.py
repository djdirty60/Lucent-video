"""Fill djdirty60/Lucent-video: plugin.video.lucent/ (source) + repo/ (Kodi repository folder).

usage: python3 tools/build_repo.py <folder plugin.video.lucent with icon.png> <checkout of Lucent-video>
Kodi's Lucent Repository (1.0.2+) reads repo/addons.xml, repo/addons.xml.md5 and repo/zips/ from here."""
import os, re, zipfile, hashlib, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
shutil.rmtree(os.path.join(dst, 'plugin.video.lucent'), ignore_errors=True)
shutil.copytree(src, os.path.join(dst, 'plugin.video.lucent'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
out = os.path.join(dst, 'repo'); zd = os.path.join(out, 'zips', 'plugin.video.lucent')
shutil.rmtree(zd, ignore_errors=True); os.makedirs(zd)
x = open(src + '/addon.xml', encoding='utf-8').read()
v = re.search(r'<addon [^>]*version="([^"]+)"', x).group(1)
with zipfile.ZipFile(os.path.join(zd, 'plugin.video.lucent-%s.zip' % v), 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '.git'))
        for f in sorted(files):
            if f.endswith('.pyc'): continue
            full = os.path.join(root, f); zf.write(full, os.path.join('plugin.video.lucent', os.path.relpath(full, src)))
shutil.copy(src + '/icon.png', zd + '/icon.png')
body = re.sub(r'^<\?xml[^>]*\?>\s*', '', x).rstrip()
xml = '<?xml version="1.0" encoding="UTF-8" standalone="yes"?>\n<addons>\n' + body + '\n</addons>\n'
open(out + '/addons.xml', 'w', encoding='utf-8').write(xml)
open(out + '/addons.xml.md5', 'w').write(hashlib.md5(xml.encode('utf-8')).hexdigest())
print(v)
