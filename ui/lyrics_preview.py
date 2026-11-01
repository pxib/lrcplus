"""Read-only lyric source preview with explicit, glyph-checked font fallback."""

import unicodedata

from PySide6.QtCore import QTextBoundaryFinder
from PySide6.QtGui import QFont, QFontDatabase, QRawFont, QSyntaxHighlighter, QTextCharFormat
from PySide6.QtWidgets import QTextEdit

from core.fonts import CONTENT_FALLBACK_FAMILIES, library_font


class _PreviewFonts(QSyntaxHighlighter):
    def __init__(self, document, font):
        super().__init__(document)
        self._font = QFont(font)
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
                    font = QFont(self._font)
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

    def highlightBlock(self, text):
        start = offset = 0
        previous = None
        # Keep combining marks and emoji sequences on one font. Qt's grapheme
        # boundaries and format offsets both use UTF-16 code units.
        encoded = text.encode("utf-16-le")
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
        boundary = finder.toNextBoundary()
        while boundary >= 0:
            cluster = encoded[offset * 2:boundary * 2].decode("utf-16-le")
            current = self._format_for(cluster)
            if current is not previous:
                if previous is not None:
                    self.setFormat(start, offset - start, previous)
                start, previous = offset, current
            offset = boundary
            boundary = finder.toNextBoundary()
        if previous is not None:
            self.setFormat(start, offset - start, previous)


class LyricsPreview(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        font = library_font(self.font())
        font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
        self.setFont(font)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self._preview_fonts = _PreviewFonts(self.document(), font)
