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
import threading
import time
from concurrent.futures import ThreadPoolExecutor

from . import archive, nntp, nzbfile, yenc


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
    def __init__(self, pool, files, kind, chosen, ahead=24 * 1024 * 1024, cache=64 * 1024 * 1024, log=None):
        self.pool = pool
        self.files = files
        self.kind = kind                    # 'video' or 'rar'
        self.chosen = chosen                # [nzbfile.Posted]
        self.ahead = ahead
        self.cache_limit = max(cache, ahead + 16 * 1024 * 1024)
        self.log = log or (lambda text: None)
        self.lock = threading.RLock()
        self.cache = collections.OrderedDict()   # (file index, article) -> bytes
        self.cached = 0
        self.pending = {}                   # (file index, article) -> Future
        self.part = {}                      # file index -> decoded bytes per article
        self.workers = ThreadPoolExecutor(max_workers=max(2, pool.limit))
        self.pieces = []
        self.size = 0
        self.name = ""
        self.closed = False
        self.last_read = time.time()

    # ------------------------------------------------------------------ articles
    def _decode(self, posted, number):
        _n, _size, message_id = posted.segments[number]
        data, info = yenc.decode(self.pool.body(message_id))
        part = self.part.get(posted.index)
        if part is None:
            # the poster's part size: from where this article starts (any article but the first), else its length
            part = (info["begin"] - 1) // number if number and info.get("begin") else (info["end"] - info["begin"] + 1)
            if part > 0:
                self.part[posted.index] = part
        if info.get("begin") and part and info["begin"] - 1 != number * part:
            raise StreamError("articles of uneven size")      # step 2: map them one by one
        return data, info

    def article(self, posted, number):
        """The decoded bytes of one article, from memory, from a fetch already running, or fetched now."""
        key = (posted.index, number)
        with self.lock:
            if key in self.cache:
                self.cache.move_to_end(key)
                return self.cache[key]
            future = self.pending.get(key)
            if future is None:
                future = self.workers.submit(self._fetch, posted, number)
                self.pending[key] = future
        return future.result(timeout=60)

    def _fetch(self, posted, number):
        key = (posted.index, number)
        try:
            data, _info = self._decode(posted, number)
        finally:
            with self.lock:
                self.pending.pop(key, None)
        with self.lock:
            if key not in self.cache:
                self.cache[key] = data
                self.cached += len(data)
                while self.cached > self.cache_limit and len(self.cache) > 1:
                    _old, dropped = self.cache.popitem(last=False)
                    self.cached -= len(dropped)
        return data

    def _ahead(self, posted, number):
        """Ask for the articles after this one, so they are there before Kodi wants them."""
        part = self.part.get(posted.index) or 700000
        count = max(1, self.ahead // part)
        with self.lock:
            if self.closed:
                return
            for n in range(number + 1, min(len(posted.segments), number + 1 + count)):
                key = (posted.index, n)
                if key not in self.cache and key not in self.pending:
                    self.pending[key] = self.workers.submit(self._fetch, posted, n)

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
        answer = self.pool.stat_many(ids)
        missing = [i for i, ok in answer.items() if not ok]
        if missing:
            raise StreamError("%d of %d checked articles are gone" % (len(missing), len(answer)))

    def open(self):
        if self.kind == "video":
            posted = self.chosen[0]
            data, info = self._decode(posted, 0)
            self._keep((posted.index, 0), data)
            if not info.get("size"):
                raise StreamError("size unknown")
            self.pieces = [Piece(posted, 0, info["size"], True)]
            self.name = posted.name
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

    def _verify(self, index):
        """A volume whose layout was worked out: read its own header once (its first article is needed anyway)."""
        piece = self.pieces[index]
        if piece.verified:
            return
        head = self.article(piece.posted, 0)
        lay = archive.layout(head)
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
            number, skip = divmod(at, part)
            if number >= len(posted.segments):
                break
            data = self.article(posted, number)
            self._ahead(posted, number)
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
        self.workers.shutdown(wait=False)


def open_release(pool, nzb_bytes, season=None, episode=None, log=None, ahead=None, cache=None):
    """NZB -> an opened Release, or StreamError / nzbfile.NzbError with the reason."""
    files = nzbfile.parse(nzb_bytes)
    kind, chosen = nzbfile.pick(files)
    release = Release(pool, files, kind, chosen, log=log, **{k: v for k, v in (("ahead", ahead), ("cache", cache)) if v})
    try:
        release.check()
        release.open()
    except (nntp.NntpError, OSError, yenc.YencError, StreamError, nzbfile.NzbError):
        release.close()
        raise
    return release
