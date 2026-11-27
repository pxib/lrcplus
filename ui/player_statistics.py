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
            self._stream_url is None
            and self.workspace_stack.currentWidget() is not self._statistics_page
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
        self._shuffle_last_played = {}
        self._shuffle_history_pending = False
        self._shuffle_history_generation = self.statistics_store.generation
        self.statistics_store.shuffle_history_ready.connect(self._receive_shuffle_history)
        self.statistics_store.changed.connect(self._refresh_shuffle_history)
        self._refresh_shuffle_history()
        self._statistics_timer = QTimer(self)
        self._statistics_timer.setInterval(250)
        self._statistics_timer.timeout.connect(self._statistics_sample)
        self.media_player.mediaStatusChanged.connect(self._statistics_state_changed)
        self.media_player.seekStarting.connect(self._statistics_sample)
        self.media_player.seekFinished.connect(self._statistics_rebase)
        self.media_player.playbackRestarting.connect(self._statistics_restart_attempt)

    def _refresh_shuffle_history(self):
        if (getattr(self, "_statistics_closed", True)
                or getattr(self, "_shuffle_history_pending", False)):
            return
        if (not self.statistics.enabled
                or self.playback_settings.get("shuffle_mode") != "weighted"
                or not self.playback_settings.get("shuffle_prefer_older", True)):
            return
        self._shuffle_history_pending = True
        self.statistics_store.request_shuffle_history()

    def _receive_shuffle_history(self, generation, history):
        self._shuffle_history_pending = False
        if self._statistics_closed:
            return
        if generation != self.statistics_store.generation:
            self._refresh_shuffle_history()
            return
        self._shuffle_history_generation = generation
        self._shuffle_last_played = history

    def _weighted_shuffle_history(self):
        if (not self.statistics.enabled
                or self._shuffle_history_generation != self.statistics_store.generation):
            return {}
        return self._shuffle_last_played

    def _statistics_snapshot(self):
        media = self.media_player
        audible = (not getattr(self, "_statistics_editor_active", False)
                   and not getattr(self, "is_muted", False)
                   and self.volume_slider.value() > 0)
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
        if (snapshot["playing"] and self.statistics.enabled
                and not getattr(self, "_statistics_editor_active", False)):
            self._statistics_timer.start()
        else:
            self._statistics_timer.stop()
            self.statistics.flush()

    def _set_statistics_editor_active(self, active):
        # Credit listening up to the transition, then discard the editing
        # interval so closing the editor cannot backfill preview playback.
        self._statistics_checkpoint()
        self._statistics_editor_active = bool(active)
        self._statistics_rebase()
        self._statistics_state_changed()

    def _statistics_track_started(self):
        if self._statistics_closed:
            return
        station = getattr(self, "_station", None)
        if station is not None:
            self.statistics.start_track({
                "path_key": station.key, "station": True, "title": station.name,
                "country": station.country,
            }, **self._statistics_snapshot())
            self._statistics_state_changed()
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
        if not enabled:
            self._shuffle_last_played = {}
        self._refresh_shuffle_history()
        self._statistics_state_changed()
        if self._statistics_page is not None:
            self._statistics_page.sync_tracking()

    def _clear_statistics(self):
        answer = QMessageBox.question(
            self, "Clear listening statistics",
            "Delete all listening statistics? Your library, playlists, Recent list, and station history will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer != QMessageBox.StandardButton.Yes:
            return False
        self.statistics.clear(**self._statistics_snapshot())
        self.statistics_store.clear()
        if self._statistics_page is not None:
            self._statistics_page.refresh()
        return True

    def _statistics_failed(self, message):
        if not self._statistics_closed:
            self.statusBar().showMessage(message, 10000)
            if self._statistics_page is not None:
                self._statistics_page.show_error(message, persistent=True)

    def _close_statistics(self):
        if not self._statistics_closed:
            self._statistics_checkpoint()
            self._statistics_closed = True
            self._statistics_timer.stop()
            if self._statistics_page is not None:
                self._statistics_page.shutdown()
