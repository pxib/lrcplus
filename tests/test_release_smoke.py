import wave
from math import pi, sin
from struct import iter_unpack, pack
from threading import Event
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioDevice, QMediaDevices, QMediaPlayer

from core.native_audio import audio_probe_executable
from core.playlists import path_key
from core.replaygain_cache import file_revision, save_scans
from core.release_smoke import _wait_for, run_release_smoke
from core.settings import save_appearance_settings, save_playback_settings
from ui import audio_devices
from ui.lrc_editor import LrcEditorDialog
from ui.main_window import Player


class NoAudioOutputs(QObject):
    audioOutputsChanged = Signal()

    @staticmethod
    def audioOutputs():
        return []

    @staticmethod
    def defaultAudioOutput():
        return QAudioDevice()


def test_release_smoke_on_host_without_audio_output(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(audio_devices, "QMediaDevices", NoAudioOutputs)
    save_appearance_settings({"artwork_source": "album_only"})
    with wave.open(str(tmp_path / "sample.wav"), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", ""))
        audio.writeframes(b"\x20\x01" * (44100 * 5))

    window = Player()
    try:
        assert window._audio_output_unavailable
        result = run_release_smoke(window, tmp_path)
        report = (tmp_path / "result.txt").read_text(encoding="utf-8")
        assert result == 0, report
        assert "pause with no audio output" in report
    finally:
        window.close()
        window.deleteLater()


def test_rust_playback_seek_and_edit_lyrics(qapp, tmp_path, monkeypatch):
    if audio_probe_executable() is None or QMediaDevices.defaultAudioOutput().isNull():
        pytest.skip("Requires the built Rust audio probe and a default Windows audio endpoint.")

    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "1")
    save_appearance_settings({"artwork_source": "album_only"})
    audio_path = tmp_path / "track.wav"
    lyrics_path = audio_path.with_suffix(".lrcx")
    with wave.open(str(audio_path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", ""))
        audio.writeframes(b"\x00\x00" * (44100 * 5))
    lyrics_path.write_text("[00:00.000]First line\n[00:02.000]Second line\n", encoding="utf-8")

    window = Player()
    media = window.media_player
    fallback_errors = []

    def details():
        return (f"Rust active={media.rust_active}, status={media.mediaStatus().name}, "
                f"position={media.position()} ms, active line={window.current_line}, "
                f"fallbacks={fallback_errors}")

    try:
        media.rustFallback.connect(fallback_errors.append)
        window.audio_output.setMuted(True)
        window.load_audio(audio_path)
        _wait_for(lambda: media.rust_active and media.isSeekable()
                  and window.current_lyrics_path == lyrics_path,
                  "Rust playback and the LRCX sidecar did not load.", details)

        window.toggle_playback()
        _wait_for(lambda: media.rust_active and media.position() >= 150 and window.current_line == 0,
                  "Rust playback did not advance the player to the first lyric.", details)
        window._seek_from_waveform(2500)
        _wait_for(lambda: media.rust_active and media.position() >= 2650 and window.current_line == 1,
                  "Rust playback did not advance past the seek into the second lyric.", details)
        assert window.seek_slider.value() == media.position()
        window.toggle_playback()
        assert media.playbackState() == QMediaPlayer.PlaybackState.PausedState

        editor = LrcEditorDialog(lyrics_path, window)
        try:
            editor.go_to_lyric_line(1)
            editor.line_text.setPlainText("Edited second line")
            assert editor.save_document()
            _wait_for(lambda: window.current_line == 1 and window.lyrics[1] == (2.0, "Edited second line"),
                      "The player did not refresh its active lyric after saving.", details)
        finally:
            editor.done(editor.DialogCode.Rejected)
            editor.deleteLater()

        reopened = LrcEditorDialog(lyrics_path, window)
        try:
            assert [(line.start, line.text) for line in reopened.lines] == [
                (0.0, "First line"), (2.0, "Edited second line"),
            ]
        finally:
            reopened.done(reopened.DialogCode.Rejected)
            reopened.deleteLater()

        assert media.rust_active and not fallback_errors, details()
        assert media.playbackState() == QMediaPlayer.PlaybackState.PausedState
    finally:
        window.close()
        window.deleteLater()


def test_rust_output_change_resumes_a_paused_track_at_its_position(qapp, tmp_path, monkeypatch):
    default = QMediaDevices.defaultAudioOutput()
    outputs = QMediaDevices.audioOutputs()
    others = [output for output in outputs if output != default and output.preferredFormat().isValid()
              and sum(device.description() == output.description() for device in outputs) == 1]
    if audio_probe_executable() is None or default.isNull() or not others:
        pytest.skip("Requires the built transport and two Windows audio outputs with distinct names.")
    selected = next((output for output in others if "Virtual Cable" in output.description()), others[0])
    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "1")
    save_appearance_settings({"artwork_source": "album_only"})
    path = tmp_path / "device.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", ""))
        audio.writeframes(bytes(44100 * 5 * 2))

    window = Player()
    media = window.media_player
    failures = []

    def details():
        return f"position={media.position()} ms, fallbacks={failures}"

    try:
        media.rustFallback.connect(failures.append)
        window.audio_output.setMuted(True)
        window.load_audio(path)
        window.toggle_playback()
        _wait_for(lambda: media.rust_active and media.position() >= 100,
                  "Rust did not play on the original endpoint.", details)
        window.toggle_playback()
        window._seek_from_waveform(1400)
        original = media._rust_process
        window._apply_output_device({"output_device": bytes(selected.id()).hex()})
        _wait_for(lambda: media.rust_active and media._rust_ready and media._rust_process is not original,
                  "Rust did not open the selected endpoint.", details)
        assert window.audio_output.device() == selected
        assert media.playbackState() == QMediaPlayer.PlaybackState.PausedState
        window.toggle_playback()
        _wait_for(lambda: media.rust_active and 1500 <= media.position() < 2000,
                  "Rust did not resume near the seek position on the new endpoint.", details)
        assert not failures, details()
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize(("replace_at", "normalize", "crossfade"), [
    (None, False, 0), (150, False, 0), (1200, False, 0), (None, True, 0), (None, False, 1),
])
def test_rust_gapless_switches_lyrics_without_restarting_the_helper(
    qapp, tmp_path, monkeypatch, replace_at, normalize, crossfade,
):
    device = QMediaDevices.defaultAudioOutput()
    if audio_probe_executable() is None or device.isNull():
        pytest.skip("Requires the built Rust audio probe and a default Windows audio endpoint.")

    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "1")
    save_appearance_settings({"artwork_source": "album_only"})
    save_playback_settings({"gapless_playback": not crossfade, "play_mode": "playlist",
                            "crossfade_seconds": crossfade, "replaygain_mode": "track" if normalize else "off"})
    sample_rate = device.preferredFormat().sampleRate()
    half = sample_rate * 5 // 4
    paths = []
    for index, name in enumerate(("First half", "Second half \u97f3\u697d")):
        path = tmp_path / f"{name}.wav"
        with wave.open(str(path), "wb") as audio:
            audio.setparams((1, 2, sample_rate, 0, "NONE", ""))
            samples = [round(1500 * sin(2 * pi * 441 * frame / sample_rate))
                       for frame in range(index * half, (index + 1) * half)]
            audio.writeframes(pack(f"<{half}h", *samples))
        path.with_suffix(".lrcx").write_text(f"[00:00.000]{name}\n", encoding="utf-8")
        paths.append(path)
    if normalize:
        rows = [(path_key(path), *file_revision(path), gain, None, None, None)
                for path, gain in zip(paths, (-6, 6))]
        assert save_scans(rows, Event())

    window = Player()
    media = window.media_player
    failures = []
    transitions = []

    def capture_peak():
        snapshot = window.playback_audio_tap.take_latest()
        return max((abs(value) for (value,) in iter_unpack("<f", snapshot[0])), default=0) if snapshot else 0

    def details():
        return (f"source={media.source().toLocalFile()}, position={media.position()} ms, "
                f"underruns={media.rust_underrun_frames}, fallbacks={failures}")

    try:
        media.rustFallback.connect(failures.append)
        window.audio_output.setMuted(True)
        if normalize:
            window.playback_audio_tap.set_collecting(True)
            window.set_playback_audio_observation(True)
        if replace_at is not None:
            discarded = tmp_path / "Discarded.wav"
            discarded.write_bytes(paths[1].read_bytes())
            window.enqueue_tracks([paths[0], discarded, paths[1]])
        else:
            window.enqueue_tracks(paths)
        window.play_queue_index(0)
        media.sourceChanged.connect(lambda url: transitions.append(url.toLocalFile()))
        _wait_for(lambda: media.rust_active and media.position() >= 150
                  and window.last_lyric_text == paths[0].stem,
                  "The first half did not play through Rust.", details)
        process = media._rust_process
        if normalize:
            _wait_for(lambda: 0.022 < capture_peak() < 0.024,
                      "The first track's ReplayGain was absent from captured PCM.", details)
        if replace_at is not None:
            _wait_for(lambda: media.position() >= replace_at and window.current_audio_path == paths[0],
                      "The first half did not reach the queue edit position.", details)
            assert media._rust_next[1].toLocalFile() == discarded.as_posix()
            window.remove_queue_tracks([1])
        _wait_for(lambda: window.last_lyric_text == paths[1].stem and media.position() >= 150,
                  "The player did not switch to the second half and its lyrics.", details)
        assert transitions == [paths[1].as_posix()]
        assert window.current_audio_path == paths[1]
        assert media._rust_process is process and media.rust_active and not failures, details()
        assert media.isPlaying() and media.rust_underrun_frames == 0, details()
        if normalize:
            _wait_for(lambda: 0.09 < capture_peak() < 0.093,
                      "The successor's ReplayGain was absent from captured PCM.", details)
            assert window._replaygain_tags.track_gain == 6
            window._equalizer = SimpleNamespace(configuration=(-6, ()))
            window._refresh_equalizer_route()
            _wait_for(lambda: 0.045 < capture_peak() < 0.047,
                      "Live Rust EQ preamp did not reach the PCM observer.", details)
            assert media._rust_process is process and not failures, details()
        if crossfade:
            assert 610 <= media.position() < 850, details()
    finally:
        window.close()
        window.deleteLater()
