"""Frequency response preview for the EQ settings panel."""

from functools import cached_property
from math import ceil, log10

import numpy as np
from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen
from PySide6.QtWidgets import QWidget

from core.theme import build_theme_palette
from plugins.core.equalizer.dsp import filter_board
from ui.settings_controls import settings_theme_options


SAMPLE_RATE = 48000
IMPULSE_LENGTH = 32768
FREQUENCIES = np.geomspace(20, 20000, 241)
FREQUENCY_TICKS = ((20, "20"), (100, "100"), (1000, "1k"), (10000, "10k"), (20000, "20k Hz"))


def response_db(preamp, bands):
    active = [band for band in bands if band.gain]
    if not active:
        return np.full(FREQUENCIES.shape, preamp, dtype=float)
    impulse = np.zeros(IMPULSE_LENGTH, dtype=np.float32)
    impulse[0] = 1
    filtered = filter_board(active)(impulse, SAMPLE_RATE)
    bins = np.fft.rfftfreq(IMPULSE_LENGTH, 1 / SAMPLE_RATE)
    magnitude = np.interp(FREQUENCIES, bins, np.abs(np.fft.rfft(filtered)))
    return preamp + 20 * np.log10(np.maximum(magnitude, 1e-12))


class ResponsePlot(QWidget):
    def __init__(self, parent):
        super().__init__(parent)
        self._response = response_db(0, ())
        self.setFixedHeight(120)
        self.setAccessibleName("Equalizer frequency response preview")
        self.setToolTip("Combined filter and preamp response at a 48 kHz sample rate.")

    @cached_property
    def _colors(self):
        # The plugin panel is attached to the settings dialog after construction.
        host = self.window().parentWidget()
        colors = build_theme_palette(*settings_theme_options(host))
        return {name: QColor(colors[name]) for name in ("surface", "border", "field_border", "muted", "accent")}

    def set_response(self, preamp, bands):
        self._response = response_db(preamp, bands)
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        colors = self._colors
        painter.setBrush(colors["surface"])
        painter.setPen(colors["border"])
        painter.drawRoundedRect(QRectF(self.rect()).adjusted(0.5, 0.5, -0.5, -0.5), 7, 7)
        painter.setBrush(Qt.BrushStyle.NoBrush)

        painter.setFont(self.font())
        metrics = painter.fontMetrics()
        peak = max(12, 12 * ceil(float(np.max(np.abs(self._response))) / 12))
        left = max(metrics.horizontalAdvance(f"−{peak}"), metrics.horizontalAdvance("0 dB")) + 15
        right = self.width() - metrics.horizontalAdvance(FREQUENCY_TICKS[-1][1]) / 2 - 10
        top = metrics.height() / 2 + 5
        bottom = self.height() - metrics.height() - 9
        width, height = right - left, bottom - top
        if width <= 0:
            return

        for value in (peak, peak // 2, 0, -peak // 2, -peak):
            y = top + (peak - value) / (2 * peak) * height
            painter.setPen(colors["field_border" if value == 0 else "border"])
            painter.drawLine(QPointF(left, y), QPointF(right, y))
            painter.setPen(colors["muted"])
            label = f"{value:+d}".replace("-", "−") if value else "0 dB"
            painter.drawText(QRectF(4, y - metrics.height() / 2, left - 11, metrics.height()),
                             Qt.AlignmentFlag.AlignRight, label)

        for frequency, label in FREQUENCY_TICKS:
            x = left + log10(frequency / 20) / 3 * width
            painter.setPen(colors["border"])
            painter.drawLine(QPointF(x, top), QPointF(x, bottom))
            painter.setPen(colors["muted"])
            label_width = metrics.horizontalAdvance(label) + 4
            painter.drawText(QRectF(x - label_width / 2, bottom + 4, label_width, metrics.height()),
                             Qt.AlignmentFlag.AlignHCenter, label)

        zero = top + height / 2
        line = QPainterPath()
        line.moveTo(left, zero - float(self._response[0]) * height / (2 * peak))
        for index, value in enumerate(self._response[1:], start=1):
            x = left + index * width / (len(self._response) - 1)
            line.lineTo(x, zero - float(value) * height / (2 * peak))
        fill = QPainterPath(line)
        fill.lineTo(right, zero)
        fill.lineTo(left, zero)
        fill.closeSubpath()
        accent = colors["accent"]
        tint = QColor(accent)
        tint.setAlpha(43)
        painter.fillPath(fill, tint)
        painter.setPen(QPen(accent, 2))
        painter.drawPath(line)
