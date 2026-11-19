"""A low-bitrate film whose skip targets lie inside the read-ahead, with a slow provider: the player must never hang
on an article that was queued for an extra reader. held_scenario.py [runs]"""
import os, subprocess, sys, tempfile
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
import engine_harness as h
import mile_scenario as m

COUNT = 3000
RATE = 3 * m.SEG                 # about 2 MB/s: 30 s ahead is 90 articles, inside a 64 MB read-ahead


def main():
    runs = int(sys.argv[1]) if len(sys.argv) > 1 else 5
    m.COUNT = COUNT
    server = h.FakeNntp(m.Lazy())
    server.delay = float(os.environ.get('DELAY', '0.25'))
    h.serve(server)
    bad = 0
    try:
        plan = h.write_plan([(server.server_address[1], False, 'fake', 4)], h.ids_for(COUNT), COUNT,
                            workers=4, total=COUNT * m.SEG, seconds=COUNT // 3)
        plan = plan.replace('part 0 0 %d %d' % (COUNT * m.SEG, h.SEG), 'part 0 0 %d %d' % (COUNT * m.SEG, m.SEG))
        plan = plan.replace('prefetch %d' % (256 * 1024), 'prefetch %d' % (64 << 20))
        plan = plan.replace('cache %d' % (8 * 1024 * 1024), 'cache %d' % (128 << 20))
        path = os.path.join(tempfile.mkdtemp(), 'held.plan')
        open(path, 'w').write(plan)
        exe = os.path.join(os.path.dirname(h.EXE), 'held_test')
        for run in range(runs):
            result = subprocess.run([exe, path, str(RATE), os.environ.get('PLAY', '40')], capture_output=True, text=True, timeout=200)
            print('run %d: %s' % (run + 1, result.stdout.strip()))
            bad += result.returncode != 0
        h.check('the player never hangs', bad == 0, '%d of %d runs hung' % (bad, runs))
    finally:
        server.shutdown(); server.server_close()


if __name__ == '__main__':
    main()
