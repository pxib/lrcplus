import pytest

from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QAudioOutput
from PySide6.QtWidgets import QLabel, QPushButton, QSlider

from core.audio_controller import AudioController


class VolumeControls(AudioController):
    def __init__(self):
        self.last_volume = 80
        self.is_muted = False
        self.playback_settings = {"high_volume_warning_shown": True}
        self.audio_output = QAudioOutput()
        self.volume_slider = QSlider(Qt.Orientation.Horizontal)
        self.volume_slider.setRange(0, 300)
        self.volume_slider.setValue(80)
        self.volume_percentage_label = QLabel("80%")
        self.mute_button = QPushButton("Mute")
        self.volume_slider.valueChanged.connect(self.handle_volume_change)
        self.mute_button.clicked.connect(self.toggle_mute)

    def _configure_pcm_volume(self, value):
        self.configured_volume = value


@pytest.mark.parametrize("volume", [1, 45, 80, 100, 145, 250, 300])
@pytest.mark.parametrize("mute_with_slider", [False, True])
def test_unmute_restores_last_percentage(qapp, volume, mute_with_slider):
    controls = VolumeControls()
    controls.volume_slider.setValue(volume)

    for _ in range(2):
        if mute_with_slider:
            controls.volume_slider.setValue(0)
        else:
            controls.mute_button.click()

        assert controls.is_muted
        assert controls.audio_output.isMuted()
        assert controls.volume_slider.value() == 0
        assert controls.volume_percentage_label.text() == "0%"
        assert controls.mute_button.text() == "Unmute"
        assert controls.configured_volume == 0

        controls.mute_button.click()

        assert not controls.is_muted
        assert not controls.audio_output.isMuted()
        assert controls.volume_slider.value() == volume
        assert controls.volume_percentage_label.text() == f"{volume}%"
        assert controls.mute_button.text() == "Mute"
        assert controls.configured_volume == volume


def test_adjusting_volume_while_muted_updates_restored_percentage(qapp):
    controls = VolumeControls()
    controls.mute_button.click()
    controls.volume_slider.setValue(65)

    assert not controls.is_muted
    assert not controls.audio_output.isMuted()

    controls.mute_button.click()
    controls.mute_button.click()

    assert controls.volume_slider.value() == 65
    assert controls.volume_percentage_label.text() == "65%"
