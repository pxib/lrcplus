"""Inline ruby objects that wrap as a unit and share the surrounding baseline."""

from dataclasses import dataclass
import math

from PySide6.QtCore import QSizeF, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QPen, QPyTextObject, QTextCharFormat,
    QTextCursor, QTextFormat,
)

from ui.lyrics_card_text import CardLyrics, utf16_offsets
from ui.lyrics_card_decorations import undecorated_document


RUBY_OBJECT = int(QTextFormat.ObjectTypes.UserObject) + 1
RUBY_INDEX = int(QTextFormat.Property.UserProperty) + 20


@dataclass(frozen=True)
class RubyWord:
    text: str
    reading: str
    styles: tuple
    translated: bool


def ruby_layout_source(source):
    """Replace each annotated word with one object, remapping Python offsets."""
    starts, ends = [0] * (len(source.text) + 1), [0] * (len(source.text) + 1)
    parts, words = [], []
    old = new = 0
    for a, b, reading in sorted(source.readings):
        if a < old or not (0 <= a < b <= len(source.text)) or "\n" in source.text[a:b]:
            continue
        while old < a:
            starts[old] = ends[old] = new
            parts.append(source.text[old])
            old += 1
            new += 1
        local_styles = tuple((max(x, a) - a, min(y, b) - a, *flags)
                             for x, y, *flags in source.styles if max(x, a) < min(y, b))
        words.append((new, RubyWord(source.text[a:b], reading, local_styles,
                                    any(x <= a and b <= y for x, y in source.translations))))
        for index in range(a, b):
            starts[index] = new
            ends[index] = new if index == a else new + 2
        # A zero-width font strut supplies descent even on an all-ruby row.
        parts.append("\ufffc\u200b")
        new += 2
        old = b
    while old < len(source.text):
        starts[old] = ends[old] = new
        parts.append(source.text[old])
        old += 1
        new += 1
    starts[old] = ends[old] = new
    def remap(spans):
        return tuple((starts[a], ends[b], *flags) for a, b, *flags in spans if starts[a] < ends[b])
    return CardLyrics("".join(parts), remap(source.styles), remap(source.quotes), remap(source.translations)), words


class RubyTextObject(QPyTextObject):
    object_type = RUBY_OBJECT
    index_property = RUBY_INDEX

    @staticmethod
    def ruby_drop(ascent):
        return max(1, round(ascent * .22))

    def attach(self, document):
        document.documentLayout().registerHandler(RUBY_OBJECT, self)
        document._card_ruby_handler = self
        document.markContentsDirty(0, document.characterCount())

    def __init__(self, document, words, font, scale, make_document):
        super().__init__(document)
        self.color = QColor("#ffffff")
        self.pieces = []
        self._outlined = {}
        for _, word in words:
            pixels = max(1, round(font.pixelSize() * (.75 if word.translated else 1)))
            base = make_document(CardLyrics(word.text, styles=word.styles), pixels)
            ruby_styles = tuple((math.floor(a * len(word.reading) / len(word.text)),
                                 math.floor(b * len(word.reading) / len(word.text)), *flags)
                                for a, b, *flags in word.styles)
            reading = make_document(CardLyrics(word.reading, styles=ruby_styles), max(8, round(pixels * scale / 100)))
            width = math.ceil(max(base.idealWidth(), reading.idealWidth()))
            base.size()
            ascent = base.begin().layout().lineAt(0).ascent()
            gap = max(1, round(pixels * .03))
            self.pieces.append((base, reading, QSizeF(width, reading.size().height() + gap + ascent), ascent))

    def intrinsicSize(self, document, position, char_format):
        return self.pieces[int(char_format.property(RUBY_INDEX))][2]

    def _document_for_pen(self, document, pen):
        if pen.style() == Qt.PenStyle.NoPen:
            return document
        key = (id(document), pen.color().rgba(), pen.widthF())
        if key not in self._outlined:
            clone = document.clone(self)
            cursor = QTextCursor(clone)
            cursor.select(QTextCursor.SelectionType.Document)
            char_format = QTextCharFormat()
            char_format.setTextOutline(pen)
            cursor.mergeCharFormat(char_format)
            self._outlined[key] = clone
        return self._outlined[key]

    def drawObject(self, painter, rect, document, position, char_format):
        base, reading, _, ascent = self.pieces[int(char_format.property(RUBY_INDEX))]
        pen = char_format.toCharFormat().textOutline()
        # Use the font's spare vertical space to bring ruby closer to its base.
        ruby_y = rect.top() + self.ruby_drop(ascent)
        for child, y in ((reading, ruby_y), (base, rect.bottom() - ascent)):
            painted = undecorated_document(self._document_for_pen(child, pen))
            painter.save()
            painter.translate(rect.left() + (rect.width() - child.size().width()) / 2, y)
            context = QAbstractTextDocumentLayout.PaintContext()
            context.palette.setColor(context.palette.ColorRole.Text, self.color)
            painted.documentLayout().draw(painter, context)
            painter.restore()


def install_ruby_objects(document, words, font, scale, make_document):
    handler = RubyTextObject(document, words, font, scale, make_document)
    handler.attach(document)
    offsets = utf16_offsets(document.toPlainText())
    for index, (position, _) in enumerate(words):
        cursor = QTextCursor(document)
        cursor.setPosition(offsets[position])
        cursor.setPosition(offsets[position + 1], QTextCursor.MoveMode.KeepAnchor)
        char_format = QTextCharFormat()
        char_format.setObjectType(RUBY_OBJECT)
        char_format.setProperty(RUBY_INDEX, index)
        cursor.mergeCharFormat(char_format)
        cursor.setPosition(offsets[position + 1])
        cursor.setPosition(offsets[position + 2], QTextCursor.MoveMode.KeepAnchor)
        strut_format = QTextCharFormat()
        strut_format.setTextOutline(QPen(Qt.PenStyle.NoPen))
        strut_format.setFontUnderline(False)
        strut_format.setFontStrikeOut(False)
        cursor.mergeCharFormat(strut_format)


def clone_ruby_handler(source, target):
    handler = getattr(source, "_card_ruby_handler", None)
    if handler is not None:
        handler.attach(target)
