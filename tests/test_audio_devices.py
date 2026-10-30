from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QMainWindow, QPushButton

from core.audio_controller import AudioController
from ui import audio_devices
from ui.audio_devices import AudioDeviceControllerMixin


PLAYING = QMediaPlayer.PlaybackState.PlayingState
PAUSED = QMediaPlayer.PlaybackState.PausedState


class Device:
    def __init__(self, name):
        self.name = name

    def id(self):
        return self.name.encode()

    def description(self):
        return self.name

    def isNull(self):
        return not self.name


@pytest.fixture
def playback(qapp, monkeypatch):
    class Devices(QObject):
        audioOutputsChanged = Signal()
        outputs = [Device("Headphones"), Device("Speakers")]
        default = outputs[0]

        @classmethod
        def audioOutputs(cls):
            return cls.outputs

        @classmethod
        def defaultAudioOutput(cls):
            return cls.default

    monkeypatch.setattr(audio_devices, "QMediaDevices", Devices)

    class Playback(AudioDeviceControllerMixin, AudioController, QMainWindow):
        def __init__(self):
            super().__init__()
            self.playback_settings = {"output_device": "Headphones".encode().hex()}
            self.state = PLAYING
            self.position = 12345
            self.current_audio_path = "song.mp3"
            self.play_calls = 0
            self.flushes = 0
            self.configurations = []
            self.pending = bytearray(b"old audio")
            self.volume_slider = SimpleNamespace(value=lambda: 75)
            self.play_button = QPushButton()
            self.media_player = SimpleNamespace(
                playbackState=lambda: self.state,
                pause=lambda: self.set_state(PAUSED),
                play=self.play,
            )
            self.audio_output = SimpleNamespace(setDevice=self.set_device)
            self._apply_output_device(self.playback_settings)
            self._watch_audio_devices()

        def set_device(self, device):
            self.device = device

        def set_state(self, state):
            self.state = state
            self.update_play_button(state)

        def play(self):
            self.play_calls += 1
            self.set_state(PLAYING)

        def _flush_pcm_output(self):
            self.flushes += 1
            self.pending.clear()

        def _configure_pcm_volume(self, volume, refresh_format=False):
            self.configurations.append((volume, refresh_format))

        def _sync_pcm_sink_state(self, state):
            pass

        def update_transport_buttons(self):
            pass

        def change_devices(self, names, default=None):
            Devices.outputs = [Device(name) for name in names]
            Devices.default = next((device for device in Devices.outputs
                                    if device.name == default),
                                   Devices.outputs[0] if Devices.outputs else Device(""))
            self._media_devices.audioOutputsChanged.emit()

    window = Playback()
    yield window
    window._close_audio_device_dialog()
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_active_device_disconnect_pauses_and_clears_processed_audio(playback):
    playback.pending.extend(b"buffered audio")
    playback.change_devices(["Speakers"])

    assert playback.state == PAUSED
    assert playback.position == 12345
    assert playback.current_audio_path == "song.mp3"
    assert not playback.pending
    assert playback.device.name == "Speakers"
    # Device formats must refresh even below 100% volume (pitch/speed routing).
    assert playback.configurations[-1] == (75, True)
    dialog = playback._audio_device_dialog
    assert dialog.isVisible()
    assert dialog.device_combo.itemText(0) == "System Default (Speakers)"
    assert dialog.device_combo.itemText(1) == "Speakers"
    assert dialog.continue_button.text() == "Continue"
    assert dialog.ok_button.text() == "OK"


@pytest.mark.parametrize("action", ["continue", "ok", "close"])
def test_recovery_only_resumes_on_continue(playback, action):
    playback.change_devices(["Speakers"])
    dialog = playback._audio_device_dialog
    if action == "continue":
        dialog.continue_button.click()
    elif action == "ok":
        dialog.ok_button.click()
    else:
        dialog.close()

    assert playback.state == (PLAYING if action == "continue" else PAUSED)
    assert playback.play_calls == (1 if action == "continue" else 0)
    assert playback.position == 12345
    assert playback._audio_device_dialog is None
    # Simply continuing on a fallback must not discard the preferred output.
    assert playback.playback_settings["output_device"] == "Headphones".encode().hex()


@pytest.mark.parametrize("action", ["continue", "ok"])
def test_explicit_choice_applies_and_persists_selected_output(playback, action):
    from core.settings import get_playback_settings

    playback.change_devices(["Speakers", "USB Audio"])
    dialog = playback._audio_device_dialog
    index = dialog.device_combo.findData("USB Audio".encode().hex())
    dialog.device_combo.setCurrentIndex(index)
    dialog.device_combo.activated.emit(index)
    button = dialog.continue_button if action == "continue" else dialog.ok_button
    button.click()

    assert playback.device.name == "USB Audio"
    assert get_playback_settings()["output_device"] == "USB Audio".encode().hex()
    assert playback.state == (PLAYING if action == "continue" else PAUSED)


