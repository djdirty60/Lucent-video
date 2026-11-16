# -*- coding: utf-8 -*-
"""A release as one long file that Kodi can read anywhere (seek), straight from the provider's articles.

The film is made of pieces: a video posted as is is one piece; a stored RAR set gives one piece per volume (the film's
data inside that volume). A posted file is cut in articles of a fixed decoded size (the poster's part size, learnt
from the first article), so 'byte X of the film' -> piece -> article number -> bytes, without downloading anything
else. While Kodi reads, the next articles are fetched ahead with several connections; the last ones stay in memory, so
a short step back does not hit the network again. Nothing is written to disk.
"""
import bisect
import collections
import re
import threading
import time
from concurrent.futures import CancelledError, Future, ThreadPoolExecutor, TimeoutError as FutureTimeout

from . import archive, nntp, nzbfile, sevenzip, yenc


FILL_SHARE = 0.02          # at most 2% of a release may be filled with zeros
FILL_RUN = 4               # and at most 4 articles in a row
SAMPLE_GONE = 0.1          # more than this share gone in the sample: refuse at once, without counting
COUNT_LIMIT = 20           # seconds the full gap count may take before playing starts
COUNT_PART = 500           # articles per STAT round
SAMPLE_PART = 10           # sample articles per connection
KEEP_READ = 8              # articles Kodi just read that stay in memory anyway (a small step back)
AHEAD_SECONDS = 15         # a heavy film: read at least this many seconds of it ahead
AHEAD_MOST = 192 * 1024 * 1024    # but never more than this (memory on small boxes)
PIPELINE = True            # read-ahead asks two neighbouring articles at once over one connection
PRIORITY = True            # what Kodi waits for goes before read-ahead, stale read-ahead is dropped on a jump
SLOW_ARTICLE = 15          # an article this many seconds late is told in the log
URGENT = 3                 # articles Kodi waits for, fetched before any read-ahead
COUNT_WORKERS = 8          # connections used for counting
PIN_START = 4 * 1024 * 1024
PIN_END = 16 * 1024 * 1024


class StreamError(Exception):
    """This release cannot be played; the text says why (shown in the log, never with a login in it)."""


def _vint_len(value):
    length = 1
    while value >= 0x80:
        value >>= 7
        length += 1
    return length


class Piece:
    def __init__(self, posted, data_offset, data_length, verified):
        self.posted = posted
        self.data_offset = data_offset      # where the film's bytes start in this posted file
        self.data_length = data_length
        self.verified = verified            # the layout was read from this volume itself (not worked out)
        self.start = 0                      # where this piece starts in the film


