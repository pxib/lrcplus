"""Read-only lyric source preview with explicit, glyph-checked font fallback."""

from PySide6.QtGui import QFont, QSyntaxHighlighter
from PySide6.QtWidgets import QTextEdit

from core.fonts import library_font
from ui.content_fonts import FontRunResolver


class _PreviewFonts(QSyntaxHighlighter):
    def __init__(self, document, font):
        super().__init__(document)
        self._resolver = FontRunResolver(font)

    def highlightBlock(self, text):
        for value in self._resolver.formats(text):
            self.setFormat(value.start, value.length, value.format)


class LyricsPreview(QTextEdit):
    def __init__(self, parent=None):
        super().__init__(parent)
        font = library_font(self.font())
        font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
        self.setFont(font)
        self.setReadOnly(True)
        self.setAcceptRichText(False)
        self._preview_fonts = _PreviewFonts(self.document(), font)
