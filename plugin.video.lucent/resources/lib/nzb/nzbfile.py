# -*- coding: utf-8 -*-
"""An NZB: XML with one <file> per posted file and one <segment> per article (its number, its size and its id).

Here: which posted files there are, which one is the film (a video file posted as is, or a RAR set holding it) and the
order of the RAR or 7z volumes. Compressed archives and par2 are not played; a release made only of those is refused
with a reason.
"""
import re
import xml.etree.ElementTree as ET

VIDEO = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm")
_QUOTED = re.compile(r'"([^"]+)"')
_NAMEISH = re.compile(r"([\w.\-\[\]() ]+\.(?:mkv|mp4|avi|ts|m2ts|mov|wmv|webm|rar|r\d{2,3}|\d{3}|par2|nfo|7z|sfv|nzb|jpg|png|srt))", re.I)
_PART = re.compile(r"\.part0*(\d+)\.rar$", re.I)
_RNN = re.compile(r"\.r(\d{2,3})$", re.I)
_NUMBERED = re.compile(r"\.(\d{3})$")
_SEVEN = re.compile(r"\.7z(?:\.(\d{3}))?$", re.I)


class NzbError(Exception):
    """The NZB cannot be read, or holds nothing we can play."""


class Posted:
    """One posted file: its name and its articles, in order."""

    def __init__(self, subject, segments, index, raw=None):
        self.subject = subject
        self._segments = segments         # [(number, encoded bytes, message id)], sorted by number
        self._raw = raw                   # or the file's piece of the NZB, read only when its articles are needed
        self._encoded = None
        self.index = index                # place in the NZB: the order of an obfuscated RAR set
        self.name = name_of(subject)

    @property
    def segments(self):
        if self._segments is None:
            self._segments = _unique(_segments_of(self._raw))
            self._raw = None
        return self._segments

    @property
    def encoded(self):
        if self._encoded is None:
            if self._segments is None and self._raw is not None:
                self._encoded = _encoded_size(self._raw)        # without reading every article
            else:
                self._encoded = sum(size for _n, size, _m in self.segments)
        return self._encoded

    @property
    def low(self):
        return self.name.lower()

    def __repr__(self):
        return "<Posted %s, %d articles>" % (self.name, len(self.segments))


def name_of(subject):
    """The file name the poster put in the subject: between quotes, else the first token that looks like a file."""
    quoted = _QUOTED.search(subject or "")
    if quoted:
        return quoted.group(1).strip()
    found = _NAMEISH.search(subject or "")
    return found.group(1).strip() if found else (subject or "").strip()


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


_FILE = re.compile(rb"<file\b([^>]*)>(.*?)</file\s*>", re.S)
_SEGMENT = re.compile(rb"<segment\b([^>]*)>\s*([^<]*?)\s*</segment\s*>")
_ATTR = re.compile(rb"""([\w:.-]+)\s*=\s*(?:"([^"]*)"|'([^']*)')""")
_QUICK = re.compile(rb'\s+(?:bytes="(\d+)"\s+number="(\d+)"|number="(\d+)"\s+bytes="(\d+)")\s*')
_BYTES = re.compile(rb'<segment\b[^>]*?\bbytes\s*=\s*["\'](\d+)')
_PLAIN_BYTES = re.compile(rb'<segment bytes="(\d+)"')


def _encoded_size(raw):
    """The sum of the articles' sizes in a file's piece of the NZB (a plain search first, the careful one otherwise)."""
    plain = _PLAIN_BYTES.findall(raw)
    if len(plain) == raw.count(b"<segment") - raw.count(b"<segments"):
        return sum(map(int, plain))
    return sum(int(n) for n in _BYTES.findall(raw))


_ENTITIES = {"&quot;": '"', "&apos;": "'", "&lt;": "<", "&gt;": ">", "&amp;": "&"}


def _text(raw):
    text = raw.decode("utf-8", "replace")
    if "&" in text:
        text = re.sub(r"&(?:quot|apos|lt|gt|amp);", lambda m: _ENTITIES[m.group(0)], text)
        text = re.sub(r"&#(\d+);", lambda m: chr(int(m.group(1))), text)
        text = re.sub(r"&#x([0-9a-fA-F]+);", lambda m: chr(int(m.group(1), 16)), text)
    return text


def _attrs(raw):
    return {m.group(1).decode("ascii", "replace"): (m.group(2) if m.group(2) is not None else m.group(3)) for m in _ATTR.finditer(raw)}