class Release:
    # read 64 MB ahead (some seconds of a heavy 4K remux) and keep 128 MB: more connections busy before a busy scene
    def __init__(self, pool, files, kind, chosen, ahead=64 * 1024 * 1024, cache=128 * 1024 * 1024, log=None, backup=None,
                 fill=False):
        self.pool = pool
        self.backup = backup                # a second provider, only asked for articles the first one lacks
        self.fill = fill                    # play on over a few missing articles (zeros), up to FILL_SHARE / FILL_RUN
        self.filled = set()                 # (file index, article) filled with zeros
        self.pinned = set()                 # articles at the very start and end of the film (the MKV index): kept
        self.files = files
        self.kind = kind                    # 'video' or 'rar'
        self.chosen = chosen                # [nzbfile.Posted]
        self.ahead = ahead
        self.cache_limit = max(cache, ahead + 16 * 1024 * 1024)
        self.log = log or (lambda text: None)
        self.lock = threading.RLock()
        self.cache = collections.OrderedDict()   # (file index, article) -> bytes
        self.used = collections.OrderedDict()    # articles Kodi has read, oldest first: these go first when memory is full
        self.cached = 0
        self.pending = {}                   # (file index, article) -> Future
        self.part = {}                      # file index -> decoded bytes per article
        self.uneven = set()                 # file indexes whose articles are not all the same size
        self.ranges = {}                    # (file index, article) -> (first byte, last byte) in that file, from yEnc
        self.file_size = {}                 # file index -> decoded size of the whole posted file
        # read-ahead leaves a couple of connections free, so what Kodi waits for (a jump) never queues behind it
        keep_free = (2 if pool.limit >= 16 else 1 if pool.limit >= 8 else 0) if PRIORITY else 0
        self.workers = ThreadPoolExecutor(max_workers=max(2, pool.limit - keep_free))
        self.urgent = ThreadPoolExecutor(max_workers=URGENT)    # only for what Kodi is waiting for right now
        self.pieces = []
        self.size = 0
        self.name = ""
        self.closed = False
        self.last_read = time.time()
        self.opened = time.time()
        self.want = None                    # for an episode: the pattern its name must have (if it has one at all)
        self._want_key = None               # (season, episode) of that episode
        self.stats = {"in": 0, "out": 0, "waits": 0, "waited": 0.0}     # for the speed line in the log

    # ------------------------------------------------------------------ articles
    def _body(self, message_id, urgent=False):
        """The article from the provider, or from the backup provider when the first one does not have it."""
        try:
            return self.pool.body(message_id, urgent=urgent) if urgent else self.pool.body(message_id)
        except nntp.Missing:
            if not self.backup:
                raise
            body = self.backup.body(message_id)
            self.stats["backup"] = self.stats.get("backup", 0) + 1
            return body

    def _decode(self, posted, number, urgent=False, body=None):
        _n, _size, message_id = posted.segments[number]
        started = time.time()
        if body is None:
            body = self._body(message_id, urgent) if urgent else self._body(message_id)
            fetched = time.time()
            self.stats["net"] = self.stats.get("net", 0.0) + fetched - started  # waiting for the provider (all connections)
        else:
            fetched = started                       # fetched with its neighbour: that time is counted there
        data, info = yenc.decode(body)
        stats = self.stats
        stats["raw"] = stats.get("raw", 0) + len(body)
        stats["decode"] = stats.get("decode", 0.0) + time.time() - fetched   # unpacking in Python
        if info.get("begin"):
            self.ranges[(posted.index, number)] = (info["begin"] - 1, info["end"] - 1)
        if info.get("size"):
            self.file_size.setdefault(posted.index, info["size"])
        part = self.part.get(posted.index)
        if part is None:
            # the poster's part size: from where this article starts (any article but the first), else its length
            part = (info["begin"] - 1) // number if number and info.get("begin") else (info["end"] - info["begin"] + 1)
            if part > 0:
                self.part[posted.index] = part
        short = info.get("begin") and part and number < len(posted.segments) - 1 and info["end"] - info["begin"] + 1 != part
        if info.get("begin") and part and (info["begin"] - 1 != number * part or short) and posted.index not in self.uneven:
            self.uneven.add(posted.index)       # from now on this file is read article by article (see _locate)
            self.log("articles of uneven size in %s: mapped one by one" % (posted.name or posted.index))
        return data, info

    def _locate(self, posted, at):
        """(article, skip) for byte `at` of a file with uneven articles. A first guess from the sizes in the NZB (the
        encoded size grows evenly with the decoded size), then a step or two up or down with what yEnc tells."""
        segments = posted.segments
        total = self.file_size.get(posted.index) or 0
        encoded = posted.encoded or 1
        if not hasattr(posted, "_starts"):
            starts, run = [], 0
            for _n, size, _m in segments:
                starts.append(run)
                run += size
            posted._starts = starts             # noqa: SLF001 - a cache on the posted file itself
        guess = bisect.bisect_right(posted._starts, at * encoded / total) - 1 if total else 0
        number = max(0, min(len(segments) - 1, guess))
        for _ in range(12):
            self.article(posted, number)
            first, last = self.ranges.get((posted.index, number), (None, None))
            if first is None:
                break
            if at < first and number > 0:
                number -= 1
            elif at > last and number < len(segments) - 1:
                number += 1
            else:
                return number, at - first
        raise StreamError("uneven articles: byte %d not found" % at)

    def article(self, posted, number):
        """The decoded bytes of one article, from memory, from a fetch already running, or fetched now."""
        key = (posted.index, number)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                self._mark_used(key)
                return self.cache[key]
            future = self.pending.get(key)
            if PRIORITY and future is not None and future.cancel():
                future = None                       # it was still waiting in the read-ahead queue: fetch it first
            if future is None:
                if PRIORITY:
                    self._drop_stale(posted, number)    # a jump: read-ahead for the old place is no use any more
                    future = self.urgent.submit(self._fetch, posted, number, True)
                else:
                    future = self.workers.submit(self._fetch, posted, number)
                self.pending[key] = future
        started = time.time()
        try:
            try:
                data = future.result(timeout=SLOW_ARTICLE)
            except FutureTimeout:
                self._tell_slow(posted, number, future)     # so a hang shows up in the log: what it is waiting for
                data = future.result(timeout=60 - SLOW_ARTICLE)
        except CancelledError:
            return self.article(posted, number)     # another read jumped elsewhere and cancelled it: ask again
        except nntp.Missing:
            data = self._filler(posted, number)
        with self.lock:
            self._mark_used(key)
        waited = time.time() - started
        if waited > 0.05:                   # Kodi had to wait for this article: read-ahead was not far enough
            self.stats["waits"] += 1
            self.stats["waited"] += waited
            self.stats["longest"] = max(self.stats.get("longest", 0.0), waited)
        return data

    def _tell_slow(self, posted, number, future):
        pool = self.pool
        state = "running" if future.running() else "done" if future.done() else "waiting in the queue"
        self.log("article %d of %s: no answer after %d s (%s; connections %d of %d in use, %d idle, %d asked first)"
                 % (number, posted.name or posted.index, SLOW_ARTICLE, state, pool.count - len(pool.idle), pool.limit,
                    len(pool.idle), pool.urgent))

    def _filler(self, posted, number):
        """Zeros for an article that is gone everywhere, when that is allowed: at most FILL_SHARE of all articles and
        FILL_RUN in a row. The picture shows a short glitch; the film plays on. Otherwise the release is given up
        (and the player moves on to the next source)."""
        key = (posted.index, number)
        part = self.part.get(posted.index)
        total = sum(len(p.segments) for p in self.chosen) or 1
        run = 1
        while (posted.index, number - run) in self.filled:
            run += 1
        if not self.fill or not part or posted.index in self.uneven or run > FILL_RUN or len(self.filled) + 1 > max(1, total * FILL_SHARE):
            raise StreamError("article %d of %s is gone" % (number, posted.name or posted.index))
        size = part
        whole = self.file_size.get(posted.index)
        if whole and number == len(posted.segments) - 1:
            size = whole - number * part
        with self.lock:
            self.filled.add(key)
            self.ranges[key] = (number * part, number * part + size - 1)
            self.cache[key] = bytes(size)
            self.cached += size
        self.log("article %d of %s is gone: filled in (%d so far)" % (number, posted.name or posted.index, len(self.filled)))
        return self.cache[key]

    def fit_to_film(self, minutes):
        """A heavy film (a remux) needs more per second: read at least AHEAD_SECONDS of it ahead, up to AHEAD_MOST."""
        if not minutes or not self.size:
            return
        per_second = self.size / (minutes * 60.0)
        want = min(AHEAD_MOST, max(self.ahead, int(per_second * AHEAD_SECONDS)))
        if want > self.ahead:
            self.ahead = want
            self.cache_limit = max(self.cache_limit, want + 64 * 1024 * 1024)
        self.log("film needs %.1f MB/s: reading %d MB ahead (%.0f s), memory %d MB"
                 % (per_second / 1e6, self.ahead >> 20, self.ahead / per_second, self.cache_limit >> 20))

    def _drop_stale(self, posted, number):
        """Cancel read-ahead that has not started and lies outside the new reading window (called under the lock)."""
        part = self.part.get(posted.index) or 700000
        window = max(1, self.ahead // part) + 2
        for key, future in list(self.pending.items()):
            if key[0] == posted.index and number <= key[1] <= number + window:
                continue
            if future.cancel():
                del self.pending[key]

    def _fetch(self, posted, number, urgent=False):
        key = (posted.index, number)
        try:
            data, _info = self._decode(posted, number, urgent) if urgent else self._decode(posted, number)
        finally:
            with self.lock:
                self.pending.pop(key, None)
        self._store(key, data)
        return data

    def _mark_used(self, key):
        """Kodi has read this article (called under the lock)."""
        self.used[key] = None
        self.used.move_to_end(key)

    def _store(self, key, data):
        with self.lock:
            self.stats["in"] += len(data)
            if key not in self.cache:
                self.cache[key] = data
                self.cached += len(data)
                self._make_room()

    def _make_room(self):
        """Memory full: first what Kodi has already read (oldest first, the last few stay for a small step back), only
        then the oldest of the rest. Before, read-ahead that Kodi had not reached yet could go first: then it was
        fetched a second time while Kodi waited (a remux on a slow box kept stopping)."""
        while self.cached > self.cache_limit and len(self.used) > KEEP_READ:
            old, _none = self.used.popitem(last=False)
            if old in self.cache and old not in self.pinned:
                self.cached -= len(self.cache.pop(old))
        rounds = len(self.cache)
        while self.cached > self.cache_limit and len(self.cache) > 1 and rounds > 0:
            rounds -= 1
            old, dropped = self.cache.popitem(last=False)
            if old in self.pinned:          # the MKV index: back at the end, it stays
                self.cache[old] = dropped
                continue
            self.used.pop(old, None)
            self.cached -= len(dropped)

    def _fetch_pair(self, posted, numbers, futures):
        """Read-ahead: two neighbouring articles over one connection, both asked at once (pipelined), so the provider's
        answer time is paid once. Each article's own future is filled the moment it is in."""
        todo = [(n, f) for n, f in zip(numbers, futures) if f.set_running_or_notify_cancel()]
        if not todo:
            return                                  # both cancelled (Kodi jumped elsewhere)
        started = time.time()

        def done(n, future, body):
            try:
                if body is None or isinstance(body, nntp.Missing):
                    data, _info = self._decode(posted, n)              # alone, with the backup provider when it is gone
                else:
                    data, _info = self._decode(posted, n, body=body)
                self._store((posted.index, n), data)
                future.set_result(data)
            except BaseException as error:          # noqa: BLE001 - handed to whoever reads this article
                future.set_exception(error)
            finally:
                with self.lock:
                    if self.pending.get((posted.index, n)) is future:
                        del self.pending[(posted.index, n)]

        finished = set()

        def each(index, body):
            n, future = todo[index]
            finished.add(n)
            done(n, future, body)

        try:
            if len(todo) == 2:
                self.pool.body_many([posted.segments[n][2] for n, _f in todo], each)
                self.stats["net"] = self.stats.get("net", 0.0) + time.time() - started
                self.stats["pairs"] = self.stats.get("pairs", 0) + 1
        except Exception:                           # noqa: BLE001 - the pair failed: the rest each on its own
            pass
        for n, future in todo:
            if n not in finished:
                done(n, future, None)

    def _ahead(self, posted, number):
        """Ask for the articles after this one, so they are there before Kodi wants them."""
        part = self.part.get(posted.index) or 700000
        count = max(1, self.ahead // part)
        with self.lock:
            if self.closed:
                return
            wanted = [n for n in range(number + 1, min(len(posted.segments), number + 1 + count))
                      if (posted.index, n) not in self.cache and (posted.index, n) not in self.pending]
            at = 0
            while at < len(wanted):
                pair = wanted[at:at + 2]
                if PIPELINE and len(pair) == 2 and pair[1] == pair[0] + 1:
                    futures = [Future(), Future()]
                    for n, future in zip(pair, futures):
                        self.pending[(posted.index, n)] = future
                    self.workers.submit(self._fetch_pair, posted, pair, futures)
                    at += 2
                else:
                    self.pending[(posted.index, pair[0])] = self.workers.submit(self._fetch, posted, pair[0])
                    at += 1

    # ------------------------------------------------------------------ opening
    def check(self, sample=40):
        """STAT on articles spread over the release: gone ones mean it cannot be played to the end."""
        ids = []
        for posted in self.chosen:
            segments = posted.segments
            for n in sorted({0, len(segments) - 1, len(segments) // 2}):
                ids.append(segments[n][2])
        step = max(1, sum(len(p.segments) for p in self.chosen) // sample)
        flat = [s[2] for p in self.chosen for s in p.segments]
        ids += flat[::step]
        ids = list(dict.fromkeys(ids))[:sample * 2]
        started = time.time()
        answer = self._stat_spread(self.pool, ids)
        missing = [i for i, ok in answer.items() if not ok]
        if missing and self.backup:
            on_backup = self._stat_spread(self.backup, missing)
            missing = [i for i in missing if not on_backup.get(i)]
        self.log("checked %d articles in %.1f s, %d gone" % (len(answer), time.time() - started, len(missing)))
        if not missing:
            return
        if self.fill and len(missing) > max(2, len(answer) * SAMPLE_GONE):
            # far too much gone in the sample already (often the whole release was taken down): no need to count
            raise StreamError("%d of %d checked articles are gone" % (len(missing), len(answer)))
        if self.fill:
            # the sample says some are gone: count them all before deciding. Over several connections at once and
            # within COUNT_LIMIT seconds, so a large release can never keep the player waiting.
            flat_ids = [s[2] for p in self.chosen for s in p.segments]
            parts = [flat_ids[at:at + COUNT_PART] for at in range(0, len(flat_ids), COUNT_PART)]
            started = time.time()
            gone, done = [], 0
            workers = max(1, min(COUNT_WORKERS, self.pool.limit))
            pool_run = ThreadPoolExecutor(max_workers=workers)
            try:
                jobs = [pool_run.submit(self._count_part, part, started) for part in parts]
                for job in jobs:
                    left = COUNT_LIMIT - (time.time() - started)
                    try:
                        lost = job.result(timeout=max(0.1, left))
                    except Exception:                        # noqa: BLE001 - too slow or a broken connection
                        lost = None
                    if lost is None:
                        continue
                    done += 1
                    gone += lost
            finally:
                for job in jobs:                     # (shutdown's cancel_futures needs Python 3.9; older Kodi has 3.8)
                    job.cancel()
                pool_run.shutdown(wait=False)
            if done < len(parts) and len(gone) > len(flat_ids) * FILL_SHARE:
                raise StreamError("%d of %d articles are gone already (count not finished)" % (len(gone), len(flat_ids)))
            if done < len(parts):
                self.log("counting gaps stopped after %.0f s (%d of %d parts, %d gone so far): playing, gaps are filled"
                         % (time.time() - started, done, len(parts), len(gone)))
                return
            where = {m: n for n, m in enumerate(flat_ids)}
            spots = sorted(where[m] for m in gone)
            run = longest = 0
            for n, spot in enumerate(spots):
                run = run + 1 if n and spot == spots[n - 1] + 1 else 1
                longest = max(longest, run)
            if len(gone) <= max(1, len(flat_ids) * FILL_SHARE) and longest <= FILL_RUN:
                self.log("%d of %d articles are gone: they will be filled in" % (len(gone), len(flat_ids)))
                return
            raise StreamError("%d of %d articles are gone (%d in a row)" % (len(gone), len(flat_ids), longest))
        raise StreamError("%d of %d checked articles are gone" % (len(missing), len(answer)))

    @staticmethod
    def _stat_spread(pool, ids):
        """STAT over several connections at once: some providers answer a missing article slowly, one by one."""
        parts = [ids[at:at + SAMPLE_PART] for at in range(0, len(ids), SAMPLE_PART)]
        if len(parts) < 2:
            return pool.stat_many(ids)
        answer = {}
        with ThreadPoolExecutor(max_workers=max(1, min(COUNT_WORKERS, pool.limit, len(parts)))) as run:
            for found in run.map(pool.stat_many, parts):
                answer.update(found)
        return answer

    def _count_part(self, part, started):
        """The gone articles of one part, or None when the count ran out of time before it started."""
        if time.time() - started > COUNT_LIMIT:
            return None
        found = self.pool.stat_many(part)
        lost = [i for i in part if not found.get(i)]
        if lost and self.backup:
            extra = self.backup.stat_many(lost)
            lost = [i for i in lost if not extra.get(i)]
        return lost

    def open(self):
        if self.kind == "video":
            posted = self.chosen[0]
            data, info = self._decode(posted, 0)
            self._keep((posted.index, 0), data)
            if not info.get("size"):
                raise StreamError("size unknown")
            self.pieces = [Piece(posted, 0, info["size"], True)]
            self.name = posted.name
        elif self.kind == "7z":
            self._open_7z()
        else:
            self._open_rar()
        start = 0
        for piece in self.pieces:
            piece.start = start
            start += piece.data_length
        self.size = start
        self.starts = [p.start for p in self.pieces]
        if self.size <= 0:
            raise StreamError("empty")
        self.log("release: %s, %d piece(s), %.2f GB" % (self.kind, len(self.pieces), self.size / 1e9))

    def _head(self, posted):
        data, info = self._decode(posted, 0)
        self._keep((posted.index, 0), data)
        return data, info

    def _keep(self, key, data):
        with self.lock:
            if key not in self.cache:
                self.cache[key] = data
                self.cached += len(data)

    def _open_rar(self):
        volumes = self.chosen
        try:
            first, _info = self._head(volumes[0])
            layout = archive.layout(first)
        except archive.Unsupported as error:
            raise StreamError("RAR: %s" % error) from None
        self.name = layout.name
        if self._want_key and len(volumes) > 1 and nzbfile.has_episode(layout.name):
            # an episode: the set may hold the whole season, so find exactly where this episode begins and ends
            self._open_member(volumes, first.startswith(archive.RAR5))
            return
        if self.want and nzbfile.has_episode(layout.name) and not self.want.search(layout.name):
            raise StreamError("RAR holds another episode (%s)" % layout.name)
        if len(volumes) == 1:
            self.pieces = [Piece(volumes[0], layout.data_offset, layout.data_length, True)]
            return
        try:
            second, _info2 = self._head(volumes[1])
            layout2 = archive.layout(second)
        except archive.Unsupported as error:
            raise StreamError("RAR: %s" % error) from None
        pieces = [Piece(volumes[0], layout.data_offset, layout.data_length, True),
                  Piece(volumes[1], layout2.data_offset, layout2.data_length, True)]
        rar5 = first.startswith(archive.RAR5)
        base = _vint_len(layout2.volume_number or 1)
        for number, posted in enumerate(volumes[2:], start=2):
            grow = (_vint_len(number) - base) if rar5 else 0      # RAR5 writes the volume number: one byte more from 128
            pieces.append(Piece(posted, layout2.data_offset + grow, layout2.data_length - grow, False))
        pieces[-1].data_length = layout.total - sum(p.data_length for p in pieces[:-1])
        if not 0 < pieces[-1].data_length <= layout2.data_length + 16:
            self.log("RAR: volume sizes are not even, reading every volume header")
            pieces = pieces[:2]
            heads = list(self.workers.map(self._head, volumes[2:]))
            for posted, (head, _i) in zip(volumes[2:], heads):
                lay = archive.layout(head)
                pieces.append(Piece(posted, lay.data_offset, lay.data_length, True))
            if sum(p.data_length for p in pieces) != layout.total:
                raise StreamError("RAR: volumes do not add up")
        self.pieces = pieces

    def _open_7z(self):
        """A 7z set is one archive cut in equal volumes: read the header at the end, then point at the film's bytes."""
        volumes = self.chosen
        first, info = self._head(volumes[0])
        try:
            header_at, header_size = sevenzip.start_header(first[:32])
        except sevenzip.Unsupported as error:
            raise StreamError("7z: %s" % error) from None
        size_one = info.get("size") or 0
        size_last = size_one
        if len(volumes) > 1:
            _data, last_info = self._head(volumes[-1])
            size_last = last_info.get("size") or 0
        if not size_one or not size_last:
            raise StreamError("7z: volume size unknown")
        # the whole archive as one stream (volumes in order), to read the header from
        self.pieces = [Piece(p, 0, size_one if n < len(volumes) - 1 else size_last, True) for n, p in enumerate(volumes)]
        start = 0
        for piece in self.pieces:
            piece.start = start
            start += piece.data_length
        self.starts, self.size = [p.start for p in self.pieces], start
        if header_at + header_size > self.size:
            raise StreamError("7z: volumes missing or of uneven size")
        try:
            found = sevenzip.entries(self.read(header_at, header_size), self.read)
        except sevenzip.Unsupported as error:
            raise StreamError("7z: %s" % error) from None
        videos = [e for e in found if archive.is_video(e.name)]
        if self.want:
            videos = [e for e in videos if self.want.search(e.name)] or [e for e in videos if not nzbfile.has_episode(e.name)]
        if not videos:
            raise StreamError("7z: no video inside")
        entry = max(videos, key=lambda e: e.size)
        self.name = entry.name
        # cut the film's range [offset, offset + size) out of the volumes
        pieces, end = [], entry.offset + entry.size
        for piece in self.pieces:
            low, high = max(entry.offset, piece.start), min(end, piece.start + piece.data_length)
            if low < high:
                pieces.append(Piece(piece.posted, low - piece.start, high - low, True))
        self.pieces = pieces

    # ------------------------------------------------------------------ a season in one RAR set
    _EPISODE = re.compile(r"s(\d{1,2}) ?e(\d{1,3})|(\d{1,2})x(\d{2,3})", re.I)

    def _key(self, name):
        found = self._EPISODE.search(name or "")
        if not found:
            return None
        return tuple(int(x) for x in found.groups() if x is not None)

    def _posted_bytes(self, posted, offset, length):
        """Bytes from anywhere in one posted file (for a header in the middle of a volume)."""
        out, at = b"", offset
        while len(out) < length:
            part = self.part.get(posted.index)
            if part is None:
                self.article(posted, 0)
                part = self.part[posted.index]
            if posted.index in self.uneven:
                number, skip = self._locate(posted, at)
            else:
                number, skip = divmod(at, part)
            if number >= len(posted.segments):
                break
            chunk = self.article(posted, number)[skip:skip + length - len(out)]
            if not chunk:
                break
            out += chunk
            at += len(chunk)
        return out

    def _open_member(self, volumes, rar5):
        """The wanted episode inside a RAR set that holds the whole season, one file after the other. Each volume starts
        with the file that runs through it, so a binary search over the volumes' first articles finds where the
        episode begins (a handful of articles, not the whole season); then it is read like any RAR set."""
        heads = {}

        def first_entries(number):
            if number not in heads:
                head, _info = self._head(volumes[number])
                try:
                    heads[number] = archive.entries(head)
                except archive.Unsupported as error:
                    raise StreamError("RAR: %s" % error) from None
            return heads[number]

        def key(number):
            entries = first_entries(number)
            return self._key(entries[0].name) if entries else None

        mine = self._want_key
        if not mine:
            raise StreamError("RAR holds another episode (%s)" % self.name)
        low, high = 0, len(volumes) - 1
        if key(high) is None or key(high) < mine:
            # the last volume starts with an earlier episode: the wanted one can only begin inside it
            low = high
        else:
            # low: a volume that starts with an earlier episode; high: one that starts with the wanted one or later
            while high - low > 1:
                middle = (low + high) // 2
                found = key(middle)
                if found is not None and found >= mine:
                    high = middle
                else:
                    low = middle
        start = None
        for number in (low, high):
            for entry in first_entries(number):
                if self.want.search(entry.name) and not entry.before:
                    start, wanted = number, entry
                    break
            if start is not None:
                break
        if start is None:
            # the header is further into the volume than its first article: right after the earlier episode's data
            last = first_entries(low)[-1]
            if not last.more:
                at = last.data_offset + last.data_length
                entry = archive.entry_at(self._posted_bytes(volumes[low], at, 8192), rar5)
                if entry and self.want.search(entry.name) and not entry.before:
                    entry.data_offset += at
                    start, wanted = low, entry
        if start is None:
            raise StreamError("RAR: episode not found in this set")
        self.name = wanted.name
        self.log("RAR: %s starts in volume %d of %d" % (wanted.name, start + 1, len(volumes)))
        pieces = [Piece(volumes[start], wanted.data_offset, wanted.data_length, True)]
        done = wanted.data_length
        number = start + 1
        if wanted.more and number < len(volumes):
            follow = first_entries(number)[0]
            base = _vint_len(follow.volume_number or number)
            while done < wanted.total and number < len(volumes):
                grow = (_vint_len(number) - base) if rar5 else 0
                length = min(follow.data_length - grow, wanted.total - done)
                pieces.append(Piece(volumes[number], follow.data_offset + grow, length, number == start + 1))
                done += length
                number += 1
        if done != wanted.total:
            raise StreamError("RAR: volumes do not add up")
        self.pieces = pieces

    def _verify(self, index):
        """A volume whose layout was worked out: read its own header once (its first article is needed anyway)."""
        piece = self.pieces[index]
        if piece.verified:
            return
        head = self.article(piece.posted, 0)
        found = [e for e in archive.entries(head) if e.name == self.name]
        lay = found[0] if found else archive.layout(head)
        with self.lock:
            piece.verified = True
            if lay.data_offset != piece.data_offset or (lay.data_length != piece.data_length and index != len(self.pieces) - 1):
                self.log("RAR: volume %d differs from the worked-out layout" % index)
                raise StreamError("RAR: uneven volumes")

    # ------------------------------------------------------------------ reading
    def read(self, offset, length):
        """`length` bytes of the film from `offset` (fewer only at the end), across volumes when needed."""
        if self.closed:
            raise StreamError("closed")
        self.last_read = time.time()
        out = []
        while length > 0 and offset < self.size:
            chunk = self._read_piece(offset, length)
            if not chunk:
                break
            out.append(chunk)
            offset += len(chunk)
            length -= len(chunk)
        self.stats["out"] += sum(len(c) for c in out)
        return b"".join(out)

    def _read_piece(self, offset, length):
        index = bisect.bisect_right(self.starts, offset) - 1
        piece = self.pieces[index]
        self._verify(index)
        inside = offset - piece.start
        length = min(length, piece.data_length - inside)
        at = piece.data_offset + inside             # in the posted file
        posted = piece.posted
        out = []
        while length > 0:
            part = self.part.get(posted.index)
            if part is None:
                self.article(posted, 0)
                part = self.part[posted.index]
            if posted.index in self.uneven:
                number, skip = self._locate(posted, at)
            else:
                number, skip = divmod(at, part)
                if number < len(posted.segments) and (posted.index, number) in self.ranges \
                        and self.ranges[(posted.index, number)][0] != number * part:
                    self.uneven.add(posted.index)          # found out on an article fetched ahead
                    number, skip = self._locate(posted, at)
            if number >= len(posted.segments):
                break
            data = self.article(posted, number)
            first = self.ranges.get((posted.index, number), (None,))[0]
            if (first is not None and first != at - skip) or (skip >= len(data) and number < len(posted.segments) - 1):
                # this very article showed the sizes are uneven (it starts elsewhere, or is shorter than the others)
                self.uneven.add(posted.index)
                number, skip = self._locate(posted, at)
                data = self.article(posted, number)
            self._ahead(posted, number)
            if offset < PIN_START or offset > self.size - PIN_END:
                self.pinned.add((posted.index, number))      # start and end of the film: the MKV index lives there
            if index + 1 < len(self.pieces) and number + max(1, self.ahead // part) >= len(posted.segments):
                self._ahead(self.pieces[index + 1].posted, -1)      # the next volume's start too
            chunk = data[skip:skip + length]
            if not chunk:
                break
            out.append(chunk)
            at += len(chunk)
            length -= len(chunk)
        return b"".join(out)

    def close(self):
        with self.lock:
            self.closed = True
            for future in self.pending.values():
                future.cancel()
            self.pending.clear()
            self.cache.clear()
            self.used.clear()
        self.workers.shutdown(wait=False)
        self.urgent.shutdown(wait=False)


SNIFF_FILES = 12                            # at most this many first articles to look inside a release with hidden names


def _sniff(pool, files, log):
    """Hidden (obfuscated) names: look at the first article of the biggest unknown files. The yEnc header often still
    has the real name; otherwise the first bytes tell what it is (RAR, 7z, Matroska, MP4). True when something was found."""
    unknown = [p for p in files if nzbfile.kind_of(p) == "other" and p.encoded > 1000000]
    if not unknown:
        return False
    look = sorted(unknown, key=lambda p: -p.encoded)[:SNIFF_FILES]

    def first(posted):
        try:
            return posted, yenc.decode(pool.body(posted.segments[0][2]))
        except (nntp.NntpError, OSError, yenc.YencError):
            return posted, (b"", {})

    with ThreadPoolExecutor(max_workers=min(6, len(look))) as workers:
        answers = list(workers.map(first, look))
    found, kinds = 0, {}
    for posted, (data, info) in answers:
        real = (info.get("name") or "").strip()
        if real and real != posted.name and nzbfile.kind_of(nzbfile.Posted('"%s"' % real, [], 0)) != "other":
            posted.name = real
            found += 1
            continue
        if data.startswith((archive.RAR4, archive.RAR5)):
            kinds[posted.index] = "rar"
        elif data.startswith(sevenzip.SIGNATURE):
            kinds[posted.index] = "7z"
        elif data[:4] == b"\x1a\x45\xdf\xa3":
            kinds[posted.index] = "mkv"
        elif data[4:8] == b"ftyp":
            kinds[posted.index] = "mp4"
    if "rar" in kinds.values() or "7z" in kinds.values():
        # an archive set with hidden names: the unknown files in NZB order are its volumes
        kind = "rar" if "rar" in kinds.values() else "7z"
        volumes = sorted((p for p in unknown if kinds.get(p.index, kind) == kind), key=lambda p: p.index)
        for number, posted in enumerate(volumes, 1):
            posted.name = ("hidden.part%03d.rar" if kind == "rar" else "hidden.7z.%03d") % number
            found += 1
    for posted in unknown:
        if kinds.get(posted.index) in ("mkv", "mp4"):
            posted.name = "hidden%d.%s" % (posted.index, kinds[posted.index])
            found += 1
    log("hidden names: %d of %d files recognised" % (found, len(unknown)))
    return bool(found)


def open_release(pool, nzb_bytes, season=None, episode=None, log=None, ahead=None, cache=None, pack=False, backup=None,
                 fill=False, minutes=0):
    """NZB -> an opened Release, or StreamError / nzbfile.NzbError with the reason."""
    files = nzbfile.parse(nzb_bytes)
    try:
        kind, chosen = nzbfile.pick(files, season, episode, pack)
    except nzbfile.NzbError:
        if not _sniff(pool, files, log or (lambda text: None)):
            raise
        kind, chosen = nzbfile.pick(files, season, episode, pack)
    release = Release(pool, files, kind, chosen, log=log, backup=backup, fill=fill,
                      **{k: v for k, v in (("ahead", ahead), ("cache", cache)) if v})
    if season and episode:
        release.want = nzbfile.episode_tag(season, episode)
        release._want_key = (int(season), int(episode))        # noqa: SLF001 - set right after making it
    try:
        release.check()
        release.open()
    except (nntp.NntpError, OSError, yenc.YencError, StreamError, nzbfile.NzbError):
        release.close()
        raise
    release.fit_to_film(minutes)
    return release
