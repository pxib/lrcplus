"""
Simple Karaoke plugin.

Uses timed LRCX segments:
    [00:10.000]first <00:10.500>second <00:11.000>third

Segments become highlighted when playback reaches their timestamps.
"""

import time

from PySide6.QtCore import QTimer, Qt
from PySide6.QtMultimedia import QMediaPlayer

from core.settings import get_karaoke_settings


class KaraokePlugin:
    name = "Karaoke"
    version = "0.1.0"
    description = "Simple timed highlighting for LRCX lyrics."

    def on_load(self, context):
        self.context = context
        self.window = None
        self.connected = False
        self._position_anchor_monotonic = None
        self.karaoke_timer = None
        self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = None
        self._playback_rate = 1.0
        context.on("app_ready", self.on_app_ready)
        context.log(f"Loaded {self.name} {self.version}")

    def on_app_ready(self, window):
        self.window = window

        if not self.connected:
            self.karaoke_timer = QTimer(self.window)
            self.karaoke_timer.setTimerType(
                Qt.TimerType.PreciseTimer
            )
            self._apply_sweep_fps()
            self._apply_sweep_style()
            self.karaoke_timer.timeout.connect(
                self.update_karaoke_position
            )

            window.media_player.playbackStateChanged.connect(
                self.on_playback_state_changed
            )
            window.media_player.positionChanged.connect(
                self.on_media_position_changed
            )
            if hasattr(window.media_player, "playbackRateChanged"):
                window.media_player.playbackRateChanged.connect(
                    self.on_playback_rate_changed
                )

            # Start immediately if the plugin is loaded while audio is already playing.
            self.on_playback_state_changed(
                window.media_player.playbackState()
            )
            self.connected = True

    def _apply_sweep_fps(self):
        if self.karaoke_timer is None:
            return
        fps = get_karaoke_settings().get("sweep_fps", 60)
        self.karaoke_timer.setInterval(
            max(1, round(1000 / max(1, int(fps))))
        )

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

        lines = getattr(self.window, "lyric_lines", [])
        # Karaoke state is explicit. The editor may rebuild/split/merge segments,
        # so inferring this from segment timestamps makes playback depend on mutable
        # editor state and can disable highlighting entirely.
        return any(
            getattr(line, "has_karaoke", False)
            for line in lines
        )

    def on_media_position_changed(self, position_ms):
        """Re-anchor the smooth karaoke clock to Qt's media position."""
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
            # First frame: use the media player's current reported position.
            try:
                return (
                    float(self.window.media_player.position()) / 1000.0
                    if self.window is not None
                    else 0.0
                )
            except (TypeError, ValueError):
                return 0.0

        elapsed = max(
            0.0,
            time.monotonic() - self._position_anchor_monotonic,
        )
        return max(
            0.0,
            (
                self._position_anchor_ms
                + elapsed * 1000.0 * self._playback_rate
            ) / 1000.0,
        )

    def on_playback_state_changed(self, state):
        if self.karaoke_timer is None:
            return

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_sweep_fps()
            self._apply_sweep_style()
            try:
                self._position_anchor_ms = float(
                    self.window.media_player.position()
                )
            except (TypeError, ValueError):
                self._position_anchor_ms = 0.0
            self._position_anchor_monotonic = time.monotonic()
            self.karaoke_timer.start()
            self.update_karaoke_position()
        else:
            # Pause must freeze the current karaoke frame.  Clearing the
            # position makes the lyric row fall back to its ordinary
            # "current line" highlight state, which fills the entire line.
            #
            # Keep the last authoritative playback position on the widgets;
            # simply stop the animation timer.  The Playing branch re-anchors
            # from QMediaPlayer.position() before continuing, so resume cannot
            # drift from the frozen frame.
            self.karaoke_timer.stop()
            self._position_anchor_monotonic = None

    def update_karaoke_position(self):
        if self.window is None or self.karaoke_timer is None:
            return

        # Karaoke animation has its own frame clock. QMediaPlayer.positionChanged
        # is a coarse state-change signal (~10 Hz on this backend) and is not a
        # suitable animation clock.
        if not self._has_timed_segments():
            self._clear_karaoke_position()
            return

        self._apply_sweep_style()
        position = self._estimated_position_seconds()
        self.window.lyric_widget.set_karaoke_position(position)
        self.window.scrolling_lyrics_widget.set_karaoke_position(position)

    def _clear_karaoke_position(self):
        if self.window is None:
            return
        self.window.lyric_widget.set_karaoke_position(None)
        self.window.scrolling_lyrics_widget.set_karaoke_position(None)

    def on_unload(self, context):
        if self.karaoke_timer is not None:
            self.karaoke_timer.stop()
            self.karaoke_timer.deleteLater()
            self.karaoke_timer = None

        self._clear_karaoke_position()
        self.connected = False

        context.log(f"Unloaded {self.name}")


PLUGIN_CLASS = KaraokePlugin