def _fast(data):
    """The same as the XML reader, but each file's articles are only read when they are needed: a 100 GB release has
    an NZB of 10+ MB with 120,000 articles, and reading all of them keeps a slow box busy for seconds (the check
    before the list needs a few articles, playing reads one file after the other)."""
    if b"<!--" in data or b"CDATA" in data or b"<segment" not in data:
        return None                             # unusual NZB: the full XML reader
    files = []
    for head, body in _file_blocks(data):
        if b"<segment" in body:
            files.append((_text(_attrs(head).get("subject") or b""), None, body))
    return files


_FILE_END = re.compile(rb"</file\s*>")


def _file_blocks(data):
    """(attributes, body) of each <file>, the same as _FILE.finditer but with plain searches: the lazy regex walks a
    10+ MB NZB byte by byte, which is slow on a weak box."""
    at = 0
    while True:
        start = data.find(b"<file", at)
        if start < 0:
            return
        after = start + 5
        if after < len(data) and (data[after:after + 1].isalnum() or data[after:after + 1] == b"_"):
            at = after                          # <files ...> or similar: not a file
            continue
        close = data.find(b">", after)
        if close < 0:
            return
        head = data[after:close]
        end = _FILE_END.search(data, close + 1)
        if not end:
            return
        yield head, data[close + 1:end.start()]
        at = end.end()


_PLAIN_BN = re.compile(rb'<segment bytes="(\d+)" number="(\d+)">([^<]+)</segment>')
_PLAIN_NB = re.compile(rb'<segment number="(\d+)" bytes="(\d+)">([^<]+)</segment>')


def _segments_of(block):
    """[(number, bytes, id)] of one file. Almost every NZB writes each article the same plain way: then one regex pass
    reads them all (several times faster on a slow box); anything else goes through the careful reader below."""
    if b"&" not in block:
        total = block.count(b"<segment") - block.count(b"<segments")
        for pattern, bytes_first in ((_PLAIN_BN, True), (_PLAIN_NB, False)):
            plain = pattern.findall(block)
            if len(plain) != total:
                continue
            if bytes_first:
                found = [(int(b), int(a), c.strip().decode("utf-8", "replace")) for a, b, c in plain]
            else:
                found = [(int(a), int(b), c.strip().decode("utf-8", "replace")) for a, b, c in plain]
            if all(item[2] for item in found):
                return found
            break
    return _segments_careful(block)


def _segments_careful(block):
    segments = []
    for segment in _SEGMENT.finditer(block):
        raw = segment.group(2)
        if not raw:
            continue
        head = segment.group(1)
        quick = _QUICK.fullmatch(head)
        if quick:                               # the usual bytes="" number="" (either order): no dict per article
            size, number = (quick.group(1), quick.group(2)) if quick.group(1) else (quick.group(4), quick.group(3))
        else:
            attrs = _attrs(head)
            size, number = attrs.get("bytes"), attrs.get("number")
        text = raw.decode("utf-8", "replace") if b"&" not in raw else _text(raw)
        try:
            segments.append((int(number or 0), int(size or 0), text))
        except ValueError:
            continue
    return segments


def parse(data):
    """NZB bytes -> [Posted] in NZB order."""
    fast = _fast(data) if isinstance(data, bytes) else None
    if fast:
        return _collect(fast)
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise NzbError("not an NZB (%s)" % error) from None
    found = []
    for element in root.iter():
        if _tag(element) != "file":
            continue
        segments = []
        for child in element.iter():
            if _tag(child) == "segment" and (child.text or "").strip():
                try:
                    segments.append((int(child.get("number") or 0), int(child.get("bytes") or 0), child.text.strip()))
                except ValueError:
                    continue
        if segments:
            found.append((element.get("subject") or "", segments, None))
    return _collect(found)


def _unique(segments):
    """Articles in order, a number posted twice only once (the first)."""
    numbers = [segment[0] for segment in segments]
    if all(a < b for a, b in zip(numbers, numbers[1:])):
        return segments                         # already in order without doubles (nearly always)
    segments.sort()
    seen, unique = set(), []
    for segment in segments:
        if segment[0] not in seen:
            seen.add(segment[0])
            unique.append(segment)
    return unique


def _collect(found):
    """[(subject, [(number, bytes, id)] or None, raw piece)] -> [Posted]."""
    files = []
    for subject, segments, raw in found:
        if segments is not None:
            segments = _unique(segments)
        files.append(Posted(subject, segments, len(files), raw))
    if not files:
        raise NzbError("empty NZB")
    return files


