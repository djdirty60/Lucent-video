"""8 Mile on a small cache: run mile_test against a fake provider and print its totals.

Real sizes: 700 KB articles, 204 MB cache, 96 MB prefetch, about 12 articles a second.
Articles are made when asked for, so the 4 GB test film never sits in memory."""
import os, re, subprocess, sys, tempfile, time, zlib
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_harness as h

SEG = int(os.environ.get('SEGKB', '700')) * 1024
COUNT = int(os.environ.get('COUNT', '6000'))
RATE = float(os.environ.get('RATE', 12 * SEG))
ESCAPE = re.compile(rb'[\x00\x0a\x0d\x3d]')
LINE = re.compile(rb'(?:=.|[^=]){1,128}', re.S)
SHIFT = bytes((v + 42) & 0xFF for v in range(256))


def payload(index):
    seed = bytes((i * 11) & 0xFF for i in range(251))
    data = (seed * (SEG // len(seed) + 1))[:SEG]
    return h.MAGIC + data[4:] if index == 0 else data


from functools import lru_cache


@lru_cache(maxsize=2)
def encoded(first):
    data = payload(0 if first else 1)
    body = ESCAPE.sub(lambda m: b'=' + bytes(((m.group()[0] + 64) & 0xFF,)), data.translate(SHIFT))
    return b'\r\n'.join((b'.' + line if line[:1] == b'.' else line) for line in LINE.findall(body)), zlib.crc32(data) & 0xFFFFFFFF


def article(index):
    lines, crc = encoded(index == 0)
    start = index * SEG
    return (b'=ybegin part=1 total=1 line=128 size=%d name=fake.mkv\r\n=ypart begin=%d end=%d\r\n'
            % (COUNT * SEG, start + 1, start + SEG) + lines +
            b'\r\n=yend size=%d part=1 pcrc32=%08x\r\n' % (SEG, crc))


def _old_article(index):
    data = payload(index)
    body = ESCAPE.sub(lambda m: b'=' + bytes(((m.group()[0] + 64) & 0xFF,)), data.translate(SHIFT))
    lines = b'\r\n'.join((b'.' + line if line[:1] == b'.' else line) for line in LINE.findall(body))
    start = index * SEG
    return (b'=ybegin part=1 total=1 line=128 size=%d name=fake.mkv\r\n=ypart begin=%d end=%d\r\n'
            % (COUNT * SEG, start + 1, start + SEG) + lines +
            b'\r\n=yend size=%d part=1 pcrc32=%08x\r\n' % (SEG, zlib.crc32(data) & 0xFFFFFFFF))


PIPE = {'next': 0.0}
PIPE_LOCK = __import__('threading').Lock()


def pipe_wait(size):
    # one shared line to the provider: BW bytes a second for all connections together
    rate = float(os.environ.get('BW', '0'))
    if rate <= 0:
        return
    with PIPE_LOCK:
        now = time.time()
        PIPE['next'] = max(now, PIPE['next']) + size / rate
        until = PIPE['next']
    time.sleep(max(0.0, until - time.time()))


class Lazy(dict):
    def get(self, mid, default=None):
        if not mid.startswith('a') or not mid.endswith('@fake'):
            return default
        index = int(mid[1:-5])
        if index >= COUNT:
            return default
        if mid not in self:
            self[mid] = article(index)
            if len(self) > 400:
                self.pop(next(iter(self)))
        found = dict.get(self, mid)
        pipe_wait(len(found))
        return found


def main():
    stamps = []
    server = h.FakeNntp(Lazy())
    server.delay = float(os.environ.get('DELAY', '0.15'))
    class Timed(list):
        def append(self, item):
            list.append(self, item); stamps.append(time.time())
    server.requests = Timed()
    h.serve(server)
    begin = time.time()
    try:
        workers = int(os.environ.get('WORKERS', '12'))
        ids = ['a%04d@fake' % i for i in range(COUNT)]
        plan = h.write_plan([(server.server_address[1], False, 'fake', workers)], ids, COUNT,
                            workers=workers, total=COUNT * SEG, seconds=int(COUNT * SEG / RATE))
        plan = plan.replace('part 0 0 %d %d' % (COUNT * SEG, h.SEG), 'part 0 0 %d %d' % (COUNT * SEG, SEG))
        plan = plan.replace('prefetch %d' % (256 * 1024), 'prefetch %d' % (int(os.environ.get('PREFETCHMB', '96')) << 20))
        plan = plan.replace('cache %d' % (8 * 1024 * 1024), 'cache %d' % (int(os.environ.get('CACHEMB', '204')) << 20))
        folder = tempfile.mkdtemp()
        path = os.path.join(folder, 'mile.plan')
        open(path, 'w').write(plan)
        exe = os.path.join(os.path.dirname(h.EXE), 'mile_test')
        result = subprocess.run([exe, path, str(RATE)], capture_output=True, text=True, timeout=300)
        print(result.stdout.strip())
        for line in result.stderr.splitlines():
            if 'end, details' in line or os.environ.get('ALL'):
                print(line)
        if os.environ.get('SHOW'):
            seen = {}
            again = []
            for mid, at in zip(server.requests, stamps):
                k = int(mid[1:-5])
                if k in seen:
                    again.append((round(seen[k] - begin, 1), round(at - begin, 1), k))
                seen.setdefault(k, at)
            print('fetched again (first s, again s, article):', again)
        print('articles asked at the provider: %d (%d different)'
              % (len(server.requests), len(set(server.requests))))
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
