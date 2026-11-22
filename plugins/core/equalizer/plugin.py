"""Equalizer controls and saved settings."""

from dataclasses import asdict, dataclass
from math import isfinite

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFormLayout, QGridLayout, QHBoxLayout,
    QLabel, QPushButton, QSlider, QSpinBox, QVBoxLayout, QWidget,
)

from plugins.core.equalizer.dsp import Band, EqualizerProcessor, FREQUENCIES_BY_COUNT, graphic_bands
from ui.settings_controls import ThemedComboBox


FILTER_LABELS = {"bell": "Bell", "low_shelf": "Low shelf", "high_shelf": "High shelf"}
FREQUENCY_RANGE = (20, 20000)
GAIN_RANGE = (-18, 18)
Q_RANGE = (0.1, 18)


def bounded_number(value, minimum, maximum, default):
    if type(value) is int or (type(value) is float and isfinite(value)):
        return min(maximum, max(minimum, value))
    return default


def read_band(saved, default):
    if not isinstance(saved, dict):
        return default
    shape = saved.get("shape")
    return Band(
        frequency=round(bounded_number(saved.get("frequency"), *FREQUENCY_RANGE, default.frequency), 1),
        gain=round(bounded_number(saved.get("gain"), *GAIN_RANGE, 0), 1),
        q=round(bounded_number(saved.get("q"), *Q_RANGE, default.q), 2),
        shape=shape if isinstance(shape, str) and shape in FILTER_LABELS else "bell",
    )


@dataclass(frozen=True)
class Curve:
    gains: tuple[int, ...]
    parametric: tuple[Band, ...]

    @classmethod
    def from_settings(cls, saved, count):
        if not isinstance(saved, dict):
            saved = {}
        gains = saved.get("gains")
        if not isinstance(gains, list) or len(gains) != count:
            gains = [0] * count
        gains = tuple(max(-12, min(12, gain)) if type(gain) is int else 0 for gain in gains)
        parametric = saved.get("parametric")
        if not isinstance(parametric, list) or len(parametric) != count:
            parametric = [None] * count
        defaults = graphic_bands((0,) * count)
        return cls(gains, tuple(read_band(band, default) for band, default in zip(parametric, defaults)))

    def to_settings(self):
        return {"gains": list(self.gains), "parametric": [asdict(band) for band in self.parametric]}


