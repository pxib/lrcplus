import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject

from assets.scarlet.boss_music import BossTheme


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
