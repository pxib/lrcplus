"""An optional spectrum driven by the player's decoded audio."""

from math import ceil, cos, pi, sin
from time import monotonic

from PySide6.QtCore import QLineF, QPointF, QRectF, Qt, QTimer, Signal
from PySide6.QtGui import (
    QBrush, QColor, QImage, QLinearGradient, QPainter, QPainterPath, QPalette, QPen,
    QPolygonF, QRadialGradient,
)
from PySide6.QtMultimedia import QAudioFormat
from PySide6.QtWidgets import QComboBox, QFormLayout, QWidget

from core.playback_audio_tap import PLAYBACK_SAMPLE_FRAMES


_MENU_TEXT = "Audio Visualizer"
_BAND_COUNT = 28
_ANALYSIS_FRAMES = 3072
_SPECTROGRAM = "Spectrogram"
_OSCILLOSCOPE = "Stereo Oscilloscope"
_WAVEFORM_RIBBON = "Waveform Ribbon"
_WATERFALL = "Waterfall"
_VISUAL_STYLES = (
    "Aurora", "Particle Sphere", "Radial", "Mirror", _SPECTROGRAM, _OSCILLOSCOPE, _WAVEFORM_RIBBON,
    _WATERFALL,
)
_HISTORY_INTERVAL = 0.04
_HISTORY_COLUMNS = 112
_WATERFALL_ROWS = 36
_WATERFALL_RASTER_WIDTH = 768
_SCOPE_RASTER_SIZE = 288
_RIBBON_RASTER_WIDTH = 1152
_AURORA_RASTER_WIDTH = 1920
_FRAME_INTERVAL_MS = 16
_PAUSE_SETTLE_SECONDS = 0.18
_PAUSE_HOLD_SECONDS = 5.0
_PAUSE_FADE_SECONDS = 0.35
_ARTWORK_BASELINES = {
    "Aurora": 0.92, "Particle Sphere": 0.60, "Radial": 0.60, "Mirror": 0.66,
    _SPECTROGRAM: 0.93, _WAVEFORM_RIBBON: 0.87, _WATERFALL: 0.93,
}
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
        self.visual_style = "Aurora"
        self._levels = np.zeros(_BAND_COUNT)
        self._target = np.zeros(_BAND_COUNT)
        self._peaks = np.zeros(_BAND_COUNT)
        self._window = np.hanning(_ANALYSIS_FRAMES)
        self._frequencies = np.geomspace(45, 14_000, _BAND_COUNT + 1)
        self._reference_db = None
        self._idle_ticks = 0
        self._accent_key = None
        self._band_colors = ()
        self._visual_time = 0.0
        self._last_tick = None
        self._effect_update_pending = False
        self._artwork_updated_last_tick = False
        self._band_positions = np.linspace(0, 1, _BAND_COUNT)
        self._radial_directions = tuple(
            (sin(angle), -cos(angle)) for angle in np.linspace(0, 2 * pi, _BAND_COUNT, endpoint=False)
        )
        self._sphere = None
        self._history = np.zeros((_BAND_COUNT, _HISTORY_COLUMNS), dtype=np.uint8)
        self._history_pending = np.zeros(_BAND_COUNT)
        self._history_time = None
        self._heat_palette = None
        self._heat_fade = np.minimum(np.linspace(0, 5, _HISTORY_COLUMNS), 1)
        self._trace = None
        self._trace_opacity = 0.0
        self._effect_image = QImage()
        self._pause_started = None
        self._collapse = 0.0
        self._flat_opacity = 0.0
        self.setObjectName("audioVisualizer")
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.setAutoFillBackground(False)
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(_FRAME_INTERVAL_MS)
        self._timer.timeout.connect(self._advance)

    def set_running(self, running):
        if self._timer.isActive() and (not running or self._pause_started is not None):
            self._timer.stop()
            self._last_tick = None
            self._pause_started = None
            self._collapse = 0.0
            self._flat_opacity = 0.0
            self._clear_frames()
            self.update()
        if running and not self._timer.isActive():
            self._last_tick = monotonic()
            self._timer.start(_FRAME_INTERVAL_MS)

    def pause(self):
        if self._pause_started is None and self._timer.isActive():
            if self._active_opacity() > 0:
                self._pause_started = monotonic()
            else:
                self.set_running(False)

    def _advance_pause(self, now):
        elapsed = now - self._pause_started
        if elapsed >= _PAUSE_HOLD_SECONDS + _PAUSE_FADE_SECONDS:
            self.set_running(False)
            return

        self._collapse = min(1.0, elapsed / _PAUSE_SETTLE_SECONDS)
        fade = max(0.0, elapsed - _PAUSE_HOLD_SECONDS) / _PAUSE_FADE_SECONDS
        self._flat_opacity = self._collapse * (1 - fade)
        interval = _FRAME_INTERVAL_MS
        if _PAUSE_SETTLE_SECONDS <= elapsed < _PAUSE_HOLD_SECONDS:
            interval = ceil((_PAUSE_HOLD_SECONDS - elapsed) * 1000)
        if self._timer.interval() != interval:
            self._timer.setInterval(interval)
        self.update()

    def set_visual_style(self, style):
        if self.visual_style != style:
            self.visual_style = style
            self._clear_frames()
            self.update()

    def _clear_frames(self):
        self._effect_update_pending = False
        self._artwork_updated_last_tick = False
        self._levels.fill(0)
        self._target.fill(0)
        self._peaks.fill(0)
        self._reference_db = None
        self._idle_ticks = 0
        self._history.fill(0)
        self._history_pending.fill(0)
        self._history_time = None
        self._trace = None
        self._trace_opacity = 0.0
        self._effect_image = QImage()

    def _decode_samples(self, data, channels, sample_format, limit):
        sample_type = _SAMPLE_TYPES.get(sample_format)
        if sample_type is None or channels <= 0:
            return None
        dtype, center, scale = sample_type
        samples = self._np.frombuffer(data, dtype=dtype).reshape(-1, channels)[-limit:]
        return (samples.astype(self._np.float32) - center) / scale

    def _spectrum(self, data, rate, channels, sample_format):
        np = self._np
        samples = self._decode_samples(data, channels, sample_format, _ANALYSIS_FRAMES)
        if rate <= 0 or samples is None or len(samples) < 256:
            return np.zeros(_BAND_COUNT)
        frames = len(samples)
        window = self._window if frames == len(self._window) else np.hanning(frames)
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
        now = monotonic()
        if self._pause_started is not None:
            self._advance_pause(now)
            return
        if self._last_tick is not None:
            self._visual_time += min(0.05, now - self._last_tick)
        self._last_tick = now
        changed = self._advance_playback(self._tap.take_latest())
        self._effect_update_pending |= changed

        # Repainting the artwork also repaints the transparent lyrics above it.
        # Keep sampling every tick, and carry a skipped update into the next tick.
        if self.over_artwork and self._artwork_updated_last_tick:
            self._artwork_updated_last_tick = False
        elif self._effect_update_pending:
            self._effect_update_pending = False
            self._artwork_updated_last_tick = self.over_artwork
            self.update(self._effect_rect())

    def _advance_playback(self, sample):
        if self.over_artwork and self.visual_style in (_OSCILLOSCOPE, _WAVEFORM_RIBBON):
            return self._advance_trace(sample)
        if sample is not None:
            self._target = self._spectrum(*sample)
            self._idle_ticks = 0
        else:
            self._idle_ticks += 1
            if self._idle_ticks > 6:
                self._target.fill(0)
        if self.over_artwork and self.visual_style in (_SPECTROGRAM, _WATERFALL):
            return self._advance_history()
        delta = self._target - self._levels
        self._levels += delta * self._np.where(delta > 0, 0.91, 0.44)
        # Let the peaks fall more slowly than the live signal.
        np = self._np
        was_visible = self._peaks.max() >= 0.025
        np.maximum(self._levels, self._peaks - 0.062, out=self._peaks)
        return bool(was_visible or self._peaks.max() >= 0.025)

    def _advance_history(self):
        self._np.maximum(self._history_pending, self._target, out=self._history_pending)
        now = self._visual_time
        if self._history_time is None:
            self._history_time = now - _HISTORY_INTERVAL

        waterfall = self.visual_style == _WATERFALL
        history = self._history[:, -(_WATERFALL_ROWS + 1):] if waterfall else self._history
        columns = int((now - self._history_time) / _HISTORY_INTERVAL + 1e-9)
        has_history = history.any()
        if columns:
            self._history_time += columns * _HISTORY_INTERVAL
            columns = min(columns, history.shape[1])
            active = self._history_pending.max() >= 0.025
            if active or has_history:
                history[:, :-columns] = history[:, columns:]
                history[:, -columns:] = (self._history_pending[::-1, None] * 255) if active else 0
            self._history_pending.fill(0)
            return bool(active or has_history)
        return bool(waterfall and has_history)

    def _advance_trace(self, sample):
        np = self._np
        if sample is not None:
            data, rate, channels, sample_format = sample
            ribbon = self.visual_style == _WAVEFORM_RIBBON
            seconds = 0.05 if ribbon else 0.018
            samples = self._decode_samples(data, channels, sample_format, max(1, round(rate * seconds)))
            if samples is not None and len(samples) > 1:
                if ribbon:
                    trace = self._ribbon_samples(samples, max(2, round(rate * 0.028)))
                else:
                    frames = samples[np.linspace(0, len(samples) - 1, min(384, len(samples)), dtype=int)]
                    left, right = frames[:, 0], frames[:, min(1, channels - 1)]
                    # Centered audio runs vertically. Stereo differences move the trace side to side.
                    trace = np.column_stack((left - right, left + right)) * 0.5
                trace -= trace.mean(axis=0)
                peak = float(np.abs(trace).max())
                if np.isfinite(peak) and peak > 1e-4:
                    scale = max(0.045, peak)
                    self._trace = trace * (0.92 / (scale ** 0.55 if ribbon and scale < 1 else scale))
                    self._trace_opacity = 1.0
                    self._idle_ticks = 0
                    return True
        self._idle_ticks += 1
        if self._trace_opacity and (sample is not None or self._idle_ticks > 6 or self._trace_opacity < 1):
            self._trace_opacity = max(0.0, self._trace_opacity - 0.22)
            return True
        return False

    def _ribbon_samples(self, samples, length):
        np = self._np
        mix = samples.mean(axis=1)
        if samples.shape[1] > 1:
            channel_energy = np.mean(samples * samples, axis=0)
            loudest = int(channel_energy.argmax())
            # Keep audible signals visible when their channels nearly cancel in the mix.
            if np.mean(mix * mix) < channel_energy[loudest] * 0.0625:
                mix = samples[:, loudest]

        length = min(length, len(mix))
        start = len(mix) - length
        # Align periodic signals to a recent rising crossing when a whole window follows it.
        crossing = np.flatnonzero((mix[:start] <= 0) & (mix[1:start + 1] > 0))
        if crossing.size:
            start = int(crossing[-1]) + 1
        window = mix[start:start + length]
        if length <= 480:
            return window.copy()
        edges = np.linspace(0, length, 481, dtype=int)
        return np.add.reduceat(window, edges[:-1]) / np.diff(edges)

    def showEvent(self, event):
        super().showEvent(event)
        self.visibility_changed.emit()

    def hideEvent(self, event):
        super().hideEvent(event)
        self.visibility_changed.emit()

    def _active_opacity(self):
        if self.over_artwork:
            if self.visual_style == _SPECTROGRAM:
                return float(self._history.any())
            if self.visual_style == _WATERFALL:
                return float(self._history[:, -(_WATERFALL_ROWS + 1):].any())
            if self.visual_style in (_OSCILLOSCOPE, _WAVEFORM_RIBBON):
                return self._trace_opacity
        return min(1.0, (float(self._peaks.max()) - 0.025) / 0.28)

    def paintEvent(self, event):
        if min(self.width(), self.height()) < 8:
            return
        active_opacity = self._active_opacity() * (1 - self._collapse)
        if active_opacity <= 0 and self._flat_opacity <= 0:
            return

        colors, light_dock = self._visual_colors()
        with QPainter(self) as painter:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            if self._pause_started is None:
                painter.setOpacity(active_opacity if self.over_artwork else 1.0)
                self._paint_active(painter, colors, light_dock)
                return

            line = self._flat_line()
            if active_opacity > 0:
                painter.save()
                painter.setOpacity(active_opacity)
                painter.translate(0, line.y1())
                painter.scale(1, 1 - self._collapse)
                painter.translate(0, -line.y1())
                self._paint_active(painter, colors, light_dock)
                painter.restore()
            self._paint_flat_line(painter, line, colors, light_dock)

    def _paint_active(self, painter, colors, light_dock):
        style = self.visual_style
        if not self.over_artwork:
            self._paint_seekbar(painter, colors, light_dock)
        elif style == _SPECTROGRAM:
            self._paint_spectrogram(painter, colors)
        elif style == _WATERFALL:
            self._paint_bounded_effect(painter, colors, self._paint_waterfall, _WATERFALL_RASTER_WIDTH)
        elif style == _OSCILLOSCOPE:
            self._paint_bounded_effect(painter, colors, self._paint_scope, _SCOPE_RASTER_SIZE, glow=True)
        elif style == _WAVEFORM_RIBBON:
            self._paint_bounded_effect(painter, colors, self._paint_ribbon, _RIBBON_RASTER_WIDTH, glow=True)
        elif style == "Particle Sphere":
            self._paint_sphere(painter, colors)
        elif style == "Radial":
            self._paint_radial(painter, colors)
        elif style == "Mirror":
            self._paint_mirror(painter, colors)
        else:
            self._paint_bounded_effect(painter, colors, self._paint_artwork, _AURORA_RASTER_WIDTH)

    def _flat_line(self):
        if not self.over_artwork:
            y = self.height() - 3
            return QLineF(0, y, self.width(), y)
        if self.visual_style == _OSCILLOSCOPE:
            center, radius = self._scope_geometry()
            return QLineF(center.x() - radius, center.y(), center.x() + radius, center.y())
        width = self.width()
        if self.visual_style in ("Particle Sphere", "Radial"):
            width = min(width, self.height()) * 0.48
        else:
            width *= 0.91
        left = (self.width() - width) * 0.5
        y = self.height() * _ARTWORK_BASELINES.get(self.visual_style, 0.92)
        return QLineF(left, y, left + width, y)

    def _paint_flat_line(self, painter, line, colors, light_dock):
        gradient = QLinearGradient(line.p1(), line.p2())
        for position, index, alpha in ((0, 0, 0), (0.18, 4, 115), (0.5, 14, 160), (0.82, 23, 115), (1, -1, 0)):
            tint = colors[index].darker(145) if light_dock and not self.over_artwork else QColor(colors[index])
            tint.setAlpha(alpha)
            gradient.setColorAt(position, tint)
        painter.setOpacity(self._flat_opacity)
        painter.setPen(QPen(QBrush(gradient), 1.4))
        painter.drawLine(line)

    def _effect_rect(self):
        if not self.over_artwork:
            return self.rect()
        if self.visual_style == _WATERFALL:
            # The highest ridge can reach 55% of the widget height.
            return self.rect().adjusted(0, max(0, int(self.height() * 0.55) - 6), 0, 0)
        if self.visual_style == _OSCILLOSCOPE:
            center, radius = self._scope_geometry()
            bounds = radius + 6
            return QRectF(center.x() - bounds, center.y() - bounds, 2 * bounds, 2 * bounds).toAlignedRect()
        if self.visual_style == _SPECTROGRAM:
            return self._spectrogram_rect().toAlignedRect().adjusted(-1, -1, 1, 1)
        if self.visual_style == _WAVEFORM_RIBBON:
            baseline, amplitude = self._ribbon_geometry()
            extent = amplitude + 8
            bounds = QRectF(0, baseline - extent, self.width(), 2 * extent)
            return bounds.toAlignedRect().intersected(self.rect())
        if self.visual_style == "Aurora":
            return self.rect().adjusted(0, max(0, int(self.height() * 0.56) - 12), 0, 0)
        return self.rect()

    def _paint_bounded_effect(self, painter, colors, render, raster_limit, *, glow=False):
        rect = self._effect_rect()
        span = max(rect.width(), rect.height())
        pixel_ratio = self.devicePixelRatioF()
        if not glow and span * pixel_ratio <= raster_limit:
            render(painter, colors)
            return

        # Compositing a bounded transparent surface keeps glowing strokes affordable on large displays.
        scale = min(pixel_ratio, raster_limit / span)
        width = max(1, round(rect.width() * scale))
        height = max(1, round(rect.height() * scale))
        if self._effect_image.width() != width or self._effect_image.height() != height:
            self._effect_image = QImage(width, height, QImage.Format.Format_ARGB32_Premultiplied)
        self._effect_image.fill(Qt.GlobalColor.transparent)
        with QPainter(self._effect_image) as effect:
            effect.setRenderHint(QPainter.RenderHint.Antialiasing)
            effect.scale(width / rect.width(), height / rect.height())
            effect.translate(-rect.x(), -rect.y())
            render(effect, colors)

        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        if glow:
            halo = self._effect_image.scaled(
                max(1, width // 3), max(1, height // 3),
                Qt.AspectRatioMode.IgnoreAspectRatio, Qt.TransformationMode.SmoothTransformation,
            )
            opacity = painter.opacity()
            painter.setOpacity(opacity * 0.42)
            painter.drawImage(rect, halo)
            painter.setOpacity(opacity)
        painter.drawImage(rect, self._effect_image)

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
            self._heat_palette = None
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
        height = self.height()
        amplitude = height * 0.28
        bottom = height * 0.92
        drift = self._np.sin(self._band_positions * 10 - self._visual_time * 1.4)
        live = self._spectrum_curve(self._levels * 0.84 + drift * 0.14, bottom, amplitude)
        trail = self._spectrum_curve(self._peaks * 0.76 - drift * 0.18, bottom - height * 0.09, amplitude)

        curtain = QPainterPath(live)
        curtain.lineTo(width, height)
        curtain.lineTo(0, height)
        curtain.closeSubpath()
        fill = QLinearGradient(0, bottom - amplitude, 0, height)
        tint = QColor(colors[_BAND_COUNT // 2])
        for position, alpha in ((0.0, 48), (0.58, 38), (1.0, 0)):
            tint.setAlpha(alpha)
            fill.setColorAt(position, tint)
        painter.fillPath(curtain, fill)

        for path, thickness, alpha in (
            (trail, 12.0, 26),
            (trail, 1.6, 112),
            (live, 22.0, 18),
            (live, 9.0, 45),
            (live, 2.4, 226),
        ):
            gradient = QLinearGradient(0, 0, width, 0)
            for position, index in ((0.0, 0), (0.34, 9), (0.70, 19), (1.0, -1)):
                tint = colors[index].lighter(135) if thickness < 3 else QColor(colors[index])
                tint.setAlpha(alpha)
                gradient.setColorAt(position, tint)
            painter.strokePath(path, QPen(QBrush(gradient), thickness))

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
                peak_y = max(1.0, bottom - area * float(peak) - 3)
                painter.drawRoundedRect(QRectF(left, peak_y, bar_width, 1.8), 0.9, 0.9)

    def _paint_sphere(self, painter, colors):
        np = self._np
        if self._sphere is None:
            # A Fibonacci sphere spaces the points evenly without polar seams.
            indices = np.arange(1250)
            y = 1 - 2 * (indices + 0.5) / len(indices)
            ring = np.sqrt(1 - y * y)
            angle = indices * (pi * (3 - np.sqrt(5)))
            bands = ((1 - y) * 0.5 * _BAND_COUNT).astype(int)
            self._sphere = (np.cos(angle) * ring, y, np.sin(angle) * ring, bands)
        x, y, z, bands = self._sphere
        angle = self._visual_time * 0.36
        depth = z * cos(angle) + x * sin(angle)
        spun_x = x * cos(angle) - z * sin(angle)
        spun_y = y * 0.96 - depth * 0.28
        depth = depth * 0.96 + y * 0.28
        energy = self._levels[bands]
        bass = float(self._levels[:7].max())
        radius = min(self.width(), self.height()) * (0.224 + 0.055 * bass)
        perspective = radius * (1 + 0.15 * energy) / (1 - 0.17 * depth)
        center = QPointF(self.width() * 0.5, self.height() * 0.60)
        px = center.x() + spun_x * perspective
        py = center.y() - spun_y * perspective

        halo = QRadialGradient(center, radius * 1.38)
        tint = QColor(colors[11])
        for position, alpha in ((0, 29), (0.63, 19), (1, 0)):
            tint.setAlpha(alpha)
            halo.setColorAt(position, tint)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(halo)
        painter.drawEllipse(center, halo.radius(), halo.radius())

        size = max(0.75, min(2.2, radius / 63))
        # Draw the far points first, then tint and brighten the front surface.
        boundaries = (-1.01, -0.34, 0.12, 0.55, 1.01)
        band_slices = (
            (bands < 9, 2),
            ((bands >= 9) & (bands < 19), 13),
            (bands >= 19, 25),
        )
        for i, (lower, upper) in enumerate(zip(boundaries, boundaries[1:])):
            layer = (depth >= lower) & (depth < upper)
            for band_slice, color_index in band_slices:
                mask = layer & band_slice
                points = QPolygonF([QPointF(a, b) for a, b in zip(px[mask], py[mask])])
                tint = colors[color_index].lighter(100 + 10 * i)
                tint.setAlpha(49 + 50 * i)
                pen = QPen(tint, size * (0.86 + 0.20 * i))
                pen.setCapStyle(Qt.PenCapStyle.RoundCap)
                painter.setPen(pen)
                painter.drawPoints(points)

    def _paint_radial(self, painter, colors):
        center = QPointF(self.width() * 0.5, self.height() * 0.60)
        radius = min(self.width(), self.height()) * 0.22
        thickness = max(1.2, radius * 0.048)
        bass = float(self._levels[:7].max())
        inner = radius * (0.82 + 0.12 * bass)
        gradient = QRadialGradient(center, radius)
        tint = QColor(colors[11])
        for position, alpha in ((0.0, 4), (0.70, 12), (0.88, 29), (1.0, 0)):
            tint.setAlpha(alpha)
            gradient.setColorAt(position, tint)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(gradient)
        painter.drawEllipse(center, radius, radius)

        for (dx, dy), level, peak, color in zip(self._radial_directions, self._levels, self._peaks, colors):
            direction = QPointF(dx, dy)
            start = center + direction * inner
            end = center + direction * (inner + radius * (0.08 + 0.67 * float(level)))
            tint = QColor(color)
            tint.setAlpha(32 + int(88 * level))
            painter.setPen(QPen(tint, thickness * 2.6, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QLineF(start, end))
            tint = color.lighter(125)
            tint.setAlpha(70 + int(160 * level))
            painter.setPen(QPen(tint, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
            painter.drawLine(QLineF(start, end))
            if peak > 0.16:
                tint.setAlpha(int(180 * peak))
                painter.setPen(QPen(tint, thickness * 0.75, Qt.PenStyle.SolidLine, Qt.PenCapStyle.RoundCap))
                painter.drawPoint(center + direction * (inner + radius * (0.15 + 0.67 * float(peak))))

    def _paint_mirror(self, painter, colors):
        center = self.height() * 0.66
        left = self.width() * 0.045
        spacing = (self.width() - 2 * left) / _BAND_COUNT
        bar_width = min(20.0, spacing * 0.56)
        amplitude = self.height() * 0.28
        line = QLinearGradient(left, 0, self.width() - left, 0)
        for position, index, alpha in ((0, 0, 0), (0.15, 4, 112), (0.85, 23, 112), (1, -1, 0)):
            tint = QColor(colors[index])
            tint.setAlpha(alpha)
            line.setColorAt(position, tint)
        painter.setPen(QPen(QBrush(line), 1))
        painter.drawLine(QLineF(left, center, self.width() - left, center))
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (level, color) in enumerate(zip(self._levels, colors)):
            extent = 1.5 + amplitude * float(level)
            x = left + (index + 0.5) * spacing - 0.5 * bar_width
            fill = QLinearGradient(0, center - extent, 0, center + extent)
            for position, alpha in ((0.0, 68), (0.22, 132), (0.5, 220), (0.78, 132), (1.0, 68)):
                tint = color.lighter(140) if position == 0.5 else QColor(color)
                tint.setAlpha(int(alpha * (0.30 + 0.70 * level)))
                fill.setColorAt(position, tint)
            painter.setBrush(fill)
            painter.drawRoundedRect(QRectF(x, center - extent, bar_width, extent * 2), bar_width / 2, bar_width / 2)

    def _spectrogram_rect(self):
        height = self.height()
        return QRectF(self.width() * 0.045, height * 0.61, self.width() * 0.91, height * 0.32)

    def _paint_spectrogram(self, painter, colors):
        np = self._np
        if self._heat_palette is None:
            stops = (
                (*colors[3].darker(210).getRgb()[:3], 0),
                (*colors[9].getRgb()[:3], 85),
                (*colors[21].lighter(116).getRgb()[:3], 185),
                (255, 247, 207, 247),
            )
            self._heat_palette = np.column_stack([
                np.interp(np.arange(256), (0, 72, 175, 255), channel)
                for channel in zip(*stops)
            ]).astype(np.uint8)

        rgba = self._heat_palette[self._history]
        rgba[:, :, 3] = rgba[:, :, 3] * self._heat_fade
        image = QImage(rgba.data, _HISTORY_COLUMNS, _BAND_COUNT, rgba.strides[0], QImage.Format.Format_RGBA8888)
        rect = self._spectrogram_rect()
        clip = QPainterPath()
        clip.addRoundedRect(rect, 7, 7)
        painter.setClipPath(clip)
        painter.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        painter.drawImage(rect, image)

    def _paint_waterfall(self, painter, colors):
        # The stored bands run high to low for the spectrogram image.
        frames = self._history[::-1, -(_WATERFALL_ROWS + 1):].T
        phase = min(1.0, max(0.0, (self._visual_time - self._history_time) / _HISTORY_INTERVAL))
        # Blend neighboring snapshots at fixed baselines to keep their geometry steady.
        layers = (frames[:-1] * (1 - phase) + frames[1:] * phase) / 255
        padded = self._np.pad(layers, ((0, 0), (1, 1)), mode="edge")
        smoothed = 0.12 * padded[:, :-2] + 0.76 * layers + 0.12 * padded[:, 2:]
        height = self.height()
        opacity = painter.opacity()
        stroke_opacity = min(1.0, painter.transform().m11() ** 0.5)
        for index, bands in enumerate(smoothed):
            strength = float(bands.max())
            if strength <= 0:
                continue

            depth = 1 - index / (_WATERFALL_ROWS - 1)
            near = (1 - depth) ** 1.25
            width = self.width() * (0.90 - 0.25 * depth)
            left = (self.width() - width) * 0.5
            baseline = height * (0.93 - 0.26 * depth)
            amplitude = height * (0.19 - 0.07 * depth)
            points = QPolygonF([
                QPointF(left + x * width, baseline - level * amplitude)
                for x, level in zip(self._band_positions, bands)
            ])
            gradient = QLinearGradient(left, 0, left + width, 0)
            alpha = round(170 * strength ** 0.8 * near * stroke_opacity)
            for position, band in ((0.0, 1), (0.33, 9), (0.68, 18), (1.0, 26)):
                tint = colors[band].lighter(107 + int(13 * near))
                tint.setAlpha(alpha)
                gradient.setColorAt(position, tint)

            brush = QBrush(gradient)
            if depth < 0.16:
                fill = QPainterPath()
                fill.addPolygon(points)
                fill.lineTo(left + width, baseline)
                fill.lineTo(left, baseline)
                fill.closeSubpath()
                painter.setOpacity(opacity * 0.15)
                painter.fillPath(fill, brush)
            painter.setOpacity(opacity)
            painter.setPen(QPen(brush, 0))
            painter.drawPolyline(points)

    def _scope_geometry(self):
        center = QPointF(self.width() * 0.5, self.height() * 0.68)
        radius = min(self.width() * 0.29, self.height() * 0.24)
        return center, radius

    def _paint_scope(self, painter, colors):
        center, radius = self._scope_geometry()
        grid = QColor(colors[15])
        grid.setAlpha(47)
        painter.setPen(QPen(grid, 1))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(center, radius, radius)
        painter.drawEllipse(center, radius * 0.5, radius * 0.5)
        painter.drawLine(QLineF(center.x() - radius, center.y(), center.x() + radius, center.y()))
        painter.drawLine(QLineF(center.x(), center.y() - radius, center.x(), center.y() + radius))

        trace = QPolygonF([
            QPointF(center.x() + x * radius, center.y() - y * radius)
            for x, y in self._trace
        ])
        gradient = QLinearGradient(center.x() - radius, center.y() - radius,
                                   center.x() + radius, center.y() + radius)
        for position, color in ((0, colors[0]), (0.5, colors[12].lighter(135)), (1, colors[-1])):
            gradient.setColorAt(position, color)
        # One-pixel strokes avoid constructing wide paths from hundreds of intersecting segments.
        painter.setPen(QPen(QBrush(gradient), 0))
        painter.drawPolyline(trace)

    def _ribbon_geometry(self):
        height = self.height()
        return height * 0.87, min(48, height * 0.105)

    def _paint_ribbon(self, painter, colors):
        left = self.width() * 0.045
        right = self.width() - left
        baseline, amplitude = self._ribbon_geometry()
        spacing = (right - left) / (len(self._trace) - 1)
        points = QPolygonF([
            QPointF(left + i * spacing, baseline - float(sample) * amplitude)
            for i, sample in enumerate(self._trace)
        ])
        fill = QPainterPath()
        fill.addPolygon(points)
        fill.lineTo(right, baseline)
        fill.lineTo(left, baseline)
        fill.closeSubpath()

        gradient = QLinearGradient(left, baseline, right, baseline)
        stops = ((0.0, 0, 0), (0.14, 4, 180), (0.54, 13, 250), (0.87, 23, 180), (1.0, -1, 0))
        for position, index, alpha in stops:
            tint = QColor(colors[index])
            tint.setAlpha(alpha)
            gradient.setColorAt(position, tint)
        brush = QBrush(gradient)
        painter.setOpacity(0.16)
        painter.fillPath(fill, brush)

        painter.setOpacity(0.94)
        painter.setPen(QPen(brush, 0))
        painter.drawPolyline(points)


class AudioVisualizerPlugin:
    def on_load(self, context):
        self.api = context.api
        self.player = None
        self.widget = None
        self.visible = bool(self.api.get_setting("visible", False))
        self.placement = self.api.get_setting("placement", "artwork")
        if self.placement not in ("artwork", "seekbar"):
            self.placement = "artwork"
        self.style = self.api.get_setting("style", "Aurora")
        if self.style not in _VISUAL_STYLES:
            self.style = "Aurora"
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
            self.widget.set_visual_style(self.style)
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
        showing = self.visible and self.widget.isVisible() and not self.player.isMinimized()
        running = showing and self.player.media_player.isPlaying()
        self.player.playback_audio_tap.set_collecting(running)
        if showing and not running:
            self.widget.pause()
        else:
            self.widget.set_running(running)

    def _settings(self, parent):
        widget = QWidget(parent)
        layout = QFormLayout(widget)
        widget.placement = QComboBox(widget)
        widget.placement.addItem("Over album artwork in Lyrics", "artwork")
        widget.placement.addItem("Below the seekbar", "seekbar")
        widget.placement.setCurrentIndex(widget.placement.findData(self.placement))
        layout.addRow("Placement", widget.placement)
        widget.style = QComboBox(widget)
        for style in _VISUAL_STYLES:
            widget.style.addItem(style, style)
        widget.style.setCurrentIndex(widget.style.findData(self.style))
        layout.addRow("Artwork visualizer", widget.style)

        def sync_style_enabled():
            widget.style.setEnabled(widget.placement.currentData() == "artwork")

        widget.placement.currentIndexChanged.connect(sync_style_enabled)
        sync_style_enabled()
        return widget

    def _apply_settings(self, widget):
        style = widget.style.currentData()
        if style != self.style:
            self.style = style
            self.api.set_setting("style", style)
            if self.widget is not None:
                self.widget.set_visual_style(style)
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
