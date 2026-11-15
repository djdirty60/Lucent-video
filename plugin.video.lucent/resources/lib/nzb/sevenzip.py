# -*- coding: utf-8 -*-
"""Where the film sits inside a 7z set, for stored (uncompressed, 'Copy' method) archives only.

A 7z archive starts with a 32-byte signature header that points to the real header at the end of the archive. A split
set (name.7z.001, .002, ...) is simply that one archive cut in equal pieces. With the Copy method the files' bytes lie
one after the other from byte 32 on, so once we know a file's place we can read it straight from the volumes.

The header itself is often LZMA-compressed, even in a stored archive (7-Zip's default); Python's lzma module reads that.
Compressed or encrypted contents cannot be streamed and are refused with a reason.

Format: '7z Format' description (DOC/7zFormat.txt) published with 7-Zip by Igor Pavlov.
"""
import struct

SIGNATURE = b"7z\xbc\xaf\x27\x1c"

ID_END, ID_HEADER, ID_ARCHIVE_PROPERTIES, ID_ADDITIONAL_STREAMS = 0x00, 0x01, 0x02, 0x03
ID_MAIN_STREAMS, ID_FILES, ID_PACK_INFO, ID_UNPACK_INFO, ID_SUBSTREAMS = 0x04, 0x05, 0x06, 0x07, 0x08
ID_SIZE, ID_CRC, ID_FOLDER, ID_CODERS_UNPACK_SIZE, ID_NUM_UNPACK_STREAM = 0x09, 0x0A, 0x0B, 0x0C, 0x0D
ID_EMPTY_STREAM, ID_EMPTY_FILE, ID_NAME, ID_ENCODED_HEADER, ID_DUMMY = 0x0E, 0x0F, 0x11, 0x17, 0x19

COPY, LZMA, LZMA2, AES = b"\x00", b"\x03\x01\x01", b"\x21", b"\x06\xf1\x07\x01"


class Unsupported(Exception):
    """Compressed, encrypted, or not a 7z we understand."""


class Entry:
    def __init__(self, name, offset, size):
        self.name = name
        self.offset = offset            # from the start of the whole archive (volume 1, byte 0)
        self.size = size

    def __repr__(self):
        return "<7z %s at %d, %d bytes>" % (self.name, self.offset, self.size)


def start_header(head):
    """(offset, size) of the real header, from the first 32 bytes."""
    if not head.startswith(SIGNATURE) or len(head) < 32:
        raise Unsupported("not a 7z")
    offset, size = struct.unpack_from("<QQ", head, 12)
    if size <= 0 or size > 64 * 1024 * 1024:
        raise Unsupported("header size")
    return 32 + offset, size


class _Reader:
    def __init__(self, data):
        self.data, self.at = data, 0

    def byte(self):
        if self.at >= len(self.data):
            raise Unsupported("header cut off")
        value = self.data[self.at]
        self.at += 1
        return value

    def number(self):
        """7z's variable-length number: the high bits of the first byte say how many bytes follow."""
        first = self.byte()
        mask, value = 0x80, 0
        for count in range(8):
            if not first & mask:
                high = first & (mask - 1)
                return value | (high << (8 * count))
            value |= self.byte() << (8 * count)
            mask >>= 1
        return value

    def take(self, length):
        if self.at + length > len(self.data):
            raise Unsupported("header cut off")
        out = self.data[self.at:self.at + length]
        self.at += length
        return out

    def bits(self, count):
        out, mask, value = [], 0, 0
        for _ in range(count):
            if not mask:
                value, mask = self.byte(), 0x80
            out.append(bool(value & mask))
            mask >>= 1
        return out

    def defined(self, count):
        if self.byte():
            return [True] * count
        return self.bits(count)

    def skip_property(self):
        self.take(self.number())


def _pack_info(r):
    pack_pos = r.number()
    count = r.number()
    sizes = []
    while True:
        kind = r.byte()
        if kind == ID_END:
            return pack_pos, sizes
        if kind == ID_SIZE:
            sizes = [r.number() for _ in range(count)]
        elif kind == ID_CRC:
            defined = r.defined(count)
            r.take(4 * sum(defined))
        else:
            r.skip_property()


def _folder(r):
    coders, total_in, total_out = [], 0, 0
    for _ in range(r.number()):
        flags = r.byte()
        method = r.take(flags & 0x0F)
        ins = outs = 1
        if flags & 0x10:
            ins, outs = r.number(), r.number()
        props = r.take(r.number()) if flags & 0x20 else b""
        coders.append((method, props))
        total_in += ins
        total_out += outs
    for _ in range(total_out - 1):                 # bind pairs
        r.number(), r.number()
    packed = total_in - (total_out - 1)
    if packed > 1:
        for _ in range(packed):
            r.number()
    return {"coders": coders, "outs": total_out, "sizes": [], "crc": False}


def _unpack_info(r):
    if r.byte() != ID_FOLDER:
        raise Unsupported("folders")
    count = r.number()
    if r.byte():
        raise Unsupported("external folders")
    folders = [_folder(r) for _ in range(count)]
    if r.byte() != ID_CODERS_UNPACK_SIZE:
        raise Unsupported("unpack sizes")
    for folder in folders:
        folder["sizes"] = [r.number() for _ in range(folder["outs"])]
    while True:
        kind = r.byte()
        if kind == ID_END:
            return folders
        if kind == ID_CRC:
            defined = r.defined(count)
            for folder, known in zip(folders, defined):
                folder["crc"] = known
            r.take(4 * sum(defined))
        else:
            r.skip_property()


