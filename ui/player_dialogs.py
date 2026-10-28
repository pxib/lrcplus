import math

from PySide6.QtCore import Qt, QTimer, Signal, QThread
from PySide6.QtGui import QPixmap, QPainter
from PySide6.QtWidgets import (
    QDialog,
    QComboBox,
    QCheckBox,
    QDoubleSpinBox,
    QGraphicsScene,
    QGraphicsView,
    QHBoxLayout,
    QLabel,
    QPushButton,
    QSlider,
    QStyledItemDelegate,
    QToolButton,
    QVBoxLayout,
)
from core.music_library import scan_folders

class ClickableSlider(QSlider):
    """A slider that seeks immediately when its groove is clicked."""

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            if self.orientation() == Qt.Orientation.Horizontal:
                span = self.width()
                position = event.position().x()
            else:
                span = self.height()
                position = self.height() - event.position().y()

            if span > 0:
                ratio = max(0.0, min(1.0, position / span))
                value = self.minimum() + ratio * (
                    self.maximum() - self.minimum()
                )
                self.setValue(round(value))
                self.sliderMoved.emit(self.value())

        super().mousePressEvent(event)


class ClickableTimeLabel(QLabel):
    """A time label that emits a click for switching its display mode."""

    clicked = Signal()

    def mousePressEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mousePressEvent(event)


class AlbumCoverDelegate(QStyledItemDelegate):
    """Paint embedded artwork strictly inside the current cover-column bounds."""

    def __init__(self, parent=None, cover_cache=None):
        super().__init__(parent)
        self._cover_cache = cover_cache

    def paint(self, painter, option, index):
        if self._cover_cache is None:
            cover = index.data(Qt.ItemDataRole.DecorationRole)
        else:
            cover = self._cover_cache.get(str(index.data(Qt.ItemDataRole.UserRole)))
        if isinstance(cover, QPixmap) and not cover.isNull():
            painter.save()
            # Keep a small margin and scale to whatever width the user gives this column.
            rect = option.rect.adjusted(4, 3, -4, -3)
            if rect.width() > 0 and rect.height() > 0:
                scaled = cover.scaled(
                    rect.size(),
                    Qt.AspectRatioMode.KeepAspectRatio,
                    Qt.TransformationMode.SmoothTransformation,
                )
                x = rect.x() + (rect.width() - scaled.width()) // 2
                y = rect.y() + (rect.height() - scaled.height()) // 2
                painter.setClipRect(option.rect)
                painter.drawPixmap(x, y, scaled)
            painter.restore()
            return
        super().paint(painter, option, index)


class CoverPreviewView(QGraphicsView):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        scene = QGraphicsScene(self)
        self.image_item = scene.addPixmap(pixmap)
        self.setScene(scene)
        self.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform)
        self.setTransformationAnchor(QGraphicsView.ViewportAnchor.AnchorUnderMouse)
        self.setDragMode(QGraphicsView.DragMode.ScrollHandDrag)

    def fit_to_view(self):
        self.resetTransform()
        self.fitInView(self.image_item, Qt.AspectRatioMode.KeepAspectRatio)

    def set_actual_size(self):
        self.resetTransform()
        self.centerOn(self.image_item)

    def zoom(self, factor):
        current_scale = self.transform().m11()
        if 0.05 <= current_scale * factor <= 8:
            self.scale(factor, factor)

    def wheelEvent(self, event):
        delta = event.angleDelta().y()
        if delta:
            self.zoom(1.2 if delta > 0 else 1 / 1.2)
            event.accept()
            return
        super().wheelEvent(event)


