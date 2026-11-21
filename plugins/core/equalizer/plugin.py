"""Equalizer controls and saved settings."""

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QGridLayout, QLabel, QPushButton, QSlider,
    QSpinBox, QVBoxLayout, QWidget,
)

from plugins.core.equalizer.dsp import FREQUENCIES, EqualizerProcessor


class EqualizerPanel(QWidget):
    def __init__(self, parent, enabled, preamp, gains):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        self.enabled = QCheckBox("Enable equalizer", self)
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)

        form = QFormLayout()
        self.preamp = QSpinBox(self)
        self.preamp.setRange(-24, 0)
        self.preamp.setSuffix(" dB")
        self.preamp.setValue(preamp)
        self.preamp.setToolTip("Lower the preamp to leave headroom when boosting bands.")
        form.addRow("Preamp", self.preamp)
        layout.addLayout(form)

        grid = QGridLayout()
        self.bands = []
        for column, (frequency, gain) in enumerate(zip(FREQUENCIES, gains)):
            label = f"{frequency / 1000:g} kHz" if frequency >= 1000 else f"{frequency} Hz"
            slider = QSlider(Qt.Orientation.Vertical, self)
            slider.setRange(-12, 12)
            slider.setValue(gain)
            slider.setAccessibleName(label)
            slider.setMinimumHeight(120)
            value = QLabel(f"{gain:+d} dB", self)
            slider.valueChanged.connect(lambda db, value=value: value.setText(f"{db:+d} dB"))
            grid.addWidget(value, 0, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(slider, 1, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(QLabel(label, self), 2, column, Qt.AlignmentFlag.AlignCenter)
            self.bands.append(slider)
        layout.addLayout(grid)

        reset = QPushButton("Reset to flat", self)
        reset.clicked.connect(self._reset)
        layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)

    def _reset(self):
        self.preamp.setValue(0)
        for slider in self.bands:
            slider.setValue(0)


class EqualizerPlugin:
    configuration = None
    _window = None

    def on_load(self, context):
        self._api = context.api
        saved = self._api.get_setting("equalizer", {})
        if not isinstance(saved, dict):
            saved = {}
        self._enabled = saved.get("enabled") is True
        preamp = saved.get("preamp")
        self._preamp = max(-24, min(0, preamp)) if type(preamp) is int else 0
        gains = saved.get("gains")
        if not isinstance(gains, list) or len(gains) != len(FREQUENCIES):
            gains = [0] * len(FREQUENCIES)
        self._gains = tuple(max(-12, min(12, gain)) if type(gain) is int else 0 for gain in gains)
        self._update_configuration()
        self._api.register_service("playback_equalizer", self)
        self._api.register_settings_widget(
            "Equalizer", self._create_panel, apply=self._apply_panel,
            description="Adjust ten frequency bands and playback headroom.",
        )
        context.on("app_ready", self._on_app_ready)

    def _on_app_ready(self, window):
        self._window = window

    def create_processor(self):
        return EqualizerProcessor(lambda: self.configuration)

    def _create_panel(self, parent):
        return EqualizerPanel(parent, self._enabled, self._preamp, self._gains)

    def _apply_panel(self, panel):
        self._enabled = panel.enabled.isChecked()
        self._preamp = panel.preamp.value()
        self._gains = tuple(slider.value() for slider in panel.bands)
        self._api.set_setting("equalizer", {
            "enabled": self._enabled, "preamp": self._preamp, "gains": list(self._gains),
        })
        self._update_configuration()

    def _update_configuration(self):
        was_active = self.configuration is not None
        self.configuration = ((self._preamp, self._gains)
                              if self._enabled and (self._preamp or any(self._gains)) else None)
        self._refresh_route(was_active)

    def _refresh_route(self, was_active):
        if self._window is not None and was_active != (self.configuration is not None):
            self._window._refresh_equalizer_route()

    def on_unload(self, context):
        was_active = self.configuration is not None
        self.configuration = None
        self._refresh_route(was_active)
        self._window = None


PLUGIN_CLASS = EqualizerPlugin