def _substreams(r, folders):
    per = [1] * len(folders)
    sizes = None
    kind = r.byte()
    if kind == ID_NUM_UNPACK_STREAM:
        per = [r.number() for _ in folders]
        kind = r.byte()
    if kind == ID_SIZE:
        sizes = []
        for folder, count in zip(folders, per):
            if not count:
                continue
            own = [r.number() for _ in range(count - 1)]
            sizes += own + [folder["sizes"][-1] - sum(own)]
        kind = r.byte()
    if sizes is None:
        sizes = [f["sizes"][-1] for f, c in zip(folders, per) if c]
    while kind != ID_END:
        if kind == ID_CRC:
            unknown = sum(c for f, c in zip(folders, per) if not (c == 1 and f["crc"]))
            defined = r.defined(unknown)
            r.take(4 * sum(defined))
        else:
            r.skip_property()
        kind = r.byte()
    return per, sizes


def _streams(r):
    pack_pos, pack_sizes, folders = 0, [], []
    per, sizes = None, None
    while True:
        kind = r.byte()
        if kind == ID_END:
            break
        if kind == ID_PACK_INFO:
            pack_pos, pack_sizes = _pack_info(r)
        elif kind == ID_UNPACK_INFO:
            folders = _unpack_info(r)
        elif kind == ID_SUBSTREAMS:
            per, sizes = _substreams(r, folders)
        else:
            raise Unsupported("streams info")
    if per is None:
        per = [1] * len(folders)
        sizes = [f["sizes"][-1] for f in folders]
    return {"pack_pos": pack_pos, "pack_sizes": pack_sizes, "folders": folders, "per": per, "sizes": sizes}


def _names(r, size, count):
    end = r.at + size
    if r.byte():
        raise Unsupported("external names")
    raw = r.data[r.at:end]
    r.at = end
    names = raw.decode("utf-16-le", "replace").split("\x00")
    return names[:count]


def _files(r):
    count = r.number()
    empty, names = [False] * count, [""] * count
    while True:
        kind = r.byte()
        if kind == ID_END:
            return names, empty
        size = r.number()
        if kind == ID_NAME:
            names = _names(r, size, count)
        elif kind == ID_EMPTY_STREAM:
            start = r.at
            empty = r.bits(count)
            r.at = start + size
        else:
            r.take(size)


def _decode_header(streams, read):
    """An encoded (packed) header: read the packed bytes and unpack them with LZMA or LZMA2."""
    import lzma
    if len(streams["folders"]) != 1:
        raise Unsupported("encoded header")
    folder = streams["folders"][0]
    if any(method == AES for method, _p in folder["coders"]):
        raise Unsupported("encrypted")
    if len(folder["coders"]) != 1:
        raise Unsupported("header filters")
    method, props = folder["coders"][0]
    packed = read(32 + streams["pack_pos"], streams["pack_sizes"][0])
    if method == LZMA:
        if len(props) < 5:
            raise Unsupported("LZMA properties")
        lc_lp_pb = props[0]
        filters = [{"id": lzma.FILTER_LZMA1, "dict_size": struct.unpack_from("<I", props, 1)[0],
                    "lc": lc_lp_pb % 9, "lp": (lc_lp_pb // 9) % 5, "pb": lc_lp_pb // 45}]
    elif method == LZMA2:
        bits = props[0] if props else 0
        dict_size = 0xFFFFFFFF if bits >= 40 else (2 | (bits & 1)) << (bits // 2 + 11)
        filters = [{"id": lzma.FILTER_LZMA2, "dict_size": dict_size}]
    elif method == COPY:
        return packed
    else:
        raise Unsupported("header method")
    try:
        data = lzma.LZMADecompressor(lzma.FORMAT_RAW, filters=filters).decompress(packed, folder["sizes"][-1])
    except lzma.LZMAError:
        raise Unsupported("header damaged") from None
    return data[:folder["sizes"][-1]]


def entries(header, read):
    """header: the bytes at start_header(). read(offset, length) reads the archive (for an encoded header).
    Returns [Entry] of the stored files; raises Unsupported for compressed or encrypted contents."""
    r = _Reader(header)
    kind = r.byte()
    if kind == ID_ENCODED_HEADER:
        r = _Reader(_decode_header(_streams(r), read))
        kind = r.byte()
    if kind != ID_HEADER:
        raise Unsupported("header")
    streams, names, empty = None, [], []
    while True:
        kind = r.byte()
        if kind == ID_END:
            break
        if kind == ID_ARCHIVE_PROPERTIES:
            while r.byte():
                r.skip_property()
        elif kind == ID_ADDITIONAL_STREAMS:
            raise Unsupported("additional streams")
        elif kind == ID_MAIN_STREAMS:
            streams = _streams(r)
        elif kind == ID_FILES:
            names, empty = _files(r)
        else:
            raise Unsupported("header part")
    if not streams:
        raise Unsupported("no files")
    for folder in streams["folders"]:
        for method, _props in folder["coders"]:
            if method == AES:
                raise Unsupported("encrypted")
            if method != COPY:
                raise Unsupported("compressed")
    out, sizes = [], iter(streams["sizes"])
    offset = 32 + streams["pack_pos"]
    with_data = [n for n, e in zip(names, empty) if not e]
    for name in with_data:
        size = next(sizes, None)
        if size is None:
            break
        out.append(Entry(name, offset, size))
        offset += size
    return out
