from PySide6.QtCore import Qt, QSize, Signal, QTextBoundaryFinder
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QHBoxLayout, QLabel, QPushButton, QSlider, QSizePolicy, QVBoxLayout, QWidget

from core.fonts import content_font


class WaveformControls(QWidget):
    """The same visible navigation controls in both editor timelines."""

    def __init__(self, waveform, *, karaoke=False, parent=None):
        super().__init__(parent)
        self.waveform = waveform
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)
        row = QHBoxLayout()
        row.setContentsMargins(0, 0, 0, 0)
        self.pan_left = self._button("←", "Pan earlier (Left arrow or mouse wheel).", lambda: waveform.pan_view(-0.12))
        self.pan_right = self._button("→", "Pan later (Right arrow or mouse wheel).", lambda: waveform.pan_view(0.12))
        self.zoom_out = self._button("Zoom −", "Zoom out (− or Ctrl+wheel).", lambda: waveform.zoom_view(1 / 1.2))
        self.zoom_in = self._button("Zoom +", "Zoom in (+ or Ctrl+wheel).", lambda: waveform.zoom_view(1.2))
        self.fit_line = self._button("Fit line", "Fit the selected lyric line (Home).", waveform.fit_selection)
        self.all_audio = self._button("All audio", "Show the entire audio file (Ctrl+Home).", waveform.show_full_audio)
        self.pan_left.setAccessibleName("Pan waveform earlier")
        self.pan_right.setAccessibleName("Pan waveform later")
        self.pan_left.setFixedWidth(32)
        self.pan_right.setFixedWidth(32)
        for button in (self.pan_left, self.pan_right, self.zoom_out, self.zoom_in, self.fit_line, self.all_audio):
            row.addWidget(button)
        row.addStretch()
        layout.addLayout(row)
        preview_hint = "Ctrl+right click: preview segment" if karaoke else "Shift+click: play from here"
        self.hint = QLabel(f"Left: start · Right: end · Ctrl+click: seek · {preview_hint}")
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)

    def _button(self, text, tooltip, callback):
        button = QPushButton(text)
        button.setToolTip(tooltip)
        button.setAutoDefault(False)
        button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        button.setProperty("waveformNavigation", True)
        button.clicked.connect(lambda: self._activate(callback))
        return button

    def _activate(self, callback):
        callback()
        self.waveform.setFocus(Qt.FocusReason.OtherFocusReason)


class LyricLabel(QLabel):
    def setText(self, text):
        self.setFont(content_font(self.font(), text))
        super().setText(text)

