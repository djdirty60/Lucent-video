"""Skip targets from the Matroska index: serve a real MKV (made with ffmpeg) and check which articles
the engine fetches for skips forward. cues_scenario.py <file.mkv>"""
import os, re, subprocess, sys, tempfile, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_harness as h

SEG = 512 * 1024
ESCAPE = re.compile(rb'[\x00\x0a\x0d\x3d]')
LINE = re.compile(rb'(?:=.|[^=]){1,128}', re.S)
SHIFT = bytes((v + 42) & 0xFF for v in range(256))


def yenc(data, start, total):
    body = ESCAPE.sub(lambda m: b'=' + bytes(((m.group()[0] + 64) & 0xFF,)), data.translate(SHIFT))
    lines = b'\r\n'.join((b'.' + line if line[:1] == b'.' else line) for line in LINE.findall(body))
    return (b'=ybegin part=1 total=1 line=128 size=%d name=fake.mkv\r\n=ypart begin=%d end=%d\r\n'
            % (total, start + 1, start + len(data)) + lines +
            b'\r\n=yend size=%d part=1 pcrc32=%08x\r\n' % (len(data), zlib.crc32(data) & 0xFFFFFFFF))


def vint(data, at, keep):
    first = data[at]
    n = 1
    while not first & (0x80 >> (n - 1)):
        n += 1
    value = first if keep else first & (0xFF >> n)
    for b in data[at + 1:at + n]:
        value = (value << 8) | b
    return value, at + n


def children(data, start, end):
    at = start
    while at < end:
        eid, at2 = vint(data, at, True)
        size, body = vint(data, at2, False)
        yield eid, body, size
        at = body + size


def cues_of(data):
    _, b = vint(data, 0, True); hsize, hbody = vint(data, b, False)
    seg_at = hbody + hsize
    _, b = vint(data, seg_at, True); _, segment = vint(data, b, False)
    cues_at = data.rfind(b'\x1c\x53\xbb\x6b')
    _, b = vint(data, cues_at, True); size, body = vint(data, b, False)
    points = []
    for eid, pb, ps in children(data, body, body + size):
        if eid != 0xBB:
            continue
        t = pos = None
        for cid, cb, cs in children(data, pb, pb + ps):
            if cid == 0xB3:
                t = int.from_bytes(data[cb:cb + cs], 'big')
            elif cid == 0xB7 and pos is None:
                for did, db, ds in children(data, cb, cb + cs):
                    if did == 0xF1:
                        pos = segment + int.from_bytes(data[db:db + ds], 'big')
        points.append((t, pos))   # TimecodeScale 1 ms (ffmpeg default)
    return sorted(points), cues_at


def main():
    data = open(sys.argv[1], 'rb').read()
    count = (len(data) + SEG - 1) // SEG
    articles = {'a%04d@fake' % i: yenc(data[i * SEG:(i + 1) * SEG], i * SEG, len(data)) for i in range(count)}
    points, cues_at = cues_of(data)
    seconds = points[-1][0] // 1000
    play = len(data) // 5
    now = max(t for t, p in points if p <= play)
    wanted = []
    for skip in (30, 60):
        before = [p for t, p in points if t <= now + skip * 1000]
        if not before or now + skip * 1000 > points[-1][0]:
            continue
        wanted.append(before[-1] // SEG)
    server = h.FakeNntp(articles)
    h.serve(server)
    try:
        plan = h.write_plan([(server.server_address[1], False, 'fake', 4)], h.ids_for(count), count,
                            workers=4, total=len(data), seconds=seconds * 3 // 2)   # a wrong running time on purpose
        plan = plan.replace('part 0 0 %d %d' % (len(data), h.SEG), 'part 0 0 %d %d' % (len(data), SEG))
        plan = plan.replace('cache %d' % (8 * 1024 * 1024), 'cache %d' % (64 << 20))
        path = os.path.join(tempfile.mkdtemp(), 'cues.plan')
        open(path, 'w').write(plan)
        exe = os.path.join(os.path.dirname(h.EXE), 'cues_test')
        result = subprocess.run([exe, path, str(cues_at), str(play)], capture_output=True, text=True, timeout=120)
        log = [l for l in result.stderr.splitlines() if 'skip targets' in l or 'error' in l.lower()]
        print('\n'.join(log))
        got = sorted({int(m[1:5]) for m in server.requests})
        far = [i for i in got if i * SEG > play + 2 * (1 << 20) and i * SEG < cues_at - (4 << 20)]
        print('play at article %d (%d s); key frames wanted at articles %s; far articles fetched %s'
              % (play // SEG, now // 1000, wanted, far))
        h.check('cues exit code', result.returncode == 0, result.stderr[-500:])
        h.check('index used', any('Matroska index' in l for l in log), log)
        h.check('skip targets on key frames', all(w in far for w in wanted), (wanted, far))
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
