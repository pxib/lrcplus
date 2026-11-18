"""Literal lyric search and replacements that retain authored annotations."""

from copy import deepcopy
import re

from lyrics.editor_operations import correct_line_text
from lyrics.formatting import parse_emphasis, parse_quote_prefix
from lyrics.reading_groups import remap_reading_groups


def find_text_matches(text, query, *, case_sensitive=False):
    """Return non-overlapping character ranges, including Unicode case folds."""
    if not query:
        return []
    boundaries = None
    if not case_sensitive:
        pieces, boundaries, offset = [], {0: 0}, 0
        for index, char in enumerate(text):
            piece = char.casefold()
            pieces.append(piece)
            offset += len(piece)
            boundaries[offset] = index + 1
        text, query = "".join(pieces), query.casefold()
    matches, position = [], 0
    while (start := text.find(query, position)) >= 0:
        end = start + len(query)
        if boundaries is None:
            matches.append((start, end))
        elif start in boundaries and end in boundaries:
            matches.append((boundaries[start], boundaries[end]))
        else:
            position = start + 1
            continue
        position = end
    return matches


def visible_text_ranges(raw):
    """Map visible lyric characters to UTF-16 ranges in the markup editor."""
    markup, quoted = parse_quote_prefix(raw)
    prefix = 2 if quoted else 0
    decoded, ranges, position = [], [], 0
    for match in re.finditer(r"\{\\nt?\}|\n", markup):
        for index in range(position, match.start()):
            decoded.append(markup[index])
            ranges.append((prefix + index, prefix + index + 1))
        decoded.append("\n")
        ranges.append((prefix + match.start(), prefix + match.end()))
        position = match.end()
    for index in range(position, len(markup)):
        decoded.append(markup[index])
        ranges.append((prefix + index, prefix + index + 1))
    _text, _styles, offsets = parse_emphasis("".join(decoded))
    utf16 = [0]
    for char in raw:
        utf16.append(utf16[-1] + (2 if ord(char) > 0xFFFF else 1))
    return [(utf16[a], utf16[b]) for index, (a, b) in enumerate(ranges)
            if offsets[index + 1] > offsets[index]]


def replace_lyric_matches(line, matches, replacement):
    """Return one corrected line, preserving timing and unaffected readings."""
    result = deepcopy(line)
    for start, end in reversed(matches):
        old = result.text
        text = old[:start] + replacement + old[end:]
        if text == old:
            continue
        change = (start, end, start, start + len(replacement))
        result.segments = correct_line_text(result, text, change)
        result.reading_groups = remap_reading_groups(old, text, result.reading_groups, change)
        delta = len(replacement) - (end - start)
        styles = []
        for a, b, *flags in result.style_spans:
            a = a if a <= start else (a + delta if a >= end else start)
            b = b if b <= start else (b + delta if b >= end else start + len(replacement))
            if a < b:
                styles.append((a, b, *flags))
        result.style_spans = styles
        # Translation markers belong to the preceding newline. Keep markers
        # whose newline survives and remap their visual row after the edit.
        breaks = [index for index, char in enumerate(old) if char == "\n"]
        rows = []
        for row in result.translation_lines:
            marker = breaks[row - 1] if 0 < row <= len(breaks) else None
            if marker is not None and not start <= marker < end:
                marker += delta if marker >= end else 0
                rows.append(text[:marker].count("\n") + 1)
        result.translation_lines = rows
        result.text = text
    if not result.text:
        result.has_karaoke = False
    return result
