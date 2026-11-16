from types import SimpleNamespace
from pathlib import Path

import pytest
from PySide6.QtWidgets import QMainWindow
from ui.player_ab_loop import PlayerABLoopMixin


@pytest.fixture
def player(qapp):
    class Window(PlayerABLoopMixin, QMainWindow):
        def __init__(self):
            super().__init__()
            self.current_audio_path = "song.mp3"
            self.position = 1000
            self.track_duration = 10000
            self.playing = True
            self.seeks = []
            self.invalidations = 0
            from PySide6.QtCore import QObject, Signal
            class Media(QObject):
                durationChanged = Signal(int)
            self.media_player = Media(self)
            self.media_player.duration = lambda: self.track_duration
            self.media_player.position = lambda: self.position
            self.media_player.isPlaying = lambda: self.playing
            self.media_player.play = lambda: setattr(self, "playing", True)
            self.seek_slider = SimpleNamespace(set_loop_range=lambda a, b: setattr(self, "markers", (a, b)))
            self._init_ab_loop()

        def _invalidate_gapless_preload(self):
            self.invalidations += 1

        def _seek_from_waveform(self, value):
            self.seeks.append(value)
            self.position = value

    window = Window()
    yield window
    window._ab_timer.stop()
    window.deleteLater()
    qapp.processEvents()


def arm(player):
    player._mark_ab_start()
    player.position = 3000
    player._mark_ab_end()


def test_marks_loop_seeks_to_a_and_shows_range(player):
    arm(player)
    assert player.seeks == [1000]
    assert player._ab_timer.isActive()
    assert "0:01.000" in player.ab_loop_button.toolTip()
    assert "0:03.000" in player.ab_loop_button.toolTip()
    player.position = 2999
    assert not player._check_ab_loop()
    player.position = 3000
    assert player._check_ab_loop()
    assert player.seeks == [1000, 1000]


def test_rejects_b_before_or_too_close_to_a(player):
    player._mark_ab_start()
    for position in (0, 1000, 1099):
        player.position = position
        player._mark_ab_end()
        assert player._ab_end is None
        assert not player._ab_timer.isActive()


def test_paused_loop_does_not_resume_playback(player):
    player.playing = False
    arm(player)
    assert not player.playing
    player.position = 3001
    assert not player._check_ab_loop()
    assert player.seeks == [1000]


def test_eof_loop_restarts_same_track(player):
    arm(player)
    player.playing = False
    assert player._check_ab_loop(at_end=True)
    assert player.playing
    assert player.position == 1000


@pytest.mark.parametrize("state", ["expired", "finishing", "closing"])
def test_sleep_and_close_take_priority_over_loop(player, state):
    arm(player)
    player.position = 4000
    if state == "finishing":
        player.sleep_timer = SimpleNamespace(check=lambda: None, finishing=True)
    elif state == "expired":
        player._sleep_expired = True
    else:
        player._closing = True
    assert not player._check_ab_loop(at_end=True)
    assert player.seeks == [1000]


def test_reset_a_and_clear_stop_loop_timer(player):
    arm(player)
    player.position = 2000
    player._mark_ab_start()
    assert player._ab_start == 2000 and player._ab_end is None
    assert not player._ab_timer.isActive()
    player._clear_ab_loop()
    assert player._ab_start is None
    assert not player.ab_clear_action.isEnabled()


def test_no_audio_disables_mark_actions(player):
    player.current_audio_path = None
    player._refresh_ab_loop()
    assert not player.ab_mark_a_action.isEnabled()
    player._mark_ab_start()
    assert player._ab_start is None


def switch_song(player, path, duration=10000):
    player._suspend_ab_loop()
    player.current_audio_path = path
    player.position = 0
    player.track_duration = duration
    player._restore_ab_loop(path)


def test_return_restores_loop_and_markers_without_seeking(player):
    arm(player)
    switch_song(player, "other.mp3")
    assert player.markers == (None, None)
    assert not player._ab_timer.isActive()
    switch_song(player, "song.mp3")
    assert player.markers == (1000, 3000)
    assert player._ab_timer.isActive()
    assert player.position == 0
    assert player.seeks == [1000]
    player.position = 3000
    assert player._check_ab_loop()
    assert player.seeks == [1000, 1000]


def test_a_only_is_remembered_without_starting_loop(player):
    player._mark_ab_start()
    switch_song(player, "other.mp3")
    switch_song(player, "song.mp3")
    assert player.markers == (1000, None)
    assert not player._ab_timer.isActive()
    assert player.ab_clear_action.isEnabled()


def test_clear_forgets_only_current_song(player):
    arm(player)
    switch_song(player, "other.mp3")
    player.position = 4000
    player._mark_ab_start()
    player.position = 6000
    player._mark_ab_end()
    switch_song(player, "song.mp3")
    player._clear_ab_loop()
    switch_song(player, "other.mp3")
    assert player.markers == (4000, 6000)
    switch_song(player, "song.mp3")
    assert player.markers == (None, None)


def test_same_file_path_variants_share_markers(player):
    arm(player)
    switch_song(player, "other.mp3")
    switch_song(player, Path("song.mp3").resolve())
    assert player.markers == (1000, 3000)


def test_return_while_paused_does_not_resume(player):
    arm(player)
    switch_song(player, "other.mp3")
    player.playing = False
    switch_song(player, "song.mp3")
    player.position = 3500
    assert not player._check_ab_loop()
    assert not player.playing
    assert player.seeks == [1000]


@pytest.mark.parametrize("duration, expected", [(2500, (1000, None)), (500, (None, None)),
                                               (3000, (1000, 3000))])
def test_restore_waits_for_new_duration_and_drops_invalid_boundaries(player, duration, expected):
    arm(player)
    switch_song(player, "other.mp3")
    switch_song(player, "song.mp3", duration=0)
    assert player.markers == (1000, 3000)
    assert not player._ab_timer.isActive()
    assert not player._check_ab_loop(at_end=True)
    player.track_duration = duration
    player.media_player.durationChanged.emit(duration)
    assert player.markers == expected
    assert player._ab_timer.isActive() == (expected[1] is not None)
    switch_song(player, "other.mp3")
    switch_song(player, "song.mp3")
    assert player.markers == expected


def test_new_session_starts_without_markers(player, qapp):
    arm(player)
    fresh = type(player)()
    try:
        assert fresh.markers == (None, None)
        assert not fresh._ab_loop_memory
    finally:
        fresh._ab_timer.stop()
        fresh.deleteLater()
        qapp.processEvents()


def test_player_integration_suspends_loop_on_new_track_and_suppresses_preload(qapp, monkeypatch, tmp_path):
    from ui.main_window import Player
    monkeypatch.setattr("ui.audio_devices.QMediaDevices.audioOutputs", lambda: [])
    window = Player()
    try:
        window._ab_start, window._ab_end = 1000, 3000
        assert window._gapless_next_path() is None
        monkeypatch.setattr(window, "update_background_for_audio", lambda *_: None)
        monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *_: None)
        window.load_audio(tmp_path / "new.mp3")
        assert window._ab_start is None and window._ab_end is None
        assert not window._ab_timer.isActive()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
