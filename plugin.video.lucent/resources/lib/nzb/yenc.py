# -*- coding: utf-8 -*-
"""yEnc: how binary files are posted on Usenet (http://www.yenc.org/yenc-draft.1.3.txt).

An article is a '=ybegin' line, an optional '=ypart' line (which bytes of the file this article holds), the encoded
lines and a '=yend' line with the checksum. Every byte is stored as (byte + 42) % 256; a few bytes that would break a
line are written as '=' followed by (byte + 42 + 64) % 256. Decoding works on whole byte strings (split and translate),
never byte by byte in Python: a film is thousands of articles of about 700 KB.
"""
import re
import zlib

_MINUS_42 = bytes((b - 42) % 256 for b in range(256))
_MINUS_64 = bytes((b - 64) % 256 for b in range(256))
_FIELD = re.compile(rb"(\w+)=(\S+)")


class YencError(Exception):
    """The article is not yEnc, or it is damaged."""


def _fields(line):
    out = {}
    name = None
    if b" name=" in line:                       # the name is the rest of the line and may hold spaces
        line, name = line.split(b" name=", 1)
    for key, value in _FIELD.findall(line):
        out[key.decode("ascii", "replace")] = value.decode("ascii", "replace")
    if name is not None:
        out["name"] = name.strip().decode("utf-8", "replace")
    return out


def decode(body):
    """body: the article as the server sent it (lines with CRLF, dots already unstuffed).
    Returns (data, info). info: name, size (of the whole file), begin and end (1-based, of this part), crc."""
    lines = body.split(b"\r\n") if b"\r\n" in body else body.split(b"\n")
    start = next((i for i, line in enumerate(lines) if line.startswith(b"=ybegin ")), None)
    if start is None:
        raise YencError("no =ybegin")
    info = _fields(lines[start])
    data_from = start + 1
    if data_from < len(lines) and lines[data_from].startswith(b"=ypart "):
        info.update(_fields(lines[data_from]))
        data_from += 1
    end = next((i for i in range(len(lines) - 1, data_from - 1, -1) if lines[i].startswith(b"=yend")), None)
    if end is None:
        raise YencError("no =yend")
    trailer = _fields(lines[end])
    raw = b"".join(lines[data_from:end])
    if b"=" in raw:
        pieces = raw.split(b"=")
        fixed = [pieces[0]]
        for piece in pieces[1:]:
            if piece:
                fixed.append(piece[:1].translate(_MINUS_64) + piece[1:])
        raw = b"".join(fixed)
    data = raw.translate(_MINUS_42)
    crc = trailer.get("pcrc32") or (trailer.get("crc32") if "begin" not in info else None)
    if crc:
        try:
            if zlib.crc32(data) & 0xFFFFFFFF != int(crc, 16):
                raise YencError("checksum")
        except ValueError:
            pass
    expected = int(trailer.get("size") or 0)
    if expected and expected != len(data):
        raise YencError("size %d, expected %d" % (len(data), expected))
    result = {"name": info.get("name", ""), "size": int(info.get("size") or 0)}
    if "begin" in info:
        result["begin"], result["end"] = int(info["begin"]), int(info["end"])
    else:
        result["begin"], result["end"] = 1, len(data)
    return data, result


def encode(data, name, size, begin, line=128):
    """For tests: one article with =ypart, the way posters make them."""
    out = bytearray()
    width = 0
    lines = []
    for b in data:
        c = (b + 42) % 256
        if c in (0, 10, 13, 61) or (c in (9, 32) and width in (0, line - 1)) or (c == 46 and width == 0):
            out += b"=" + bytes([(c + 64) % 256])
            width += 2
        else:
            out.append(c)
            width += 1
        if width >= line:
            lines.append(bytes(out))
            out = bytearray()
            width = 0
    if out:
        lines.append(bytes(out))
    head = b"=ybegin part=1 line=%d size=%d name=%s\r\n=ypart begin=%d end=%d\r\n" % (line, size, name.encode(), begin, begin + len(data) - 1)
    tail = b"\r\n=yend size=%d part=1 pcrc32=%08x" % (len(data), zlib.crc32(data) & 0xFFFFFFFF)
    return head + b"\r\n".join(lines) + tail
