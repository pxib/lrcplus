"""A source field with a line gutter, lyric syntax and live diagnostics."""

import re

from PySide6.QtCore import QEvent, Qt, QTimer, Signal
from PySide6.QtGui import QColor, QFont, QPainter, QPalette, QTextCharFormat, QTextCursor, QTextFormat
from PySide6.QtWidgets import QPlainTextEdit, QTextEdit, QToolTip, QWidget

from core.fonts import library_font
from lyrics.editor_source import effective_source_suffix, validate_editor_source
from ui.content_fonts import FontRunResolver
from ui.lyrics_preview import _PreviewFonts


class _SourceSyntax(_PreviewFonts):
    _rules = (
        (r"^\s*\[[A-Za-z][A-Za-z0-9_]*:.*\]\s*$", "metadata"),
        (r"\[\d+:\d+(?:\.\d+)?\]", "timestamp"),
        (r"<\d+:\d+(?:\.\d+)?>", "karaoke"),
        (r"\{[^{}|\n]+\|[^{}\n]*\}", "reading"),
        (r"\{\\nt?\}", "break"),
        (r"\*\*|(?<!\*)\*(?!\*)|~~|</?u>|^>\s", "style"),
    )

    def __init__(self, document, font, palette, suffix):
        self.suffix = suffix
        self.update_palette(palette)
        super().__init__(document, font)

    def update_palette(self, palette):
        dark = palette.color(QPalette.ColorRole.Base).lightness() < 128
        colors = ("#79c0ff", "#d2a8ff", "#7ee787", "#e3b341", "#ffa8d2") if dark else (
            "#005f99", "#7a3e9d", "#00755d", "#886000", "#a33165")
        self.colors = dict(zip(("timestamp", "karaoke", "metadata", "reading", "style"), colors))
        self.colors["break"] = self.colors["style"]

    def set_font(self, font):
        self._resolver = FontRunResolver(font)
        self.rehighlight()

    def highlightBlock(self, text):
        super().highlightBlock(text)
        if self.suffix == ".txt":
            return
        utf16 = [0]
        for char in text:
            utf16.append(utf16[-1] + (2 if ord(char) > 0xFFFF else 1))
        for pattern, token in self._rules:
            for match in re.finditer(pattern, text):
                # Keep the font chosen for each grapheme, including CJK and
                # emoji fallbacks, when applying a syntax color.
                for position in range(utf16[match.start()], utf16[match.end()]):
                    fmt = self.format(position)
                    fmt.setForeground(QColor(self.colors[token]))
                    self.setFormat(position, 1, fmt)


class _LineNumbers(QWidget):
    def __init__(self, editor):
        super().__init__(editor)
        self.editor = editor

    def paintEvent(self, event):
        self.editor._paint_line_numbers(event)