class GraphicControls(QWidget):
    def __init__(self, parent, gains):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        self.sliders = []
        frequencies = FREQUENCIES_BY_COUNT[len(gains)]
        columns = 8 if len(frequencies) == 31 else 10
        for index, (frequency, gain) in enumerate(zip(frequencies, gains)):
            row, column = divmod(index, columns)
            label = f"{frequency / 1000:g} kHz" if frequency >= 1000 else f"{frequency:g} Hz"
            slider = QSlider(Qt.Orientation.Vertical, self)
            slider.setRange(-12, 12)
            slider.setValue(gain)
            slider.setAccessibleName(label)
            slider.setMinimumHeight(88 if columns == 8 else 120)
            value = QLabel(f"{gain:+d} dB", self)
            slider.valueChanged.connect(lambda db, value=value: value.setText(f"{db:+d} dB"))
            grid.addWidget(value, row * 3, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(slider, row * 3 + 1, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(QLabel(label, self), row * 3 + 2, column, Qt.AlignmentFlag.AlignCenter)
            self.sliders.append(slider)

    def gains(self):
        return tuple(slider.value() for slider in self.sliders)


class ParametricControls(QWidget):
    def __init__(self, parent, bands):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(1)
        headings = ("Filter", "Frequency (Hz)", "Gain (dB)", "Q")
        for column, heading in enumerate(headings):
            grid.addWidget(QLabel(heading, self), 0, column)
        self.rows = []
        for row, band in enumerate(bands, start=1):
            shape = ThemedComboBox(self)
            for key, label in FILTER_LABELS.items():
                shape.addItem(label, key)
            frequency = QDoubleSpinBox(self) if len(bands) == 31 else QSpinBox(self)
            if len(bands) == 31:
                frequency.setDecimals(1)
                frequency.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            frequency.setRange(*FREQUENCY_RANGE)
            gain = QDoubleSpinBox(self)
            gain.setDecimals(1)
            gain.setRange(*GAIN_RANGE)
            gain.setSingleStep(0.5)
            q = QDoubleSpinBox(self)
            q.setRange(*Q_RANGE)
            q.setSingleStep(0.1)
            q.setToolTip("Higher Q narrows a bell filter or sharpens a shelf transition.")
            controls = (shape, frequency, gain, q)
            for column, (name, control) in enumerate(zip(headings, controls)):
                control.setAccessibleName(f"Band {row} {name.lower()}")
                grid.addWidget(control, row, column)
            self.rows.append(controls)
        layout.addLayout(grid)
        self.set_bands(bands)

    def set_bands(self, bands):
        for band, (shape, frequency, gain, q) in zip(bands, self.rows):
            shape.setCurrentIndex(shape.findData(band.shape))
            frequency.setValue(band.frequency if len(self.rows) == 31 else int(band.frequency))
            gain.setValue(band.gain)
            q.setValue(band.q)

    def bands(self):
        return tuple(Band(frequency.value(), gain.value(), q.value(), shape.currentData())
                     for shape, frequency, gain, q in self.rows)


class EqualizerPanel(QWidget):
    def __init__(self, parent, enabled, preamp, curves, mode, count):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self._curves = curves
        self._controls = {}
        self.enabled = QCheckBox("Enable equalizer", self)
        self.enabled.setChecked(enabled)
        layout.addWidget(self.enabled)

        form = QFormLayout()
        self.modes = ThemedComboBox(self)
        self.modes.addItem("Graphic", "graphic")
        self.modes.addItem("Parametric", "parametric")
        self.modes.setCurrentIndex(self.modes.findData(mode))
        self.counts = ThemedComboBox(self)
        for size in FREQUENCIES_BY_COUNT:
            self.counts.addItem(str(size), size)
        self.counts.setCurrentIndex(self.counts.findData(count))
        selectors = QHBoxLayout()
        selectors.addWidget(self.modes, 1)
        label = QLabel("Bands", self)
        label.setBuddy(self.counts)
        selectors.addWidget(label)
        selectors.addWidget(self.counts)
        mode_label = QLabel("Mode", self)
        mode_label.setBuddy(self.modes)
        form.addRow(mode_label, selectors)
        self.preamp = QSpinBox(self)
        self.preamp.setRange(-24, 0)
        self.preamp.setSuffix(" dB")
        self.preamp.setValue(preamp)
        self.preamp.setToolTip("Lower the preamp to leave headroom when boosting bands.")
        form.addRow("Preamp", self.preamp)
        layout.addLayout(form)

        reset = QPushButton("Reset to flat", self)
        reset.clicked.connect(self._reset)
        layout.addWidget(reset, alignment=Qt.AlignmentFlag.AlignLeft)

        self.modes.currentIndexChanged.connect(self._show_active_mode)
        self.counts.currentIndexChanged.connect(self._show_active_mode)
        self._show_active_mode()

    def _create_controls(self, count):
        curve = self._curves[count]
        graphic = GraphicControls(self, curve.gains)
        parametric = ParametricControls(self, curve.parametric)
        copy_graphic = QPushButton("Copy graphic bands", parametric)
        copy_graphic.setToolTip("Replace these filters with the frequencies and gains from Graphic mode.")
        copy_graphic.clicked.connect(
            lambda: parametric.set_bands(graphic_bands(graphic.gains())),
        )
        parametric.layout().insertWidget(0, copy_graphic, alignment=Qt.AlignmentFlag.AlignLeft)
        self.layout().addWidget(graphic)
        self.layout().addWidget(parametric)
        self._controls[count] = graphic, parametric

    def _show_active_mode(self):
        count = self.count()
        if count not in self._controls:
            self._create_controls(count)
        for size, (graphic, parametric) in self._controls.items():
            graphic.setVisible(size == count and self.mode() == "graphic")
            parametric.setVisible(size == count and self.mode() == "parametric")

    @property
    def graphic(self):
        return self._controls[self.count()][0]

    @property
    def parametric(self):
        return self._controls[self.count()][1]

    def _reset(self):
        self.preamp.setValue(0)
        if self.mode() == "graphic":
            for slider in self.graphic.sliders:
                slider.setValue(0)
        else:
            for _, _, gain, _ in self.parametric.rows:
                gain.setValue(0)

    def curves(self):
        return self._curves | {
            size: Curve(graphic.gains(), parametric.bands())
            for size, (graphic, parametric) in self._controls.items()
        }

    def count(self):
        return self.counts.currentData()

    def mode(self):
        return self.modes.currentData()


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
        self._mode = "parametric" if saved.get("mode") == "parametric" else "graphic"
        self._count = 31 if saved.get("band_count") == 31 else 10
        self._curves = {10: Curve.from_settings(saved, 10), 31: Curve.from_settings(saved.get("expanded"), 31)}
        self._update_configuration()
        self._api.register_service("playback_equalizer", self)
        self._api.register_settings_widget(
            "Equalizer", self._create_panel, apply=self._apply_panel,
            description="Save applies the selected mode and band count. Each combination keeps its own bands.",
        )
        context.on("app_ready", self._on_app_ready)

    def _on_app_ready(self, window):
        self._window = window

    def create_processor(self):
        return EqualizerProcessor(lambda: self.configuration)

    def _create_panel(self, parent):
        return EqualizerPanel(parent, self._enabled, self._preamp, self._curves, self._mode, self._count)

    def _apply_panel(self, panel):
        self._enabled = panel.enabled.isChecked()
        self._preamp = panel.preamp.value()
        self._mode = panel.mode()
        self._count = panel.count()
        self._curves = panel.curves()
        self._api.set_setting("equalizer", {
            **self._curves[10].to_settings(),
            "enabled": self._enabled, "preamp": self._preamp, "mode": self._mode,
            "band_count": self._count, "expanded": self._curves[31].to_settings(),
        })
        self._update_configuration()

    def _update_configuration(self):
        was_active = self.configuration is not None
        curve = self._curves[self._count]
        bands = curve.parametric if self._mode == "parametric" else graphic_bands(curve.gains)
        active_bands = tuple(band for band in bands if band.gain)
        self.configuration = ((self._preamp, active_bands)
                              if self._enabled and (self._preamp or active_bands) else None)
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
