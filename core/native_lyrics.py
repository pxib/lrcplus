"""Decode native word lyrics and preserve their millisecond timing in LRCX."""
import base64
import binascii
from dataclasses import dataclass
import re
import zlib

from lyrics.formatting import format_emphasis


MAX_KRC_BYTES = 2 * 1024 * 1024
MAX_TEXT_BYTES = 4 * 1024 * 1024
_KRC_KEY = b"@Gaw^2tGQ61-\xce\xd2ni"
_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_WORDS = {
    "yrc": re.compile(r"\((\d+),(\d+),-?\d+\)"),
    "krc": re.compile(r"<(\d+),(\d+),-?\d+>"),
}


@dataclass(frozen=True)
class Word:
    start: int
    end: int
    text: str


@dataclass(frozen=True)
class WordLine:
    start: int
    end: int
    words: tuple[Word, ...]
    word_timed: bool

    @property
    def text(self):
        return "".join(word.text for word in self.words)


def decode_krc(encoded):
    if not isinstance(encoded, str) or len(encoded) > (MAX_KRC_BYTES * 4 // 3 + 8):
        raise ValueError("Invalid or oversized KRC file.")
    try:
        raw = base64.b64decode("".join(encoded.split()), validate=True)
        if not raw.startswith(b"krc1") or len(raw) > MAX_KRC_BYTES:
            raise ValueError("Invalid or oversized KRC file.")
        compressed = bytes(value ^ _KRC_KEY[index % len(_KRC_KEY)] for index, value in enumerate(raw[4:]))
        inflater = zlib.decompressobj()
        text = inflater.decompress(compressed, MAX_TEXT_BYTES + 1)
        if len(text) > MAX_TEXT_BYTES or not inflater.eof or inflater.unused_data:
            raise ValueError("Invalid or oversized KRC lyrics.")
        return text.decode("utf-8-sig")
    except (binascii.Error, zlib.error, UnicodeError):
        raise ValueError("Invalid KRC lyrics.") from None


def parse_word_lyrics(text, kind):
    """YRC word starts are absolute; KRC word starts are relative to the line."""
    if kind not in _WORDS or not isinstance(text, str) or len(text.encode("utf-8")) > MAX_TEXT_BYTES:
        raise ValueError("Invalid native lyrics.")
    lines = []
    for raw_line in text.lstrip("\ufeff").splitlines():
        match = _LINE.match(raw_line)
        if not match:
            # JSON credit rows and native metadata tags aren't sung lyrics.
            continue
        start, duration = int(match[1]), int(match[2])
        body = match[3]
        markers = list(_WORDS[kind].finditer(body))
        words = []
        if markers and markers[0].start():
            first_start = int(markers[0][1]) + (start if kind == "krc" else 0)
            if first_start < start:
                raise ValueError("Native lyrics have invalid word timing.")
            words.append(Word(start, first_start, body[:markers[0].start()]))
        for index, marker in enumerate(markers):
            word_start = int(marker[1]) + (start if kind == "krc" else 0)
            word_end = word_start + int(marker[2])
            content = body[marker.end():markers[index + 1].start() if index + 1 < len(markers) else len(body)]
            if word_start < start or (words and word_start < words[-1].start):
                raise ValueError("Native lyrics have invalid word timing.")
            if content:
                words.append(Word(word_start, word_end, content))
        if not markers and body:
            words.append(Word(start, start + duration, body))
        if words:
            lines.append(WordLine(start, start + duration, tuple(words), bool(markers)))
    return lines


def timestamp_ms(value):
    minutes, remainder = divmod(int(value), 60000)
    seconds, milliseconds = divmod(remainder, 1000)
    return f"{minutes:02d}:{seconds:02d}.{milliseconds:03d}"


def to_lrcx(lines):
    output = []
    for line in lines:
        parts = [f"[{timestamp_ms(line.start)}]"]
        for word in line.words:
            parts.append(f"<{timestamp_ms(word.start)}>")
            parts.append(format_emphasis(word.text, []))
            # Keep explicit ends even when words touch. Editing the next word
            # must not change the duration supplied for this word.
            parts.append(f"<{timestamp_ms(word.end)}>")
        if line.words and line.end != line.words[-1].end:
            parts.append(f"<{timestamp_ms(line.end)}>")
        output.append("".join(parts))
    return "\n".join(output)
