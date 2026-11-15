"""Wire the statistics recorder to audible playback, not file-load events."""

from pathlib import Path
import os

from PySide6.QtCore import QTimer
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMessageBox

from core.listening_statistics import ListeningStatistics
from core.playlists import path_key
from core.settings import get_statistics_settings, save_statistics_settings
from core.statistics_store import StatisticsStore


class PlayerStatisticsMixin:
    def _sync_waveform_visibility(self):
        from lyrics.waveform import find_ffmpeg

        self.waveform_widget.setVisible(
            self.workspace_stack.currentWidget() is not self.statistics_page
            and self.playback_settings.get("show_waveform", True)
            and find_ffmpeg() is not None
        )

    def _init_statistics(self):
        self._statistics_closed = False
        self.statistics_store = StatisticsStore(self)
        self.statistics_store.failed.connect(self._statistics_failed)
        self.statistics = ListeningStatistics(
            self.statistics_store.record, enabled=get_statistics_settings()["enabled"],
        )
        self._statistics_timer = QTimer(self)
        self._statistics_timer.setInterval(250)
        self._statistics_timer.timeout.connect(self._statistics_sample)
        self.media_player.mediaStatusChanged.connect(self._statistics_state_changed)
        self.media_player.seekStarting.connect(self._statistics_sample)
        self.media_player.seekFinished.connect(self._statistics_rebase)
        self.media_player.playbackRestarting.connect(self._statistics_restart_attempt)

    def _statistics_snapshot(self):
        media = self.media_player
        audible = not getattr(self, "is_muted", False) and self.volume_slider.value() > 0
        duration = media.duration()
        worker = getattr(self, "_pcm_worker", None)
        progress = worker.output_progress() if worker is not None else None
        if progress is not None:
            generation, position, playing = progress
            return dict(position_ms=position, playing=playing, audible=audible,
                        rate=1, clock_id=("pcm", generation), duration_ms=duration)
        status = media.mediaStatus()
        playable = status not in {QMediaPlayer.MediaStatus.InvalidMedia,
                                 QMediaPlayer.MediaStatus.NoMedia, QMediaPlayer.MediaStatus.LoadingMedia,
                                 QMediaPlayer.MediaStatus.StalledMedia}
        position = media.position()
        if getattr(media, "_gapless_active", False) and duration > 0:
            position = min(position, duration)
        return dict(position_ms=position, playing=media.isPlaying() and playable,
                    audible=audible, rate=getattr(self, "playback_speed", 1),
                    clock_id="native", duration_ms=duration)

    def _statistics_sample(self):
        if getattr(self, "_statistics_closed", True) or not self.statistics.track:
            return
        self.statistics.sample(**self._statistics_snapshot())

    def _statistics_rebase(self):
        if not getattr(self, "_statistics_closed", True):
            self.statistics.rebase(**self._statistics_snapshot())

    def _statistics_state_changed(self, *args):
        if self._statistics_closed:
            return
        self._statistics_sample()
        snapshot = self._statistics_snapshot()
        if snapshot["playing"] and self.statistics.enabled:
            self._statistics_timer.start()
        else:
            self._statistics_timer.stop()
            self.statistics.flush()

    def _statistics_track_started(self):
        if self._statistics_closed:
            return
        if not self.current_audio_path:
            self.statistics.start_track(None, **self._statistics_snapshot())
            return
        path = str(self.current_audio_path)
        key = path_key(path)
        track = next((track for track in self.library_tracks
                      if os.path.normcase(os.path.normpath(track.path)) == key), None)
        metadata = track.to_dict() if track else {"path": path, "title": Path(path).stem}
        metadata.update(path_key=key, metadata_complete=track is not None)
        self.statistics.start_track(metadata, **self._statistics_snapshot())
        if track and track.duration:
            self.statistics.duration_ms = track.duration * 1000
        self._statistics_state_changed()

    def _statistics_checkpoint(self):
        self._statistics_sample()
        if not getattr(self, "_statistics_closed", True):
            self.statistics.flush()

    def _statistics_restart_attempt(self):
        self._statistics_checkpoint()
        self._statistics_track_started()

    def _set_statistics_enabled(self, enabled):
        if enabled == self.statistics.enabled:
            return
        save_statistics_settings({"enabled": enabled})
        self._statistics_checkpoint()
        self.statistics.set_enabled(enabled, **self._statistics_snapshot())
        self._statistics_state_changed()
        self.statistics_page.sync_tracking()

    def _clear_statistics(self):
        answer = QMessageBox.question(
            self, "Clear listening statistics",
            "Delete all listening statistics? Your library, playlists, and Recent list will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.statistics.clear(**self._statistics_snapshot())
        self.statistics_store.clear()
        self.statistics_page.refresh()
        return True

    def _statistics_failed(self, message):
        if not self._statistics_closed:
            self.statusBar().showMessage(message, 10000)
            self.statistics_page.show_error(message, persistent=True)

    def _close_statistics(self):
        if not self._statistics_closed:
            self._statistics_checkpoint()
            self._statistics_closed = True
            self._statistics_timer.stop()
            self.statistics_page.shutdown()
