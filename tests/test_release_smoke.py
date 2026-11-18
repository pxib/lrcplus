import wave

from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QAudioDevice

from core.release_smoke import run_release_smoke
from core.settings import save_appearance_settings
from ui import audio_devices
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
