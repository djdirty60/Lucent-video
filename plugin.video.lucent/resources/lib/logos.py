# -*- coding: utf-8 -*-
"""Sharp, readable studio and network logos.

TMDB has the logos in high quality (500 px wide), but many are black, which cannot be read on the dark skin. This
module downloads such a logo once, turns its dark parts white (coloured parts stay as they are) and keeps the result
in the add-on's data folder. PNG is read and written here without extra libraries (Kodi has no Pillow)."""
import hashlib
import os
import struct
import urllib.request
import zlib

import util

FOLDER = os.path.join(util.PROFILE, "logos")
VERSION = "1"
DARK = 0.35            # a logo whose visible pixels are this dark on average is made light
TIMEOUT = 10


def readable(url):
    """A local, readable copy of a TMDB logo; the logo's own address when it is light already or cannot be read."""
    if not url or not url.startswith("http"):
        return url or ""
    name = hashlib.sha1((VERSION + url).encode("utf-8")).hexdigest() + ".png"
    path = os.path.join(FOLDER, name)
    light = path + ".light"
    if os.path.exists(path):
        return path
    if os.path.exists(light):
        return url
    try:
        request = urllib.request.Request(url, headers={"User-Agent": "Lucent-Video/0.1"})
        with urllib.request.urlopen(request, timeout=TIMEOUT) as response:
            data = response.read()
        width, height, rows = _read(data)
    except Exception as error:                  # noqa: BLE001 - any odd file: keep the original
        util.log("logos: not read (%s)" % type(error).__name__)
        return url
    try:
        os.makedirs(FOLDER, exist_ok=True)
        if not _dark(rows):
            open(light, "w").close()
            return url
        with open(path, "wb") as handle:
            handle.write(_write(width, height, _whiten(rows)))
    except OSError:
        return url
    return path


def _dark(rows):
    """A see-through logo whose visible part is dark (a logo on its own background is left alone)."""
    total = seen = clear = 0
    for row in rows:
        for i in range(0, len(row), 4 * 3):     # every third pixel is enough
            if row[i + 3] > 128:
                total += 0.299 * row[i] + 0.587 * row[i + 1] + 0.114 * row[i + 2]
                seen += 1
            else:
                clear += 1
    return seen > 0 and clear > 0 and total / seen / 255 < DARK


def _whiten(rows):
    """Dark and grey pixels become white with the same see-through; coloured pixels stay."""
    out = []
    for row in rows:
        row = bytearray(row)
        for i in range(0, len(row), 4):
            r, g, b = row[i], row[i + 1], row[i + 2]
            if max(r, g, b) - min(r, g, b) < 40 or 0.299 * r + 0.587 * g + 0.114 * b < 70:
                row[i] = row[i + 1] = row[i + 2] = 255
        out.append(row)
    return out


def _read(data):
    """(width, height, [RGBA rows]) of an 8-bit PNG (grey, RGB, palette, grey+alpha or RGBA, not interlaced)."""
    if data[:8] != b"\x89PNG\r\n\x1a\n":
        raise ValueError("no png")
    pos, idat, palette, trns = 8, [], None, None
    width = height = depth = kind = 0
    while pos < len(data):
        length, tag = struct.unpack(">I4s", data[pos:pos + 8])
        chunk = data[pos + 8:pos + 8 + length]
        pos += 12 + length
        if tag == b"IHDR":
            width, height, depth, kind, _c, _f, interlace = struct.unpack(">IIBBBBB", chunk)
            if depth != 8 or interlace:
                raise ValueError("unsupported png")
        elif tag == b"PLTE":
            palette = chunk
        elif tag == b"tRNS":
            trns = chunk
        elif tag == b"IDAT":
            idat.append(chunk)
        elif tag == b"IEND":
            break
    channels = {0: 1, 2: 3, 3: 1, 4: 2, 6: 4}[kind]
    raw = zlib.decompress(b"".join(idat))
    stride = width * channels
    rows, previous, pos = [], bytearray(stride), 0
    for _ in range(height):
        method = raw[pos]
        line = bytearray(raw[pos + 1:pos + 1 + stride])
        pos += 1 + stride
        _unfilter(method, line, previous, channels)
        rows.append(_rgba(line, kind, palette, trns, width))
        previous = line
    return width, height, rows


def _unfilter(method, line, previous, bpp):
    if method == 0:
        return
    for i in range(len(line)):
        left = line[i - bpp] if i >= bpp else 0
        up = previous[i]
        if method == 1:
            line[i] = (line[i] + left) & 255
        elif method == 2:
            line[i] = (line[i] + up) & 255
        elif method == 3:
            line[i] = (line[i] + ((left + up) >> 1)) & 255
        elif method == 4:
            corner = previous[i - bpp] if i >= bpp else 0
            p = left + up - corner
            pa, pb, pc = abs(p - left), abs(p - up), abs(p - corner)
            line[i] = (line[i] + (left if pa <= pb and pa <= pc else up if pb <= pc else corner)) & 255
        else:
            raise ValueError("bad filter")


def _rgba(line, kind, palette, trns, width):
    if kind == 6:
        return line
    out = bytearray(width * 4)
    for x in range(width):
        if kind == 2:
            r, g, b = line[3 * x:3 * x + 3]
            a = 255
        elif kind == 0:
            r = g = b = line[x]
            a = 255
        elif kind == 4:
            r = g = b = line[2 * x]
            a = line[2 * x + 1]
        else:                                   # palette
            n = line[x]
            r, g, b = palette[3 * n:3 * n + 3]
            a = trns[n] if trns and n < len(trns) else 255
        out[4 * x:4 * x + 4] = bytes((r, g, b, a))
    return out


def _write(width, height, rows):
    def chunk(tag, body):
        return struct.pack(">I", len(body)) + tag + body + struct.pack(">I", zlib.crc32(tag + body) & 0xffffffff)
    raw = b"".join(b"\x00" + bytes(row) for row in rows)
    return (b"\x89PNG\r\n\x1a\n" + chunk(b"IHDR", struct.pack(">IIBBBBB", width, height, 8, 6, 0, 0, 0))
            + chunk(b"IDAT", zlib.compress(raw, 6)) + chunk(b"IEND", b""))
