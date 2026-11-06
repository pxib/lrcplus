"""In-player karaoke timing for the main lyric displays."""

import time

from PySide6.QtCore import QEvent, QObject, QTimer, Qt
from PySide6.QtMultimedia import QMediaPlayer

from core.settings import get_karaoke_settings, apply_karaoke_settings


class _DisplayVisibilityFilter(QObject):
    def __init__(self, callback, parent):
        super().__init__(parent)
        self._callback = callback

    def eventFilter(self, watched, event):
        if event.type() in (QEvent.Type.Show, QEvent.Type.Hide):
            self._callback()
        return False


class KaraokePlugin:
    name = "Karaoke"
    version = "1.0.0"
    description = "Highlights timed lyric segments in the main player."

    def on_load(self, context):
        self.context = context
        self.window = None
        self.timer = None
        self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = None
        self._playback_rate = 1.0
        self._playing = False
        self._visibility_filter = None
        self._watched_displays = []
        # Reading settings parses the whole library; keep it off the frame timer.
        self._settings = get_karaoke_settings()
        self.connected = False
        context.on("app_ready", self.on_app_ready)
        context.on("track_changed", self.on_track_changed)
        context.on("karaoke_settings_changed", self.on_karaoke_settings_changed)

    def on_karaoke_settings_changed(self):
        self._settings = get_karaoke_settings()
        self._apply_sweep_fps()
        self._apply_sweep_style()

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
            self._visibility_filter = _DisplayVisibilityFilter(
                self._sync_timer_with_visibility, window
            )
            created = getattr(window, "lyrics_renderers_created", None)
            if created is not None:
                created.connect(self._watch_displays)
            self._watch_displays()
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
        fps = self._settings.get("sweep_fps", 60)
        self.timer.setInterval(max(1, round(1000 / max(1, int(fps)))))

    def _apply_sweep_style(self):
        widgets = self._visible_displays()
        if not widgets:
            return
        for widget in widgets:
            apply_karaoke_settings(widget, self._settings)

    def _visible_displays(self):
        return [widget for widget in self._existing_displays() if widget.isVisible()]

    def _existing_displays(self):
        if self.window is None or getattr(self.window, "_lyrics_renderers_ready", True) is False:
            return []
        return [
            widget for widget in (
                getattr(self.window, "lyric_widget", None),
                getattr(self.window, "scrolling_lyrics_widget", None),
            ) if widget is not None
        ]

    def _watch_displays(self):
        for widget in self._existing_displays():
            if widget not in self._watched_displays:
                self._watched_displays.append(widget)
                widget.installEventFilter(self._visibility_filter)
        self._apply_sweep_style()
        self._sync_timer_with_visibility()

    def _sync_timer_with_visibility(self):
        if self.timer is None:
            return
        # A newly visible display also needs its settings while playback is paused.
        self._apply_sweep_style()
        if not self._playing or not self._visible_displays():
            self.timer.stop()
            return
        if not self._has_timed_segments():
            self.timer.stop()
            self._clear_karaoke_position()
            return
        if self.timer.isActive():
            return
        self._apply_sweep_fps()
        self._position_anchor_ms = float(self.window.media_player.position())
        self._position_anchor_monotonic = time.monotonic()
        self.timer.start()
        self.update_karaoke_position()

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
        self._sync_timer_with_visibility()

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
        self._playing = state == QMediaPlayer.PlaybackState.PlayingState
        if not self._playing:
            self._position_anchor_monotonic = None
        self._sync_timer_with_visibility()

    def update_karaoke_position(self):
        if self.window is None or self.timer is None:
            return
        widgets = self._visible_displays()
        if not widgets:
            self.timer.stop()
            return
        if not self._has_timed_segments():
            self.timer.stop()
            self._clear_karaoke_position()
            return
        # Segment timestamps use the same offset lyric clock as line selection.
        position = self._estimated_position_seconds() - getattr(
            self.window, "lyrics_offset_seconds", 0.0
        )
        for widget in widgets:
            widget.set_karaoke_position(position)

    def _clear_karaoke_position(self):
        if self.window is None:
            return
        for widget in self._existing_displays():
            widget.set_karaoke_position(None)

    def on_unload(self, context):
        if self.connected:
            player = self.window.media_player
            connections = (
                ("playbackStateChanged", self.on_playback_state_changed),
                ("positionChanged", self.on_media_position_changed),
                ("playbackRateChanged", self.on_playback_rate_changed),
            )
            for name, callback in connections:
                signal = getattr(player, name, None)
                if signal is not None:
                    try:
                        signal.disconnect(callback)
                    except (RuntimeError, TypeError):
                        pass
        created = getattr(self.window, "lyrics_renderers_created", None)
        if created is not None:
            try:
                created.disconnect(self._watch_displays)
            except (RuntimeError, TypeError):
                pass
        if self._visibility_filter is not None:
            for widget in self._watched_displays:
                widget.removeEventFilter(self._visibility_filter)
            self._visibility_filter.deleteLater()
            self._visibility_filter = None
            self._watched_displays = []
        if self.timer is not None:
            self.timer.stop()
            self.timer.deleteLater()
            self.timer = None
        self._clear_karaoke_position()
        self._playing = False
        self.connected = False
        self.window = None


PLUGIN_CLASS = KaraokePlugin
