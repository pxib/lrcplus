"""Vertical lyric columns with Unicode orientation and OpenType alternates."""

from bisect import bisect_right
from dataclasses import dataclass
import unicodedata

from PySide6.QtCore import QPointF, QRectF, QSizeF, Qt, QTextBoundaryFinder
from PySide6.QtGui import QColor, QFont, QPainterPath, QPen, QTextLayout, QTransform
from PySide6.QtWidgets import QApplication

from core.fonts import library_font
from ui.lyrics_card_text import CardLyrics, utf16_offsets
from ui.lyrics_card_vertical_data import VERTICAL_ORIENTATION


_STARTS = tuple(a for a, _, _ in VERTICAL_ORIENTATION)
_NO_START = frozenset("、。，．！？：；)]｝）〕］〉》」』】〙〗〟’”ぁぃぅぇぉっゃゅょゎァィゥェォッャュョヮー々")
_NO_END = frozenset("([｛（〔［〈《「『【〘〖‘“")


def orientation(text):
    if any(unicodedata.category(char) == "Me" for char in text):
        return "U"
    code = ord(text[0])
    index = bisect_right(_STARTS, code) - 1
    if index >= 0 and code <= VERTICAL_ORIENTATION[index][1]:
        return VERTICAL_ORIENTATION[index][2]
    return "R"


def graphemes(text):
    """Qt boundaries use UTF-16; card formatting spans use Python offsets."""
    offsets = utf16_offsets(text)
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    start = 0
    while (boundary := finder.toNextBoundary()) != -1:
        end = bisect_right(offsets, boundary) - 1
        yield start, end, text[start:end]
        start = end


@dataclass
class VerticalGlyph:
    text: str
    path: QPainterPath
    advance: float
    pixels: int
    sideways: bool
    start: int
    end: int
    underline: bool = False
    strike: bool = False
    role: str = "base"


@dataclass
class _Token:
    text: str
    glyphs: list
    advance: float
    quoted: bool = False


class _NoFit(Exception):
    pass


class _Shaper:
    def __init__(self, family):
        self.family = family
        self.cache = {}
        self.references = {}

    def font(self, pixels, bold, italic, vertical=False):
        font = QFont(self.family) if self.family else library_font(QApplication.font())
        font.setPixelSize(pixels)
        font.setBold(bold)
        font.setItalic(italic)
        if vertical:
            font.setFeature(QFont.Tag.fromString("vert"), 1)
            font.setFeature(QFont.Tag.fromString("vrt2"), 1)
        return font

    def raw_path(self, text, font):
        layout = QTextLayout(text, font)
        layout.beginLayout()
        line = layout.createLine()
        line.setLineWidth(1_000_000)
        layout.endLayout()
        path = QPainterPath()
        path.setFillRule(Qt.FillRule.WindingFill)
        indexes = []
        for run in layout.glyphRuns():
            raw = run.rawFont()
            for glyph, position in zip(run.glyphIndexes(), run.positions()):
                indexes.append(glyph)
                path.addPath(raw.pathForGlyph(glyph).translated(position))
        return path, line.naturalTextWidth(), tuple(indexes)

    def shape(self, text, pixels, bold, italic, value):
        key = (text, pixels, bold, italic, value)
        if key in self.cache:
            path, advance, sideways = self.cache[key]
            return QPainterPath(path), advance, sideways
        vertical = value in ("Tu", "Tr")
        path, advance, indexes = self.raw_path(text, self.font(pixels, bold, italic, vertical))
        sideways = value == "R"
        if value == "Tr":
            _, _, horizontal_indexes = self.raw_path(text, self.font(pixels, bold, italic))
            sideways = indexes == horizontal_indexes
        if sideways:
            transform = QTransform()
            transform.rotate(90)
            path = transform.map(path)
            bounds = path.boundingRect()
            advance = max(advance, bounds.height())
            path.translate((pixels - bounds.width()) / 2 - bounds.left(),
                           (advance - bounds.height()) / 2 - bounds.top())
        else:
            reference_key = (pixels, bold, italic)
            if reference_key not in self.references:
                reference, _, _ = self.raw_path("漢", self.font(pixels, bold, italic))
                self.references[reference_key] = reference.boundingRect().center()
            center = self.references[reference_key]
            path.translate(pixels / 2 - center.x(), pixels / 2 - center.y())
            advance = pixels
        self.cache[key] = (path, advance, sideways)
        return QPainterPath(path), advance, sideways


def _flags(source, style, start, end):
    flags = [style.bold, style.italic, style.strikeout, style.underline]
    for a, b, *values in source.styles:
        if start < b and end > a:
            flags = [current or extra for current, extra in zip(flags, values)]
    translated = any(start < b and end > a for a, b in source.translations)
    return tuple(flags), translated


