"""Statistics follow the player's transport, preferences, and workspace."""

from datetime import date, datetime, timedelta
from types import SimpleNamespace
import time

import pytest
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMessageBox

from core.music_library import LibraryTrack
from core.settings import get_statistics_settings
from ui.main_window import Player
from ui.settings_dialog import SettingsDialog


@pytest.fixture
def playback(qapp, monkeypatch, tmp_path):
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    state = SimpleNamespace(now=0.0, position=0, playing=True, duration=120000,
                            status=QMediaPlayer.MediaStatus.BufferedMedia)
    wall = datetime.now().astimezone().replace(hour=12, minute=0, second=0, microsecond=0)
    window.statistics._clock = lambda: state.now
    window.statistics._wall_clock = lambda: wall + timedelta(seconds=state.now)
    window.statistics._last_flush = 0
    monkeypatch.setattr(window.media_player, "position", lambda: state.position)
    monkeypatch.setattr(window.media_player, "duration", lambda: state.duration)
    monkeypatch.setattr(window.media_player, "isPlaying", lambda: state.playing)
    monkeypatch.setattr(window.media_player, "mediaStatus", lambda: state.status)
    monkeypatch.setattr(window.media_player, "_set_position", lambda value: setattr(state, "position", value))

    def pause():
        state.playing = False
        window.media_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PausedState)

    monkeypatch.setattr(window.media_player, "pause", pause)
    window.current_audio_path = tmp_path / "song.wav"
    window.library_tracks = [LibraryTrack(str(window.current_audio_path), "Song", "Artist", "Album", duration=120)]
    window._statistics_track_started()
    qapp.processEvents()
    yield window, state
    window.close()
    window.deleteLater()
    qapp.processEvents()


def totals(window):
    window._statistics_checkpoint()
    return window.statistics_store.snapshot().result(timeout=5)["summary"]


def wait_for(qapp, condition):
    deadline = time.monotonic() + 5
    while not condition() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert condition()


def test_real_transport_seeks_exclude_even_small_position_jumps(playback):
    window, state = playback
    state.now = 10
    state.position = 10000
    window.media_player.setPosition(10200)
    state.now = 20
    state.position = 20200
    assert totals(window)["listened_ms"] == 20000
    window.media_player.setPosition(0)
    state.now = 30
    state.position = 10000
    assert totals(window)["plays"] == 1


def test_mute_and_internal_native_muting_are_distinguished(playback):
    window, state = playback
    window.audio_output.setVolume(0)  # Processed routes mute this internally.
    state.now = 10
    state.position = 10000
    window.volume_slider.setValue(0)
    state.now = 50
    state.position = 50000
    window.volume_slider.setValue(80)
    state.now = 60
    state.position = 60000
    assert totals(window)["listened_ms"] == 20000


def test_sleep_expiry_stops_counting_immediately(playback):
    window, state = playback
    window.sleep_timer._clock = lambda: state.now
    window.sleep_timer.start(1)
    state.now = 60
    state.position = 60000
    window.sleep_timer.check()
    assert not state.playing
    state.now = 120
    assert totals(window)["listened_ms"] == 60000


def test_tracking_toggle_flushes_and_can_resume_without_backfill(playback):
    window, state = playback
    state.now = 10
    state.position = 10000
    window._set_statistics_enabled(False)
    state.now = 100
    state.position = 100000
    assert totals(window)["listened_ms"] == 10000
    assert get_statistics_settings() == {"enabled": False}
    window._set_statistics_enabled(True)
    state.now = 110
    state.position = 110000
    assert totals(window)["listened_ms"] == 20000
    assert totals(window)["plays"] == 0


def test_clear_requires_confirmation_and_drops_uncommitted_activity(playback, monkeypatch):
    window, state = playback
    state.now = 10
    state.position = 10000
    assert totals(window)["listened_ms"] == 10000
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    assert not window._clear_statistics()
    assert totals(window)["listened_ms"] == 10000
    state.now = 20
    state.position = 20000
    window._statistics_sample()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    assert window._clear_statistics()
    assert totals(window)["listened_ms"] == 0
    assert len(window.library_tracks) == 1
    state.now = 25
    state.position = 25000
    assert totals(window)["listened_ms"] == 5000
    assert totals(window)["plays"] == 0


