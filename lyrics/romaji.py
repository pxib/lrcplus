"""Contextual All Romaji display, independent of lyric text and timing."""

import re

from lyrics.furigana import is_japanese_candidate, kana_to_romaji_parts, split_reading_by_text_ranges
from lyrics.japanese_numbers import number_spans


def romanized_readings(segments):
    """Convert ruby pieces together only when they share one source reading."""
    result = ["" for _ in segments]
    owners, parts = [], []
    previous_group = None

    def flush():
        for owner, text in zip(owners, kana_to_romaji_parts(parts)):
            result[owner] = text
        owners.clear()
        parts.clear()

    for index, segment in enumerate(segments):
        reading = str(segment.get("reading") or "")
        group = segment.get("_reading_group", ("segment", index))
        if not reading or segment.get("_line_break"):
            flush()
            previous_group = None
            continue
        if group != previous_group:
            flush()
        owners.append(index)
        parts.append(reading)
        previous_group = group
    flush()
    return result


def romaji_source(segment):
    text = str(segment.get("text", ""))
    reading = str(segment.get("reading") or "")
    manual = segment.get("ruby_source") == "manual" or segment.get("reading_source") == "manual"
    if reading and (any("\u3400" <= char <= "\u9fff" or char == "々" for char in text)
                    or (manual and any(char.isdecimal() for char in text))):
        return reading
    if segment.get("_romaji_particle", False):
        return {"は": "わ", "へ": "え", "を": "お"}.get(text, text)
    return text


def _number_sources(segments, sources):
    """Map whole-number readings back to their original timed source chunks."""
    originals = ["\n" if segment.get("_line_break") else str(segment.get("text", "")) for segment in segments]
    text = "".join(originals)
    offsets = []
    position = 0
    for original in originals:
        offsets.append(position)
        position += len(original)
    starts = [bool(segment.get("_romaji_word_start", False)) for segment in segments]
    edits = [[] for _ in segments]
    for start, end, reading in number_spans(text):
        owners = [i for i, original in enumerate(originals) if offsets[i] < end and start < offsets[i] + len(original)]
        if any(segments[i].get("reading") and (
                segments[i].get("ruby_source") == "manual" or segments[i].get("reading_source") == "manual") for i in owners):
            continue
        for i in owners:
            if start < offsets[i] < end:
                starts[i] = False
            if reading is None:
                continue
            a, b = max(start, offsets[i]), min(end, offsets[i] + len(originals[i]))
            piece = split_reading_by_text_ranges(text[start:end], reading, [(a - start, b - start)])[0] or ""
            edits[i].append((a - offsets[i], b - offsets[i], piece))
    result = list(sources)
    for i, replacements in enumerate(edits):
        if not replacements:
            continue
        pieces = []
        cursor = 0

        def unchanged(a, b):
            if sources[i] == originals[i]:
                return originals[i][a:b]
            return split_reading_by_text_ranges(originals[i], sources[i], [(a, b)])[0] or ""

        for a, b, replacement in replacements:
            pieces.extend((unchanged(cursor, a), replacement))
            cursor = b
        pieces.append(unchanged(cursor, len(originals[i])))
        result[i] = "".join(pieces)
    return result, starts


def _authored_pause_offsets(sources):
    """Find Japanese phrase gaps before adding grammatical word spaces."""
    text = "".join(sources)
    pauses = set()
    punctuation = ",.?!:;、。！？：；…"
    for match in re.finditer(r"[ \u3000]+", text):
        if match.start() == 0 or match.end() == len(text):
            continue
        left, right = text[match.start() - 1], text[match.end()]
        # Existing punctuation supplies its own pause. Ignore padding inside
        # delimiters, line-edge indentation and ordinary Latin word spaces.
        if (left.isspace() or right.isspace()
                or left in punctuation + '「『“(（[［{｛"＂'
                or right in punctuation + '」』”)）]］}｝"＂'):
            continue
        if any(is_japanese_candidate(char) or char == "々" or "\uff66" <= char <= "\uff9f" for char in (left, right)):
            pauses.add(match.start())
    return pauses


def all_romaji_texts(segments):
    """Return one display string per segment, retaining karaoke ownership.

    Add grammatical spaces before conversion so kana context crosses timing
    boundaries but cannot accidentally cross a word boundary. Delimiters retain
    their opening/closing role until the final punctuation-spacing pass.
    """
    raw_sources = ["\n" if segment.get("_line_break") else romaji_source(segment) for segment in segments]
    for index, segment in enumerate(segments[:-1]):
        # Prefer the requested nani reading in generated 何も. A dictionary
        # may choose nan; explicit readings remain authoritative.
        if (segment.get("text") == "何"
                and raw_sources[index] in {"なん", "ナン"}
                and segment.get("ruby_source") != "manual"
                and segment.get("reading_source") != "manual"
                and str(segments[index + 1].get("text", "")).startswith("も")):
            raw_sources[index] = "なに"
    raw_sources, word_starts = _number_sources(segments, raw_sources)
    pauses = _authored_pause_offsets(raw_sources)
    sources = []
    previous = ""
    offset = 0
    for segment, raw_source, word_start in zip(segments, raw_sources, word_starts):
        # The comma belongs to the original whitespace's timed chunk. Keep
        # this separate from the word spaces inserted below.
        source = "".join(
            "," if offset + index in pauses else char
            for index, char in enumerate(raw_source)
        )
        offset += len(raw_source)
        if not segment.get("_line_break"):
            if (word_start and previous
                    and not previous[-1].isspace()
                    and not previous.endswith(("っ", "ッ", "ｯ"))
                    and not source.startswith(tuple("ゃゅょぁぃぅぇぉャュョァィゥェォーｬｭｮｧｨｩｪｫｰﾞﾟ\u3099\u309a"))):
                source = " " + source
        sources.append(source)
        if source:
            previous = source

    converted = kana_to_romaji_parts(sources)
    output = ["" for _ in segments]
    replacements = {"、": ",", "。": ".", "〜": "~"}
    openers = {"「": '"', "『": '"', "“": '"', "(": "(", "[": "[", "{": "{"}
    closers = {"」": '"', "』": '"', "”": '"', ")": ")", "]": "]", "}": "}"}
    previous = ""
    after_open = False
    pending_space = False
    ascii_quote_open = False
    converted_text = "".join(converted)
    position = 0
    for index, text in enumerate(converted):
        for char in text:
            following = converted_text[position + 1:position + 2]
            position += 1
            if char == "\n":
                output[index] += char
                previous = ""
                after_open = pending_space = ascii_quote_open = False
                continue
            if char.isspace():
                pending_space = True
                continue
            char = replacements.get(char, char)
            opening = char in openers
            closing = char in closers
            if char == '"':
                opening, closing = not ascii_quote_open, ascii_quote_open
                ascii_quote_open = not ascii_quote_open
            if opening:
                if previous and not after_open:
                    output[index] += " "
                char = openers.get(char, char)
            elif closing:
                char = closers.get(char, char)
            elif char not in ",.?!:;" and pending_space and previous and not after_open:
                output[index] += " "
            output[index] += char
            numeric_separator = char in ",.:" and previous.isdecimal() and following.isdecimal()
            previous = char
            after_open = opening
            pending_space = closing or (char in ",.?!:;" and not numeric_separator)
    return output
