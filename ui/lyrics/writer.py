import re

from pathlib import Path

from lyrics.loader import load_lyrics
from lyrics.furigana import build_furigana_segments
from lyrics.models import LyricSegment


def format_timestamp(seconds: float) -> str:
    seconds = max(0.0, float(seconds))
    minutes = int(seconds // 60)
    remainder = seconds - minutes * 60
    return f"{minutes:02d}:{remainder:06.3f}"


def _escape_lrc_text(text: str) -> str:
    return str(text).replace("\n", " ")


def _manual_segments(line, manual_overrides):
    """Return display-token segments with only explicit manual readings changed.

    Manual overrides are stored by the FuriganaWidget as:
        {full_lyric_text: {token_index: reading}}

    The writer therefore rebuilds the same display tokens, applies only those
    explicit indexes, and leaves every other token's reading untouched.
    """
    overrides = (
        manual_overrides.get(line.text, {})
        if manual_overrides
        else {}
    )

    # Karaoke timing is authoritative. Never retokenize an explicitly timed
    # line just because the Furigana widget has manual overrides: rebuilding
    # from linguistic tokens destroys the karaoke boundaries before the LRCX
    # serializer ever sees them.
    if getattr(line, "has_karaoke", False):
        return (
            list(line.segments)
            if line.segments
            else [LyricSegment(line.start, line.text)]
        )

    # Plain .lrc lines have no segments. They still need one literal
    # segment so every lyric survives LRC -> LRCX conversion.
    if not overrides:
        return (
            list(line.segments)
            if line.segments
            else [LyricSegment(line.start, line.text)]
        )

    try:
        tokens = build_furigana_segments(line.text)
    except Exception:
        # If tokenization fails, do not destroy existing LRCX data.
        return list(line.segments) or [
            LyricSegment(line.start, line.text)
        ]

    # Map existing LRCX segments onto character positions so manual edits can
    # coexist with already-timed/ruby-tagged LRCX content.
    existing = list(line.segments)
    existing_ranges = []
    offset = 0

    for segment in existing:
        start = offset
        end = start + len(segment.text)
        existing_ranges.append((start, end, segment))
        offset = end

    result = []
    offset = 0

    for index, token in enumerate(tokens):
        text = token.get("text", "")
        token_start = offset
        token_end = token_start + len(text)
        offset = token_end

        source = None
        for start, end, segment in existing_ranges:
            if start <= token_start < end or (
                token_start == start and token_end == end
            ):
                source = segment
                break

        start_time = source.start if source is not None else line.start

        # Preserve existing explicit ruby by default.
        ruby = source.ruby if (
            source is not None
            and source.text == text
        ) else None

        # Only an explicitly edited token gets replaced/appended.
        if index in overrides:
            value = overrides[index]
            ruby = (
                value.strip()
                if isinstance(value, str) and value.strip()
                else None
            )

        result.append(
            LyricSegment(
                start=start_time,
                text=text,
                ruby=ruby,
            )
        )

    return result or [LyricSegment(line.start, line.text)]


def _generation_mode_tag(generation_mode):
    """Serialize built-in or Plugin API v2 reading provider modes."""
    mode = str(generation_mode or "").strip().lower()
    aliases = {"jyut": "jyutping"}
    mode = aliases.get(mode, mode)
    if re.fullmatch(r"[a-z0-9][a-z0-9_.-]*", mode):
        return f"[g:{mode}]"
    return None


def serialize_lrc(lines, generation_mode=None) -> str:
    output = []
    tag = _generation_mode_tag(generation_mode)
    if tag:
        output.append(tag)

    for line in lines:
        output.append(
            f"[{format_timestamp(line.start)}]"
            f"{_escape_lrc_text(line.text)}"
        )

    return "\n".join(output) + ("\n" if output else "")


def serialize_lrcx(lines, manual_overrides=None, offset_ms=0.0, generation_mode=None) -> str:
    output = ["[format:LRCX]", "[version:1]"]
    tag = _generation_mode_tag(generation_mode)
    if tag:
        output.append(tag)
    if abs(float(offset_ms)) > 0.0001:
        output.append(f"[offset:{float(offset_ms):.0f}]")

    for line in lines:
        prefix = f"[{format_timestamp(line.start)}]"
        segments = _manual_segments(
            line,
            manual_overrides,
        )

        parts = []
        active_timestamp = line.start

        # Karaoke is an explicit document property. In particular, automatic
        # separation may create several chunks that initially share the same
        # timestamp; those boundaries are still real and must survive a save.
        preserve_karaoke_boundaries = bool(
            getattr(line, "has_karaoke", False)
        )

        for segment_index, segment in enumerate(segments):
            text = segment.text

            # Empty chunks are editor garbage, not lyric content. Preserve real
            # spaces (ASCII or full-width) by checking exact emptiness only.
            if text == "":
                continue

            # Inline ruby spans are independent from karaoke timing. Serialize
            # them inside this chunk without creating another timestamp boundary.
            ruby_spans = list(getattr(segment, "ruby_spans", []) or [])
            if ruby_spans:
                rendered = []
                cursor = 0
                for span_start, span_end, reading in sorted(ruby_spans):
                    span_start = max(cursor, int(span_start))
                    span_end = min(len(text), int(span_end))
                    if span_start >= span_end:
                        continue
                    rendered.append(text[cursor:span_start])
                    rendered.append(f"{{{text[span_start:span_end]}|{reading}}}")
                    cursor = span_end
                rendered.append(text[cursor:])
                text = "".join(rendered)
            elif (
                segment.ruby
                and getattr(segment, "ruby_source", None) != "generated"
            ):
                text = f"{{{text}|{segment.ruby}}}"

            # Structural visual newlines are represented by the literal
            # LRCX marker {\n}; they are not timestamp boundaries.
            if text == "\n":
                parts.append(r"{\n}")
                continue
            text = text.replace("\n", r"{\n}")

            # Ruby is serialized inside the existing karaoke chunk, so it
            # never creates or changes karaoke boundaries.
            if segment.start != active_timestamp:
                parts.append(f"<{format_timestamp(segment.start)}>")
                active_timestamp = segment.start

            if getattr(segment, "instant", False):
                parts.append("<instant>")
            parts.append(text)

            # End timestamps are karaoke structure only. Reading generation
            # can calculate internal ends, but those must never be serialized
            # into ordinary LRCX text.
            if preserve_karaoke_boundaries:
                end_time = getattr(segment, "end", None)
                next_segment = (
                    segments[segment_index + 1]
                    if segment_index + 1 < len(segments)
                    else None
                )
                next_start = next_segment.start if next_segment is not None else None
                if end_time is not None:
                    end_time = float(end_time)
                    # Never serialize an end boundary that goes backwards from
                    # this chunk's start. Such a value is stale editor state,
                    # not a valid LRCX boundary, and would appear as a phantom
                    # timestamp inside the lyric text.
                    if end_time < float(segment.start):
                        end_time = None
                    if end_time is not None:
                        if next_start is None:
                            if end_time != float(active_timestamp):
                                parts.append(f"<{format_timestamp(end_time)}>")
                                active_timestamp = end_time
                        elif end_time != float(next_start):
                            parts.append(f"<{format_timestamp(end_time)}>")
                            active_timestamp = end_time

        if (
            preserve_karaoke_boundaries
            and getattr(line, "end", None) is not None
            and (not segments or getattr(segments[-1], "end", None) != line.end)
        ):
            parts.append(f"<{format_timestamp(float(line.end))}>")

        serialized_line = prefix + "".join(parts)
        output.append(serialized_line)

    return "\n".join(output) + ("\n" if output else "")


def save_lyrics(path, lines, manual_overrides=None, generation_mode=None) -> None:
    path = Path(path)
    suffix = path.suffix.lower()

    if suffix == ".lrc":
        contents = serialize_lrc(lines, generation_mode=generation_mode)
    elif suffix == ".lrcx":
        contents = serialize_lrcx(
            lines,
            manual_overrides=manual_overrides,
            generation_mode=generation_mode,
        )
    else:
        raise ValueError(
            f"Unsupported lyrics format: {path.suffix}. "
            "Use .lrc or .lrcx."
        )

    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(contents, encoding="utf-8")


def convert_lyrics(
    source_path,
    destination_path,
    manual_overrides=None,
) -> None:
    lines = load_lyrics(source_path)

    save_lyrics(
        destination_path,
        lines,
        manual_overrides=manual_overrides,
    )
