"""Sleep timer integration for the main player and each audio output route."""

from core.settings import get_sleep_timer_settings, save_sleep_timer_settings
from core.sleep_timer import SleepTimer


def countdown_text(seconds):
    minutes, seconds = divmod(seconds, 60)
    return f"{minutes}:{seconds:02d}"


class PlayerSleepTimerMixin:
    def _init_sleep_timer(self):
        self._sleep_expired = False
        self._sleep_was_finishing = False
        self.sleep_timer = SleepTimer(self, is_playing=self._sleep_audio_playing)
        self.sleep_timer.changed.connect(self._refresh_sleep_timer)
        self.sleep_timer.pause_requested.connect(self._pause_for_sleep_timer)
        self.media_player.before_pump = self.sleep_timer.check
        self.media_player.playbackStateChanged.connect(self._sleep_playback_state_changed)

    def _sleep_audio_playing(self):
        if getattr(self, "_stream_url", None) is not None and self.media_player.duration() <= 0:
            # A live stream may never reach EOF, so the deadline acts as Pause.
            return False
        return not self._sleep_gapless_boundary_reached() and (
            self.media_player.isPlaying() or getattr(self, "_pcm_end_pending", False)
        )

    def _sleep_gapless_boundary_reached(self):
        if getattr(self.media_player, "rust_gapless_boundary", False):
            return True
        boundary = getattr(self.media_player, "_boundary_us", None)
        sink = getattr(self.media_player, "_sink", None)
        return boundary is not None and sink is not None and sink.processedUSecs() >= boundary

    def _sleep_playback_state_changed(self, state):
        if self.media_player.isPlaying():
            # An explicit new Play resumes normal playback after expiry.
            self._sleep_expired = False

    def _refresh_sleep_timer(self):
        timer = self.sleep_timer
        if timer.active:
            self._sleep_expired = False
        if self._sleep_was_finishing != timer.finishing:
            self._sleep_was_finishing = timer.finishing
            # This also discards successor samples already fed into a gapless
            # sink, restarting the decoder at the audible position if needed.
            self._invalidate_gapless_preload()
        button = getattr(self, "sleep_timer_button", None)
        if button is None:
            return
        if timer.finishing:
            text, detail = "Sleep: song", "Playback pauses at the end of this song"
        elif timer.active:
            remaining = countdown_text(timer.remaining_seconds)
            text = f"Sleep {remaining}"
            action = "finish the current song" if timer.action == "finish_track" else "pause playback"
            detail = f"Sleep timer: {remaining} remaining, then {action}"
        else:
            text, detail = "Sleep", "Set a sleep timer"
        button.setText(text)
        button.setChecked(timer.active)
        button.setToolTip(detail)
        button.setAccessibleName(detail)

    def _start_sleep_timer(self, minutes, action):
        save_sleep_timer_settings({"minutes": minutes, "action": action})
        self.sleep_timer.start(minutes, action)

    def _sleep_at_end_of_song(self):
        if self.current_audio_path:
            if self._sleep_gapless_boundary_reached():
                # The sink clock can reach the boundary before the next GUI
                # pump updates the track. Pause there instead of seeking a
                # replacement decoder beyond the old song's EOF.
                self.sleep_timer.cancel()
                self._pause_for_sleep_timer()
            else:
                self.sleep_timer.finish_current_track()

    def _pause_for_sleep_timer(self):
        if hasattr(self, "statistics"):
            self._statistics_checkpoint()
        self._sleep_expired = True
        tail_pending = getattr(self, "_pcm_end_pending", False)
        worker = getattr(self, "_pcm_worker", None)
        if worker is not None:
            worker.set_playing(False)
        self.media_player.pause()
        if tail_pending:
            # Native playback has already stopped at EOF; discard a queued
            # processed tail and its drain notification before it can resume.
            self._flush_pcm_output()
        self.statusBar().showMessage("Sleep timer finished — playback paused.", 7000)

    def _sleep_before_track_load(self):
        timer = self.sleep_timer
        was_active = timer.active
        timer.check()
        if timer.finishing:
            timer.complete()
        # A manual skip may select another song, but must leave it stopped when
        # it consumes an end-of-song timer. Normal later Play is still allowed.
        return not (was_active and self._sleep_expired)

    def _sleep_before_advance(self):
        self.sleep_timer.check()
        if self.sleep_timer.finishing:
            self.sleep_timer.complete()
        return self._sleep_expired

    def open_sleep_timer(self):
        from ui.sleep_timer_dialog import SleepTimerDialog

        self.sleep_timer.check()
        self._refresh_sleep_timer()
        dialog = SleepTimerDialog(self, get_sleep_timer_settings())
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
