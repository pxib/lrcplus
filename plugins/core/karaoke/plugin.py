"""In-player karaoke timing for the main lyric displays."""

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtMultimedia import QMediaPlayer

from core.settings import get_karaoke_settings


class KaraokePlugin:
    name = "Karaoke"
    version = "0.1.0"
    description = "Highlights timed lyric segments in the main player."

    def on_load(self, context):
        self.context = context
        self.window = None
        self.timer = None
        self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = None
        self._playback_rate = 1.0
        self.connected = False
        context.on("app_ready", self.on_app_ready)
        context.on("track_changed", self.on_track_changed)

    def on_app_ready(self, window):
        self.window = window
        if not self.connected:
            self.timer = QTimer(window)
            self.timer.setTimerType(Qt.TimerType.PreciseTimer)
            self.timer.timeout.connect(self.update_karaoke_position)
            player = window.media_player
            player.playbackStateChanged.connect(self.on_playback_state_changed)
            player.positionChanged.connect(self.on_media_position_changed)
            if hasattr(player, "playbackRateChanged"):
                player.playbackRateChanged.connect(self.on_playback_rate_changed)
            self.connected = True
        self._apply_sweep_style()
        self.on_playback_state_changed(window.media_player.playbackState())

    def on_track_changed(self, path=None, **_kwargs):
        self._apply_sweep_style()
        if self.window is not None:
            self.update_karaoke_position()

    def _apply_sweep_fps(self):
        if self.timer is None:
            return
        fps = get_karaoke_settings().get("sweep_fps", 60)
        self.timer.setInterval(max(1, round(1000 / max(1, int(fps)))))

    def _apply_sweep_style(self):
        if self.window is None:
            return
        style = get_karaoke_settings().get("sweep_style", "classic")
        for widget in (
            getattr(self.window, "lyric_widget", None),
            getattr(self.window, "scrolling_lyrics_widget", None),
        ):
            if widget is not None and hasattr(widget, "set_karaoke_sweep_style"):
                widget.set_karaoke_sweep_style(style)

    def _has_timed_segments(self):
        if self.window is None:
            return False
        path = getattr(self.window, "current_lyrics_path", None)
        if path is None or path.suffix.lower() != ".lrcx":
            return False
        return any(
            getattr(line, "has_karaoke", False)
            for line in getattr(self.window, "lyric_lines", [])
        )

    def on_media_position_changed(self, position_ms):
        try:
            self._position_anchor_ms = float(position_ms)
        except (TypeError, ValueError):
            return
        self._position_anchor_monotonic = time.monotonic()

    def on_playback_rate_changed(self, rate):
        try:
            self._playback_rate = float(rate)
        except (TypeError, ValueError):
            self._playback_rate = 1.0

    def _estimated_position_seconds(self):
        if self._position_anchor_monotonic is None:
            try:
                return float(self.window.media_player.position()) / 1000.0
            except (AttributeError, TypeError, ValueError):
                return 0.0
        elapsed = max(0.0, time.monotonic() - self._position_anchor_monotonic)
        return max(
            0.0,
            (self._position_anchor_ms + elapsed * 1000.0 * self._playback_rate)
            / 1000.0,
        )

    def on_playback_state_changed(self, state):
        if self.timer is None:
            return
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_sweep_fps()
            self._apply_sweep_style()
            try:
                self._position_anchor_ms = float(self.window.media_player.position())
            except (AttributeError, TypeError, ValueError):
                self._position_anchor_ms = 0.0
            self._position_anchor_monotonic = time.monotonic()
            self.timer.start()
            self.update_karaoke_position()
        else:
            self.timer.stop()
            self._position_anchor_monotonic = None

    def update_karaoke_position(self):
        if self.window is None or self.timer is None:
            return
        if not self._has_timed_segments():
            self._clear_karaoke_position()
            return
        self._apply_sweep_style()
        position = self._estimated_position_seconds()
        for widget in (
            getattr(self.window, "lyric_widget", None),
            getattr(self.window, "scrolling_lyrics_widget", None),
        ):
            if widget is not None:
                widget.set_karaoke_position(position)

    def _clear_karaoke_position(self):
        if self.window is None:
            return
        for widget in (
            getattr(self.window, "lyric_widget", None),
            getattr(self.window, "scrolling_lyrics_widget", None),
        ):
            if widget is not None:
                widget.set_karaoke_position(None)

    def on_unload(self, context):
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        self._clear_karaoke_position()
        self.connected = False


PLUGIN_CLASS = KaraokePlugin