def test_dashboard_date_filters_tables_and_keyboard_workspace(playback, qapp):
    window, state = playback
    state.now = 40
    state.position = 40000
    window._statistics_checkpoint()
    window.show()
    window.shortcut_statistics.activated.emit()
    page = window.statistics_page
    wait_for(qapp, lambda: page.summary_labels["plays"].text() == "1")
    assert window.workspace_stack.currentWidget() is page
    assert window.statistics_button.isChecked()
    assert not window.waveform_widget.isVisible()
    assert page.tables["songs"].item(0, 0).text() == "Song"
    assert page.tables["artists"].item(0, 0).text() == "Artist"
    assert page.tables["albums"].item(0, 0).text() == "Album"
    assert len(page.chart.values) == 30
    page.range_combo.setCurrentIndex(0)
    wait_for(qapp, lambda: len(page.chart.values) == 1)
    window.shortcut_library.activated.emit()
    assert not window.statistics_button.isChecked()
    assert not page._refresh_timer.isActive()


def test_settings_expose_tracking_and_a_distinct_clear_action(playback):
    window, _ = playback
    dialog = SettingsDialog([], {}, parent=window, statistics_settings={"enabled": False})
    requests = []
    dialog.clear_statistics_requested.connect(lambda: requests.append(True))
    try:
        assert dialog.get_statistics_settings() == {"enabled": False}
        dialog.statistics_enabled_checkbox.setChecked(True)
        assert dialog.get_statistics_settings() == {"enabled": True}
        dialog.clear_statistics_button.click()
        assert requests == [True]
    finally:
        dialog.deleteLater()


def test_pcm_output_clock_is_used_even_after_native_decoder_stops(playback, monkeypatch):
    window, state = playback
    progress = [7, 0, True]
    monkeypatch.setattr(window._pcm_worker, "output_progress", lambda: tuple(progress))
    state.playing = False
    window._statistics_rebase()
    state.now = 1
    progress[1] = 1000
    window._statistics_sample()
    state.now = 100
    progress[1] = 2000
    assert totals(window)["listened_ms"] == 2000


def test_processed_playback_starts_statistics_after_output_controls_update(playback, monkeypatch):
    window, state = playback
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    window._pcm_worker.configure(None, 1, 0, 1.5, enabled=True, playing=False)
    window._statistics_timer.stop()
    window.media_player.playbackStateChanged.emit(QMediaPlayer.PlaybackState.PlayingState)
    assert window._pcm_worker.output_progress()[2]
    assert window._statistics_timer.isActive()


def test_play_after_eof_starts_a_new_attempt_without_reloading_the_file(playback, monkeypatch):
    window, state = playback
    state.now = 30
    state.position = 30000
    assert totals(window)["plays"] == 1
    state.status = QMediaPlayer.MediaStatus.EndOfMedia
    state.playing = False
    window._statistics_state_changed()
    # Avoid a real decoder; GaplessMediaPlayer still executes its public Play
    # wrapper and emits the restart event before handing off to native Qt.
    window.media_player.play()
    state.status = QMediaPlayer.MediaStatus.BufferedMedia
    state.position = 0
    state.playing = True
    window._statistics_rebase()
    state.now = 60
    state.position = 30000
    assert totals(window)["plays"] == 2


def test_close_checkpoints_before_stopping_output_and_waits_after_silencing(playback, monkeypatch):
    window, state = playback
    state.now = 10
    state.position = 10000
    close = window.statistics_store.close
    checked = []

    def close_store():
        assert window.audio_output.isMuted()
        assert window._pcm_worker._closed
        checked.append(True)
        close()

    monkeypatch.setattr(window.statistics_store, "close", close_store)
    window.close()
    assert checked == [True]
    assert not window._statistics_timer.isActive()
