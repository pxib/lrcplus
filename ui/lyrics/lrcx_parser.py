import re

from lyrics.models import LyricLine, LyricSegment
from lyrics.parser import GENERATION_MODE_RE, normalize_generation_mode


LINE_TIMESTAMP_RE = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
SEGMENT_TIMESTAMP_RE = re.compile(r"<(\d+):(\d+(?:\.\d+)?)>")
INSTANT_TAG = "<instant>"
RUBY_TOKEN_RE = re.compile(r"\{([^|{}]+)\|([^{}]+)\}")
METADATA_RE = re.compile(r"^\[[A-Za-z][A-Za-z0-9_]*:.*\]$")
OFFSET_RE = re.compile(r"^\[offset:([+-]?\d+(?:\.\d+)?)\]$", re.IGNORECASE)


def get_lrcx_generation_mode(path):
    """Return the document-level generated-reading mode stored as [g:mode]."""
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for raw_line in file:
                match = GENERATION_MODE_RE.match(raw_line.strip())
                if match:
                    return normalize_generation_mode(match.group(1))
    except OSError:
        pass
    return None


def get_lrcx_offset(path):
    """Return the LRCX playback offset in milliseconds."""
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for raw_line in file:
                match = OFFSET_RE.match(raw_line.strip())
                if match:
                    return float(match.group(1))
    except OSError:
        pass
    return 0.0


def _timestamp(match):
    return int(match.group(1)) * 60 + float(match.group(2))


def _parse_content(content, start_time, split_newlines=False):
    """Parse one timed LRCX chunk, including structural newline markers."""
    # LRCX uses the literal ``{\\n}`` token for an in-line visual newline.
    # Convert it before ruby parsing, then split it back into structural
    # segments so karaoke timestamps cannot accidentally become attached to
    # the newline itself.
    content = str(content or "").replace(r"{\n}", "\n")

    text_parts = []
    ruby_spans = []
    position = 0
    offset = 0

    for match in RUBY_TOKEN_RE.finditer(content):
        literal = content[position:match.start()]
        if literal:
            text_parts.append(literal)
            offset += len(literal)
        base = match.group(1)
        reading = match.group(2)
        text_parts.append(base)
        ruby_spans.append((offset, offset + len(base), reading))
        offset += len(base)
        position = match.end()

    tail = content[position:]
    if tail:
        text_parts.append(tail)

    text = ''.join(text_parts)
    if not text:
        return []

    # Plain LRCX keeps a visual newline inside the single lyric segment.
    # Explicit karaoke needs structural newline segments so each following
    # timed chunk can receive its own visual-line index.
    if not split_newlines or "\n" not in text:
        seg = LyricSegment(start=start_time, text=text)
        if ruby_spans:
            seg.ruby_spans = list(ruby_spans)
            if len(ruby_spans) == 1 and ruby_spans[0][:2] == (0, len(text)):
                seg.ruby = ruby_spans[0][2]
                seg.ruby_source = 'manual'
        seg.visual_line = 0
        return [seg]

    result = []
    visual_line = 0
    cursor = 0
    for part in text.splitlines(keepends=True):
        is_newline = part == "\n"
        if is_newline:
            seg = LyricSegment(start=start_time, text="\n")
            seg.visual_line = visual_line
            result.append(seg)
            visual_line += 1
            cursor += 1
            continue

        visible = part[:-1] if part.endswith("\n") else part
        if visible:
            seg = LyricSegment(start=start_time, text=visible)
            local_spans = []
            for a, b, reading in ruby_spans:
                overlap_a = max(a, cursor)
                overlap_b = min(b, cursor + len(visible))
                if overlap_a < overlap_b:
                    local_spans.append((overlap_a - cursor, overlap_b - cursor, reading))
            if local_spans:
                seg.ruby_spans = local_spans
                if len(local_spans) == 1 and local_spans[0][:2] == (0, len(visible)):
                    seg.ruby = local_spans[0][2]
                    seg.ruby_source = 'manual'
            seg.visual_line = visual_line
            result.append(seg)

        cursor += len(visible)
        if part.endswith("\n"):
            seg = LyricSegment(start=start_time, text="\n")
            seg.visual_line = visual_line
            result.append(seg)
            visual_line += 1
            cursor += 1

    return result


