"""An optional spectrum driven by the player's decoded audio."""

from PySide6.QtCore import QRectF, Qt, QTimer, Signal
from PySide6.QtGui import QBrush, QColor, QLinearGradient, QPainter, QPainterPath, QPalette, QPen
from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from core.playback_audio_tap import PLAYBACK_SAMPLE_FRAMES


_MENU_TEXT = "Audio Visualizer"
_BAND_COUNT = 28
_ANALYSIS_FRAMES = 3072
_SAMPLE_TYPES = {
    QAudioFormat.SampleFormat.UInt8: ("uint8", 128.0, 128.0),
    QAudioFormat.SampleFormat.Int16: ("int16", 0.0, 32768.0),
    QAudioFormat.SampleFormat.Int32: ("int32", 0.0, 2147483648.0),
    QAudioFormat.SampleFormat.Float: ("float32", 0.0, 1.0),
}


class SpectrumWidget(QWidget):
    visibility_changed = Signal()

    def __init__(self, player):
        import numpy as np

        super().__init__(player)
        self._np = np
        self._player = player
        self._tap = player.playback_audio_tap
        self.over_artwork = False
        self._levels = np.zeros(_BAND_COUNT)
        self._target = np.zeros(_BAND_COUNT)
        self._peaks = np.zeros(_BAND_COUNT)
        self._window = np.hanning(_ANALYSIS_FRAMES)
        self._frequencies = np.geomspace(45, 14_000, _BAND_COUNT + 1)
        self._reference_db = None
        self._idle_ticks = 0
        self._accent_key = None
        self._band_colors = ()
        self.setObjectName("audioVisualizer")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(16)
        self._timer.timeout.connect(self._advance)

    def set_running(self, running):
        if running:
            if not self._timer.isActive():
                self._timer.start()
        elif self._timer.isActive():
            self._timer.stop()
            self._levels.fill(0)
            self._target.fill(0)
            self._peaks.fill(0)
            self._reference_db = None
            self._idle_ticks = 0
            self.update()

    def _spectrum(self, data, rate, channels, sample_format):
        np = self._np
        sample_type = _SAMPLE_TYPES.get(sample_format)
        if sample_type is None or rate <= 0 or channels <= 0:
            return np.zeros(_BAND_COUNT)
        dtype, center, scale = sample_type
        samples = np.frombuffer(data, dtype=dtype).reshape(-1, channels)[-_ANALYSIS_FRAMES:]
        frames = len(samples)
        if frames < 256:
            return np.zeros(_BAND_COUNT)
        window = self._window if frames == len(self._window) else np.hanning(frames)
        samples = (samples.astype(np.float32) - center) / scale
        transformed = np.fft.rfft(samples * window[:, None], n=PLAYBACK_SAMPLE_FRAMES, axis=0)
        magnitude = np.sqrt(np.mean(np.abs(transformed) ** 2, axis=1)) * (2 / window.sum())
        bins = np.ceil(self._frequencies * PLAYBACK_SAMPLE_FRAMES / rate).astype(int)
        last = len(magnitude) - 1
        peaks = np.array([
            magnitude[lo:max(lo + 1, min(hi, last + 1))].max() if lo <= last else 0.0
            for lo, hi in zip(bins[:-1], bins[1:])
        ])
        db = 20 * np.log10(np.maximum(peaks, 1e-6))
        loudest = float(db.max())
        if not np.isfinite(loudest) or loudest < -88:
            return np.zeros(_BAND_COUNT)

        # Track the overall level slowly so quiet playback remains visible
        # while individual hits still have room to rise and fall.
        if self._reference_db is None:
            self._reference_db = loudest
        else:
            difference = loudest - self._reference_db
            self._reference_db += difference * (0.018 if difference > 0 else 0.009)
        relative = np.clip((db - self._reference_db + 32) / 40, 0, 1)
        return relative ** 1.35

    def _advance(self):
        sample = self._tap.take_latest()
        if sample is not None:
            self._target = self._spectrum(*sample)
            self._idle_ticks = 0
        else:
            self._idle_ticks += 1
            if self._idle_ticks > 6:
                self._target.fill(0)
        delta = self._target - self._levels
        self._levels += delta * self._np.where(delta > 0, 0.91, 0.44)
        # Let the peaks fall more slowly than the live signal.
        np = self._np
        was_visible = self._peaks.max() >= 0.025
        np.maximum(self._levels, self._peaks - 0.062, out=self._peaks)
        if was_visible or self._peaks.max() >= 0.025:
            self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit()

    def paintEvent(self, event):
        if self._peaks.max() < 0.025 or self.width() < 8 or self.height() < 8:
            return
        colors, light_dock = self._visual_colors()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self.over_artwork:
                self._paint_artwork(painter, colors)
            else:
                self._paint_seekbar(painter, colors, light_dock)

    def _visual_colors(self):
        appearance = self._player.appearance_settings
        mode = appearance.get("theme_mode")
        if mode == "qt":
            accent = self.palette().color(QPalette.ColorRole.Highlight)
            dock = self.palette().color(QPalette.ColorRole.Window)
        else:
            accent = QColor(appearance.get("theme_color", "#3a9879"))
            if not accent.isValid():
                accent = QColor("#3a9879")
            overrides = appearance.get("theme_overrides", {}).get(mode, {})
            dock = QColor(overrides.get("dock", "#242b38" if mode == "dark" else "#f4f7f8"))
        if self._accent_key != accent.rgb():
            self._accent_key = accent.rgb()
            hue = accent.hsvHueF()
            if hue < 0:
                hue = 0.56
            saturation = max(0.46, min(0.69, accent.hsvSaturationF() + 0.16))
            self._band_colors = tuple(
                QColor.fromHsvF((hue + float(offset)) % 1, saturation, 0.97)
                for offset in self._np.linspace(0.14, -0.09, _BAND_COUNT)
            )
        return self._band_colors, dock.lightnessF() > 0.62

    def _spectrum_curve(self, levels, bottom, amplitude):
        # Keep the outer frequency bands at the panel edges.
        padded = self._np.pad(levels, 1, mode="edge")
        smoothed = self._np.convolve(padded, (0.12, 0.76, 0.12), mode="valid")
        step = self.width() / (_BAND_COUNT - 1)
        x, y = 0.0, bottom - amplitude * float(smoothed[0])
        path = QPainterPath()
        path.moveTo(x, y)
        for index, level in enumerate(smoothed[1:], start=1):
            next_x = index * step
            next_y = bottom - amplitude * float(level)
            midpoint = (x + next_x) / 2
            path.cubicTo(midpoint, y, midpoint, next_y, next_x, next_y)
            x, y = next_x, next_y
        return path

    def _paint_artwork(self, painter, colors):
        width = self.width()
        amplitude = min(146.0, self.height() * 0.33)
        bottom = self.height() - 14.0
        live = self._spectrum_curve(self._levels, bottom, amplitude)
        trail = self._spectrum_curve(self._peaks, bottom - 7, amplitude * 0.94)
        activity = float(self._levels.max())
        peak_activity = float(self._peaks.max())

        under_curve = QPainterPath(live)
        under_curve.lineTo(width, bottom)
        under_curve.lineTo(0, bottom)
        under_curve.closeSubpath()
        fill = QLinearGradient(0, bottom - amplitude, 0, bottom)
        tint = QColor(colors[_BAND_COUNT // 2])
        tint.setAlphaF(0.32 * activity)
        fill.setColorAt(0.0, tint)
        tint.setAlphaF(0.16 * activity)
        fill.setColorAt(0.55, tint)
        tint.setAlpha(0)
        fill.setColorAt(1.0, tint)
        painter.fillPath(under_curve, fill)

        # A few translucent strokes soften the edge without a blur pass over the artwork.
        for path, thickness, opacity in (
            (trail, 1.2, 0.38 * peak_activity),
            (live, 16.0, 0.08 * activity),
            (live, 7.0, 0.19 * activity),
            (live, 2.1, min(0.95, 1.25 * activity)),
        ):
            glow = QLinearGradient(0, 0, width, 0)
            for position, color in ((0.0, colors[0]), (0.5, colors[_BAND_COUNT // 2]), (1.0, colors[-1])):
                tint = color.lighter(125) if thickness <= 2.1 else QColor(color)
                tint.setAlphaF(opacity)
                glow.setColorAt(position, tint)
            pen = QPen(QBrush(glow), thickness)
            pen.setCapStyle(Qt.PenCapStyle.RoundCap)
            painter.strokePath(path, pen)

    def _paint_seekbar(self, painter, colors, light_dock):
        bar_width = min(12.0, self.width() * 0.56 / _BAND_COUNT)
        spacing = (self.width() - bar_width) / (_BAND_COUNT - 1)
        area = self.height() - 8
        bottom = self.height() - 3
        radius = min(bar_width / 2, 2.8)
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (level, peak, color) in enumerate(zip(self._levels, self._peaks, colors)):
            left = index * spacing
            color = color.darker(150) if light_dock else color
            if level >= 0.025:
                bar_height = max(2.0, area * float(level))
                fill = QLinearGradient(0, bottom - bar_height, 0, bottom)
                bright = color.darker(114) if light_dock else color.lighter(137)
                bright.setAlpha(232)
                fill.setColorAt(0.0, bright)
                base = QColor(color)
                base.setAlpha(140 if light_dock else 105)
                fill.setColorAt(1.0, base)
                painter.setBrush(fill)
                painter.drawRoundedRect(
                    QRectF(left, bottom - bar_height, bar_width, bar_height), radius, radius,
                )
            if peak >= 0.08:
                cap = color.darker(125) if light_dock else color.lighter(150)
                cap.setAlphaF(min(0.94, 0.24 + float(peak) * 0.8))
                painter.setBrush(cap)
                peak_y = bottom - area * float(peak) - 3
                painter.drawRoundedRect(QRectF(left, peak_y, bar_width, 1.8), 0.9, 0.9)


class AudioVisualizerPlugin:
    def on_load(self, context):
        self.api = context.api
        self.player = None
        self.widget = None
        self.visible = bool(self.api.get_setting("visible", False))
        self.placement = self.api.get_setting("placement", "artwork")
        if self.placement not in ("artwork", "seekbar"):
            self.placement = "artwork"
        self.api.on("app_ready", self._attach)
        self.api.on("playback_state_changed", self._sync_activity)
        self.api.add_menu_action("Plugins", _MENU_TEXT, self._toggle, checkable=True)
        self.api.register_settings_widget(
            _MENU_TEXT, self._settings, apply=self._apply_settings,
        )

    def _attach(self, window):
        self.player = window
        self._apply_visibility()

    def _toggle(self):
        self.visible = not self.visible
        self.api.set_setting("visible", self.visible)
        self._apply_visibility()

    def _apply_visibility(self):
        if self.player is None:
            return
        self.api.set_menu_action_checked(_MENU_TEXT, self.visible)
        self.player.set_playback_audio_observation(self.visible)
        if not self.visible:
            if self.widget is not None:
                self.widget.hide()
            self._sync_activity()
            return
        if self.widget is None:
            self.widget = SpectrumWidget(self.player)
            self.widget.visibility_changed.connect(self._sync_activity)
        self._place_widget()

    def _place_widget(self):
        widget = self.widget
        if widget is None or not self.visible:
            return
        widget.hide()
        self.player.seek_extensions_layout.removeWidget(widget)
        widget.over_artwork = self.placement == "artwork"
        if widget.over_artwork:
            widget.setMaximumHeight(16777215)
            widget.setMinimumHeight(0)
            self.player.lyrics_background.set_content(widget)
            widget.stackUnder(self.player.lyric_display_stack)
        else:
            widget.setFixedHeight(34)
            self.player.seek_extensions_layout.addWidget(widget)
        widget.show()
        self._sync_activity()

    def _sync_activity(self, **_event):
        if self.player is None or self.widget is None:
            return
        running = (self.visible and self.widget.isVisible()
                   and not self.player.isMinimized() and self.player.media_player.isPlaying())
        self.player.playback_audio_tap.set_collecting(running)
        self.widget.set_running(running)

    def _settings(self, parent):
        widget = QWidget(parent)
        layout = QFormLayout(widget)
        widget.placement = QComboBox(widget)
        widget.placement.addItem("Over album artwork in Lyrics", "artwork")
        widget.placement.addItem("Below the seekbar", "seekbar")
        widget.placement.setCurrentIndex(widget.placement.findData(self.placement))
        layout.addRow("Placement", widget.placement)
        return widget

    def _apply_settings(self, widget):
        placement = widget.placement.currentData()
        if placement != self.placement:
            self.placement = placement
            self.api.set_setting("placement", placement)
            self._place_widget()

    def on_unload(self, context):
        if self.widget is not None:
            self.widget.hide()
            self.widget.set_running(False)
            self.widget.deleteLater()
            self.widget = None
        if self.player is not None:
            self.player.set_playback_audio_observation(False)
            self.player = None


PLUGIN_CLASS = AudioVisualizerPlugin
