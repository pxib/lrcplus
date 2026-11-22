"""Equalizer controls and saved settings."""

from dataclasses import asdict, dataclass
from math import ceil, isfinite

from PySide6.QtCore import Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
    QLabel, QMenu, QPushButton, QScrollArea, QSizePolicy, QSlider, QVBoxLayout, QWidget,
)

from plugins.core.equalizer.dsp import Band, EqualizerProcessor, FREQUENCIES_BY_COUNT, graphic_bands
from plugins.core.equalizer.presets import PRESETS, preset_gains
from plugins.core.equalizer.response import ResponsePlot, response_db
from ui.settings_controls import (
    ThemedComboBox, ThemedDoubleSpinBox, ThemedSpinBox, settings_theme_options, use_native_controls,
)


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
    changed = Signal()

    def __init__(self, parent, gains):
        super().__init__(parent)
        grid = QGridLayout(self)
        grid.setContentsMargins(0, 0, 0, 0)
        grid.setHorizontalSpacing(2)
        grid.setVerticalSpacing(4)
        self.sliders = []
        frequencies = FREQUENCIES_BY_COUNT[len(gains)]
        columns = 8 if len(frequencies) == 31 else 10
        for index, (frequency, gain) in enumerate(zip(frequencies, gains)):
            row, column = divmod(index, columns)
            label = f"{frequency / 1000:g} kHz" if frequency >= 1000 else f"{frequency:g} Hz"
            short_label = f"{frequency / 1000:g}k" if frequency >= 1000 else f"{frequency:g}"
            slider = QSlider(Qt.Orientation.Vertical, self)
            slider.setObjectName("equalizerBandGain")
            slider.setRange(-12, 12)
            slider.setValue(gain)
            slider.setAccessibleName(label)
            slider.setToolTip(f"{label} gain (dB)")
            slider.setFixedHeight(70 if columns == 8 else 74)
            slider.setTickPosition(QSlider.TickPosition.TicksBothSides)
            slider.setTickInterval(6)
            value = QLabel(str(gain) if not gain else f"{gain:+d}", self)
            slider.valueChanged.connect(lambda db, value=value: value.setText(f"{db:+d}" if db else "0"))
            slider.valueChanged.connect(self.changed)
            frequency_label = QLabel(short_label, self)
            frequency_label.setToolTip(label)
            grid.setColumnStretch(column, 1)
            grid.addWidget(value, row * 4, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(slider, row * 4 + 1, column, Qt.AlignmentFlag.AlignCenter)
            grid.addWidget(frequency_label, row * 4 + 2, column, Qt.AlignmentFlag.AlignCenter)
            if row < (len(frequencies) - 1) // columns:
                grid.setRowMinimumHeight(row * 4 + 3, 6)
            self.sliders.append(slider)

    def gains(self):
        return tuple(slider.value() for slider in self.sliders)


class ParametricControls(QWidget):
    changed = Signal()

    def __init__(self, parent, bands):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        grid = QGridLayout()
        grid.setHorizontalSpacing(8)
        grid.setVerticalSpacing(5)
        headings = ("Filter", "Frequency (Hz)", "Gain (dB)", "Q")
        grid.addWidget(QLabel("Band", self), 0, 0)
        for column, heading in enumerate(headings, start=1):
            grid.addWidget(QLabel(heading, self), 0, column)
            grid.setColumnStretch(column, 2 if column == 1 else 1)
        self.rows = []
        for row, band in enumerate(bands, start=1):
            shape = ThemedComboBox(self)
            for key, label in FILTER_LABELS.items():
                shape.addItem(label, key)
            frequency = QDoubleSpinBox(self) if len(bands) == 31 else ThemedSpinBox(self)
            if len(bands) == 31:
                frequency.setDecimals(1)
                frequency.setButtonSymbols(QAbstractSpinBox.ButtonSymbols.NoButtons)
            frequency.setRange(*FREQUENCY_RANGE)
            gain = ThemedDoubleSpinBox(self)
            gain.setDecimals(1)
            gain.setRange(*GAIN_RANGE)
            gain.setSingleStep(0.5)
            q = ThemedDoubleSpinBox(self)
            q.setRange(*Q_RANGE)
            q.setSingleStep(0.1)
            q.setToolTip("Higher Q narrows a bell filter or sharpens a shelf transition.")
            controls = (shape, frequency, gain, q)
            grid.addWidget(QLabel(str(row), self), row, 0, Qt.AlignmentFlag.AlignCenter)
            for column, (name, control) in enumerate(zip(headings, controls), start=1):
                control.setAccessibleName(f"Band {row} {name.lower()}")
                grid.addWidget(control, row, column)
            self.rows.append(controls)
        layout.addLayout(grid)
        self.set_bands(bands)
        for shape, frequency, gain, q in self.rows:
            shape.currentIndexChanged.connect(self.changed)
            for spin in (frequency, gain, q):
                spin.valueChanged.connect(self.changed)

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
        self.setObjectName("equalizerPanel")
        policy = QSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Expanding)
        policy.setVerticalStretch(1)
        self.setSizePolicy(policy)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(5)
        self._curves = curves
        self._controls = {}
        self.enabled = QCheckBox("Enable equalizer", self)
        self.enabled.setChecked(enabled)

        switches = QHBoxLayout()
        switches.addWidget(self.enabled)
        switches.addStretch()
        self.modes = ThemedComboBox(self)
        self.modes.addItem("Graphic", "graphic")
        self.modes.addItem("Parametric", "parametric")
        self.modes.setCurrentIndex(self.modes.findData(mode))
        self.modes.setToolTip("Graphic and Parametric modes keep separate curves.")
        mode_label = QLabel("Mode", self)
        mode_label.setBuddy(self.modes)
        switches.addWidget(mode_label)
        switches.addWidget(self.modes)
        self.counts = ThemedComboBox(self)
        for size in FREQUENCIES_BY_COUNT:
            self.counts.addItem(str(size), size)
        self.counts.setCurrentIndex(self.counts.findData(count))
        self.counts.setToolTip("The 10-band and 31-band layouts keep separate curves.")
        bands_label = QLabel("Bands", self)
        bands_label.setBuddy(self.counts)
        switches.addWidget(bands_label)
        switches.addWidget(self.counts)
        layout.addLayout(switches)

        response_header = QHBoxLayout()
        response_header.addWidget(QLabel("<b>Frequency response</b>", self))
        response_header.addStretch()
        self.preamp = ThemedSpinBox(self)
        self.preamp.setRange(-24, 0)
        self.preamp.setSuffix(" dB")
        self.preamp.setValue(preamp)
        self.preamp.setMinimumWidth(78)
        self.preamp.setToolTip("Lower the preamp to leave headroom when boosting bands.")
        preamp_label = QLabel("Preamp", self)
        preamp_label.setBuddy(self.preamp)
        response_header.addWidget(preamp_label)
        response_header.addWidget(self.preamp)
        layout.addLayout(response_header)

        self.response = ResponsePlot(self)
        layout.addWidget(self.response)
        self._response_timer = QTimer(self)
        self._response_timer.setSingleShot(True)
        self._response_timer.setInterval(24)
        self._response_timer.timeout.connect(self._refresh_response)
        self.preamp.valueChanged.connect(self._queue_response)

        actions = QHBoxLayout()
        self._band_heading = QLabel(self)
        actions.addWidget(self._band_heading)
        actions.addStretch()
        self._copy_graphic = QPushButton("Copy from Graphic", self)
        self._copy_graphic.setToolTip("Replace these filters with the frequencies and gains from Graphic mode.")
        self._copy_graphic.clicked.connect(self._copy_graphic_bands)
        actions.addWidget(self._copy_graphic)
        self.presets = QPushButton("Presets", self)
        self.presets.setToolTip("Load a curve into the current mode and band count. Save to apply it.")
        presets_menu = QMenu(self.presets)
        presets_menu.setObjectName("equalizerPresets")
        presets_menu.addAction("Flat", self._reset)
        presets_menu.addSeparator()
        for name in PRESETS:
            presets_menu.addAction(name, lambda _checked=False, preset=name: self._apply_preset(preset))
        self.presets.setMenu(presets_menu)
        actions.addWidget(self.presets)
        layout.addLayout(actions)

        self._band_scroll = QScrollArea(self)
        self._band_scroll.setFrameShape(QFrame.Shape.NoFrame)
        self._band_scroll.setMinimumHeight(116)
        self._band_scroll.setWidgetResizable(True)
        band_editor = QWidget()
        self._band_layout = QVBoxLayout(band_editor)
        self._band_layout.setContentsMargins(0, 0, 0, 0)
        self._band_layout.addStretch()
        self._band_scroll.setWidget(band_editor)
        layout.addWidget(self._band_scroll, 1)

        self.modes.currentIndexChanged.connect(self._show_active_mode)
        self.counts.currentIndexChanged.connect(self._show_active_mode)
        self._show_active_mode()

    def _create_controls(self, count):
        curve = self._curves[count]
        graphic = GraphicControls(self, curve.gains)
        parametric = ParametricControls(self, curve.parametric)
        theme_mode, *_ = settings_theme_options(self.window().parentWidget())
        use_native_controls(parametric, theme_mode == "qt")
        graphic.changed.connect(self._queue_response)
        parametric.changed.connect(self._queue_response)
        self._band_layout.insertWidget(0, graphic)
        self._band_layout.insertWidget(0, parametric)
        self._controls[count] = graphic, parametric

    def _show_active_mode(self):
        count = self.count()
        if count not in self._controls:
            self._create_controls(count)
        graphic_mode = self.mode() == "graphic"
        for size, (graphic, parametric) in self._controls.items():
            graphic.setVisible(size == count and graphic_mode)
            parametric.setVisible(size == count and not graphic_mode)
        self._band_heading.setText("<b>Band gain</b> (dB)" if graphic_mode else "<b>Filters</b>")
        self._copy_graphic.setVisible(not graphic_mode)
        self._band_scroll.verticalScrollBar().setValue(0)
        self._refresh_response()

    def _queue_response(self):
        if not self._response_timer.isActive():
            self._response_timer.start()

    def _refresh_response(self):
        self._response_timer.stop()
        bands = (graphic_bands(self.graphic.gains()) if self.mode() == "graphic"
                 else self.parametric.bands())
        self.response.set_response(self.preamp.value(), bands)

    def _copy_graphic_bands(self):
        self.parametric.set_bands(graphic_bands(self.graphic.gains()))

    def _apply_preset(self, name):
        gains = preset_gains(name, self.count())
        bands = graphic_bands(gains)
        if self.mode() == "graphic":
            for slider, gain in zip(self.graphic.sliders, gains):
                slider.setValue(gain)
        else:
            self.parametric.set_bands(bands)
        # Leave headroom for the combined boost, including overlapping bands.
        peak = round(float(response_db(0, bands).max()), 3)
        self.preamp.setValue(-ceil(max(0, peak)))
        self._refresh_response()

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
        self._plugin_id = context.plugin_id
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
        )
        context.on("app_ready", self._on_app_ready)
        self._api.add_menu_action("Plugins", "Equalizer…", self._open_settings)

    def _on_app_ready(self, window):
        self._window = window

    def _open_settings(self):
        if self._window is None:
            return
        from ui.settings_dialog import PluginSettingsDialog

        dialog = PluginSettingsDialog(
            self._window.plugin_manager, self._plugin_id, "Equalizer", parent=self._window,
        )
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

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
