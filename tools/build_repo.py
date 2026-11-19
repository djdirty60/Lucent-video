"""Fill djdirty60/Lucent-video: plugin.video.lucent/ (source) + repo/ (Kodi repository folder).

usage: python3 tools/build_repo.py <folder plugin.video.lucent with icon.png> <checkout of Lucent-video>
Kodi's Lucent Repository (1.0.2+) reads repo/addons.xml, repo/addons.xml.md5 and repo/zips/ from here."""
import os, re, zipfile, hashlib, shutil, sys
src, dst = sys.argv[1], sys.argv[2]
shutil.rmtree(os.path.join(dst, 'plugin.video.lucent'), ignore_errors=True)
shutil.copytree(src, os.path.join(dst, 'plugin.video.lucent'), ignore=shutil.ignore_patterns('__pycache__', '*.pyc'))
out = os.path.join(dst, 'repo'); zd = os.path.join(out, 'zips', 'plugin.video.lucent')
os.makedirs(zd, exist_ok=True)       # the previous zips stay: a Kodi with an older list must still find its zip
x = open(src + '/addon.xml', encoding='utf-8').read()
v = re.search(r'<addon [^>]*version="([^"]+)"', x).group(1)
with zipfile.ZipFile(os.path.join(zd, 'plugin.video.lucent-%s.zip' % v), 'w', zipfile.ZIP_DEFLATED) as zf:
    for root, dirs, files in os.walk(src):
        dirs[:] = sorted(d for d in dirs if d not in ('__pycache__', '.git'))
        for f in sorted(files):
            if f.endswith('.pyc'): continue
            full = os.path.join(root, f); zf.write(full, os.path.join('plugin.video.lucent', os.path.relpath(full, src)))
shutil.copy(src + '/icon.png', zd + '/icon.png')
KEEP = 3                               # the new zip and the two before it
STAGES = {'alpha': 0, 'beta': 1, 'rc': 2}
def _order(name):
    """Kodi's order: 1.0.0~beta1+91 < 1.0.0~rc1 < 1.0.0~rc2 < 1.0.0 (a ~ version comes before the final one)."""
    version = name[len('plugin.video.lucent-'):-len('.zip')]
    base, _sep, pre = version.partition('~')
    key = [int(n) for n in re.findall(r'\d+', base)][:3]
    key += [0] * (3 - len(key))
    if not pre:
        return key + [9]
    word = re.match(r'[a-z]*', pre.lower()).group(0)
    return key + [STAGES.get(word, 1)] + [int(n) for n in re.findall(r'\d+', pre)]
zips = sorted((n for n in os.listdir(zd) if n.startswith('plugin.video.lucent-') and n.endswith('.zip')), key=_order)
for old in zips[:-KEEP]:
    os.remove(os.path.join(zd, old))
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import repo_index                      # addons.xml: Lucent Video and the vfs.lucent builds (tools/add_vfs.py)
repo_index.write(dst)
print(v)