class ClickableSlider(QSlider):
    """Slider that seeks immediately when its groove is clicked."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                span = max(1, self.width())
                position = event.position().x()
            else:
                span = max(1, self.height())
                position = self.height() - event.position().y()
            ratio = max(0.0, min(1.0, position / span))
            value = self.minimum() + ratio * (self.maximum() - self.minimum())
            self.setValue(round(value))
            self.sliderMoved.emit(self.value())
        super().mousePressEvent(event)


class KaraokeSeparationWidget(QWidget):
    """A compact, text-based karaoke separator editor.

    The lyric is drawn once and every gap between characters is a clickable
    boundary. This avoids creating a QPushButton for every character/gap while
    still allowing arbitrary manual segmentation.
    """

    boundariesChanged = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self._text = ""
        self._boundaries = set()
        self._valid_boundaries = []
        self._boundary_positions = {}
        self._boundary_positions_key = None
        self._hover_boundary = None
        self._left_padding = 12
        self._right_padding = 12
        self._hit_radius = 8
        font = self.font()
        font.setPointSize(28)
        self.setFont(font)
        self._metrics_cache = None
        self._metrics_cache_key = None
        self.setMouseTracking(True)
        self.setMinimumHeight(48)
        self.setSizePolicy(QSizePolicy.Policy.Minimum, QSizePolicy.Policy.Expanding)

    def text(self):
        return self._text

    def boundaries(self):
        return set(self._boundaries)

    def set_content(self, text, boundaries=None):
        self._text = text or ""
        self.setFont(content_font(self.font(), self._text.replace("\n", "\u21b5")))
        # Qt reports UTF-16 positions; Python slices use Unicode code points.
        # Map only whole grapheme boundaries so accents and emoji stay intact.
        utf16_to_python = {0: 0}
        offset = 0
        for index, char in enumerate(self._text, 1):
            offset += 2 if ord(char) > 0xFFFF else 1
            utf16_to_python[offset] = index
        finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, self._text)
        valid = set()
        while True:
            position = finder.toNextBoundary()
            if position < 0:
                break
            boundary = utf16_to_python.get(position)
            if boundary is not None and 0 < boundary < len(self._text):
                valid.add(boundary)
        self._valid_boundaries = sorted(valid)
        self._boundaries = {int(x) for x in (boundaries or set()) if int(x) in valid}
        self._hover_boundary = None
        self._update_geometry()
        self.update()

    def _metrics(self):
        # Cache per font: this is called for every boundary on every
        # mouse-move/paint, and QFontMetrics construction is not free.
        font = self.font()
        key = font.key()
        if self._metrics_cache_key != key:
            self._metrics_cache = QFontMetrics(font)
            self._metrics_cache_key = key
        return self._metrics_cache

    def _boundary_x(self, boundary):
        # Measuring the prefix gives the same position used by Qt when the
        # complete string is painted, including normal font shaping/spacing.
        key = (self._text, self.font().key())
        if self._boundary_positions_key != key:
            self._boundary_positions = {
                index: self._left_padding + self._metrics().horizontalAdvance(self._text[:index].replace("\n", "\u21b5"))
                for index in self._valid_boundaries
            }
            self._boundary_positions_key = key
        return self._boundary_positions[boundary]

    def _update_geometry(self):
        width = self._left_padding + self._metrics().horizontalAdvance(self._text.replace("\n", "\u21b5")) + self._right_padding
        height = max(48, self._metrics().height() + 24)
        self.setMinimumSize(max(1, width), height)
        self.resize(max(1, width), height)
        self.updateGeometry()

    def sizeHint(self):
        width = self._left_padding + self._metrics().horizontalAdvance(self._text.replace("\n", "\u21b5")) + self._right_padding
        return QSize(max(1, width), max(48, self._metrics().height() + 24))

    def _nearest_boundary(self, x):
        if not self._valid_boundaries:
            return None
        nearest = min(self._valid_boundaries, key=lambda b: abs(x - self._boundary_x(b)))
        return nearest if abs(x - self._boundary_x(nearest)) <= self._hit_radius else None

    def mouseMoveEvent(self, event):
        boundary = self._nearest_boundary(event.position().x())
        if boundary != self._hover_boundary:
            self._hover_boundary = boundary
            self.setCursor(Qt.CursorShape.PointingHandCursor if boundary is not None else Qt.CursorShape.ArrowCursor)
            self.update()
        super().mouseMoveEvent(event)

    def leaveEvent(self, event):
        if self._hover_boundary is not None:
            self._hover_boundary = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.update()
        super().leaveEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            boundary = self._nearest_boundary(event.position().x())
            if boundary is not None:
                if boundary in self._boundaries:
                    self._boundaries.remove(boundary)
                else:
                    self._boundaries.add(boundary)
                self.boundariesChanged.emit()
                self.update()
                event.accept()
                return
        super().mousePressEvent(event)

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            font = self.font()
            painter.setFont(font)

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setPen(self.palette().color(self.foregroundRole()))
            metrics = self._metrics()
            baseline = (self.height() - metrics.height()) // 2 + metrics.ascent()
            painter.drawText(self._left_padding, baseline, self._text.replace("\n", "\u21b5"))

            active_color = QColor(self.palette().color(self.foregroundRole()))
            active_color.setAlpha(220)
            hover_color = QColor(active_color)
            hover_color.setAlpha(110)

            for boundary in self._valid_boundaries:
                x = self._boundary_x(boundary)
                if boundary in self._boundaries:
                    painter.setPen(QPen(active_color, 2))
                    painter.drawLine(round(x), 8, round(x), self.height() - 8)
                elif boundary == self._hover_boundary:
                    painter.setPen(QPen(hover_color, 1))
                    painter.drawLine(round(x), 10, round(x), self.height() - 10)
        finally:
            painter.end()


