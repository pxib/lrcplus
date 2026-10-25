from PySide6.QtCore import Qt, QSize, Signal
from PySide6.QtGui import QColor, QFontMetrics, QPainter, QPen
from PySide6.QtWidgets import QSlider, QSizePolicy, QWidget

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
        valid = range(1, len(self._text))
        self._boundaries = {int(x) for x in (boundaries or set()) if int(x) in valid}
        self._hover_boundary = None
        self._update_geometry()
        self.update()

    def _metrics(self):
        # Cache per font: this is called for every boundary on every
        # mouse-move/paint, and QFontMetrics construction is not free.
        font = self.font()
        key = (font.family(), font.pointSize(), font.weight(), font.italic())
        if self._metrics_cache_key != key:
            self._metrics_cache = QFontMetrics(font)
            self._metrics_cache_key = key
        return self._metrics_cache

    def _boundary_x(self, boundary):
        # Measuring the prefix gives the same position used by Qt when the
        # complete string is painted, including normal font shaping/spacing.
        return self._left_padding + self._metrics().horizontalAdvance(self._text[:boundary])

    def _update_geometry(self):
        width = self._left_padding + self._metrics().horizontalAdvance(self._text) + self._right_padding
        height = max(48, self._metrics().height() + 24)
        self.setMinimumSize(max(1, width), height)
        self.resize(max(1, width), height)
        self.updateGeometry()

    def sizeHint(self):
        width = self._left_padding + self._metrics().horizontalAdvance(self._text) + self._right_padding
        return QSize(max(1, width), max(48, self._metrics().height() + 24))

    def _nearest_boundary(self, x):
        if len(self._text) < 2:
            return None
        nearest = min(range(1, len(self._text)), key=lambda b: abs(x - self._boundary_x(b)))
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
            font.setPointSize(28)
            painter.setFont(font)

            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.setPen(self.palette().color(self.foregroundRole()))
            metrics = self._metrics()
            baseline = (self.height() - metrics.height()) // 2 + metrics.ascent()
            painter.drawText(self._left_padding, baseline, self._text)

            active_color = QColor(self.palette().color(self.foregroundRole()))
            active_color.setAlpha(220)
            hover_color = QColor(active_color)
            hover_color.setAlpha(110)

            for boundary in range(1, len(self._text)):
                x = self._boundary_x(boundary)
                if boundary in self._boundaries:
                    painter.setPen(QPen(active_color, 2))
                    painter.drawLine(round(x), 8, round(x), self.height() - 8)
                elif boundary == self._hover_boundary:
                    painter.setPen(QPen(hover_color, 1))
                    painter.drawLine(round(x), 10, round(x), self.height() - 10)
        finally:
            painter.end()


