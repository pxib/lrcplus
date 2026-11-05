"""Continuous underline/strike rules across ruby objects and layout spacing."""

from dataclasses import dataclass

from PySide6.QtCore import QPointF, Qt
from PySide6.QtGui import QColor, QFont, QFontMetricsF, QPen, QTextCharFormat, QTextCursor

from ui.lyrics_card_text import utf16_offsets


@dataclass
class DecorationSpan:
    left: float
    right: float
    y: float
    thickness: float
    stroke: QPen
    band: str
    kind: str


@dataclass
class _Piece:
    left: float | None
    right: float | None
    baseline: float
    font: QFont
    underline: bool
    strike: bool
    stroke: QPen


def _cursor_x(line, position):
    value = line.cursorToX(position)
    return value[0] if isinstance(value, tuple) else value


def _glyphs(document, origin_x=0, origin_y=0):
    document.size()
    block = document.begin()
    while block.isValid():
        fragments = []
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                char_format = fragment.charFormat()
                fragments.append((fragment.position() + fragment.length(), char_format,
                                  char_format.font().resolve(document.defaultFont())))
            iterator += 1
        layout = block.layout()
        offsets = utf16_offsets(block.text())
        index = 0
        for character, start, end in zip(block.text(), offsets, offsets[1:]):
            while index < len(fragments) and block.position() + start >= fragments[index][0]:
                index += 1
            if index == len(fragments):
                break
            line = layout.lineForTextPosition(start)
            if not line.isValid():
                continue
            _, char_format, font = fragments[index]
            x1 = origin_x + layout.position().x() + _cursor_x(line, start)
            x2 = origin_x + layout.position().x() + _cursor_x(line, end)
            baseline = origin_y + layout.position().y() + line.y() + line.ascent()
            yield ((block.position(), line.lineNumber()), character, char_format, font,
                   min(x1, x2), max(x1, x2), baseline)
        block = block.next()


def _piece(glyph, stroke=None):
    _, _, char_format, font, left, right, baseline = glyph
    return _Piece(left, right, baseline, font, font.underline(), font.strikeOut(),
                  char_format.textOutline() if stroke is None else stroke)


def _rows(document):
    rows = {}
    handler = getattr(document, "_card_ruby_handler", None)
    for glyph in _glyphs(document):
        row, character, char_format, font, left, right, baseline = glyph
        if character == "\u200b":
            continue  # Invisible ruby baseline struts must not break a rule.
        base_row = rows.setdefault((row, "base"), [])
        reading_row = rows.setdefault((row, "reading"), []) if handler is not None else None
        if handler is not None and char_format.objectType() == handler.object_type:
            index = int(char_format.property(handler.index_property))
            base, reading, size, ascent = handler.pieces[index]
            stroke = char_format.textOutline()
            base_x = left + (right - left - base.size().width()) / 2
            base_row.extend(_piece(value, stroke) for value in _glyphs(base, base_x, baseline - ascent))
            reading_x = left + (right - left - reading.size().width()) / 2
            _, reading_y = handler.child_positions(index, baseline)
            reading_stroke = stroke if handler.inherit_stroke else QPen(Qt.PenStyle.NoPen)
            reading_row.extend(_piece(value, reading_stroke) for value in _glyphs(reading, reading_x, reading_y))
        else:
            base_row.append(_piece(glyph))
            if reading_row is not None:
                # A styled kana/space without a reading can bridge two ruby
                # rules; an unstyled character explicitly ends the run.
                reading_row.append(_Piece(None, None, baseline, font, font.underline(), font.strikeOut(), QPen()))
    return rows


def decoration_spans(document):
    key = (document.revision(), document.textWidth())
    cached = getattr(document, "_card_decoration_spans", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    spans = []
    metrics_cache = {}
    for (_, band), pieces in _rows(document).items():
        for kind in ("underline", "strike"):
            previous = None
            for piece in pieces:
                if not getattr(piece, kind):
                    previous = None
                    continue
                if piece.left is None or piece.right - piece.left <= .01:
                    continue
                metric_font = QFont(piece.font)
                metric_font.setBold(False)
                metric_font.setItalic(False)
                font_key = metric_font.key()
                if font_key not in metrics_cache:
                    metrics_cache[font_key] = QFontMetricsF(metric_font)
                metrics = metrics_cache[font_key]
                y = piece.baseline + (metrics.underlinePos() if kind == "underline" else -metrics.strikeOutPos())
                thickness = max(1, metrics.lineWidth())
                if (previous is not None and abs(previous.y - y) < .5
                        and abs(previous.thickness - thickness) < .5 and previous.stroke == piece.stroke):
                    previous.left = min(previous.left, piece.left)
                    previous.right = max(previous.right, piece.right)
                else:
                    previous = DecorationSpan(piece.left, piece.right, y, thickness, QPen(piece.stroke), band, kind)
                    spans.append(previous)
    result = tuple(spans)
    document._card_decoration_spans = (key, result)
    return result


def undecorated_document(document):
    """Keep semantic styles on the source, suppress native per-token rules."""
    key = (document.revision(), document.textWidth())
    cached = getattr(document, "_card_undecorated", None)
    if cached is not None and cached[0] == key:
        return cached[1]
    decorated = document.defaultFont().underline() or document.defaultFont().strikeOut()
    block = document.begin()
    while block.isValid() and not decorated:
        iterator = block.begin()
        while not iterator.atEnd():
            fragment = iterator.fragment()
            if fragment.isValid():
                font = fragment.charFormat().font().resolve(document.defaultFont())
                if font.underline() or font.strikeOut():
                    decorated = True
                    break
            iterator += 1
        block = block.next()
    if not decorated:
        return document
    clone = document.clone(document)
    font = QFont(clone.defaultFont())
    font.setUnderline(False)
    font.setStrikeOut(False)
    clone.setDefaultFont(font)
    cursor = QTextCursor(clone)
    cursor.select(QTextCursor.SelectionType.Document)
    char_format = QTextCharFormat()
    char_format.setFontUnderline(False)
    char_format.setFontStrikeOut(False)
    cursor.mergeCharFormat(char_format)
    handler = getattr(document, "_card_ruby_handler", None)
    if handler is not None:
        handler.attach(clone)
    document._card_undecorated = (key, clone)
    return clone


def paint_decorations(painter, document, color, band=None):
    painter.save()
    for span in decoration_spans(document):
        if band is not None and span.band != band:
            continue
        if span.stroke.style() != Qt.PenStyle.NoPen:
            painter.setPen(QPen(span.stroke.color(), span.thickness + span.stroke.widthF(),
                                Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
            painter.drawLine(QPointF(span.left, span.y), QPointF(span.right, span.y))
        handler = getattr(document, "_card_ruby_handler", None)
        fill = QColor(handler.ruby_color) if span.band == "reading" and handler is not None and handler.ruby_color else color
        painter.setPen(QPen(fill, span.thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
        painter.drawLine(QPointF(span.left, span.y), QPointF(span.right, span.y))
    painter.restore()
