"""Session-only A-B practice loop using the existing playback transport."""

from PySide6.QtCore import QTimer, Qt
from PySide6.QtWidgets import QMenu, QToolButton

from core.playlists import path_key


class PlayerABLoopMixin:
    def _init_ab_loop(self):
        self._ab_loop_memory = {}
        path = getattr(self, "current_audio_path", None)
        self._ab_track_key = path_key(path) if path else None
        self._ab_duration_pending = False
        self._ab_start = self._ab_end = None
        self._ab_seeking = False
        self._ab_timer = QTimer(self)
        self._ab_timer.setInterval(20)
        self._ab_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._ab_timer.timeout.connect(self._check_ab_loop)
        self.ab_loop_button = QToolButton()
        self.ab_loop_button.setObjectName("dockAction")
        self.ab_loop_button.setAccessibleName("A-B repeat")
        self.ab_loop_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.ab_loop_button)
        self.ab_mark_a_action = menu.addAction("Set A at current position", self._mark_ab_start)
        self.ab_mark_b_action = menu.addAction("Set B and loop", self._mark_ab_end)
        menu.addSeparator()
        self.ab_clear_action = menu.addAction("Clear A-B loop", self._clear_ab_loop)
        self.ab_loop_button.setMenu(menu)
        menu.aboutToShow.connect(self._refresh_ab_loop)
        self.media_player.durationChanged.connect(self._ab_duration_changed)
        self._refresh_ab_loop()

    def _remember_ab_loop(self):
        if self._ab_track_key is None:
            return
        if self._ab_start is None:
            self._ab_loop_memory.pop(self._ab_track_key, None)
        else:
            self._ab_loop_memory[self._ab_track_key] = (self._ab_start, self._ab_end)

    def _suspend_ab_loop(self):
        """Leave the outgoing song without forgetting its markers."""
        self._remember_ab_loop()
        self._ab_timer.stop()
        self._ab_start = self._ab_end = None
        self._ab_track_key = None
        self._ab_duration_pending = False
        self._refresh_ab_loop()

    def _restore_ab_loop(self, path):
        # Called after setSource so the outgoing track's duration cannot trim
        # a remembered range on the incoming track.
        self._ab_track_key = path_key(path)
        self._ab_start, self._ab_end = self._ab_loop_memory.get(self._ab_track_key, (None, None))
        self._ab_duration_pending = self._ab_start is not None
        self._ab_duration_changed(self.media_player.duration())

    def _ab_duration_changed(self, duration):
        if duration > 0 and self._ab_start is not None:
            self._ab_duration_pending = False
            previous = (self._ab_start, self._ab_end)
            if self._ab_start >= duration:
                self._ab_start = self._ab_end = None
            elif self._ab_end is not None and self._ab_end > duration:
                self._ab_end = None
            self._remember_ab_loop()
            if self._ab_end is not None:
                self._ab_timer.start()
            else:
                self._ab_timer.stop()
            if previous != (self._ab_start, self._ab_end):
                self._invalidate_gapless_preload()
        self._refresh_ab_loop()

    @staticmethod
    def _ab_time(value):
        minutes, ms = divmod(value, 60000)
        return f"{minutes}:{ms / 1000:06.3f}"

    def _refresh_ab_loop(self):
        ready = bool(getattr(self, "current_audio_path", None)) and self.media_player.duration() > 0
        start, end = self._ab_start, self._ab_end
        slider = getattr(self, "seek_slider", None)
        if slider is not None:
            slider.set_loop_range(start, end)
        self.ab_mark_a_action.setEnabled(ready)
        self.ab_mark_b_action.setEnabled(ready and start is not None)
        self.ab_clear_action.setEnabled(start is not None)
        self.ab_loop_button.setText("A-B (on)" if end is not None else "A..." if start is not None else "A-B")
        if end is not None:
            tip = f"Looping {self._ab_time(start)} - {self._ab_time(end)}"
        elif start is not None:
            tip = f"A: {self._ab_time(start)}. Set B to start looping."
        else:
            tip = "Repeat a section: set A, then set B."
        self.ab_loop_button.setToolTip(f"{tip}\nMarkers stay with each song until LRCPlus closes.")
        self.ab_mark_a_action.setText("Set A at current position" if start is None else f"Reset A (currently {self._ab_time(start)})")
        self.ab_mark_b_action.setText("Set B and loop" if end is None else f"Reset B (currently {self._ab_time(end)})")

    def _mark_ab_start(self):
        if not getattr(self, "current_audio_path", None) or self.media_player.duration() <= 0:
            return
        self._ab_timer.stop()
        self._ab_start = max(0, min(self.media_player.position(), self.media_player.duration()))
        self._ab_end = None
        self._ab_duration_pending = False
        self._remember_ab_loop()
        self._invalidate_gapless_preload()
        self._refresh_ab_loop()

    def _mark_ab_end(self):
        if self._ab_start is None or self.media_player.duration() <= 0:
            return
        end = min(self.media_player.position(), self.media_player.duration())
        if end - self._ab_start < 100:
            self.statusBar().showMessage("Set B at least 0.1 seconds after A.", 4000)
            return
        self._ab_end = end
        self._remember_ab_loop()
        self._invalidate_gapless_preload()
        self._seek_from_waveform(self._ab_start)
        self._ab_timer.start()
        self._refresh_ab_loop()

    def _clear_ab_loop(self):
        self._ab_timer.stop()
        self._ab_start = self._ab_end = None
        self._ab_duration_pending = False
        self._remember_ab_loop()
        self._invalidate_gapless_preload()
        self._refresh_ab_loop()

    def _check_ab_loop(self, at_end=False):
        if (self._ab_end is None or self._ab_seeking or self._ab_duration_pending
                or getattr(self, "_closing", False) or getattr(self, "_sleep_expired", False)):
            return False
        timer = getattr(self, "sleep_timer", None)
        if timer is not None:
            timer.check()
            if timer.finishing or getattr(self, "_sleep_expired", False):
                return False
        if not at_end and not self.media_player.isPlaying():
            return False
        if not at_end and self.media_player.position() < self._ab_end:
            return False
        self._ab_seeking = True
        try:
            self._seek_from_waveform(self._ab_start)
            if at_end:
                self.media_player.play()
        finally:
            self._ab_seeking = False
        return True