class CoverPreviewDialog(QDialog):
    def __init__(self, pixmap, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Album Cover")
        self.resize(900, 700)

        self.view = CoverPreviewView(pixmap, self)
        controls = QHBoxLayout()
        zoom_out = QToolButton(self)
        zoom_out.setText("-")
        zoom_out.setToolTip("Zoom out")
        zoom_in = QToolButton(self)
        zoom_in.setText("+")
        zoom_in.setToolTip("Zoom in")
        fit = QPushButton("Fit", self)
        actual_size = QPushButton("100%", self)
        zoom_out.clicked.connect(lambda: self.view.zoom(1 / 1.2))
        zoom_in.clicked.connect(lambda: self.view.zoom(1.2))
        fit.clicked.connect(self.view.fit_to_view)
        actual_size.clicked.connect(self.view.set_actual_size)
        controls.addWidget(zoom_out)
        controls.addWidget(zoom_in)
        controls.addWidget(fit)
        controls.addWidget(actual_size)
        controls.addStretch(1)

        layout = QVBoxLayout(self)
        layout.addLayout(controls)
        layout.addWidget(self.view, 1)
        QTimer.singleShot(0, self.view.fit_to_view)


class PitchChangerDialog(QDialog):
    """Compact, live pitch-shift controls with selectable display units."""

    def __init__(self, semitones, mode, on_change, parent=None, *, speed_percent=100,
                 link_pitch_to_speed=False, on_speed_change=None,
                 on_link_change=None):
        super().__init__(parent)
        self.setWindowTitle("Pitch changer")
        self.setMinimumWidth(380)
        self._on_change = on_change

        heading = QLabel("Pitch changer")
        heading.setStyleSheet("font-size: 19px; font-weight: 600;")
        subtitle = QLabel("Set a pitch offset; linked speed adds its natural pitch change")
        subtitle.setStyleSheet("color: #9aaab5;")

        speed_layout = QHBoxLayout()
        speed_layout.addWidget(QLabel("Speed"))
        self.speed_slider = QSlider(Qt.Orientation.Horizontal)
        self.speed_slider.setRange(25, 200)
        self.speed_slider.setValue(int(speed_percent))
        self.speed_slider.setToolTip("Playback speed from 25% to 200%")
        self.speed_label = QLabel(f"{int(speed_percent)}%")
        self.speed_label.setFixedWidth(48)
        speed_layout.addWidget(self.speed_slider, 1)
        speed_layout.addWidget(self.speed_label)

        self.link_pitch_checkbox = QCheckBox("Link pitch to speed")
        self.link_pitch_checkbox.setToolTip(
            "When enabled, speed changes pitch like a tape deck and manual pitch controls are disabled."
        )
        self.link_pitch_checkbox.setChecked(bool(link_pitch_to_speed))

        self.unit_combo = QComboBox()
        self.unit_combo.addItems(("Semitones", "Octaves", "Percent"))
        self.unit_combo.setCurrentText(mode if mode in {"Semitones", "Octaves", "Percent"} else "Semitones")
        units_layout = QHBoxLayout()
        self.units_label = QLabel("Units")
        units_layout.addWidget(self.units_label)
        units_layout.addStretch(1)
        units_layout.addWidget(self.unit_combo)

        self.readout = QLabel()
        self.readout.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.readout.setStyleSheet("font-size: 34px; font-weight: 600; padding: 5px;")

        self.interval = QLabel()
        self.interval.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.interval.setStyleSheet("color: #9aaab5; font-size: 13px;")

        self.value_spin = QDoubleSpinBox()
        self._configure_value_spin()
        self.value_spin.setKeyboardTracking(False)
        self.value_spin.setToolTip("Pitch shift in semitones")

        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.setRange(-240, 240)
        self.slider.setSingleStep(1)
        self.slider.setPageStep(10)
        self.slider.setToolTip("Drag to shift pitch; each major interval is one semitone")

        extremes = QHBoxLayout()
        low = QLabel("-24 st")
        high = QLabel("+24 st")
        low.setStyleSheet("color: #82939e;")
        high.setStyleSheet("color: #82939e;")
        extremes.addWidget(low)
        extremes.addStretch(1)
        extremes.addWidget(high)

        quick_layout = QHBoxLayout()
        quick_layout.setSpacing(6)
        self.quick_pitch_buttons = []
        for label, shift in (("-12", -12), ("-7", -7), ("-5", -5), ("-2", -2),
                             ("Reset", 0), ("+2", 2), ("+5", 5), ("+7", 7), ("+12", 12)):
            button = QPushButton(label)
            button.setToolTip("Reset to concert pitch" if shift == 0 else f"Set to {shift:+d} semitones")
            button.clicked.connect(lambda _checked=False, value=shift: self.set_value(value))
            quick_layout.addWidget(button)
            self.quick_pitch_buttons.append(button)

        fine_layout = QHBoxLayout()
        down_button = QToolButton()
        down_button.setText("-")
        down_button.setToolTip("Lower by 0.1 semitone")
        up_button = QToolButton()
        up_button.setText("+")
        up_button.setToolTip("Raise by 0.1 semitone")
        self.fine_tune_label = QLabel("Fine tune")
        fine_layout.addWidget(self.fine_tune_label)
        fine_layout.addStretch(1)
        fine_layout.addWidget(down_button)
        fine_layout.addWidget(self.value_spin)
        fine_layout.addWidget(up_button)

        close_button = QPushButton("Done")
        close_button.clicked.connect(self.accept)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(22, 20, 22, 18)
        layout.setSpacing(12)
        layout.addWidget(heading)
        layout.addWidget(subtitle)
        layout.addLayout(speed_layout)
        layout.addWidget(self.link_pitch_checkbox)
        layout.addLayout(units_layout)
        layout.addSpacing(4)
        layout.addWidget(self.readout)
        layout.addWidget(self.interval)
        layout.addWidget(self.slider)
        layout.addLayout(extremes)
        layout.addLayout(quick_layout)
        layout.addLayout(fine_layout)
        layout.addWidget(close_button)

        self.slider.valueChanged.connect(self._from_slider)
        self.value_spin.valueChanged.connect(self._from_spin)
        self.unit_combo.currentTextChanged.connect(self._on_units_changed)
        self.speed_slider.valueChanged.connect(self._on_speed_changed)
        if on_speed_change is not None:
            self._on_speed_change = on_speed_change
        else:
            self._on_speed_change = lambda _value: None
        self._on_link_change = on_link_change
        self.link_pitch_checkbox.toggled.connect(self._on_link_toggled)
        down_button.clicked.connect(lambda: self.set_value(self.slider.value() / 10.0 - 0.1))
        up_button.clicked.connect(lambda: self.set_value(self.slider.value() / 10.0 + 0.1))
        self.set_value(semitones, notify=False)

        self._pitch_controls = [
            self.units_label, self.unit_combo, self.readout, self.interval,
            self.slider, low, high, self.fine_tune_label, down_button,
            self.value_spin, up_button, *self.quick_pitch_buttons,
        ]
        self._set_pitch_controls_enabled(not link_pitch_to_speed)

    def _set_pitch_controls_enabled(self, enabled):
        for control in getattr(self, "_pitch_controls", ()):
            control.setEnabled(bool(enabled))

    def _on_link_toggled(self, linked):
        self._set_pitch_controls_enabled(not linked)
        if self._on_link_change is not None:
            self._on_link_change(linked)

    def _on_speed_changed(self, value):
        if abs(value - 100) <= 5:
            value = 100
            self.speed_slider.blockSignals(True)
            self.speed_slider.setValue(value)
            self.speed_slider.blockSignals(False)
        self.speed_label.setText(f"{value}%")
        self._on_speed_change(value)

    def set_value(self, value, notify=True):
        value = max(-24.0, min(24.0, round(float(value), 1)))
        self.slider.blockSignals(True)
        self.value_spin.blockSignals(True)
        self.slider.setValue(round(value * 10))
        self.value_spin.setValue(self._value_in_units(value))
        self.slider.blockSignals(False)
        self.value_spin.blockSignals(False)
        self._refresh_labels(value)
        if notify:
            self._on_change(self.unit_combo.currentText(), self._value_in_units(value))

    def _from_slider(self, value):
        self.set_value(value / 10.0)

    def _from_spin(self, value):
        mode = self.unit_combo.currentText()
        if mode == "Octaves":
            semitones = value * 12.0
        elif mode == "Percent":
            semitones = 12.0 * math.log2(max(0.01, value) / 100.0)
        else:
            semitones = value
        self.set_value(semitones)

    def _on_units_changed(self, mode):
        self._configure_value_spin()
        self.set_value(self.slider.value() / 10.0)

    def _value_in_units(self, semitones):
        mode = self.unit_combo.currentText()
        if mode == "Octaves":
            return round(semitones / 12.0, 2)
        if mode == "Percent":
            return round(100.0 * (2.0 ** (semitones / 12.0)), 1)
        return round(semitones, 1)

    def _configure_value_spin(self):
        mode = self.unit_combo.currentText()
        self.value_spin.blockSignals(True)
        if mode == "Octaves":
            self.value_spin.setRange(-2.0, 2.0)
            self.value_spin.setDecimals(2)
            self.value_spin.setSingleStep(0.01)
            self.value_spin.setSuffix(" oct")
            self.value_spin.setToolTip("Pitch shift in octaves")
        elif mode == "Percent":
            self.value_spin.setRange(25.0, 400.0)
            self.value_spin.setDecimals(1)
            self.value_spin.setSingleStep(0.1)
            self.value_spin.setSuffix("%")
            self.value_spin.setToolTip("Pitch frequency as a percentage of the original")
        else:
            self.value_spin.setRange(-24.0, 24.0)
            self.value_spin.setDecimals(1)
            self.value_spin.setSingleStep(0.1)
            self.value_spin.setSuffix(" st")
            self.value_spin.setToolTip("Pitch shift in semitones")
        self.value_spin.blockSignals(False)

    def _refresh_labels(self, value):
        mode = self.unit_combo.currentText()
        display_value = self._value_in_units(value)
        if mode == "Octaves":
            self.readout.setText(f"{display_value:+.2f} oct")
        elif mode == "Percent":
            self.readout.setText(f"{display_value:.1f}%")
        else:
            self.readout.setText(f"{display_value:+.1f} st")
        rounded = round(value)
        names = {
            0: "Concert pitch",
            1: "Minor second up", 2: "Major second up", 3: "Minor third up",
            4: "Major third up", 5: "Perfect fourth up", 6: "Tritone up",
            7: "Perfect fifth up", 8: "Minor sixth up", 9: "Major sixth up",
            10: "Minor seventh up", 11: "Major seventh up", 12: "Octave up",
        }
        if rounded < 0:
            interval = names.get(abs(rounded), f"{abs(rounded)} semitones")
            interval = interval.replace(" up", " down")
        else:
            interval = names.get(rounded, f"{rounded} semitones")
        cents = round((value - rounded) * 100)
        ratio = 2.0 ** (value / 12.0)
        detail = f"{interval}"
        if cents:
            detail += f" ({cents:+d} cents)"
        self.interval.setText(f"{detail} ({value:+.1f} st) | {ratio:.3f}x frequency")


class LibraryScanWorker(QThread):
    completed = Signal(list)

    def __init__(self, folders, parent=None):
        super().__init__(parent)
        self.folders = list(folders)

    def run(self):
        self.completed.emit(scan_folders(self.folders))

