# -*- coding: utf-8 -*-
"""Where the film sits inside one RAR volume, for stored (uncompressed, -m0) sets only.

A stored RAR set is the film cut in pieces with a few headers in front of each piece, so it can be read straight
from the volumes: we only need, per volume, the offset of the film's data and its length. Compressed or encrypted
archives cannot be read that way and are refused with a reason.

Formats: RAR 4.x ('Rar!\\x1a\\x07\\x00', fixed-size header fields) and RAR 5.0 ('Rar!\\x1a\\x07\\x01\\x00', variable-length
integers), as published by RARLAB in 'RAR 5.0 archive format' and the RAR 4 technote.
"""
import struct

RAR4 = b"Rar!\x1a\x07\x00"
RAR5 = b"Rar!\x1a\x07\x01\x00"


class Unsupported(Exception):
    """Compressed, encrypted, or not a RAR we understand."""


class Layout:
    """One volume: the stored file's name, its full size, and where this volume's part of it is."""

    def __init__(self, name, total, data_offset, data_length, volume_number=None, more=False, before=False):
        self.name = name
        self.before = before                # this volume goes on with a file that began in an earlier volume
        self.total = total                  # unpacked size of the whole film
        self.data_offset = data_offset      # in this volume file
        self.data_length = data_length      # bytes of the film in this volume
        self.volume_number = volume_number
        self.more = more                    # the film goes on in the next volume

    def __repr__(self):
        return "<Layout %s at %d, %d of %d>" % (self.name, self.data_offset, self.data_length, self.total)


def _vint(data, at):
    value = shift = 0
    while True:
        if at >= len(data):
            raise IndexError("vint")
        byte = data[at]
        at += 1
        value |= (byte & 0x7F) << shift
        if not byte & 0x80:
            return value, at
        shift += 7
        if shift > 63:
            raise Unsupported("bad number")


def is_video(name):
    return name.lower().endswith((".mkv", ".mp4", ".avi", ".ts", ".m2ts", ".mov", ".wmv", ".webm"))


def layout(head):
    """head: the first bytes of a volume (one article is plenty). Returns the Layout of the biggest video in it."""
    if head.startswith(RAR5):
        return _pick(_rar5(head))
    if head.startswith(RAR4):
        return _pick(_rar4(head))
    raise Unsupported("not a RAR")


def entries(head):
    """Every stored file whose header is in these bytes (the start of a volume), in order. A season in one RAR set:
    the first one is often the end of the episode before (before=True)."""
    if head.startswith(RAR5):
        return _rar5(head)
    if head.startswith(RAR4):
        return _rar4(head)
    raise Unsupported("not a RAR")


def entry_at(data, rar5):
    """The file header that starts at the beginning of `data` (a header in the middle of a volume), or None."""
    try:
        found = _rar5(data, at=0, volume_number=None) if rar5 else _rar4(data, at=0)
    except Unsupported:
        return None
    return found[0] if found else None


def _pick(found):
    videos = [f for f in found if is_video(f.name)]
    if not videos:
        raise Unsupported("no video inside")
    return max(videos, key=lambda f: f.total)


def _rar5(data, at=None, volume_number=None):
    at = len(RAR5) if at is None else at
    found = []
    try:
        while at < len(data):
            block = at
            at += 4                                     # header CRC32
            size, at = _vint(data, at)
            body = at
            kind, at = _vint(data, at)
            flags, at = _vint(data, at)
            extra = packed = 0
            if flags & 0x01:
                extra, at = _vint(data, at)
            if flags & 0x02:
                packed, at = _vint(data, at)
            data_offset = body + size
            if kind == 4:
                raise Unsupported("encrypted")
            if kind == 1:                               # main archive header
                archive_flags, at = _vint(data, at)
                if archive_flags & 0x02:
                    volume_number, at = _vint(data, at)
            elif kind == 2:                             # file header
                file_flags, at = _vint(data, at)
                unpacked, at = _vint(data, at)
                _attributes, at = _vint(data, at)
                if file_flags & 0x02:
                    at += 4                             # mtime
                if file_flags & 0x04:
                    at += 4                             # data CRC32
                compression, at = _vint(data, at)
                _host, at = _vint(data, at)
                name_length, at = _vint(data, at)
                name = data[at:at + name_length].decode("utf-8", "replace")
                if (compression >> 7) & 0x07:
                    if is_video(name):
                        raise Unsupported("compressed")
                elif not file_flags & 0x01:             # a file, not a folder
                    found.append(Layout(name, unpacked, data_offset, packed, volume_number, bool(flags & 0x10), bool(flags & 0x08)))
            elif kind == 5:                             # end of archive
                break
            at = data_offset + (packed if flags & 0x02 else 0)
            if found and at > len(data):
                break                                   # the data runs past what we read: we have what we need
            if at <= block:
                raise Unsupported("bad header")
    except (IndexError, struct.error):
        if not found:
            raise Unsupported("header cut off") from None
    return found


def _rar4(data, at=None):
    at = len(RAR4) if at is None else at
    found = []
    try:
        while at + 7 <= len(data):
            _crc, kind, flags, size = struct.unpack_from("<HBHH", data, at)
            if size < 7:
                raise Unsupported("bad header")
            add = struct.unpack_from("<I", data, at + 7)[0] if flags & 0x8000 else 0
            if kind == 0x73 and flags & 0x0080:
                raise Unsupported("encrypted")
            if kind == 0x74:                            # file header
                packed, unpacked = struct.unpack_from("<II", data, at + 7)
                method = data[at + 25]
                name_size = struct.unpack_from("<H", data, at + 26)[0]
                name_at = at + 32
                if flags & 0x0100:                      # files over 4 GB: high 32 bits
                    high_packed, high_unpacked = struct.unpack_from("<II", data, at + 32)
                    packed |= high_packed << 32
                    unpacked |= high_unpacked << 32
                    name_at += 8
                name = data[name_at:name_at + name_size].split(b"\x00", 1)[0].decode("utf-8", "replace")
                if flags & 0x0004:
                    raise Unsupported("encrypted")
                if method != 0x30:
                    if is_video(name):
                        raise Unsupported("compressed")
                elif not (flags & 0x00E0) == 0x00E0:    # not a folder
                    found.append(Layout(name, unpacked, at + size, packed, None, bool(flags & 0x0002), bool(flags & 0x0001)))
                at += size + packed
            elif kind == 0x7B:                          # end of archive
                break
            else:
                at += size + add
    except (IndexError, struct.error):
        if not found:
            raise Unsupported("header cut off") from None
    return found
