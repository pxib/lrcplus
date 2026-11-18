"""Exercise an installed build using fixtures supplied by tools/smoke_release.py."""

from pathlib import Path
from time import monotonic, sleep
import traceback

from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioDecoder, QMediaPlayer
from PySide6.QtWidgets import QApplication

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
                f"error={media.errorString() or 'none'}")

    try:
        window.load_audio(audio_path)
        _wait_for(lambda: media.isSeekable() and media.duration() >= 4000,
                  "The sample WAV did not load as seekable audio.", transport_details)
        window._seek_from_waveform(2500)
        _wait_for(lambda: abs(media.position() - 2500) <= 100,
                  "The player did not seek to the requested position.", transport_details)
        window.toggle_playback()
        _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                  "The player did not enter the playing state.", transport_details)
        window.toggle_playback()
        _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PausedState,
                  "The player did not enter the paused state.", transport_details)
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


def run_release_smoke(window, directory):
    directory = Path(directory)
    report = directory / "result.txt"
    try:
        _check_playback(window, directory / "sample.wav")
        _check_lyric_editor(window, directory / "sample.lrcx")
    except Exception:
        report.write_text(traceback.format_exc(), encoding="utf-8")
        return 1
    report.write_text("Audio decoding, play/pause, seeking, and LRCX round trip passed.\n",
                      encoding="utf-8")
    return 0
