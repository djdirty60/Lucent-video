# -*- coding: utf-8 -*-
"""An NZB: XML with one <file> per posted file and one <segment> per article (its number, its size and its id).

Here: which posted files there are, which one is the film (a video file posted as is, or a RAR set holding it) and the
order of the RAR volumes. Compressed archives, 7z and par2 are not played (step 1); a release made only of those is
refused with a reason.
"""
import re
import xml.etree.ElementTree as ET

VIDEO = (".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm")
_QUOTED = re.compile(r'"([^"]+)"')
_NAMEISH = re.compile(r"([\w.\-\[\]() ]+\.(?:mkv|mp4|avi|ts|m2ts|mov|wmv|webm|rar|r\d{2,3}|\d{3}|par2|nfo|7z|sfv|nzb|jpg|png|srt))", re.I)
_PART = re.compile(r"\.part0*(\d+)\.rar$", re.I)
_RNN = re.compile(r"\.r(\d{2,3})$", re.I)
_NUMBERED = re.compile(r"\.(\d{3})$")


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
    if low.endswith(".rar") or _RNN.search(low) or _NUMBERED.search(low):
        return "rar"
    if low.endswith(".7z") or re.search(r"\.7z\.\d{3}$", low):
        return "7z"
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


def pick(files):
    """What to play: ('video', [Posted]) or ('rar', [volumes]). The biggest candidate wins; samples never do.
    Raises NzbError with a reason when there is nothing playable."""
    videos = [p for p in files if kind_of(p) == "video" and not _is_sample(p)]
    sets = rar_sets(files)
    best_video = max(videos, key=lambda p: p.encoded) if videos else None
    best_set = max(sets.values(), key=lambda vs: sum(p.encoded for p in vs)) if sets else None
    if best_video and (not best_set or best_video.encoded >= sum(p.encoded for p in best_set)):
        return "video", [best_video]
    if best_set:
        return "rar", best_set
    if any(kind_of(p) == "7z" for p in files):
        raise NzbError("7z")                    # step 2
    raise NzbError("no video")
