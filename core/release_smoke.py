"""Exercise an installed build using fixtures supplied by tools/smoke_release.py."""

from pathlib import Path
from struct import pack
import sys
from time import monotonic, sleep
import traceback

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioDecoder, QAudioFormat, QMediaPlayer
from PySide6.QtWidgets import QApplication

from core.audio_dsp import apply_pcm_rate_to_bytes
from core.pcm_resampler import _native_renderer
from ui.lrc_editor import LrcEditorDialog


def _wait_for(condition, message, details):
    deadline = monotonic() + 8
    while not condition():
        if monotonic() >= deadline:
            raise RuntimeError(f"{message} {details()}")
        QApplication.processEvents()
        sleep(0.01)


def _check_decoding(audio_path):
    decoder = QAudioDecoder()
    decoder.setSource(QUrl.fromLocalFile(str(audio_path)))
    try:
        decoder.start()
        _wait_for(decoder.bufferAvailable, "The sample WAV did not produce a decoded buffer.",
                  lambda: f"Decoder error: {decoder.errorString() or 'none'}")
        buffer = decoder.read()
        if not buffer.isValid() or buffer.frameCount() <= 0:
            raise RuntimeError("The sample WAV produced an empty or invalid audio buffer.")
    finally:
        decoder.stop()


def _check_playback(window, audio_path):
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    _check_decoding(audio_path)

    media = window.media_player
    # Test transport commands independently of the Windows audio endpoint.
    media.setAudioOutput(None)

    def transport_details():
        return (f"Status={media.mediaStatus().name}, state={media.playbackState().name}, "
                f"position={media.position()} ms, duration={media.duration()} ms, "
                f"output unavailable={window._audio_output_unavailable}, "
                f"error={media.errorString() or 'none'}")

    try:
        window.load_audio(audio_path)
        _wait_for(lambda: media.isSeekable() and media.duration() >= 4000,
                  "The sample WAV did not load as seekable audio.", transport_details)
        window._seek_from_waveform(2500)
        _wait_for(lambda: abs(media.position() - 2500) <= 100,
                  "The player did not seek to the requested position.", transport_details)
        window.toggle_playback()
        if window._audio_output_unavailable:
            # The application's audio-device guard pauses a play attempt when
            # the host has no output device.
            _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                      "The player did not pause with no audio output.", transport_details)
            return "pause with no audio output"

        _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                  "The player did not enter the playing state.", transport_details)
        window.toggle_playback()
        _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                  "The player did not enter the paused state.", transport_details)
        return "play/pause"
    finally:
        media.stop()
        media.setAudioOutput(window.audio_output)


def _check_lyric_editor(window, target):
    if target.exists():
        raise FileExistsError(target)
    editor = LrcEditorDialog(target, window)
    editor.show()
    try:
        editor.add_line()
        editor.line_text.setPlainText("LRCPlus release smoke test")
        if not editor.save_document() or not target.is_file():
            raise RuntimeError("The lyrics editor did not save the LRCX file.")
    finally:
        editor.done(editor.DialogCode.Rejected)
        editor.deleteLater()

    reopened = LrcEditorDialog(target, window)
    try:
        if [line.text for line in reopened.lines] != ["LRCPlus release smoke test"]:
            raise RuntimeError("The reopened LRCX file did not contain the saved lyric.")
    finally:
        reopened.done(reopened.DialogCode.Rejected)
        reopened.deleteLater()


def _check_speed_resampling():
    if _native_renderer() is None:
        raise RuntimeError("The packaged Rust PCM resampler could not load.")
    sample = pack("<h", 1500)
    output = apply_pcm_rate_to_bytes(sample * 120, QAudioFormat.SampleFormat.Int16, 1, 1.25)
    if output != sample * 96:
        raise RuntimeError("The packaged Rust PCM resampler produced unexpected output.")


def _check_melody_detection():
    import numpy as np

    from core.rhythm_melody import (
        FIRST_MIDI_NOTE, LAST_MIDI_NOTE, MelodyAnalyzer, _native_peak_collector,
    )

    if _native_peak_collector() is None:
        raise RuntimeError("The packaged Rust peak collector could not load.")
    melody = MelodyAnalyzer(22050, 512)
    spectra = np.zeros((1, melody._high_bin + 2), dtype=np.float32)
    spectra[0, round(440 / melody._bin_hz)] = 1
    pitches = np.zeros((1, LAST_MIDI_NOTE - FIRST_MIDI_NOTE + 1), dtype=np.float32)
    melody._collect_peaks(spectra, pitches)
    if list(np.flatnonzero(pitches[0]) + FIRST_MIDI_NOTE) != [69]:
        raise RuntimeError("The packaged Rust peak collector produced an unexpected melody.")


def _check_pcm_engine():
    from core.audio_dsp import _native_gain, apply_pcm_gain_to_bytes
    from core.pcm_buffers import _native_api, make_inbox, make_output

    if _native_api() is None:
        raise RuntimeError("The packaged Rust PCM engine could not load.")
    sample_format = QAudioFormat.SampleFormat.Int16
    if (_native_gain() is None
            or apply_pcm_gain_to_bytes(pack("<hh", 20_000, -20_000), sample_format, 2)
            != pack("<hh", 32_767, -32_768)):
        raise RuntimeError("The packaged Rust PCM gain produced unexpected output.")
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(sample_format)
    pcm = pack("<h", 1500) * 120
    inbox = make_inbox()
    inbox.push(pcm, fmt, 4_000_000, len(pcm))
    output = make_output(fmt)
    data, decoded_format, position = inbox.pop()
    output.add_source(120, position)
    output.add_resampled(120, 1.0)
    output.queue(data, padding=0, content=120)
    if decoded_format != fmt or output.peek(len(pcm)) != pcm:
        raise RuntimeError("The packaged Rust PCM engine changed the decoded audio.")
    output.commit(len(pcm))
    if output.position_ms(1250) != 4001.25:
        raise RuntimeError("The packaged Rust PCM engine produced an unexpected playback position.")


def run_release_smoke(window, directory):
    directory = Path(directory)
    report = directory / "result.txt"
    frozen = getattr(sys, "frozen", False)
    try:
        transport_check = _check_playback(window, directory / "sample.wav")
        if frozen:
            _check_speed_resampling()
            _check_melody_detection()
            _check_pcm_engine()
        _check_lyric_editor(window, directory / "sample.lrcx")
    except Exception:
        report.write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    audio_checks = "Rust speed resampling, melody detection, PCM playback, " if frozen else ""
    report.write_text(f"Audio decoding, seeking, {transport_check}, {audio_checks}"
                      "and LRCX round trip passed.\n",
                      encoding="utf-8")
    return 0
