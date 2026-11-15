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

    def __init__(self, subject, segments, index):
        self.subject = subject
        self.segments = segments          # [(number, encoded bytes, message id)], sorted by number
        self.index = index                # place in the NZB: the order of an obfuscated RAR set
        self.name = name_of(subject)

    @property
    def encoded(self):
        return sum(size for _n, size, _m in self.segments)

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


def parse(data):
    """NZB bytes -> [Posted] in NZB order."""
    try:
        root = ET.fromstring(data)
    except ET.ParseError as error:
        raise NzbError("not an NZB (%s)" % error) from None
    files = []
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
            segments.sort()
            seen, unique = set(), []
            for segment in segments:             # a number posted twice: keep the first
                if segment[0] not in seen:
                    seen.add(segment[0])
                    unique.append(segment)
            files.append(Posted(element.get("subject") or "", unique, len(files)))
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
