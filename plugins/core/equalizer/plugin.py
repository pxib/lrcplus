"""Equalizer controls and saved settings."""

from dataclasses import asdict, dataclass
from math import ceil, isfinite

from PySide6.QtCore import QSignalBlocker, Signal, Qt, QTimer
from PySide6.QtWidgets import (
    QAbstractSpinBox, QCheckBox, QDoubleSpinBox, QFrame, QGridLayout, QHBoxLayout,
    QInputDialog, QLabel, QMenu, QMessageBox, QPushButton, QScrollArea, QSizePolicy,
    QSlider, QVBoxLayout, QWidget,
)

from plugins.core.equalizer.dsp import (
    Band, EqualizerProcessor, FREQUENCIES_BY_COUNT, graphic_bands, load_dsp_dependencies,
)
from plugins.core.equalizer.presets import PRESETS, preset_gains
from ui.settings_controls import (
    ThemedComboBox, ThemedDoubleSpinBox, ThemedSpinBox, settings_theme_options, use_native_controls,
)


FILTER_LABELS = {"bell": "Bell", "low_shelf": "Low shelf", "high_shelf": "High shelf"}
FREQUENCY_RANGE = (20, 20000)
GAIN_RANGE = (-18, 18)
Q_RANGE = (0.1, 18)
PRESET_NAME_LIMIT = 64


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


def eq_configuration(enabled, preamp, bands):
    active_bands = tuple(band for band in bands if band.gain)
    return (preamp, active_bands) if enabled and (preamp or active_bands) else None


def clean_preset_name(value):
    name = value.strip() if isinstance(value, str) else ""
    return name if 0 < len(name) <= PRESET_NAME_LIMIT and name.isprintable() else None


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


