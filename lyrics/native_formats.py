"""Bridge the native LRC codecs to the Python lyric model."""

from ctypes import (
    CDLL, POINTER, Structure, byref, c_bool, c_char_p, c_size_t, c_ubyte, c_void_p, string_at,
)
from functools import lru_cache
from pathlib import Path
from struct import pack, unpack_from
import sys


class _Buffer(Structure):
    _fields_ = [("data", c_void_p), ("size", c_size_t)]


@lru_cache(maxsize=1)
def _api():
    if sys.platform != "win32":
        return None
    if getattr(sys, "frozen", False):
        directory = Path(sys._MEIPASS)
    else:
        directory = Path(__file__).resolve().parents[1] / "build" / "lyric-formats" / "release"
    try:
        library = CDLL(str(directory / "lrcplus_lyric_formats_v1.dll"))
        transform = library.lrcplus_lyrics_transform_v1
        free = library.lrcplus_lyrics_free_v1
    except (OSError, AttributeError):
        return None
    transform.argtypes = (c_ubyte, c_char_p, c_size_t, POINTER(_Buffer))
    transform.restype = c_bool
    free.argtypes = (c_void_p, c_size_t)
    free.restype = None
    return transform, free


def available():
    return _api() is not None


def _transform(operation, data):
    api = _api()
    if api is None:
        return None
    transform, free = api
    output = _Buffer()
    if not transform(operation, data, len(data), byref(output)):
        return None
    try:
        return string_at(output.data, output.size)
    finally:
        free(output.data, output.size)


class _Reader:
    def __init__(self, data):
        self.data = data
        self.offset = 0

    def _number(self, format, size):
        value, = unpack_from(format, self.data, self.offset)
        self.offset += size
        return value

    def flags(self):
        return self._number("<B", 1)

    def count(self):
        return self._number("<I", 4)

    def time(self):
        return self._number("<d", 8)

    def text(self):
        size = self.count()
        start = self.offset
        self.offset += size
        return self.data[start:self.offset].decode("utf-8")


def _text(value):
    data = value.encode("utf-8")
    return pack("<I", len(data)) + data


def parse_lrc(lines):
    try:
        payload = pack("<I", len(lines)) + b"".join(_text(line) for line in lines)
    except UnicodeEncodeError:
        return None
    result = _transform(0, payload)
    if result is None:
        return None
    reader = _Reader(result)
    return [(reader.time(), reader.text()) for _ in range(reader.count())]


def _decode_records(reader):
    for _ in range(reader.count()):
        kind = reader.flags()
        if kind != 2:
            yield ("reading_groups" if kind == 0 else "line_ends"), reader.text()
            continue
        start = reader.time()
        flags = reader.flags()
        end = reader.time() if reader.flags() else None
        translations = [reader.count() for _ in range(reader.count())]
        chunks = [(reader.time(), reader.text()) for _ in range(reader.count())]
        yield "line", (start, end, bool(flags & 1), bool(flags & 2), bool(flags & 4), translations, chunks)


def scan_lrcx(text):
    if not available():
        return None
    try:
        result = _transform(1, _text(text))
    except UnicodeEncodeError:
        return None
    return None if result is None else _decode_records(_Reader(result))


def _decode_output(data):
    return None if data is None else data.decode("utf-8")


def write_lrc(header, lines):
    try:
        payload = _text(header) + pack("<I", len(lines)) + b"".join(
            pack("<d", float(line.start)) + _text(str(line.text)) for line in lines
        )
    except (OverflowError, UnicodeEncodeError):
        return None
    return _decode_output(_transform(2, payload))


def _time(value):
    if type(value) not in (int, float):
        raise TypeError
    return pack("<d", value)


def _optional_time(value):
    return b"\0" if value is None else b"\1" + _time(value)


def write_lrcx(header, lines):
    if not available():
        return None
    try:
        payload = bytearray(_text(header) + pack("<I", len(lines)))
        for line, segments in lines:
            payload.extend(_time(line.start))
            payload.extend(_optional_time(line.end))
            payload.append(bool(line.is_blockquote) | (bool(line.has_karaoke) << 1))
            payload.extend(pack("<I", len(segments)))
            for segment, markup, is_break, has_mora in segments:
                payload.extend(_time(segment.start))
                payload.extend(_optional_time(getattr(segment, "end", None)))
                payload.append(bool(getattr(segment, "instant", False)) | (int(is_break) << 1)
                               | (int(segment.text == "\n") << 2) | (int(has_mora) << 3)
                               | (int(segment.text == "") << 4))
                payload.extend(_text(markup))
    except (OverflowError, TypeError, UnicodeEncodeError):
        return None
    return _decode_output(_transform(3, bytes(payload)))
