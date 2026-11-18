"""Exercise an installed build using fixtures supplied by tools/smoke_release.py."""

from pathlib import Path
from time import monotonic, sleep
import traceback

from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
from PySide6.QtWidgets import QApplication

from ui.lrc_editor import LrcEditorDialog


def _wait_for(condition, message):
    deadline = monotonic() + 8
    while monotonic() < deadline:
        QApplication.processEvents()
        if condition():
            return
        sleep(0.01)
    raise RuntimeError(message)


def _check_playback(window, audio_path):
    if not audio_path.is_file():
        raise FileNotFoundError(audio_path)
    media = window.media_player
    window.load_audio(audio_path)
    # Windows CI can lack an audio endpoint. Keep the real decoder and playback
    # clock, and observe decoded samples through Qt's FFmpeg buffer output.
    media.setAudioOutput(None)
    output = QAudioBufferOutput(media)
    decoded_audio = []
    output.audioBufferReceived.connect(lambda buffer: decoded_audio.append(buffer.isValid()))
    media.setAudioBufferOutput(output)
    try:
        _wait_for(lambda: media.isSeekable() and media.duration() >= 4000,
                  "The sample WAV did not load as seekable audio.")
        window.toggle_playback()
        _wait_for(lambda: media.playbackState() == QMediaPlayer.PlaybackState.PlayingState
                  and media.position() >= 100 and any(decoded_audio),
                  "Playback did not advance and produce decoded audio.")
        window.toggle_playback()
        window._seek_from_waveform(2500)
        _wait_for(lambda: abs(media.position() - 2500) <= 100,
                  "Playback did not seek to the requested position.")
    finally:
        media.stop()
        media.setAudioBufferOutput(None)
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
    report.write_text("Playback, seeking, and LRCX round trip passed.\n", encoding="utf-8")
    return 0