def load_lrcx(path):
    """Load LRCX v1.

    Syntax:
        [mm:ss.xxx]plain text{base|ruby}more text
        [mm:ss.xxx]first chunk<mm:ss.xxx>next chunk

    The line timestamp is the implicit timestamp for the first chunk. Ruby
    annotations can appear anywhere inside a chunk and multiple annotations
    may be adjacent without corrupting the surrounding lyric text.
    """
    lines = []

    with open(path, "r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.rstrip("\r\n")
            if not line or METADATA_RE.match(line):
                continue

            line_matches = list(LINE_TIMESTAMP_RE.finditer(line))
            if not line_matches:
                continue

            line_start = _timestamp(line_matches[0])
            content = line[line_matches[-1].end():]
            segment_matches = list(SEGMENT_TIMESTAMP_RE.finditer(content))
            # Karaoke is explicit in LRCX: segment timestamps or instant tags.
            has_karaoke = bool(segment_matches) or INSTANT_TAG in content
            segments = []

            if not segment_matches:
                instant = content.startswith(INSTANT_TAG)
                if instant:
                    content = content[len(INSTANT_TAG):]
                parsed = _parse_content(content, line_start, split_newlines=has_karaoke)
                for segment in parsed:
                    segment.instant = instant
                segments.extend(parsed)
            else:
                # Leading content inherits the line timestamp.
                leading = content[:segment_matches[0].start()]
                instant = leading.startswith(INSTANT_TAG)
                if instant:
                    leading = leading[len(INSTANT_TAG):]
                parsed = _parse_content(leading, line_start, split_newlines=has_karaoke)
                for segment in parsed:
                    segment.instant = instant
                segments.extend(parsed)

                for index, match in enumerate(segment_matches):
                    text_start = match.end()
                    text_end = (
                        segment_matches[index + 1].start()
                        if index + 1 < len(segment_matches)
                        else len(content)
                    )
                    chunk = content[text_start:text_end]
                    timestamp = _timestamp(match)

                    # An empty timestamp between two chunks is an explicit end
                    # boundary for the preceding chunk, allowing a real pause:
                    #   word<00:01.000><00:02.000>next
                    if not chunk and segments:
                        segments[-1].end = timestamp
                        continue

                    instant = False
                    if chunk.startswith(INSTANT_TAG):
                        instant = True
                        chunk = chunk[len(INSTANT_TAG):]

                    parsed = _parse_content(chunk, timestamp, split_newlines=has_karaoke)
                    for segment in parsed:
                        segment.instant = instant
                    segments.extend(parsed)

            # Assign visual-line indices after all chunks for this lyric
            # have been parsed. Timestamped chunks are parsed independently,
            # so the index must carry across chunk boundaries.
            visual_line = 0
            for segment in segments:
                segment.visual_line = visual_line
                if segment.text == "\n":
                    visual_line += 1

            text = "".join(segment.text for segment in segments)

            # A timestamp with no following lyric text is a terminal karaoke
            # boundary, not an empty segment. Preserve it as the line ending.
            explicit_end = None
            if segments and getattr(segments[-1], "end", None) is not None:
                explicit_end = segments[-1].end
            elif segment_matches:
                last_match = segment_matches[-1]
                trailing = content[last_match.end():]
                if not trailing:
                    explicit_end = _timestamp(last_match)
                    if segments:
                        segments[-1].end = explicit_end

            lines.append(
                LyricLine(
                    start=line_start,
                    text=text,
                    segments=segments,
                    end=explicit_end,
                    has_karaoke=has_karaoke,
                )
            )

    # Keep document/source order intact. A lyric editor may intentionally have
    # unfinished or newly marked timestamps that are out of chronological order.
    # Sorting here changes the user's line priority.
    #
    # Natural karaoke ends still need chronological timing, so compute that
    # relationship through a separate sorted index without moving `lines`.
    timeline = sorted(
        enumerate(lines),
        key=lambda item: (item[1].start, item[0]),
    )
    for timeline_index, (original_index, lyric) in enumerate(timeline):
        if lyric.end is None:
            lyric.end = (
                timeline[timeline_index + 1][1].start
                if timeline_index + 1 < len(timeline)
                else None
            )

    return lines
