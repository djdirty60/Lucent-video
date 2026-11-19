# -*- coding: utf-8 -*-
"""The audio and subtitle tracks that are really in a Matroska (MKV) file: read from the first megabytes of the
release, before you play it. Only the track list (Tracks) is read: no picture, no sound.

Matroska is EBML: every element is an id (1-4 bytes) and a size (1-8 bytes), both with a length marker in the first
byte. The Segment holds Tracks; every TrackEntry has a TrackType (2 = audio, 17 = subtitles) and a Language (three
letters, 'eng' when it is missing) or LanguageBCP47.
"""
SEGMENT, TRACKS, ENTRY = 0x18538067, 0x1654AE6B, 0xAE
TYPE, LANGUAGE, BCP47, CLUSTER = 0x83, 0x22B59C, 0x22B59D, 0x1F43B675
NUMBER, CODEC, PRIVATE = 0xD7, 0x86, 0x63A2
ADD_MAPPING, ADD_TYPE, ADD_DATA = 0x41E4, 0x41E7, 0x41ED
SIMPLE_BLOCK, BLOCK_GROUP, BLOCK = 0xA3, 0xA0, 0xA1
DOVI_CONFIG = (0x64766343, 0x64767643)  # 'dvcC', 'dvvC': the Dolby Vision configuration record
UNKNOWN = -1
# Dolby Vision profile 7 (UHD Blu-ray) has an enhancement layer (EL) next to the picture: FEL carries real picture
# data, MEL is almost empty. Measured over the first frames: the EL's share of the video bytes.
MEL_BELOW, FEL_ABOVE, FRAMES_NEEDED = 0.01, 0.03, 4

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


def _video(fields, data):
    """(track number, NAL length size, Dolby Vision profile or 0) of an HEVC video TrackEntry."""
    number, nal_size, profile = 0, 4, 0
    for field, fstart, fend, _fstop in fields:
        raw = data[fstart:fend]
        if field == NUMBER:
            number = int.from_bytes(raw, "big")
        elif field == PRIVATE and len(raw) > 21:
            nal_size = (raw[21] & 3) + 1          # hvcC: lengthSizeMinusOne
        elif field == ADD_MAPPING:
            kind, extra = 0, b""
            for inner, istart, iend, _istop in _children(data, fstart, fend):
                if inner == ADD_TYPE:
                    kind = int.from_bytes(data[istart:iend], "big")
                elif inner == ADD_DATA:
                    extra = data[istart:iend]
            if kind in DOVI_CONFIG and len(extra) >= 4:
                profile = extra[2] >> 1
    return number, nal_size, profile


def _frames(data, start, end, number, nal_size, share):
    """Adds the bytes of the enhancement layer (NAL type 63) and of the rest of the video, frame by frame."""
    for ident, bstart, bend, bstop in _children(data, start, end):
        if bstop > len(data):
            return
        if ident == BLOCK_GROUP:
            _frames(data, bstart, bend, number, nal_size, share)
            continue
        if ident not in (SIMPLE_BLOCK, BLOCK):
            continue
        track, at = _vint(data, bstart, False)
        if track != number or data[at + 2] & 0x06:
            continue                                # another track, or laced (never for video)
        at += 3
        while at + nal_size < bend:
            length = int.from_bytes(data[at:at + nal_size], "big")
            at += nal_size
            if length <= 0 or at + length > bend:
                break
            key = "el" if (data[at] >> 1) & 0x3F == 63 else "bl"
            share[key] += length
            at += length
        share["frames"] += 1


def dolby(data, number, nal_size, start):
    """'fel', 'mel' or '' (not sure) for a profile 7 file, from the clusters that are in these bytes."""
    share = {"el": 0, "bl": 0, "frames": 0}
    try:
        for ident, cstart, cend, cstop in _children(data, start, len(data)):
            if ident == CLUSTER:
                _frames(data, cstart, min(cend, len(data)), number, nal_size, share)
            if cstop > len(data) or share["frames"] >= 24:
                break
    except (ValueError, IndexError):
        pass
    total = share["el"] + share["bl"]
    if share["frames"] < FRAMES_NEEDED or not total:
        return ""
    part = share["el"] / total
    return "mel" if part < MEL_BELOW else ("fel" if part > FEL_ABOVE else "")


def read(data):
    """{'audio': ['nl', 'en'], 'subs': [...], 'dv': 7, 'video': (number, NAL size), 'after': where the clusters start}
    from the start of an MKV file, or None when the track list is not in these bytes (or it is not Matroska)."""
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
                out = {"audio": [], "subs": [], "dv": 0, "video": None, "after": istop, "segment": (start, end)}
                for entry, estart, eend, _estop in _children(data, istart, iend):
                    if entry != ENTRY:
                        continue
                    kind, lang, bcp = 0, None, None
                    fields = list(_children(data, estart, eend))
                    for field, fstart, fend, _fstop in fields:
                        raw = data[fstart:fend]
                        if field == TYPE:
                            kind = int.from_bytes(raw, "big")
                        elif field == LANGUAGE:
                            lang = raw.decode("ascii", "replace").strip("\x00 ")
                        elif field == BCP47:
                            bcp = raw.decode("ascii", "replace").strip("\x00 ")
                    if kind == 1 and out["video"] is None:
                        number, nal_size, profile = _video(fields, data)
                        out["video"], out["dv"] = (number, nal_size), profile
                    if kind in (2, 17):
                        code = _code(lang, bcp)
                        bucket = out["audio" if kind == 2 else "subs"]
                        if code and code != "un" and code not in bucket:
                            bucket.append(code)
                return out
    except (ValueError, IndexError):
        return None
    return None
