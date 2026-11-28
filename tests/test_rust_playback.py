from types import SimpleNamespace
import sys

import pytest
from PySide6.QtCore import QUrl

from core import rust_playback


@pytest.fixture
def make_player(qapp, monkeypatch):
    players = []
    device = SimpleNamespace(isNull=lambda: False, id=lambda: b"default")
    monkeypatch.setattr(rust_playback, "QMediaDevices", SimpleNamespace(defaultAudioOutput=lambda: device))

    def make(*, mode=None, library=True, output="default"):
        if mode is None:
            monkeypatch.delenv("LRCPLUS_RUST_PLAYBACK", raising=False)
        else:
            monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", mode)
        monkeypatch.setattr(rust_playback, "_pitch_library", lambda: "rubberband.dll" if library else None)
        player = rust_playback.RustMediaPlayer(executable="probe")
        route_device = device if output == "default" else SimpleNamespace(
            isNull=lambda: False, id=lambda: b"other",
        )
        player.audioOutput = lambda: None if output is None else SimpleNamespace(device=lambda: route_device)
        player._start_process = lambda: None
        players.append(player)
        return player

    yield make
    for player in players:
        player.stop()
        player.deleteLater()


@pytest.mark.parametrize(("rate", "linked", "pitch", "expected"), [
    (1.0, False, 0, False),
    (1.25, True, 0, False),
    (1.25, False, 0, True),
    (1.0, False, 3, True),
    (1.0, True, 3, False),
    (1.0, False, 25, False),
])
def test_automatic_rust_preference_applies_to_independent_pitch_at_track_load(
    make_player, tmp_path, rate, linked, pitch, expected,
):
    player = make_player()
    player.setPlaybackRate(rate)
    player.configure_rust(1, compatible=True, pitch_linked=linked, semitones=pitch)
    url = QUrl.fromLocalFile(str(tmp_path / "track.wav"))
    player.setSource(url)
    assert player.rust_active == expected
    assert player.source() == url


@pytest.mark.parametrize(("library", "output", "compatible"), [
    (False, "default", True),
    (True, "other", True),
    (True, None, True),
    (True, "default", False),
])
def test_automatic_rust_preference_respects_backend_requirements(
    make_player, tmp_path, library, output, compatible,
):
    player = make_player(library=library, output=output)
    player.setPlaybackRate(1.25)
    player.configure_rust(1, compatible=compatible, pitch_linked=False, semitones=0)
    player.setSource(QUrl.fromLocalFile(str(tmp_path / "track.mp3")))
    assert not player.rust_active


def test_automatic_selection_waits_for_the_next_source(make_player, tmp_path):
    player = make_player()
    first, second = [QUrl.fromLocalFile(str(tmp_path / name)) for name in ("first.wav", "second.wav")]
    player.configure_rust(1, compatible=True, pitch_linked=False, semitones=0)
    player.setSource(first)
    player.setPlaybackRate(1.25)
    player.configure_rust(1, compatible=True, pitch_linked=False, semitones=0)
    assert player.source() == first and not player.rust_active
    player.setSource(second)
    assert player.source() == second and player.rust_independent_pitch

    player.setPlaybackRate(1.0)
    player.configure_rust(1, compatible=True, pitch_linked=False, semitones=0)
    assert player.rust_active
    player.setSource(first)
    assert player.source() == first and not player.rust_active


def test_experimental_override_can_select_rust_at_unity(make_player, tmp_path):
    player = make_player(mode="1", library=False)
    player.configure_rust(1, compatible=True, pitch_linked=True, semitones=0)
    player.setSource(QUrl.fromLocalFile(str(tmp_path / "track.wav")))
    assert player.rust_active


def test_packaged_app_prefers_rust_at_unity(make_player, tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    player = make_player()
    player.configure_rust(1, compatible=True, pitch_linked=True, semitones=0)
    player.setSource(QUrl.fromLocalFile(str(tmp_path / "track.wav")))
    assert player.rust_active


@pytest.mark.parametrize(("mode", "rate", "pitch", "expected"), [
    (None, 1.0, 0, False), ("1", 1.25, 0, False), ("1", 1.0, 3, False), ("1", 1.0, 0, True),
])
def test_gapless_rust_route_requires_opt_in_and_neutral_playback(make_player, tmp_path, mode, rate, pitch, expected):
    player = make_player(mode=mode)
    player.setPlaybackRate(rate)
    player.configure_rust(1, compatible=True, pitch_linked=False, semitones=pitch, gapless=True)
    player.setSource(QUrl.fromLocalFile(str(tmp_path / "track.wav")))
    assert player.rust_active == (mode == "1")
    assert player._rust_gapless == expected
    if expected:
        assert not player.rust_independent_pitch


def test_qt_override_disables_the_playback_helper(monkeypatch):
    monkeypatch.setattr(rust_playback, "audio_probe_executable", lambda: "probe")
    monkeypatch.delenv("LRCPLUS_RUST_PLAYBACK", raising=False)
    assert rust_playback.rust_playback_executable() == "probe"
    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "0")
    assert rust_playback.rust_playback_executable() is None