@dataclass(frozen=True)
class SavedPreset:
    mode: str
    count: int
    preamp: int
    curve: Curve

    @classmethod
    def from_settings(cls, saved):
        if not isinstance(saved, dict):
            return None
        mode, count = saved.get("mode"), saved.get("band_count")
        if mode not in ("graphic", "parametric") or type(count) is not int or count not in FREQUENCIES_BY_COUNT:
            return None
        values = saved.get("gains" if mode == "graphic" else "parametric")
        if not isinstance(values, list) or len(values) != count:
            return None
        preamp = saved.get("preamp")
        return cls(mode, count, max(-24, min(0, preamp)) if type(preamp) is int else 0,
                   Curve.from_settings(saved, count))

    def to_settings(self):
        key = "gains" if self.mode == "graphic" else "parametric"
        return {"mode": self.mode, "band_count": self.count, "preamp": self.preamp,
                key: self.curve.to_settings()[key]}


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

    def set_gains(self, gains):
        for slider, gain in zip(self.sliders, gains):
            slider.setValue(gain)


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
    preview_changed = Signal(object)
    preview_ended = Signal()

    def __init__(self, parent, enabled, preamp, curves, mode, count, *, get_presets, store_presets):
        from plugins.core.equalizer.response import ResponsePlot

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
        self._previewing = False
        self._get_presets = get_presets
        self._store_presets = store_presets
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
        self.bypass = QCheckBox("Bypass", self)
        self.bypass.setEnabled(enabled)
        self.bypass.setToolTip("Temporarily listen without equalization. This does not affect Save.")
        response_header.addWidget(self.bypass)
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
        self.enabled.toggled.connect(self.bypass.setEnabled)
        self.enabled.toggled.connect(self._refresh_response)
        self.bypass.toggled.connect(self._refresh_response)

        actions = QHBoxLayout()
        self._band_heading = QLabel(self)
        actions.addWidget(self._band_heading)
        actions.addStretch()
        self._copy_graphic = QPushButton("Copy from Graphic", self)
        self._copy_graphic.setToolTip("Replace these filters with the frequencies and gains from Graphic mode.")
        self._copy_graphic.clicked.connect(self._copy_graphic_bands)
        actions.addWidget(self._copy_graphic)
        self.presets = QPushButton("Presets", self)
        self.presets.setToolTip("Try a preset in the current mode and band count. Save to keep it.")
        presets_menu = QMenu(self.presets)
        presets_menu.setObjectName("equalizerPresets")
        presets_menu.addAction("Flat", self._reset)
        presets_menu.addSeparator()
        for name in PRESETS:
            presets_menu.addAction(name, lambda _checked=False, preset=name: self._apply_preset(preset))
        presets_menu.addSeparator()
        self._my_presets_menu = presets_menu.addMenu("My Presets")
        presets_menu.addAction("Save current as…", self._save_preset)
        self._delete_preset_menu = presets_menu.addMenu("Delete preset")
        for menu in (self._my_presets_menu, self._delete_preset_menu):
            menu.setObjectName("equalizerPresets")
        presets_menu.aboutToShow.connect(self._refresh_preset_menus)
        self._refresh_preset_menus()
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
        preamp = self.preamp.value()
        self.response.set_response(preamp, bands)
        if self._previewing:
            enabled = self.enabled.isChecked() and not self.bypass.isChecked()
            self.preview_changed.emit(eq_configuration(enabled, preamp, bands))

    def showEvent(self, event):
        super().showEvent(event)
        self._previewing = True
        self._refresh_response()

    def hideEvent(self, event):
        self._previewing = False
        self._response_timer.stop()
        self.preview_ended.emit()
        super().hideEvent(event)

    def _copy_graphic_bands(self):
        self.parametric.set_bands(graphic_bands(self.graphic.gains()))

    def _apply_preset(self, name):
        from plugins.core.equalizer.response import response_db

        gains = preset_gains(name, self.count())
        bands = graphic_bands(gains)
        if self.mode() == "graphic":
            self.graphic.set_gains(gains)
        else:
            self.parametric.set_bands(bands)
        # Leave headroom for the combined boost, including overlapping bands.
        peak = round(float(response_db(0, bands).max()), 3)
        self.preamp.setValue(-ceil(max(0, peak)))
        self._refresh_response()

    def _refresh_preset_menus(self):
        self._my_presets_menu.clear()
        self._delete_preset_menu.clear()
        presets = self._get_presets()
        for name in sorted(presets, key=str.casefold):
            label = name.replace("&", "&&")
            self._my_presets_menu.addAction(label, lambda _checked=False, key=name: self._load_preset(key))
            self._delete_preset_menu.addAction(label, lambda _checked=False, key=name: self._delete_preset(key))
        self._my_presets_menu.setEnabled(bool(presets))
        self._delete_preset_menu.setEnabled(bool(presets))

    def _save_preset(self):
        name, accepted = QInputDialog.getText(self, "Save EQ Preset", "Preset name")
        if not accepted:
            return
        name = clean_preset_name(name)
        if name is None:
            QMessageBox.warning(self, "Save EQ Preset", f"Enter a name of 1 to {PRESET_NAME_LIMIT} printable characters.")
            return
        presets = self._get_presets()
        existing = next((key for key in presets if key.casefold() == name.casefold()), None)
        if existing is not None:
            answer = QMessageBox.question(self, "Replace EQ Preset", "Replace the preset with this name?",
                                          QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                          QMessageBox.StandardButton.No)
            if answer != QMessageBox.StandardButton.Yes:
                return
            name = existing
        curve = Curve(self.graphic.gains(), self.parametric.bands())
        preset = SavedPreset(self.mode(), self.count(), self.preamp.value(), curve)
        self._save_preset_collection(presets | {name: preset}, "Save EQ Preset")

    def _load_preset(self, name):
        preset = self._get_presets().get(name)
        if preset is None:
            return
        with QSignalBlocker(self.modes), QSignalBlocker(self.counts):
            self.modes.setCurrentIndex(self.modes.findData(preset.mode))
            self.counts.setCurrentIndex(self.counts.findData(preset.count))
        if preset.count not in self._controls:
            self._create_controls(preset.count)
        if preset.mode == "graphic":
            self.graphic.set_gains(preset.curve.gains)
        else:
            self.parametric.set_bands(preset.curve.parametric)
        self.preamp.setValue(preset.preamp)
        self._show_active_mode()

    def _delete_preset(self, name):
        answer = QMessageBox.question(self, "Delete EQ Preset", "Delete the selected preset?",
                                      QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                      QMessageBox.StandardButton.No)
        if answer == QMessageBox.StandardButton.Yes:
            presets = self._get_presets().copy()
            presets.pop(name, None)
            self._save_preset_collection(presets, "Delete EQ Preset")

    def _save_preset_collection(self, presets, title):
        try:
            self._store_presets(presets)
        except OSError:
            QMessageBox.warning(self, title, "Could not write your EQ presets.")
        else:
            self._refresh_preset_menus()

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
        self._preview_owner = None
        stored_presets = self._api.get_setting("custom_presets", {})
        self._custom_presets = {}
        if isinstance(stored_presets, dict):
            for name, data in stored_presets.items():
                name = clean_preset_name(name)
                preset = SavedPreset.from_settings(data)
                if name is not None and preset is not None:
                    self._custom_presets[name] = preset
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
        load_dsp_dependencies()
        panel = EqualizerPanel(
            parent, self._enabled, self._preamp, self._curves, self._mode, self._count,
            get_presets=lambda: self._custom_presets, store_presets=self._store_custom_presets,
        )
        owner = object()
        panel.preview_changed.connect(lambda configuration: self._preview(owner, configuration))
        panel.preview_ended.connect(lambda: self._end_preview(owner))
        panel.destroyed.connect(lambda: self._end_preview(owner))
        return panel

    def _store_custom_presets(self, presets):
        self._api.set_setting("custom_presets", {name: preset.to_settings() for name, preset in presets.items()})
        self._custom_presets = presets

    def _apply_panel(self, panel):
        enabled = panel.enabled.isChecked()
        preamp = panel.preamp.value()
        mode, count = panel.mode(), panel.count()
        curves = panel.curves()
        self._api.set_setting("equalizer", {
            **curves[10].to_settings(),
            "enabled": enabled, "preamp": preamp, "mode": mode,
            "band_count": count, "expanded": curves[31].to_settings(),
        })
        self._enabled, self._preamp = enabled, preamp
        self._mode, self._count, self._curves = mode, count, curves
        self._update_configuration()

    def _update_configuration(self):
        curve = self._curves[self._count]
        bands = curve.parametric if self._mode == "parametric" else graphic_bands(curve.gains)
        self._set_configuration(eq_configuration(self._enabled, self._preamp, bands))

    def _set_configuration(self, configuration):
        was_active = self.configuration is not None
        if configuration is not None and not was_active:
            # Load the backend before publishing the configuration to the audio worker.
            load_dsp_dependencies()
        self.configuration = configuration
        if self._window is not None and was_active != (configuration is not None):
            self._window._refresh_equalizer_route()

    def _preview(self, owner, configuration):
        if self._window is None:
            return
        self._preview_owner = owner
        if configuration is None and self.configuration is not None:
            # Keep the PCM route warm for A/B switching with an identity filter.
            configuration = (0, ())
        self._set_configuration(configuration)

    def _end_preview(self, owner):
        if owner is self._preview_owner:
            self._preview_owner = None
            self._update_configuration()

    def on_unload(self, context):
        self._preview_owner = None
        self._set_configuration(None)
        self._window = None


PLUGIN_CLASS = EqualizerPlugin
