# -*- coding: utf-8 -*-
"""The audio and subtitle tracks that are really in a Matroska (MKV) file: read from the first megabytes of the
release, before you play it. Only the track list (Tracks) is read: no picture, no sound.

Matroska is EBML: every element is an id (1-4 bytes) and a size (1-8 bytes), both with a length marker in the first
byte. The Segment holds Tracks; every TrackEntry has a TrackType (2 = audio, 17 = subtitles) and a Language (three
letters, 'eng' when it is missing) or LanguageBCP47.
"""
SEGMENT, TRACKS, ENTRY = 0x18538067, 0x1654AE6B, 0xAE
TYPE, LANGUAGE, BCP47, CLUSTER = 0x83, 0x22B59C, 0x22B59D, 0x1F43B675
UNKNOWN = -1

THREE = {"dut": "nl", "nld": "nl", "eng": "en", "ger": "de", "deu": "de", "fre": "fr", "fra": "fr", "spa": "es", "ita": "it",
         "por": "pt", "rum": "ro", "ron": "ro", "pol": "pl", "swe": "sv", "dan": "da", "nor": "no", "nob": "no", "nno": "no",
         "fin": "fi", "hun": "hu", "cze": "cs", "ces": "cs", "tur": "tr", "rus": "ru", "jpn": "ja", "kor": "ko", "chi": "zh",
         "zho": "zh", "ukr": "uk", "gre": "el", "ell": "el", "heb": "he", "ara": "ar", "hin": "hi", "tha": "th", "vie": "vi"}


def _vint(data, at, keep_marker):
    first = data[at]
    length = 1
    while length <= 8 and not first & (0x80 >> (length - 1)):
        length += 1
    if length > 8 or at + length > len(data):
        raise ValueError("bad vint")
    value = first if keep_marker else first & (0xFF >> length)
    for byte in data[at + 1:at + length]:
        value = (value << 8) | byte
    if not keep_marker and value == (1 << (7 * length)) - 1:
        value = UNKNOWN                         # 'unknown size' (live-style files): runs to the end of its parent
    return value, at + length


def _children(data, start, end):
    at = start
    while at < end:
        ident, at = _vint(data, at, True)
        size, at = _vint(data, at, False)
        stop = end if size == UNKNOWN else at + size
        yield ident, at, min(stop, end), stop
        at = stop


def _code(entry_lang, bcp47):
    if bcp47:
        return bcp47.split("-")[0].lower()[:2]
    lang = (entry_lang or "eng").lower()
    return THREE.get(lang, lang[:2])


def read(data):
    """{'audio': ['nl', 'en'], 'subs': [...]} from the start of an MKV file, or None when the track list is not in
    these bytes (or it is not Matroska)."""
    if data[:4] != b"\x1a\x45\xdf\xa3":
        return None
    try:
        for ident, start, end, stop in _children(data, 0, len(data)):
            if ident != SEGMENT:
                continue
            for inner, istart, iend, istop in _children(data, start, end):
                if inner == CLUSTER:
                    return None                 # the picture starts and no track list came before it
                if inner != TRACKS:
                    continue
                if istop > len(data):
                    return None                 # the track list is longer than what was read
                out = {"audio": [], "subs": []}
                for entry, estart, eend, _estop in _children(data, istart, iend):
                    if entry != ENTRY:
                        continue
                    kind, lang, bcp = 0, None, None
                    for field, fstart, fend, _fstop in _children(data, estart, eend):
                        raw = data[fstart:fend]
                        if field == TYPE:
                            kind = int.from_bytes(raw, "big")
                        elif field == LANGUAGE:
                            lang = raw.decode("ascii", "replace").strip("\x00 ")
                        elif field == BCP47:
                            bcp = raw.decode("ascii", "replace").strip("\x00 ")
                    if kind in (2, 17):
                        code = _code(lang, bcp)
                        bucket = out["audio" if kind == 2 else "subs"]
                        if code and code != "un" and code not in bucket:
                            bucket.append(code)
                return out
    except (ValueError, IndexError):
        return None
    return None
