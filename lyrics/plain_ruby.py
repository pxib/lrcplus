"""Store authored readings with untimed plain lyrics."""

from html import unescape
import json
import re

from lyrics.models import LyricLine, LyricSegment


_HEADER = "[plain_ruby:"
_RUBY = re.compile(r"<ruby>([^<>]+)<rt>([^<>]+)</rt></ruby>", re.IGNORECASE)
_BR = re.compile(r"<br\s*/?>", re.IGNORECASE)


def encode_plain_ruby(text, markup):
    """Accept a reading only when the source's base text matches the lyric."""
    rows = text.splitlines()
    ruby_rows = _BR.sub("\n", markup).splitlines()
    if len(rows) != len(ruby_rows):
        return text
    annotations = {}
    for index, (row, source) in enumerate(zip(rows, ruby_rows)):
        pieces = []
        spans = []
        offset = cursor = 0
        for match in _RUBY.finditer(source):
            prefix = unescape(source[cursor:match.start()])
            base, reading = map(unescape, match.groups())
            offset += len(prefix)
            spans.append((offset, offset + len(base), reading))
            pieces.extend((prefix, base))
            offset += len(base)
            cursor = match.end()
        pieces.append(unescape(source[cursor:]))
        if spans and "".join(pieces) == row:
            annotations[index] = spans
    if not annotations:
        return text
    return f"{_HEADER}{json.dumps(annotations, ensure_ascii=False)}]\n{text}"


def load_plain_rows(file):
    rows = [raw.rstrip("\r\n") for raw in file]
    annotations = {}
    if rows and rows[0].startswith(_HEADER) and rows[0].endswith("]"):
        try:
            metadata = json.loads(rows[0][len(_HEADER):-1])
        except (ValueError, RecursionError):
            metadata = None
        if isinstance(metadata, dict):
            annotations = metadata
            rows = rows[1:]
    lines = []
    for index, row in enumerate(rows):
        if not row:
            continue
        spans = annotations.get(str(index))
        accepted = []
        for span in spans if isinstance(spans, list) else ():
            if (isinstance(span, list) and len(span) == 3 and type(span[0]) is int and type(span[1]) is int
                    and 0 <= span[0] < span[1] <= len(row) and isinstance(span[2], str) and span[2]):
                accepted.append(tuple(span))
        start = float(index)
        segments = [LyricSegment(start, row, ruby_source="manual", ruby_spans=accepted)] if accepted else []
        lines.append(LyricLine(start, row, segments))
    return lines
