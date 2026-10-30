"""Audio output selection and recovery when an active device disappears."""

from PySide6.QtMultimedia import QAudioDevice, QMediaDevices, QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox, QDialog, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from core.settings import save_playback_settings


def _device_id(device):
    return bytes(device.id()).hex()


def _default_output(devices):
    default_id = _device_id(QMediaDevices.defaultAudioOutput())
    return next((device for device in devices if _device_id(device) == default_id),
                devices[0] if devices else QAudioDevice())


class AudioDeviceDisconnectedDialog(QDialog):
    CONTINUE = 2

    def __init__(self, devices, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Audio device disconnected")
        self.setMinimumWidth(440)
        layout = QVBoxLayout(self)
        message = QLabel(
            "An audio device was just disconnected and your playback has been "
            "paused. Please choose from the available devices below, or continue "
            "playing using the system default."
        )
        message.setWordWrap(True)
        layout.addWidget(message)
        layout.addWidget(QLabel("Audio output:"))
        self.device_combo = QComboBox()
        self.selection_changed = False
        self.device_combo.activated.connect(self._selection_changed)
        layout.addWidget(self.device_combo)
        self.no_devices_label = QLabel(
            "No audio output devices are available. Connect a device to continue."
        )
        self.no_devices_label.setWordWrap(True)
        layout.addWidget(self.no_devices_label)
        buttons = QHBoxLayout()
        buttons.addStretch()
        self.continue_button = QPushButton("Continue")
        self.continue_button.clicked.connect(lambda: self.done(self.CONTINUE))
        buttons.addWidget(self.continue_button)
        self.ok_button = QPushButton("OK")
        self.ok_button.setDefault(True)
        self.ok_button.clicked.connect(self.accept)
        buttons.addWidget(self.ok_button)
        layout.addLayout(buttons)
        self.update_devices(devices)

    def _selection_changed(self, _index):
        self.selection_changed = True

    def selected_device_id(self):
        return self.device_combo.currentData() or ""

    def update_devices(self, devices):
        selected_id = self.selected_device_id()
        self.device_combo.clear()
        if devices:
            default = _default_output(devices)
            self.device_combo.addItem(f"System Default ({default.description()})", "")
            for device in devices:
                self.device_combo.addItem(
                    device.description().strip() or "Unnamed output", _device_id(device)
                )
            index = self.device_combo.findData(selected_id)
            self.device_combo.setCurrentIndex(max(0, index))
            if index < 0:
                self.selection_changed = False
        else:
            self.selection_changed = False
        self.device_combo.setEnabled(bool(devices))
        self.continue_button.setEnabled(bool(devices))
        self.no_devices_label.setVisible(not devices)


class AudioDeviceControllerMixin:
    def _apply_output_device(self, settings):
        """Apply a preferred output, temporarily falling back to the default."""
        device_id = str((settings or {}).get("output_device", "") or "")
        devices = QMediaDevices.audioOutputs()
        selected = next((device for device in devices if _device_id(device) == device_id),
                        _default_output(devices))
        # Track the actual device independently: Qt may update a default output
        # before delivering the notification that the old device disappeared.
        self._active_output_device_id = _device_id(selected)
        self._audio_output_unavailable = selected.isNull()
        if hasattr(self, "volume_slider"):
            self._flush_pcm_output()
        self.audio_output.setDevice(selected)
        if hasattr(self, "volume_slider"):
            # Pitch and speed processing also use a device-specific PCM sink,
            # including when the volume is at or below 100%.
            self._configure_pcm_volume(self.volume_slider.value(), refresh_format=True)

    def _watch_audio_devices(self):
        self._audio_device_dialog = None
        self._media_devices = QMediaDevices(self)
        self._media_devices.audioOutputsChanged.connect(self._handle_audio_outputs_changed)

    def _handle_audio_outputs_changed(self):
        devices = QMediaDevices.audioOutputs()
        available_ids = {_device_id(device) for device in devices}
        active_id = self._active_output_device_id
        disconnected = bool(active_id) and active_id not in available_ids
        playing = self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
        dialog = self._audio_device_dialog

        if disconnected:
            if playing:
                self.media_player.pause()
            self._apply_output_device(self.playback_settings)
            if playing and dialog is None:
                dialog = AudioDeviceDisconnectedDialog(devices, self)
                self._audio_device_dialog = dialog
                dialog.finished.connect(
                    lambda result: self._finish_audio_device_recovery(dialog, result)
                )
                dialog.open()
        elif dialog is None:
            preferred_id = str(self.playback_settings.get("output_device", "") or "")
            # Follow system-default changes, and restore a reconnected preferred
            # device while paused. Adding an unrelated device must not interrupt
            # playback through an explicitly selected output.
            if not playing or not preferred_id:
                selected = next((device for device in devices
                                 if _device_id(device) == preferred_id),
                                _default_output(devices))
                if _device_id(selected) != active_id:
                    self._apply_output_device(self.playback_settings)

        if dialog is not None:
            dialog.update_devices(devices)

    def _recover_disconnected_output(self):
        """Use disconnect recovery instead of a generic PCM error dialog."""
        active_id = self._active_output_device_id
        if active_id and all(_device_id(device) != active_id
                             for device in QMediaDevices.audioOutputs()):
            self._handle_audio_outputs_changed()
            return True
        return False

    def _finish_audio_device_recovery(self, dialog, result):
        if self._audio_device_dialog is not dialog:
            return
        self._audio_device_dialog = None
        device_id = dialog.selected_device_id()
        available_ids = {_device_id(device) for device in QMediaDevices.audioOutputs()}
        if device_id and device_id not in available_ids:
            device_id = ""
        elif dialog.selection_changed:
            # A temporary default fallback must not erase the preferred device.
            self.playback_settings["output_device"] = device_id
            save_playback_settings(self.playback_settings)
        self._apply_output_device({"output_device": device_id})
        dialog.deleteLater()
        if (result == dialog.CONTINUE and not self._audio_output_unavailable
                and self.current_audio_path):
            self.media_player.play()

    def _close_audio_device_dialog(self):
        dialog = self._audio_device_dialog
        self._audio_device_dialog = None
        if dialog is not None:
            dialog.reject()
            dialog.deleteLater()
