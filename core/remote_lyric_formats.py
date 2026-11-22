"""Normalize remote word timestamps without estimating missing timing."""

from html import unescape
import math
import re

from lyrics.models import LyricLine, LyricSegment
from lyrics.writer import serialize_lrcx


_QRC_CONTENT = re.compile(r'\bLyricContent\s*=\s*(["\'])(.*?)\1', re.DOTALL)
_QRC_LINE = re.compile(r"^\[(\d+),(\d+)\](.*)$")
_QRC_WORD = re.compile(r"\((\d+),(\d+)\)")


def timed_payload(lines):
    if not lines:
        return {}
    return {
        "syncedLyrics": serialize_lrcx(lines), "lyricsFormat": "lrcx",
        "hasWordTiming": any(line.has_karaoke for line in lines),
    }


def qrc_payload(xml):
    # XML attribute parsing normalizes literal line breaks to spaces. Keep
    # those separators while decoding the attribute's escaped characters.
    attribute = _QRC_CONTENT.search(xml)
    lines = []
    for raw in unescape(attribute[2] if attribute else xml).splitlines():
        match = _QRC_LINE.fullmatch(raw.strip())
        if not match:
            continue
        start, duration = int(match[1]), int(match[2])
        text = match[3]
        words = []
        cursor = 0
        for token in _QRC_WORD.finditer(text):
            word = text[cursor:token.start()]
            offset, length = int(token[1]), int(token[2])
            if word:
                words.append({"text": word, "start": offset, "end": offset + length})
            cursor = token.end()
        end = start + duration if duration else max((word["end"] for word in words), default=start)
        lines.append({"start": start, "end": end, "text": _QRC_WORD.sub("", text), "words": words})
    return json_timing_payload(lines)


def _seconds(value):
    if not isinstance(value, (int, float)) or isinstance(value, bool):
        return None
    try:
        return value / 1000 if math.isfinite(value) and 0 <= value <= 86_400_000 else None
    except OverflowError:
        return None


def _segments(text, words, line_start, line_end):
    if not isinstance(words, list):
        return []
    segments = []
    cursor = 0
    previous_start = line_start
    for word in words:
        if not isinstance(word, dict):
            return []
        token = word.get("text", word.get("word"))
        start, end = _seconds(word.get("start")), _seconds(word.get("end"))
        if not isinstance(token, str) or not token or start is None or end is None:
            return []
        position = text.find(token, cursor)
        if (position < 0 or any(char.isalnum() for char in text[cursor:position])
                or not previous_start <= start <= end or (line_end is not None and end > line_end)):
            return []
        stop = position + len(token)
        segments.append(LyricSegment(start, text[cursor:stop], end=end, instant=start == end))
        cursor = stop
        previous_start = start
    if segments:
        if any(char.isalnum() for char in text[cursor:]):
            return []
        segments[-1].text += text[cursor:]
    return segments


def json_timing_payload(rows, indexed_words=None):
    if not isinstance(rows, list):
        return {}
    indexed_words = indexed_words if isinstance(indexed_words, dict) else {}
    lines = []
    for row in rows:
        if not isinstance(row, dict) or row.get("isInstrumental"):
            continue
        text = row.get("text")
        start = _seconds(row.get("start", row.get("start_time")))
        end = _seconds(row.get("end", row.get("end_time")))
        if not isinstance(text, str) or not text.strip() or start is None:
            continue
        if end is not None and end < start:
            end = None
        words = row.get("words") or indexed_words.get(str(row.get("id")))
        segments = _segments(text, words, start, end)
        lines.append(LyricLine(start, text, segments, end, has_karaoke=bool(segments)))
    return timed_payload(sorted(lines, key=lambda line: line.start))
