"""Player integration, output-tail cancellation, and sleep timer controls."""

from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QDialog

from core.settings import get_sleep_timer_settings
from ui.main_window import Player
from ui.sleep_timer_dialog import SleepTimerDialog


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    now = [0.0]
    window.sleep_timer._clock = lambda: now[0]
    window._timer_test_clock = now
    for name in ("update_background_for_audio", "_start_lyrics_search"):
        monkeypatch.setattr(window, name, lambda *args: None)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    qapp.processEvents()
    yield window
    window.close()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    qapp.processEvents()


def test_dialog_exposes_presets_custom_duration_and_saved_action(window):
    dialog = SleepTimerDialog(window, {"minutes": 47, "action": "finish_track"})
    try:
        assert dialog.duration_combo.currentData() is None
        assert dialog.minutes_spin.isEnabled()
        assert dialog.minutes_spin.value() == 47
        assert not dialog.end_song_button.isEnabled()
        assert not dialog.cancel_button.isEnabled()
        dialog.minutes_spin.setValue(17)
        dialog.start_button.click()
        assert window.sleep_timer.remaining_seconds == 17 * 60
        assert window.sleep_timer.action == "finish_track"
        assert window.sleep_timer_button.isChecked()
        assert get_sleep_timer_settings() == {"minutes": 17, "action": "finish_track"}
    finally:
        dialog.deleteLater()


def test_dialog_live_controls_update_countdown_and_cancel_without_expiring(window):
    window.sleep_timer.start(15)
    dialog = SleepTimerDialog(window, get_sleep_timer_settings())
    try:
        assert dialog.start_button.text() == "Replace timer"
        assert "15:00 remaining" in dialog.status_label.text()
        dialog.add_button.click()
        assert "20:00 remaining" in dialog.status_label.text()
        dialog.cancel_button.click()
        assert not window.sleep_timer.active
        assert not window._sleep_expired
        assert window.sleep_timer_button.text() == "Sleep"
    finally:
        dialog.deleteLater()


def test_escape_closes_the_dialog_without_cancelling_timer(window):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest

    window.sleep_timer.start(10)
    dialog = SleepTimerDialog(window, get_sleep_timer_settings())
    try:
        dialog.show()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert not dialog.isVisible()
        assert window.sleep_timer.active
    finally:
        dialog.deleteLater()


def test_end_of_song_control_and_paused_target(window):
    window.current_audio_path = Path("song.wav")
    dialog = SleepTimerDialog(window, get_sleep_timer_settings())
    try:
        assert dialog.end_song_button.isEnabled()
        dialog.end_song_button.click()
        assert window.sleep_timer.finishing
        assert window.sleep_timer_button.isChecked()
        assert "end of this song" in window.sleep_timer_button.toolTip()
        window.sleep_timer.check()
        assert window.sleep_timer.finishing  # Paused targets wait for resume.
    finally:
        dialog.deleteLater()


def test_end_of_song_control_tracks_queue_clearing(window):
    window.current_audio_path = Path("song.wav")
    window.playback_settings["play_mode"] = "playlist"
    dialog = SleepTimerDialog(window, get_sleep_timer_settings())
    try:
        window.clear_queue()
        assert not dialog.end_song_button.isEnabled()
    finally:
        dialog.deleteLater()


def test_preference_write_error_keeps_dialog_open_and_timer_inactive(window, monkeypatch):
    def fail(*args):
        raise OSError("Test disk is unavailable")

    monkeypatch.setattr(window, "_start_sleep_timer", fail)
    dialog = SleepTimerDialog(window, get_sleep_timer_settings())
    try:
        dialog.start_button.click()
        assert not window.sleep_timer.active
        assert "Could not save timer settings" in dialog.status_label.text()
        assert dialog.result() != QDialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()


def test_closed_dialogs_release_signal_connections(window, monkeypatch):
    def finish(dialog):
        window.sleep_timer.start(10)
        dialog.accept()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SleepTimerDialog, "exec", finish)
    for _ in range(5):
        window.open_sleep_timer()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not window.findChildren(SleepTimerDialog)
        window.sleep_timer.cancel()


@pytest.mark.parametrize("repeat", ["repeat_track", "repeat_all", "next_track"])
def test_finish_timer_wins_over_repeat_and_progression(window, monkeypatch, repeat):
    window.playback_settings["after_playback"] = repeat
    window.current_audio_path = Path("song.wav")
    window.sleep_timer.finish_current_track()
    monkeypatch.setattr(window.media_player, "mediaStatus", lambda: QMediaPlayer.MediaStatus.EndOfMedia)
    monkeypatch.setattr(window, "_playback_paths", lambda: pytest.fail("Timer must prevent advancement"))
    window.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    window.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert not window.sleep_timer.active
    assert window._sleep_expired


@pytest.mark.parametrize("source", ["next", "queue", "library", "remove"])
def test_manual_track_change_consumes_finish_timer_without_autoplay(window, monkeypatch, tmp_path, source):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QTreeWidgetItem

    paths = [tmp_path / name for name in ("first.wav", "second.wav")]
    for path in paths:
        path.touch()
    window.playback_settings["play_mode"] = "playlist"
    window.playlist = paths
    window.current_index = 0
    window.current_audio_path = paths[0]
    starts = []
    monkeypatch.setattr(window.media_player, "play", lambda: starts.append(True))
    monkeypatch.setattr(window.media_player, "isPlaying", lambda: True)
    window.sleep_timer.finish_current_track()
    if source == "next":
        window.next_track()
    elif source == "queue":
        window.play_queue_index(1)
    elif source == "remove":
        window.remove_queue_tracks([0])
    else:
        item = QTreeWidgetItem(["song"])
        item.setData(0, Qt.ItemDataRole.UserRole, str(paths[1]))
        window._play_library_item(item)
    assert not starts
    assert window.current_audio_path == paths[1]
    assert not window.sleep_timer.active


def test_stop_cancels_timer_and_close_never_restores_an_armed_deadline(window):
    window.sleep_timer.start(30)
    window.stop_playback()
    assert not window.sleep_timer.active
    window._start_sleep_timer(17, "finish_track")
    window.close()
    assert not window.sleep_timer.active
    assert not window.sleep_timer._timer.isActive()
    assert get_sleep_timer_settings() == {"minutes": 17, "action": "finish_track"}


def test_expiry_cancels_processed_eof_tail_and_stale_drain_notification(window, monkeypatch):
    worker = window._pcm_worker
    worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
    with worker._lock:
        worker._has_input = True
        worker._end_of_stream = True
    assert worker.finish(decoder_finished=True)
    window._pcm_end_pending = True
    original_volume = window.volume_slider.value()
    window.sleep_timer.start(1)
    window._timer_test_clock[0] = 60
    window.sleep_timer.check()
    assert not worker._controls.playing
    assert not window._pcm_end_pending
    assert not worker.finish(decoder_finished=True)
    monkeypatch.setattr(window, "_playback_paths", lambda: pytest.fail("Expired tail cannot advance"))
    window._pcm_output_drained()
    window.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
    assert window.volume_slider.value() == original_volume


def test_resume_clears_the_expiry_guard(window, monkeypatch):
    window.sleep_timer.start(1)
    window._timer_test_clock[0] = 60
    window.sleep_timer.check()
    assert window._sleep_expired
    monkeypatch.setattr(window.media_player, "isPlaying", lambda: True)
    window._sleep_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert not window._sleep_expired
