import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from assets.scarlet.boss_music import BossTheme, WindowsMidiFile


def test_theme_loops_only_while_playing_and_preserves_pause_position(qapp, fake_midi):
    theme = BossTheme(backend=fake_midi)
    try:
        theme.sync("ready")
        assert not fake_midi.calls
        theme.sync("playing")
        theme.sync("playing")
        assert fake_midi.calls == [("play", True)]
        fake_midi.current_mode = "stopped"
        theme._poll()
        assert fake_midi.calls[-1] == ("play", True)
        theme.sync("paused")
        assert fake_midi.current_mode == "paused"
        assert not theme.timer.isActive()
        theme._poll()
        assert fake_midi.calls[-1] == ("pause",)
        theme.sync("playing")
        assert fake_midi.calls[-1] == ("play", False)
    finally:
        theme.stop()
        theme.deleteLater()


@pytest.mark.parametrize("state, visible", [("won", True), ("lost", True), ("ready", True), ("paused", False)])
def test_ending_restarting_or_hiding_closes_the_midi_device(qapp, fake_midi, state, visible):
    theme = BossTheme(backend=fake_midi)
    try:
        theme.sync("playing")
        theme.sync(state, visible)
        assert fake_midi.calls[-1] == ("close",)
        assert not theme.timer.isActive()
        theme.sync("playing")
        assert fake_midi.calls[-1] == ("play", True)
    finally:
        theme.stop()
        theme.deleteLater()


def test_music_mute_before_combat_does_not_open_a_device(qapp, fake_midi):
    theme = BossTheme(backend=fake_midi)
    try:
        theme.toggle_mute()
        theme.sync("playing")
        assert fake_midi.calls == []
        assert "OFF" in theme.status_label
        theme.toggle_mute()
        assert fake_midi.calls == [("play", True)]
        theme.toggle_mute()
        theme.toggle_mute()
        assert fake_midi.calls[-1] == ("play", False)
    finally:
        theme.stop()
        theme.deleteLater()


def test_backend_failure_releases_device_and_does_not_retry_every_frame(qapp, fake_midi):
    fake_midi.fail_play = True
    theme = BossTheme(backend=fake_midi)
    try:
        theme.sync("playing")
        assert theme.error
        assert "再生不可" in theme.status_label
        assert fake_midi.calls == [("play", True), ("close",)]
        theme.sync("playing")
        assert len(fake_midi.calls) == 2
        assert not theme.timer.isActive()
    finally:
        theme.stop()
        theme.deleteLater()


def test_parent_destruction_closes_playing_midi(qapp, fake_midi):
    owner = QObject()
    theme = BossTheme(owner, backend=fake_midi)
    theme.sync("playing")
    owner.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert fake_midi.calls[-1] == ("close",)
    assert fake_midi.current_mode == "stopped"


def test_missing_theme_is_nonfatal(qapp, fake_midi, tmp_path):
    theme = BossTheme(path=tmp_path / "missing.mid", backend=fake_midi)
    try:
        theme.sync("playing")
        assert theme.error
        assert fake_midi.calls == []
    finally:
        theme.deleteLater()


def test_preloaded_theme_stays_silent_and_ready_until_cue(qapp, fake_midi, monkeypatch):
    preparations = []
    monkeypatch.setattr(fake_midi, "prepare", lambda: preparations.append(True), raising=False)
    theme = BossTheme(backend=fake_midi)
    try:
        theme.prepare()
        theme.prepare()
        theme.sync("ready")
        assert preparations == [True]
        assert fake_midi.calls == []
        assert not theme.timer.isActive()
        theme.sync("playing")
        assert fake_midi.calls == [("play", True)]
        theme.sync("paused")
        theme.sync("playing")
        assert fake_midi.calls[-1] == ("play", False)
    finally:
        theme.stop()
        theme.deleteLater()


@pytest.mark.parametrize("hide", [False, True])
def test_preloaded_device_is_released_even_if_music_never_started(qapp, fake_midi, monkeypatch, hide):
    monkeypatch.setattr(fake_midi, "prepare", lambda: None, raising=False)
    theme = BossTheme(backend=fake_midi)
    try:
        theme.prepare()
        if hide:
            theme.sync("ready", visible=False)
        else:
            theme.stop()
        assert fake_midi.calls == [("close",)]
        assert not theme._prepared
    finally:
        theme.stop()
        theme.deleteLater()


def test_failed_preload_is_nonfatal_and_releases_device(qapp, fake_midi, monkeypatch):
    def fail():
        raise OSError("MIDI output unavailable")

    monkeypatch.setattr(fake_midi, "prepare", fail, raising=False)
    theme = BossTheme(backend=fake_midi)
    try:
        theme.prepare()
        theme.sync("playing")
        assert theme.error
        assert fake_midi.calls == [("close",)]
    finally:
        theme.stop()
        theme.deleteLater()


def test_windows_midi_cue_only_issues_play_after_silent_preload(tmp_path):
    # Exercise the actual MCI command sequence without requiring an audio device.
    player = object.__new__(WindowsMidiFile)
    player.path = tmp_path / "theme.mid"
    player.alias = "test_boss"
    player.opened = False
    commands = []
    player.command = commands.append
    player.prepare()
    assert len(commands) == 2
    assert commands[0].startswith("open ")
    assert commands[1] == "set test_boss time format milliseconds"
    player.prepare()
    player.play(restart=True)
    assert commands[2:] == ["play test_boss from 0"]
    player.pause()
    player.play()
    assert commands[-2:] == ["pause test_boss", "play test_boss"]
    player.close()
    assert not player.opened