def test_unrelated_device_removal_does_not_pause(playback):
    initial_flushes = playback.flushes
    playback.change_devices(["Headphones"])
    assert playback.state == PLAYING
    assert playback._audio_device_dialog is None
    assert playback.flushes == initial_flushes


def test_paused_device_removal_does_not_show_playback_paused_dialog(playback):
    playback.set_state(PAUSED)
    playback.change_devices(["Speakers"])
    assert playback.state == PAUSED
    assert playback._audio_device_dialog is None
    assert playback.device.name == "Speakers"


def test_device_changes_refresh_one_dialog_and_remove_stale_choices(playback):
    playback.change_devices(["Speakers", "USB Audio"])
    dialog = playback._audio_device_dialog
    index = dialog.device_combo.findData("USB Audio".encode().hex())
    dialog.device_combo.setCurrentIndex(index)
    dialog.device_combo.activated.emit(index)
    playback.change_devices(["HDMI"])

    assert playback._audio_device_dialog is dialog
    assert dialog.device_combo.count() == 2
    assert dialog.device_combo.findData("USB Audio".encode().hex()) == -1
    dialog.continue_button.click()
    assert playback.device.name == "HDMI"
    assert playback.state == PLAYING
    assert playback.playback_settings["output_device"] == "Headphones".encode().hex()


def test_no_outputs_disables_continue_until_a_device_connects(playback):
    playback.change_devices([])
    dialog = playback._audio_device_dialog
    assert playback.state == PAUSED
    assert not dialog.continue_button.isEnabled()
    assert not dialog.device_combo.isEnabled()
    assert dialog.no_devices_label.isVisible()
    playback.change_devices(["USB Audio"])
    assert playback._audio_device_dialog is dialog
    assert dialog.continue_button.isEnabled()
    assert not dialog.no_devices_label.isVisible()
    dialog.continue_button.click()
    assert playback.state == PLAYING
    assert playback.device.name == "USB Audio"


def test_play_is_blocked_without_outputs_and_works_after_reconnection(playback):
    playback.change_devices([])
    playback._audio_device_dialog.ok_button.click()
    playback.play()
    assert playback.state == PAUSED
    playback.change_devices(["Headphones"])
    assert playback.state == PAUSED
    playback.play()
    assert playback.state == PLAYING


def test_losing_all_outputs_clears_a_stale_explicit_dialog_selection(playback):
    playback.change_devices(["Speakers", "USB Audio"])
    dialog = playback._audio_device_dialog
    index = dialog.device_combo.findData("USB Audio".encode().hex())
    dialog.device_combo.setCurrentIndex(index)
    dialog.device_combo.activated.emit(index)
    playback.change_devices([])
    playback.change_devices(["Speakers"])
    dialog.ok_button.click()
    assert playback.playback_settings["output_device"] == "Headphones".encode().hex()


def test_external_play_is_blocked_while_dialog_is_open(playback):
    playback.change_devices(["Speakers"])
    playback.play()
    assert playback.state == PAUSED
    assert playback._audio_device_dialog is not None


def test_preferred_output_restores_on_reconnection_while_paused(playback):
    playback.change_devices(["Speakers"])
    playback._audio_device_dialog.ok_button.click()
    playback.change_devices(["Speakers", "Headphones"])
    assert playback.device.name == "Headphones"
    assert playback.state == PAUSED


def test_system_default_changes_update_actual_output_without_disconnect(playback):
    playback.playback_settings["output_device"] = ""
    playback.change_devices(["Headphones", "Speakers"], default="Speakers")
    assert playback.device.name == "Speakers"
    assert playback.state == PLAYING
    assert playback._audio_device_dialog is None
    # Even if Qt now reports the new default, losing the tracked active output
    # must still be recognized as a disconnect.
    playback.change_devices(["Headphones"])
    assert playback.state == PAUSED
    assert playback._audio_device_dialog is not None


def test_pcm_failure_during_disconnect_uses_recovery_dialog(playback, monkeypatch):
    from core import audio_controller

    def unexpected_warning(*args):
        pytest.fail("A disconnect must not open the generic PCM failure dialog")

    monkeypatch.setattr(audio_controller.QMessageBox, "warning", unexpected_warning)
    devices = type(playback._media_devices)
    devices.outputs = [Device("Speakers")]
    devices.default = devices.outputs[0]
    playback._pcm_output_failed("Device disappeared")
    assert playback.state == PAUSED
    assert playback._audio_device_dialog is not None
    # A later device notification must not create a duplicate dialog.
    dialog = playback._audio_device_dialog
    playback._media_devices.audioOutputsChanged.emit()
    assert playback._audio_device_dialog is dialog


def test_closing_player_with_dialog_does_not_resume_or_apply_a_selection(playback):
    playback.change_devices(["Speakers"])
    initial_flushes = playback.flushes
    playback._close_audio_device_dialog()
    assert playback._audio_device_dialog is None
    assert playback.state == PAUSED
    assert playback.play_calls == 0
    assert playback.flushes == initial_flushes
