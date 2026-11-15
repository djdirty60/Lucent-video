# -*- coding: utf-8 -*-
"""yEnc: how binary files are posted on Usenet (http://www.yenc.org/yenc-draft.1.3.txt).

An article is a '=ybegin' line, an optional '=ypart' line (which bytes of the file this article holds), the encoded
lines and a '=yend' line with the checksum. Every byte is stored as (byte + 42) % 256; a few bytes that would break a
line are written as '=' followed by (byte + 42 + 64) % 256. Decoding works on whole byte strings (split and translate),
never byte by byte in Python: a film is thousands of articles of about 700 KB.
"""
import os
import platform
import re
import threading
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


# ------------------------------------------------------------------ the fast way: a tiny C file (native/ydec.c)
_native = {"done": False, "fn": None, "why": ""}
_native_lock = threading.Lock()
_ARCH = {"aarch64": "aarch64", "arm64": "aarch64", "armv8l": "armv7", "armv7l": "armv7", "armv7": "armv7",
         "x86_64": "x86_64", "amd64": "x86_64"}


_COPY_DIR = {"path": ""}


def set_copy_dir(path):
    """Android: a folder inside the app (not on the 'sdcard', where Android refuses to load code) to copy the unpacker
    to when it cannot be loaded from the add-on's own folder."""
    _COPY_DIR["path"] = path or ""


def _candidates():
    """(path, label) of the unpacker files to try for this system, best first."""
    here = os.path.join(os.path.dirname(os.path.abspath(__file__)), "native")
    system = platform.system().lower()
    arch = _ARCH.get(platform.machine().lower())
    if system == "windows":
        return [(os.path.join(here, "windows-x86_64", "ydec.dll"), "windows-x86_64")] if arch == "x86_64" else []
    if system != "linux" or not arch:
        return []
    if os.environ.get("ANDROID_ROOT") or os.environ.get("ANDROID_DATA"):
        name = os.path.join(here, "android-%s" % arch, "libydec.so")
        out = [(name, "android-%s" % arch)]
        if _COPY_DIR["path"]:
            out.append((os.path.join(_COPY_DIR["path"], "libydec-%s.so" % arch), "android-%s (copied into the app)" % arch))
        return out
    return [(os.path.join(here, "linux-%s" % arch, "libydec.so"), "linux-%s" % arch)]


def _try(path, ctypes, source=None):
    if source and not os.path.isfile(path):
        import shutil
        os.makedirs(os.path.dirname(path), exist_ok=True)
        shutil.copyfile(source, path + ".tmp")
        os.chmod(path + ".tmp", 0o700)
        os.replace(path + ".tmp", path)
    lib = ctypes.CDLL(path)                     # CDLL: Python's lock is let go during each call
    fn = lib.lucent_ydecode
    fn.restype = ctypes.c_ulong
    fn.argtypes = [ctypes.c_char_p, ctypes.c_ulong, ctypes.c_void_p]
    sample = bytes(range(256)) * 4
    if _c_unpack(fn, ctypes, _python_lines(encode(sample, "t", len(sample), 1))) != sample:
        raise OSError("self-test failed")
    return fn


def _load():
    """The C unpacker for this system, or None (then Python does it, as before). Loaded once, checked with a test
    article: a wrong or unreadable file can never give wrong pictures, it is simply not used."""
    with _native_lock:
        if _native["done"]:
            return _native["fn"]
        _native["done"] = True
        tries = _candidates()
        if not tries:
            _native["why"] = "no fast version for %s %s" % (platform.system(), platform.machine())
            return None
        try:
            import ctypes
        except ImportError:
            _native["why"] = "ctypes missing"
            return None
        reasons, first = [], tries[0][0]
        for number, (path, label) in enumerate(tries):
            try:
                fn = _try(path, ctypes, source=first if number else None)
            except (OSError, AttributeError, ValueError) as error:
                reasons.append("%s: %s" % (label, (str(error).split(":")[-1].strip() or type(error).__name__)[:60]))
                continue
            _native["fn"], _native["label"] = (fn, ctypes), label
            return _native["fn"]
        _native["why"] = "fast version not usable (%s)" % "; ".join(reasons)
        return None


def _python_lines(article):
    """The data lines of a test article (between its header lines and =yend)."""
    start = article.index(b"\n", article.index(b"=ypart")) + 1
    return article[start:article.rindex(b"\n=yend")]


def _c_unpack(fn, ctypes, raw):
    out = bytearray(len(raw))
    holder = (ctypes.c_char * len(out)).from_buffer(out) if out else None
    size = fn(raw, len(raw), ctypes.addressof(holder) if holder is not None else None)
    del holder
    return bytes(memoryview(out)[:size])


def mode():
    """'C' or 'Python (reason)', for the log."""
    return "C, %s" % _native.get("label", "") if _load() else "Python (%s)" % (_native["why"] or "?")


def _decode_fast(body, native):
    """Like decode(), but the header lines are found with find() and the data is unpacked in C."""
    fn, ctypes = native
    start = 0 if body.startswith(b"=ybegin ") else body.find(b"\n=ybegin ") + 1
    if start <= 0 and not body.startswith(b"=ybegin "):
        raise YencError("no =ybegin")
    line_end = body.find(b"\n", start)
    info = _fields(body[start:line_end].rstrip(b"\r"))
    data_from = line_end + 1
    if body.startswith(b"=ypart ", data_from):
        line_end = body.find(b"\n", data_from)
        info.update(_fields(body[data_from:line_end].rstrip(b"\r")))
        data_from = line_end + 1
    end = body.rfind(b"\n=yend")
    if end < data_from - 1:
        raise YencError("no =yend")
    trailer_end = body.find(b"\n", end + 1)
    trailer = _fields(body[end + 1:trailer_end if trailer_end > 0 else len(body)].rstrip(b"\r"))
    data = _c_unpack(fn, ctypes, body[data_from:end])
    return _finish(data, info, trailer)


def _finish(data, info, trailer):
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


def decode(body):
    """body: the article as the server sent it (lines with CRLF, dots already unstuffed).
    Returns (data, info). info: name, size (of the whole file), begin and end (1-based, of this part), crc."""
    native = _native["fn"] if _native["done"] else _load()
    if native:
        return _decode_fast(body, native)
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
