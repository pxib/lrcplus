"""Immutable visible-text formatting for cards, independent of lyric timing."""

from dataclasses import dataclass

from PySide6.QtGui import QFont, QTextCharFormat, QTextCursor, QTextFormat
from lyrics.furigana import get_cached_furigana_segments, split_reading_by_text_ranges
from lyrics.line_breaks import reading_source, decode_line_breaks


QUOTE_GROUP = int(QTextFormat.Property.UserProperty) + 1
QUOTE_INDENT = QUOTE_GROUP + 1


@dataclass(frozen=True)
class CardLyrics:
    text: str
    styles: tuple = ()
    quotes: tuple = ()
    translations: tuple = ()
    readings: tuple = ()

    @property
    def has_formatting(self):
        return bool(self.styles or self.quotes or self.translations or self.readings)

    def trimmed(self):
        start = len(self.text) - len(self.text.lstrip())
        end = len(self.text.rstrip())
        def clip(spans):
            return tuple((max(a, start) - start, min(b, end) - start, *rest)
                         for a, b, *rest in spans if max(a, start) < min(b, end))
        return CardLyrics(self.text[start:end], clip(self.styles), clip(self.quotes), clip(self.translations), clip(self.readings))

    def without_formatting(self):
        return CardLyrics(self.text, readings=self.readings)


def _line_readings(line):
    text = str(line.text)
    manual, generated = [], []
    segments = list(getattr(line, "segments", ()) or ())
    if "".join(segment.text for segment in segments) == text:
        offset = 0
        for segment in segments:
            spans = list(getattr(segment, "ruby_spans", ()) or ())
            if not spans and segment.ruby:
                spans = [(0, len(segment.text), segment.ruby)]
            for start, end, reading in spans:
                start, end = max(0, int(start)), min(len(segment.text), int(end))
                if start < end and reading and "\n" not in segment.text[start:end] and "\n" not in str(reading):
                    target = generated if segment.ruby_source == "generated" else manual
                    target.append((offset + start, offset + end, str(reading)))
            offset += len(segment.text)
    # Reuse whole-line cached readings; never run a provider while making a card.
    cached = get_cached_furigana_segments(reading_source(text, line.translation_lines))
    if cached is not None:
        pieces = [(decode_line_breaks(str(token.get("text", "")))[0], token.get("reading")) for token in cached]
        visible = "".join(value for value, _ in pieces)
        offset = 0 if visible == text else len(text) - len(text.lstrip())
        if visible == text[offset:] and any(reading for _, reading in pieces):
            generated = []
            for value, reading in pieces:
                if value and reading and "\n" not in value and "\n" not in str(reading):
                    generated.append((offset, offset + len(value), str(reading)))
                offset += len(value)
    translations = []
    offset = 0
    for row, value in enumerate(text.split("\n")):
        if row in (line.translation_lines or ()):
            translations.append((offset, offset + len(value)))
        offset += len(value) + 1
    manual = sorted(manual)
    readings = []
    for start, end, reading in manual:
        if not readings or start >= readings[-1][1]:
            readings.append((start, end, reading))
    for start, end, reading in generated:
        if any(start < b and end > a for a, b in translations):
            continue
        ranges = [(start, end)]
        for a, b, _ in readings:
            ranges = [(x, y) for left, right in ranges
                      for x, y in ((left, min(right, a)), (max(left, b), right)) if x < y]
        local_ranges = [(a - start, b - start) for a, b in ranges]
        values = split_reading_by_text_ranges(text[start:end], reading, local_ranges)
        readings.extend((a, b, value) for (a, b), value in zip(ranges, values) if value)
        readings.sort()
    return tuple(readings)


def snapshot_card_lyrics(lines, include_readings=False):
    parts, styles, quotes, translations, readings = [], [], [], [], []
    offset = 0
    for line in lines:
        text = str(line.text)
        parts.append(text)
        for start, end, *flags in (getattr(line, "style_spans", ()) or ()):
            start, end = max(0, int(start)), min(len(text), int(end))
            if start < end and len(flags) == 4 and any(flags):
                styles.append((offset + start, offset + end, *map(bool, flags)))
        if getattr(line, "is_blockquote", False) and text:
            quotes.append((offset, offset + len(text)))
        row_offset = offset
        translated_rows = set(getattr(line, "translation_lines", ()) or ())
        for row, value in enumerate(text.split("\n")):
            if row in translated_rows and value:
                translations.append((row_offset, row_offset + len(value)))
            row_offset += len(value) + 1
        if include_readings:
            readings.extend((offset + a, offset + b, value) for a, b, value in _line_readings(line))
        offset += len(text) + 1
    # Only visible readings are copied from segments, never timing or karaoke state.
    return CardLyrics("\n".join(parts), tuple(styles), tuple(quotes), tuple(translations), tuple(readings))


def utf16_offsets(text):
    offsets = [0]
    for char in text:
        offsets.append(offsets[-1] + (2 if ord(char) > 0xffff else 1))
    return offsets


def _selection(document, offsets, start, end):
    start, end = max(0, int(start)), min(len(offsets) - 1, int(end))
    if start >= end:
        return None
    cursor = QTextCursor(document)
    cursor.setPosition(offsets[start])
    cursor.setPosition(offsets[end], QTextCursor.MoveMode.KeepAnchor)
    return cursor


def apply_character_styles(document, source, font):
    offsets = utf16_offsets(source.text)
    for start, end in source.translations:
        cursor = _selection(document, offsets, start, end)
        if cursor is not None:
            small_font = QFont(font)
            small_font.setPixelSize(max(1, round(font.pixelSize() * .75)))
            char_format = QTextCharFormat()
            char_format.setFont(small_font)
            cursor.mergeCharFormat(char_format)
    for start, end, bold, italic, strike, underline in source.styles:
        cursor = _selection(document, offsets, start, end)
        if cursor is None:
            continue
        char_format = QTextCharFormat()
        # Source emphasis adds to the card-wide style rather than disabling it.
        if bold:
            char_format.setFontWeight(QFont.Weight.Bold)
        if italic:
            char_format.setFontItalic(True)
        if strike:
            char_format.setFontStrikeOut(True)
        if underline:
            char_format.setFontUnderline(True)
        cursor.mergeCharFormat(char_format)


def apply_block_quotes(document, source, indent):
    offsets = utf16_offsets(source.text)
    for group, (start, end) in enumerate(source.quotes, 1):
        start, end = max(0, start), min(len(source.text), end)
        if start >= end:
            continue
        block = document.findBlock(offsets[start])
        while block.isValid() and block.position() < offsets[end]:
            cursor = QTextCursor(block)
            block_format = cursor.blockFormat()
            block_format.setLeftMargin(block_format.leftMargin() + indent)
            block_format.setProperty(QUOTE_GROUP, group)
            block_format.setProperty(QUOTE_INDENT, indent)
            cursor.setBlockFormat(block_format)
            block = block.next()


def quote_bar_rects(document):
    from PySide6.QtCore import QRectF
    groups = {}
    block = document.begin()
    while block.isValid():
        block_format = block.blockFormat()
        group = block_format.property(QUOTE_GROUP)
        if group:
            bounds = document.documentLayout().blockBoundingRect(block)
            thickness = max(2, document.defaultFont().pixelSize() / 16)
            x = block_format.leftMargin() - block_format.property(QUOTE_INDENT) + 2
            bar = QRectF(x, bounds.top(), thickness, bounds.height())
            groups[group] = groups[group].united(bar) if group in groups else bar
        block = block.next()
    return tuple(groups.values())
