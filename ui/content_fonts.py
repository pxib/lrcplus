"""Choose covering fonts per grapheme without loading legacy CJK fallbacks."""

import html
import unicodedata

from PySide6.QtCore import QTextBoundaryFinder
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QTextCharFormat, QTextLayout

from core.fonts import CONTENT_FALLBACK_FAMILIES


def graphemes(text):
    encoded = text.encode("utf-16-le")
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    offset = 0
    boundary = finder.toNextBoundary()
    while boundary >= 0:
        yield offset, boundary, encoded[offset * 2:boundary * 2].decode("utf-16-le")
        offset = boundary
        boundary = finder.toNextBoundary()


class FontRunResolver:
    def __init__(self, font):
        self.font = QFont(font)
        self._raw = QRawFont.fromFont(font)
        self._characters = {}
        self._candidates = {}
        self._families = [family for family in (
            *CONTENT_FALLBACK_FAMILIES, "Segoe UI", "Segoe UI Emoji",
            "Segoe UI Symbol", "Noto Sans", "DejaVu Sans", "Apple Color Emoji",
        ) if QFontDatabase.hasFamily(family)]
        fallback = QFont(font)
        fallback.setStyleStrategy(font.styleStrategy() & ~QFont.StyleStrategy.NoFontMerging)
        self._automatic = QTextCharFormat()
        self._automatic.setFont(fallback)

    def _format_for(self, cluster):
        if cluster in self._characters:
            return self._characters[cluster]
        characters = {ord(char) for char in unicodedata.normalize("NFC", cluster)
                      if not char.isspace() and unicodedata.category(char) != "Cf"
                      and not (0xFE00 <= ord(char) <= 0xFE0F or 0xE0100 <= ord(char) <= 0xE01EF)}
        result = None
        if not all(self._raw.supportsCharacter(codepoint) for codepoint in characters):
            result = self._automatic
            for family in self._families:
                if family not in self._candidates:
                    font = QFont(self.font)
                    font.setFamily(family)
                    raw = QRawFont.fromFont(font)
                    char_format = QTextCharFormat()
                    char_format.setFont(font)
                    self._candidates[family] = raw, char_format
                raw, char_format = self._candidates[family]
                if raw.isValid() and all(raw.supportsCharacter(codepoint) for codepoint in characters):
                    result = char_format
                    break
        if len(self._characters) >= 4096:
            self._characters.clear()
        self._characters[cluster] = result
        return result

    def formats(self, text):
        result = []
        start = end = 0
        previous = None
        for offset, boundary, cluster in graphemes(text):
            current = self._format_for(cluster)
            if current is not previous:
                if previous is not None:
                    result.append(self._range(start, offset, previous))
                start, previous = offset, current
            end = boundary
        if previous is not None:
            result.append(self._range(start, end, previous))
        return result

    def html(self, text):
        """Keep rich-text styling while assigning covering font families."""
        encoded = text.encode("utf-16-le")
        parts = []
        offset = 0
        automatic = False
        for value in self.formats(text):
            parts.append(html.escape(encoded[offset * 2:value.start * 2].decode("utf-16-le")))
            end = value.start + value.length
            content = html.escape(encoded[value.start * 2:end * 2].decode("utf-16-le"))
            font = value.format.font()
            automatic |= not bool(font.styleStrategy() & QFont.StyleStrategy.NoFontMerging)
            family = font.family().replace("\\", "\\\\").replace("'", "\\'")
            style = html.escape(f"font-family: '{family}';", quote=True)
            parts.append(f'<span style="{style}">{content}</span>')
            offset = end
        parts.append(html.escape(encoded[offset * 2:].decode("utf-16-le")))
        return "".join(parts), automatic

    @staticmethod
    def _range(start, end, char_format):
        value = QTextLayout.FormatRange()
        value.start, value.length, value.format = start, end - start, char_format
        return value