class SourceTextEdit(QPlainTextEdit):
    diagnosticsChanged = Signal(list)
    sourceTextChanged = Signal()

    def __init__(self, parent=None, *, suffix=".lrcx"):
        super().__init__(parent)
        self.setAccessibleName("Lyrics source editor")
        self.setToolTip("Hold Ctrl and scroll to zoom the source text.")
        self.setPlaceholderText("[00:12.500]Lyric text")
        self.setLineWrapMode(QPlainTextEdit.LineWrapMode.NoWrap)
        font = library_font(self.font())
        font.setStyleStrategy(font.styleStrategy() | QFont.StyleStrategy.NoFontMerging)
        self.setFont(font)
        self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
        self._suffix = suffix
        self._issues = []
        self._diagnostics_enabled = True
        self._zoom_wheel_remainder = 0
        self._preview_fonts = _SourceSyntax(self.document(), font, self.palette(), suffix)
        self.line_numbers = _LineNumbers(self)
        self._diagnostics_timer = QTimer(self)
        self._diagnostics_timer.setSingleShot(True)
        self._diagnostics_timer.setInterval(200)
        self._diagnostics_timer.timeout.connect(self.refresh_diagnostics)
        self.document().contentsChange.connect(self._source_contents_changed)
        self.blockCountChanged.connect(self._update_gutter_width)
        self.updateRequest.connect(self._update_gutter)
        self.cursorPositionChanged.connect(self._update_selections)
        self._update_gutter_width()
        self._update_selections()

    @property
    def diagnostics(self):
        return tuple(self._issues)

    def set_source_format(self, suffix):
        if self._suffix != suffix:
            self._suffix = suffix
            self._preview_fonts.suffix = suffix
            self._preview_fonts.rehighlight()
        self._diagnostics_timer.start()

    def _source_contents_changed(self, _position, removed, added):
        if self._diagnostics_enabled and (removed or added):
            self._diagnostics_timer.start()
            # QPlainTextEdit.textChanged also fires when a highlighter only
            # updates formats. Only content changes should dirty the lyrics.
            self.sourceTextChanged.emit()

    def refresh_diagnostics(self):
        if not self._diagnostics_enabled:
            return
        self._diagnostics_timer.stop()
        text = self.toPlainText()
        self._issues = validate_editor_source(text, effective_source_suffix(text, self._suffix))
        self._update_selections()
        self.line_numbers.update()
        self.diagnosticsChanged.emit(self._issues)

    def shutdown(self):
        self._diagnostics_enabled = False
        self._diagnostics_timer.stop()

    def go_to_issue(self, issue):
        block = self.document().findBlockByNumber(issue.line_number - 1)
        if not block.isValid():
            return
        start, end = self._issue_positions(block, issue)
        cursor = QTextCursor(self.document())
        cursor.setPosition(start)
        cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
        self.setTextCursor(cursor)
        self.ensureCursorVisible()
        self.setFocus(Qt.FocusReason.OtherFocusReason)

    @staticmethod
    def _issue_positions(block, issue):
        text = block.text()
        start = len(text[:issue.column].encode("utf-16-le")) // 2
        end = len(text[:issue.column + issue.length].encode("utf-16-le")) // 2
        return block.position() + start, block.position() + end

    def _error_color(self):
        dark = self.palette().color(QPalette.ColorRole.Base).lightness() < 128
        return QColor("#ff7b72" if dark else "#b42318")

    def _update_selections(self):
        current = QTextEdit.ExtraSelection()
        current.cursor = self.textCursor()
        current.cursor.clearSelection()
        current.format.setBackground(self.palette().color(QPalette.ColorRole.AlternateBase))
        current.format.setProperty(QTextFormat.Property.FullWidthSelection, True)
        selections = [current]
        for issue in self._issues:
            block = self.document().findBlockByNumber(issue.line_number - 1)
            if not block.isValid():
                continue
            selection = QTextEdit.ExtraSelection()
            start, end = self._issue_positions(block, issue)
            selection.cursor = QTextCursor(self.document())
            selection.cursor.setPosition(start)
            selection.cursor.setPosition(end, QTextCursor.MoveMode.KeepAnchor)
            selection.format.setUnderlineStyle(QTextCharFormat.UnderlineStyle.WaveUnderline)
            selection.format.setUnderlineColor(self._error_color())
            selections.append(selection)
        self.setExtraSelections(selections)
        if hasattr(self, "line_numbers"):
            self.line_numbers.update()

    def _gutter_width(self):
        return 14 + len(str(max(1, self.blockCount()))) * self.fontMetrics().horizontalAdvance("9")

    def _update_gutter_width(self, *_args):
        self.setViewportMargins(self._gutter_width(), 0, 0, 0)
        rect = self.contentsRect()
        self.line_numbers.setGeometry(rect.left(), rect.top(), self._gutter_width(), rect.height())

    def _update_gutter(self, rect, dy):
        if dy:
            self.line_numbers.scroll(0, dy)
        else:
            self.line_numbers.update(0, rect.y(), self._gutter_width(), rect.height())
        if rect.contains(self.viewport().rect()):
            self._update_gutter_width()

    def resizeEvent(self, event):
        super().resizeEvent(event)
        rect = self.contentsRect()
        self.line_numbers.setGeometry(rect.left(), rect.top(), self._gutter_width(), rect.height())

    def _paint_line_numbers(self, event):
        painter = QPainter(self.line_numbers)
        painter.fillRect(event.rect(), self.palette().color(QPalette.ColorRole.AlternateBase))
        painter.setFont(self.font())
        block = self.firstVisibleBlock()
        top = self.blockBoundingGeometry(block).translated(self.contentOffset()).top()
        errors = {issue.line_number for issue in self._issues}
        current = self.textCursor().blockNumber()
        while block.isValid() and top <= event.rect().bottom():
            height = self.blockBoundingRect(block).height()
            if block.isVisible() and top + height >= event.rect().top():
                color = (self._error_color() if block.blockNumber() + 1 in errors else
                         self.palette().color(QPalette.ColorRole.Text if block.blockNumber() == current
                                              else QPalette.ColorRole.PlaceholderText))
                painter.setPen(color)
                painter.drawText(0, round(top), self._gutter_width() - 7, self.fontMetrics().height(),
                                 Qt.AlignmentFlag.AlignRight, str(block.blockNumber() + 1))
            top += height
            block = block.next()

    def changeEvent(self, event):
        super().changeEvent(event)
        if event.type() == QEvent.Type.PaletteChange and hasattr(self, "_preview_fonts"):
            self._preview_fonts.update_palette(self.palette())
            self._preview_fonts.rehighlight()
            self._update_selections()
            self.diagnosticsChanged.emit(self._issues)
        elif event.type() == QEvent.Type.FontChange and hasattr(self, "line_numbers"):
            self._preview_fonts.set_font(self.font())
            self.setTabStopDistance(self.fontMetrics().horizontalAdvance(" ") * 4)
            self._update_gutter_width()
            self._update_selections()

    def wheelEvent(self, event):
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Wheel notches are 120 angle units. Pixel-only trackpads use a
            # similar step per 40 pixels; smaller movements accumulate.
            delta = event.angleDelta().y() or event.pixelDelta().y() * 3
            self._zoom_wheel_remainder += delta
            steps = int(self._zoom_wheel_remainder / 120)
            self._zoom_wheel_remainder -= steps * 120
            if steps:
                font = self.font()
                size = font.pointSizeF()
                if size <= 0:
                    size = self.fontInfo().pointSizeF()
                font.setPointSizeF(max(6.0, min(40.0, size + steps)))
                self.setFont(font)
            event.accept()
            return
        self._zoom_wheel_remainder = 0
        super().wheelEvent(event)

    def viewportEvent(self, event):
        if event.type() == QEvent.Type.ToolTip:
            line = self.cursorForPosition(event.pos()).blockNumber() + 1
            messages = [issue.message for issue in self._issues if issue.line_number == line]
            if messages:
                QToolTip.showText(event.globalPos(), "\n".join(messages), self)
                return True
        return super().viewportEvent(event)
