"""Text corrections and line surgery without discarding authored lyric data."""

from copy import deepcopy
from difflib import SequenceMatcher

from lyrics.models import LyricLine, LyricSegment
from lyrics.karaoke_timing import segment_end
from lyrics.formatting import parse_emphasis, parse_quote_prefix
from lyrics.line_breaks import decode_line_breaks


def visible_text_position(raw, utf16_position):
    """Map a Qt text cursor through quote, break and emphasis markup."""
    position = len(raw.encode("utf-16-le")[:max(0, utf16_position) * 2].decode("utf-16-le", errors="ignore"))
    markup, quoted = parse_quote_prefix(raw)
    position = max(0, position - (2 if quoted else 0))
    decoded, _ = decode_line_breaks(markup)
    prefix, _ = decode_line_breaks(markup[:position])
    _text, _styles, offsets = parse_emphasis(decoded)
    return offsets[min(len(prefix), len(offsets) - 1)]


def _ruby_spans(segment):
    if segment.ruby_spans:
        return list(segment.ruby_spans)
    if segment.ruby and segment.ruby_source != "generated":
        return [(0, len(segment.text), segment.ruby)]
    return []


def _set_ruby_spans(segment, spans):
    segment.ruby_spans = spans
    segment.ruby = None
    segment.ruby_source = None
    if len(spans) == 1 and spans[0][:2] == (0, len(segment.text)):
        segment.ruby = spans[0][2]
        segment.ruby_source = "manual"


def replace_reading_span(line, start, end, reading):
    """Edit one annotation in place, preserving timing and other annotations."""
    from lyrics.furigana import split_reading_by_text_ranges

    if not 0 <= start < end <= len(line.text):
        raise ValueError("The reading range must be inside the lyric text.")
    offset = 0
    for segment in line.segments:
        segment_start = offset
        offset += len(segment.text)
        first, last = max(start, segment_start), min(end, offset)
        if first >= last:
            continue
        local_first, local_last = first - segment_start, last - segment_start
        spans = []
        for a, b, old_reading in _ruby_spans(segment):
            if b <= local_first or a >= local_last:
                spans.append((a, b, old_reading))
                continue
            # If an imported annotation crosses the edited word, keep its
            # untouched prefix/suffix with their corresponding readings.
            for left, right in ((a, min(b, local_first)), (max(a, local_last), b)):
                if left < right:
                    piece = split_reading_by_text_ranges(
                        segment.text[a:b], old_reading, [(left - a, right - a)],
                    )[0]
                    if piece:
                        spans.append((left, right, piece))
        if reading:
            piece = reading
            if first != start or last != end:
                piece = split_reading_by_text_ranges(
                    line.text[start:end], reading, [(first - start, last - start)],
                )[0]
            if piece:
                spans.append((local_first, local_last, piece))
        _set_ruby_spans(segment, sorted(spans))


def _slice_segment(segment, first, last):
    child = deepcopy(segment)
    child.text = segment.text[first:last]
    if first == 0 and last == len(segment.text):
        return child
    spans = []
    for start, end, reading in _ruby_spans(segment):
        a, b = max(first, start), min(last, end)
        if a >= b:
            continue
        if a != start or b != end:
            from lyrics.furigana import split_reading_by_text_ranges

            reading = split_reading_by_text_ranges(
                segment.text[start:end], reading, [(a - start, b - start)],
            )[0]
        if reading:
            spans.append((a - first, b - first, reading))
    _set_ruby_spans(child, spans)
    return child


def _visual_rows(segments):
    row = 0
    for segment in segments:
        segment.visual_line = row
        row += segment.text.count("\n")