def kind_of(posted):
    low = posted.low
    if low.endswith(VIDEO):
        return "video"
    if _SEVEN.search(low):
        return "7z"                         # before RAR: 'name.7z.001' would otherwise look like a numbered RAR volume
    if low.endswith(".rar") or _RNN.search(low) or _NUMBERED.search(low):
        return "rar"
    if low.endswith(".par2"):
        return "par2"
    return "other"


def _is_sample(posted):
    low = posted.low
    return bool(re.search(r"(^|[\W_])(sample|trailer|proof)([\W_]|$)", low))


def _volume_order(posted):
    """(set stem, volume number) of a RAR volume: name.part01.rar, name.rar + name.r00, or name.001."""
    low = posted.low
    match = _PART.search(low)
    if match:
        return low[:match.start()], int(match.group(1))
    if low.endswith(".rar"):
        return low[:-4], 0
    match = _RNN.search(low)
    if match:
        return low[:match.start()], int(match.group(1)) + 1
    match = _NUMBERED.search(low)
    if match:
        return low[:match.start()], int(match.group(1))
    return low, 0


def rar_sets(files):
    """{stem: [Posted in volume order]} for every RAR set in the NZB."""
    sets = {}
    for posted in files:
        if kind_of(posted) == "rar" and not _is_sample(posted):
            stem, number = _volume_order(posted)
            sets.setdefault(stem, []).append((number, posted.index, posted))
    return {stem: [p for _n, _i, p in sorted(volumes, key=lambda v: (v[0], v[1]))] for stem, volumes in sets.items()}


_ANY_EPISODE = re.compile(r"(?:^|[\W_])s\d{1,2} ?e\d{1,3}(?:[\W_]|$)|(?:^|[\W_])\d{1,2}x\d{2,3}(?:[\W_]|$)", re.I)


def episode_tag(season, episode):
    """A pattern for 'S02E05' / '2x05' in a name."""
    s, e = int(season), int(episode)
    return re.compile(r"(?:^|[\W_])s0*%d ?e0*%d(?:[\W_]|$)|(?:^|[\W_])0*%dx0*%d(?:[\W_]|$)" % (s, e, s, e), re.I)


def has_episode(name):
    return bool(_ANY_EPISODE.search(name or ""))


def _for_episode(videos, sets, season, episode, pack):
    """Keep only what is the wanted episode. In a season pack each episode is its own file or RAR set; when the names
    are hidden (obfuscated) we cannot tell which one it is, and say so."""
    wanted = episode_tag(season, episode)
    name_of_set = lambda volumes: volumes[0].name
    good_videos = [p for p in videos if wanted.search(p.name)]
    good_sets = {k: v for k, v in sets.items() if wanted.search(name_of_set(v))}
    if good_videos or good_sets:
        return good_videos, good_sets
    if pack:
        named = any(has_episode(p.name) for p in videos) or any(has_episode(name_of_set(v)) for v in sets.values())
        if named:
            raise NzbError("episode not in this pack")
        if sets:
            return [], sets                 # one archive with the whole season: the names inside it decide (see stream)
        raise NzbError("pack with hidden names")
    return videos, sets


def seven_sets(files):
    """{stem: [Posted in volume order]} for every 7z set: name.7z, or name.7z.001, .002, ..."""
    sets = {}
    for posted in files:
        if kind_of(posted) == "7z" and not _is_sample(posted):
            match = _SEVEN.search(posted.low)
            sets.setdefault(posted.low[:match.start()], []).append((int(match.group(1) or 1), posted.index, posted))
    return {stem: [p for _n, _i, p in sorted(volumes, key=lambda v: (v[0], v[1]))] for stem, volumes in sets.items()}


def pick(files, season=None, episode=None, pack=False):
    """What to play: ('video', [Posted]) or ('rar', [volumes]). The biggest candidate wins; samples never do. With a
    season and episode only that episode can win (season packs). Raises NzbError with a reason when nothing fits."""
    videos = [p for p in files if kind_of(p) == "video" and not _is_sample(p)]
    sets = rar_sets(files)
    kinds = {stem: "rar" for stem in sets}
    for stem, volumes in seven_sets(files).items():
        sets["7z:" + stem] = volumes
        kinds["7z:" + stem] = "7z"
    if season and episode:
        videos, sets = _for_episode(videos, sets, season, episode, pack)
    best_video = max(videos, key=lambda p: p.encoded) if videos else None
    best = max(sets, key=lambda stem: sum(p.encoded for p in sets[stem])) if sets else None
    if best_video and (best is None or best_video.encoded >= sum(p.encoded for p in sets[best])):
        return "video", [best_video]
    if best is not None:
        return kinds[best], sets[best]
    raise NzbError("no video")