def _units(text, offset, source, style, pixels, shaper, reading=False):
    clusters = list(graphemes(text))
    index = 0
    while index < len(clusters):
        a, b, value = clusters[index]
        flags, translated = _flags(source, style, offset + a, offset + b)
        kind = orientation(value)
        # Shape Latin words as sideways runs, preserving kerning and combining marks.
        if kind == "R" and not value.isspace():
            while index + 1 < len(clusters):
                _, next_end, next_value = clusters[index + 1]
                next_flags = _flags(source, style, offset + b, offset + next_end)
                if orientation(next_value) != "R" or next_value.isspace() or next_flags != (flags, translated):
                    break
                value += next_value
                b = next_end
                index += 1
        size = max(8, round(pixels * (.75 if translated and not reading else 1)))
        path, advance, sideways = shaper.shape(value, size, flags[0], flags[1], kind)
        yield VerticalGlyph(value, path, max(1, advance), size, sideways, offset + a, offset + b,
                            flags[3], flags[2], "ruby" if reading else "base")
        index += 1


def _token(units, reading, source, style, pixels, shaper, start, end, height):
    glyphs = []
    base_height = sum(g.advance for g in units)
    ruby = []
    if reading:
        size = max(8, round(pixels * style.furigana_scale / 100))
        # Reading text inherits emphasis from its associated base range.
        flags, _ = _flags(source, style, start, end)
        ruby_source = CardLyrics(reading, styles=((0, len(reading), *flags),))
        ruby = list(_units(reading, 0, ruby_source, style, size, shaper, reading=True))
    ruby_height = sum(g.advance for g in ruby)
    advance = max(base_height, ruby_height)
    if advance > height:
        raise _NoFit()
    y = (advance - base_height) / 2
    for glyph in units:
        glyphs.append((glyph, QPointF(0, y)))
        y += glyph.advance
    y = (advance - ruby_height) / 2
    gap = max(4, pixels * .12) + style.ruby_gap
    ruby_x = -(ruby[0].pixels + gap) if ruby and style.ruby_position == "left" else pixels + gap
    for glyph in ruby:
        glyphs.append((glyph, QPointF(ruby_x, y)))
        y += glyph.advance
    quoted = any(start < b and end > a for a, b in source.quotes)
    return _Token("".join(g.text for g in units), glyphs, advance, quoted)


def _tokens(source, style, pixels, shaper, height):
    readings = {a: (b, value) for a, b, value in source.readings} if style.include_furigana else {}
    clusters = list(graphemes(source.text))
    starts = {a for a, _, _ in clusters}
    position = 0
    while position < len(source.text):
        if source.text[position] in "\r\n":
            yield None
            position += 2 if source.text.startswith("\r\n", position) else 1
            continue
        end, reading = readings.get(position, (position, ""))
        if (reading and position < end <= len(source.text)
                and (end in starts or end == len(source.text))
                and not any(char in source.text[position:end] for char in "\r\n")):
            units = list(_units(source.text[position:end], position, source, style, pixels, shaper))
            yield _token(units, reading, source, style, pixels, shaper, position, end, height)
            position = end
            continue
        # Stop at the next authored reading or explicit column break.
        end = next((a for a, _, _ in clusters if a > position and (a in readings or source.text[a] in "\r\n")), len(source.text))
        for unit in _units(source.text[position:end], position, source, style, pixels, shaper):
            if unit.advance > height:
                # Very long sideways words may wrap at grapheme boundaries.
                for a, b, value in graphemes(unit.text):
                    part = list(_units(value, unit.start + a, source, style, pixels, shaper))
                    yield _token(part, "", source, style, pixels, shaper, unit.start + a, unit.start + b, height)
            else:
                yield _token([unit], "", source, style, pixels, shaper, unit.start, unit.end, height)
        position = end