def correct_line_text(line, text, change=None):
    """Return corrected chunks, retaining times and readings on unchanged text.

    Insertions at a chunk boundary belong to the preceding chunk. Replacements
    spanning chunks distribute their text across the existing timed owners.
    Only readings whose base text changed are removed.
    """
    old = line.text
    if text == old:
        return deepcopy(line.segments)
    if not text:
        return [LyricSegment(line.start, "")]
    segments = line.segments
    if not old or not segments or "".join(s.text for s in segments) != old:
        return [LyricSegment(line.start, text, needs_timing_review=line.has_karaoke)]
    owners = []
    starts = []
    for index, segment in enumerate(segments):
        starts.append(len(owners))
        owners.extend([index] * len(segment.text))
    new_owners = []
    equal_positions = {}
    opcodes = None
    if change is not None:
        a, b, c, d = change
        if (0 <= a <= b <= len(old) and 0 <= c <= d <= len(text)
                and old[:a] == text[:c] and old[b:] == text[d:]
                and (a or b < len(old) or c or d < len(text))):
            kind = "insert" if a == b else ("delete" if c == d else "replace")
            opcodes = [("equal", 0, a, 0, c), (kind, a, b, c, d),
                       ("equal", b, len(old), d, len(text))]
    if opcodes is None:
        opcodes = SequenceMatcher(None, old, text, autojunk=False).get_opcodes()
    for kind, a, b, c, d in opcodes:
        if kind == "equal":
            new_owners.extend(owners[a:b])
            equal_positions.update((a + offset, c + offset) for offset in range(b - a))
        elif kind == "insert":
            owner = owners[max(0, a - 1)]
            new_owners.extend([owner] * (d - c))
        elif kind == "replace":
            new_owners.extend(owners[a + min(b - a - 1, (position - c) * (b - a) // (d - c))]
                              for position in range(c, d))
    positions_by_owner = {}
    for position, owner in enumerate(new_owners):
        positions_by_owner.setdefault(owner, []).append(position)
    result = []
    for index, segment in enumerate(segments):
        positions = positions_by_owner.get(index, [])
        if not positions:
            continue
        first, last = positions[0], positions[-1] + 1
        child = deepcopy(segment)
        child.text = text[first:last]
        if child.text != segment.text:
            spans = []
            for a, b, reading in _ruby_spans(segment):
                mapped = [equal_positions.get(starts[index] + p) for p in range(a, b)]
                if mapped and None not in mapped and mapped == list(range(mapped[0], mapped[0] + len(mapped))):
                    spans.append((mapped[0] - first, mapped[-1] + 1 - first, reading))
            _set_ruby_spans(child, spans)
            # Punctuation and whitespace corrections keep the same spoken text.
            spoken = lambda value: "".join(char.casefold() for char in value if char.isalnum())
            child.needs_timing_review |= line.has_karaoke and spoken(child.text) != spoken(segment.text)
        result.append(child)
    _visual_rows(result)
    return result


def split_lyric_line(line, position, *, fallback_end=None):
    """Split visible text; interpolate only the chunk crossed by the cursor."""
    if not 0 < position < len(line.text) or not line.text[:position].strip() or not line.text[position:].strip():
        raise ValueError("Place the cursor between two non-empty parts of the lyric.")
    original = line.segments or [LyricSegment(line.start, line.text)]
    if "".join(segment.text for segment in original) != line.text:
        raise ValueError("The karaoke chunks do not match the lyric text. Correct them before splitting.")
    left_segments, right_segments = [], []
    cursor = 0
    split_time = None
    for row, segment in enumerate(original):
        end = cursor + len(segment.text)
        if end <= position:
            left_segments.append(deepcopy(segment))
        elif cursor >= position:
            right_segments.append(deepcopy(segment))
        else:
            cut = position - cursor
            boundary = (segment_end(line, row, fallback_end) if line.segments else
                        (line.end if line.end is not None else fallback_end))
            if boundary is None or boundary <= segment.start:
                boundary = max(segment.start + 1.0, line.start + 1.0)
            split_time = (segment.start if segment.instant else
                          segment.start + (boundary - segment.start) * cut / len(segment.text))
            left = _slice_segment(segment, 0, cut)
            right = _slice_segment(segment, cut, len(segment.text))
            left.end = (segment.end if segment.instant else split_time) if line.has_karaoke else None
            right.start = split_time
            if line.has_karaoke:
                left.needs_timing_review = right.needs_timing_review = True
            left_segments.append(left)
            right_segments.append(right)
        cursor = end
    split_time = right_segments[0].start if split_time is None else split_time
    if split_time < line.start:
        raise ValueError("The second part starts before this lyric. Correct its timing before splitting.")
    left, right = deepcopy(line), deepcopy(line)
    left.text, right.text = line.text[:position], line.text[position:]
    left.reading_groups = [(a, b) for a, b in line.reading_groups if b <= position]
    right.reading_groups = [(a - position, b - position) for a, b in line.reading_groups if a >= position]
    left.segments, right.segments = left_segments, right_segments
    left.end = (left_segments[-1].end if left_segments[-1].end is not None else split_time) if (
        line.has_karaoke or line.end is not None) else None
    right.start = split_time
    left.style_spans = [(a, min(b, position), *styles) for a, b, *styles in line.style_spans if a < position]
    right.style_spans = [(max(a, position) - position, b - position, *styles)
                         for a, b, *styles in line.style_spans if b > position]
    visual_row = line.text[:position].count("\n")
    left.translation_lines = [row for row in line.translation_lines if row < visual_row or (
        row == visual_row and not left.text.endswith("\n"))]
    right.translation_lines = [row - visual_row for row in line.translation_lines if row >= visual_row]
    # LRCX marks translations with a preceding break. Retain that marker when
    # a new timestamp begins inside an existing translation row.
    if 0 in right.translation_lines:
        right.text = "\n" + right.text
        right.segments.insert(0, LyricSegment(right.start, "\n"))
        right.style_spans = [(a + 1, b + 1, *styles) for a, b, *styles in right.style_spans]
        right.reading_groups = [(a + 1, b + 1) for a, b in right.reading_groups]
        right.translation_lines = [row + 1 for row in right.translation_lines]
    _visual_rows(left.segments)
    _visual_rows(right.segments)
    return left, right


def merge_lyric_lines(lines, separator=" "):
    """Combine adjacent lyrics, retaining each original chunk's absolute time."""
    if len(lines) < 2:
        raise ValueError("Select at least two adjacent lyric lines to merge.")
    if any(second.start < first.start for first, second in zip(lines, lines[1:])):
        raise ValueError("The selected lines must be in timestamp order before merging.")
    if separator != "\n" and any(line.translation_lines for line in lines):
        raise ValueError("Choose Newline to keep original and translation rows separate.")
    result = LyricLine(lines[0].start, "", end=lines[-1].end,
                       has_karaoke=any(line.has_karaoke for line in lines) or any(
                           line.start != lines[0].start for line in lines),
                       is_blockquote=lines[0].is_blockquote)
    for index, line in enumerate(lines):
        if index:
            if separator:
                result.segments.append(LyricSegment(line.start, separator))
                result.text += separator
        offset = len(result.text)
        row_offset = result.text.count("\n")
        segments = deepcopy(line.segments) or [LyricSegment(line.start, line.text)]
        if "".join(segment.text for segment in segments) != line.text:
            raise ValueError("The karaoke chunks do not match the lyric text. Correct them before merging.")
        if index + 1 < len(lines) and segments[-1].end is None:
            segments[-1].end = line.end if line.end is not None else lines[index + 1].start
        result.segments.extend(segments)
        result.style_spans.extend((a + offset, b + offset, *styles) for a, b, *styles in line.style_spans)
        result.reading_groups.extend((a + offset, b + offset) for a, b in line.reading_groups)
        result.translation_lines.extend(row + row_offset for row in line.translation_lines)
        result.text += line.text
    _visual_rows(result.segments)
    return result


def set_line_format(line, styles, enabled):
    """Apply/remove whole-line styles while leaving other styles untouched."""
    flags = ("bold", "italic", "strike", "underline")
    changed = {flags.index(style) for style in styles if style in flags}
    spans = []
    for a, b, *current in line.style_spans:
        current = [flag and (enabled or index not in changed) for index, flag in enumerate(current)]
        if any(current):
            spans.append((a, b, *current))
    if enabled and changed and line.text:
        spans.append((0, len(line.text), *(index in changed for index in range(4))))
    line.style_spans = spans
    if "quote" in styles:
        line.is_blockquote = enabled