class VerticalLyrics:
    def __init__(self, source, style, pixels, width, height, shaper):
        self.source = source
        self.font = shaper.font(pixels, style.bold, style.italic)
        self.glyphs = []
        self.path = QPainterPath()
        self.path.setFillRule(Qt.FillRule.WindingFill)
        self.base_path, self.ruby_path = QPainterPath(), QPainterPath()
        base_decorations, ruby_decorations = QPainterPath(), QPainterPath()
        for path in (self.base_path, self.ruby_path, base_decorations, ruby_decorations):
            path.setFillRule(Qt.FillRule.WindingFill)
        ruby_lane = (max(8, round(pixels * style.furigana_scale / 100)) + max(4, pixels * .12)
                     + style.ruby_gap) if style.include_furigana and source.readings else 0
        base_offset = ruby_lane if style.ruby_position == "left" else 0
        column_width = pixels + ruby_lane
        pitch = (column_width + pixels * .3) * style.line_spacing / 100
        if (source.text.count("\n") * pitch + column_width) > width:
            raise _NoFit()
        tokens = list(_tokens(source, style, pixels, shaper, height))
        columns = [[]]
        used = [0.0]
        for index, token in enumerate(tokens):
            if token is None:
                columns.append([])
                used.append(0.0)
                continue
            indent = pixels * .6 if token.quoted else 0
            needed = token.advance
            following = tokens[index + 1] if index + 1 < len(tokens) else None
            if following is not None and (following.text[0] in _NO_START or token.text[-1] in _NO_END):
                needed += following.advance
            if max(used[-1], indent) + needed > height and columns[-1]:
                columns.append([])
                used.append(0.0)
            if max(used[-1], indent) + needed > height:
                raise _NoFit()
            if not columns[-1]:
                used[-1] = indent
            columns[-1].append((token, used[-1]))
            used[-1] += token.advance
            if (len(columns) - 1) * pitch + column_width > width:
                raise _NoFit()
        for index, column in enumerate(columns):
            x = (len(columns) - 1 - index if style.writing_mode == "vertical_rl" else index) * pitch
            if column and column[0][0].quoted:
                base_decorations.addRect(QRectF(x + base_offset, pixels * .12, pixels, max(1, pixels * .025)))
            for token, y in column:
                for glyph, point in token.glyphs:
                    origin = QPointF(x + base_offset, y) + point
                    self.glyphs.append((glyph, origin))
                    target = self.ruby_path if glyph.role == "ruby" else self.base_path
                    target.addPath(glyph.path.translated(origin))
                    decorations = ruby_decorations if glyph.role == "ruby" else base_decorations
                    # Decorations form vertical rules through styled advances/spaces.
                    thickness = max(1, glyph.pixels * .045)
                    if glyph.underline:
                        decorations.addRect(QRectF(origin.x() + glyph.pixels + 1, origin.y(), thickness, glyph.advance))
                    if glyph.strike:
                        decorations.addRect(QRectF(origin.x() + glyph.pixels / 2, origin.y(), thickness, glyph.advance))
                base = [(glyph, point) for glyph, point in token.glyphs if glyph.role == "base"]
                # Continue decorations through the padding introduced by long ruby.
                for (glyph, point), start, end in (
                        (base[0], 0, base[0][1].y()),
                        (base[-1], base[-1][1].y() + base[-1][0].advance, token.advance)) if base else ():
                    thickness = max(1, glyph.pixels * .045)
                    if glyph.underline and end > start:
                        base_decorations.addRect(QRectF(x + base_offset + glyph.pixels + 1, y + start, thickness, end - start))
                    if glyph.strike and end > start:
                        base_decorations.addRect(QRectF(x + base_offset + glyph.pixels / 2, y + start, thickness, end - start))
        self.base_path = self.base_path.united(base_decorations)
        self.ruby_path = self.ruby_path.united(ruby_decorations)
        self.path = self.base_path.united(self.ruby_path)
        logical = QRectF(0, 0, column_width + (len(columns) - 1) * pitch, max(used, default=0))
        bounds = logical.united(self.path.boundingRect())
        if bounds.width() > width or bounds.height() > height:
            raise _NoFit()
        self.path.translate(-bounds.left(), -bounds.top())
        self.base_path.translate(-bounds.left(), -bounds.top())
        self.ruby_path.translate(-bounds.left(), -bounds.top())
        self.glyphs = [(glyph, point - bounds.topLeft()) for glyph, point in self.glyphs]
        self.bounds = QSizeF(bounds.size())

    def size(self):
        return self.bounds

    def defaultFont(self):
        return self.font

    def textWidth(self):
        return self.bounds.width()

    def toPlainText(self):
        return self.source.text

    def paint(self, painter, x, y, color, style, part="all"):
        painter.save()
        painter.translate(x, y)
        for path, fill, outline in ((self.base_path, color, style.stroke_enabled),
                                     (self.ruby_path, style.ruby_color or color,
                                      style.stroke_enabled and style.ruby_inherit_stroke)):
            if (part == "base" and path is self.ruby_path) or (part == "ruby" and path is self.base_path):
                continue
            painter.setBrush(QColor(fill))
            if outline:
                pen = QPen(QColor(style.stroke_color), style.stroke_width * 2)
                pen.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
                painter.setPen(pen)
                painter.drawPath(path)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.drawPath(path)
        painter.restore()


def fit_vertical_lyrics(text, style, width, height, source=None):
    source = source or CardLyrics(text)
    shaper = _Shaper(style.font_family)
    for pixels in range(style.font_size, 17, -2):
        try:
            return VerticalLyrics(source, style, pixels, width, height, shaper)
        except _NoFit:
            continue
    raise ValueError("The vertical lyrics or furigana are too long for this card. Check fewer lines, reduce reading size, or choose a wider card.")
