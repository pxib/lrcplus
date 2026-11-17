"""Standalone lyric renderer debugger.

Run from the repository root: Scripts/python.exe tools/renderer_playground.py
"""

import json
from pathlib import Path
import sys
from time import perf_counter

if __package__ in (None, ""):
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from PySide6.QtCore import Qt, QTimer
from PySide6.QtGui import QColor, QImage, QPainter, QPen
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDoubleSpinBox, QFileDialog,
    QFormLayout, QHBoxLayout, QLabel, QLineEdit, QMainWindow, QMessageBox,
    QPlainTextEdit, QPushButton, QScrollArea, QSlider, QSpinBox, QSplitter,
    QVBoxLayout, QWidget,
)

from core.fonts import library_font
from core.settings import KARAOKE_SWEEP_STYLES
from lyrics.furigana_widget import FuriganaWidget
from lyrics.lrcx_parser import parse_lrcx


PRESETS = {
    "Karaoke and ruby": "[00:00.000]<00:00.000>{君|きみ}の<00:02.000>{声|こえ}が<00:04.000>{届|とど}く<00:06.000>",
    "Wrapping": "[00:00.000]I won't spark it, I'll put it in my pocket and save it like rocket fuel<00:08.000>",
    "Newlines and formatting": r"[00:00.000]> **First row**{\n}*Second row*{\n}{言葉|ことば}<00:06.000>",
    "Pauses": "[00:00.000]<00:00.000>First<00:01.000><00:03.000> second<00:04.000><00:06.000> last<00:08.000>",
    "Dense readings": "[00:00.000]" + "{你|nǐ}{好|hǎo}" * 80 + "<00:15.000>",
    "Repeated timestamps": "[00:00.000]First simultaneous row\n[00:00.000]Second simultaneous row\n[00:04.000]Next row",
}


class PreviewRow(FuriganaWidget):
    """Measure the production paint path; optionally show the widget boundary."""

    show_bounds = False
    paint_ms = 0.0

    def paintEvent(self, event):
        started = perf_counter()
        super().paintEvent(event)
        self.paint_ms = (perf_counter() - started) * 1000
        if self.show_bounds:
            painter = QPainter(self)
            painter.setPen(QPen(QColor("#55ddee"), 1, Qt.PenStyle.DashLine))
            painter.drawRect(self.rect().adjusted(0, 0, -1, -1))
            painter.end()


class RendererPlayground(QMainWindow):
    def __init__(self):
        super().__init__()
        self.setWindowTitle("LRCPlus Renderer Playground")
        self.resize(1250, 850)
        self.lines = []
        self.controls = {}
        self.row = PreviewRow()
        self.row.setAutoFillBackground(True)
        palette = self.row.palette()
        palette.setColor(self.row.backgroundRole(), QColor("#18202c"))
        self.row.setPalette(palette)
        self.row.set_focus_transition(False)
        self.row.set_current_line(True)

        splitter = QSplitter()
        self.setCentralWidget(splitter)
        sidebar = QWidget()
        sidebar.setFont(library_font(QApplication.font()))
        left = QVBoxLayout(sidebar)
        actions = QHBoxLayout()
        for title, handler in (("Load case", self.load_case), ("Save case", self.save_case),
                               ("Export PNG", self.export_png)):
            button = QPushButton(title)
            button.clicked.connect(handler)
            actions.addWidget(button)
        left.addLayout(actions)
        self.presets = QComboBox()
        self.presets.addItems(PRESETS)
        left.addWidget(self.presets)
        left.addWidget(QLabel("LRCX source (updates after typing):"))
        self.source = QPlainTextEdit()
        left.addWidget(self.source, 1)
        self.line_choice = QComboBox()
        left.addWidget(QLabel("Preview row (select simultaneous rows individually):"))
        left.addWidget(self.line_choice)
        form = QFormLayout()
        left.addLayout(form)
        for key, label, minimum, maximum, value in (
            ("width", "Viewport width", 80, 4096, 800),
            ("height", "Viewport height", 60, 4096, 280),
            ("font_size", "Font size (px)", 8, 200, 36),
            ("ruby_size", "Ruby size (px)", 6, 120, 20),
            ("ruby_gap", "Ruby padding", 0, 80, 2),
            ("max_lines", "Maximum rows (0 = fit height)", 0, 30, 0),
        ):
            control = QSpinBox()
            control.setRange(minimum, maximum)
            control.setValue(value)
            self.controls[key] = control
            form.addRow(label, control)
            control.valueChanged.connect(self.apply_controls)
        self.controls["font_family"] = QLineEdit(library_font(QApplication.font()).family())
        form.addRow("Font family", self.controls["font_family"])
        self.controls["font_family"].textChanged.connect(self.apply_controls)
        for key, label, choices in (
            ("direction", "Direction", ("horizontal", "vertical")),
            ("alignment", "Alignment", ("center", "left", "right")),
            ("ruby_position", "Ruby position", ("above", "below")),
            ("sweep", "Karaoke sweep", tuple(KARAOKE_SWEEP_STYLES)),
        ):
            control = QComboBox()
            control.addItems(choices)
            if key == "sweep":
                control.setCurrentText("classic")
            self.controls[key] = control
            form.addRow(label, control)
            control.currentTextChanged.connect(self.apply_controls)
        for key, label, checked in (("ruby", "Show ruby", True),
                                    ("wrap", "Wrap text", True),
                                    ("romaji", "All romaji", False),
                                    ("bounds", "Show viewport bounds", True)):
            control = QCheckBox(label)
            control.setChecked(checked)
            self.controls[key] = control
            form.addRow(control)
            control.toggled.connect(self.apply_controls)
        settings_scroll = QScrollArea()
        settings_scroll.setWidgetResizable(True)
        settings_scroll.setWidget(sidebar)
        splitter.addWidget(settings_scroll)

        right = QWidget()
        layout = QVBoxLayout(right)
        canvas = QScrollArea()
        canvas.setWidget(self.row)
        canvas.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(canvas, 1)
        timeline = QHBoxLayout()
        self.play = QPushButton("Play")
        self.play.setCheckable(True)
        self.play.toggled.connect(self.toggle_play)
        timeline.addWidget(self.play)
        self.slider = QSlider(Qt.Orientation.Horizontal)
        self.slider.valueChanged.connect(lambda value: self.position.setValue(value / 1000))
        timeline.addWidget(self.slider, 1)
        self.position = QDoubleSpinBox()
        self.position.setDecimals(3)
        self.position.setSingleStep(0.1)
        self.position.setSuffix(" s")
        self.position.valueChanged.connect(self.seek)
        timeline.addWidget(self.position)
        layout.addLayout(timeline)
        self.info = QLabel()
        layout.addWidget(self.info)
        splitter.addWidget(right)
        splitter.setSizes([420, 830])
        self.debounce = QTimer(self)
        self.debounce.setSingleShot(True)
        self.debounce.setInterval(180)
        self.debounce.timeout.connect(self.parse_source)
        self.source.textChanged.connect(lambda: self.debounce.start())
        self.line_choice.currentIndexChanged.connect(self.select_line)
        self.presets.currentTextChanged.connect(self.use_preset)
        self.clock = QTimer(self)
        self.clock.setInterval(16)
        self.clock.timeout.connect(self.tick)
        self.metrics = QTimer(self)
        self.metrics.setInterval(250)
        self.metrics.timeout.connect(self.update_metrics)
        self.metrics.start()
        self.apply_controls()
        self.use_preset(self.presets.currentText())

    def use_preset(self, name):
        self.source.setPlainText(PRESETS[name])
        self.parse_source()

    def parse_source(self):
        self.debounce.stop()
        self.play.setChecked(False)
        previous = self.line_choice.currentIndex()
        try:
            self.lines = parse_lrcx(self.source.toPlainText())
        except (ValueError, TypeError, OverflowError) as error:
            self.lines = []
            self.statusBar().showMessage(f"Parse error: {error}")
        else:
            self.statusBar().showMessage(f"{len(self.lines)} parsed rows" if self.lines else
                                         "No timestamped lyrics. Start a row with [00:00.000].")
        self.line_choice.blockSignals(True)
        self.line_choice.clear()
        for index, line in enumerate(self.lines):
            self.line_choice.addItem(f"{index + 1}: {line.start:.3f}s  {line.text[:45]}")
        self.line_choice.setCurrentIndex(max(0, min(previous, len(self.lines) - 1)))
        self.line_choice.blockSignals(False)
        ends = [0.0]
        for line in self.lines:
            ends.extend([line.start + 5, line.end or 0])
            for segment in line.segments:
                ends.extend([segment.start + 1, segment.end or 0])
        duration = max(ends)
        self.position.setRange(0, duration)
        self.slider.setRange(0, round(duration * 1000))
        self.select_line(self.line_choice.currentIndex())

    def select_line(self, index):
        if not 0 <= index < len(self.lines):
            self.row.set_segments("", [])
            return
        line = self.lines[index]
        self.row.set_segments(line.text, line.segments, line_end=line.end,
                              style_spans=line.style_spans, is_blockquote=line.is_blockquote,
                              translation_lines=line.translation_lines)
        # Plain LRC must retain its non-karaoke appearance.
        self.row.set_karaoke_position(self.position.value() if line.has_karaoke else None)
        self.row.update()

    def apply_controls(self, *_):
        values = self.control_values()
        self.row.setFixedSize(values["width"], values["height"])
        self.row.set_font(values["font_family"], values["font_size"], values["ruby_size"])
        self.row.set_direction(values["direction"])
        self.row.set_alignment(values["alignment"])
        self.row.set_ruby_visible(values["ruby"])
        self.row.set_ruby_position(values["ruby_position"])
        self.row.set_ruby_padding(values["ruby_gap"])
        self.row.set_all_romaji_visible(values["romaji"])
        self.row.set_text_wrapping(values["wrap"], values["max_lines"])
        self.row.set_karaoke_sweep_style(values["sweep"])
        self.row.show_bounds = values["bounds"]
        self.row.update()

    def control_values(self):
        values = {}
        for key, control in self.controls.items():
            if isinstance(control, QCheckBox):
                values[key] = control.isChecked()
            elif isinstance(control, QComboBox):
                values[key] = control.currentText()
            elif isinstance(control, QLineEdit):
                values[key] = control.text()
            else:
                values[key] = control.value()
        return values

    def seek(self, seconds):
        self.slider.blockSignals(True)
        self.slider.setValue(round(seconds * 1000))
        self.slider.blockSignals(False)
        index = self.line_choice.currentIndex()
        self.row.set_karaoke_position(seconds if 0 <= index < len(self.lines)
                                      and self.lines[index].has_karaoke else None)

    def toggle_play(self, playing):
        self.play.setText("Pause" if playing else "Play")
        if playing:
            if self.position.value() >= self.position.maximum():
                self.position.setValue(0)
            self.last_tick = perf_counter()
            self.clock.start()
        else:
            self.clock.stop()

    def tick(self):
        now = perf_counter()
        self.position.setValue(self.position.value() + now - self.last_tick)
        self.last_tick = now
        if self.position.value() >= self.position.maximum():
            self.play.setChecked(False)

    def update_metrics(self):
        self.info.setText(f"{self.row.width()} × {self.row.height()} px  |  "
                          f"DPR {self.row.devicePixelRatioF():g}  |  "
                          f"{len(self.row.segments)} display segments  |  "
                          f"Last paint: {self.row.paint_ms:.2f} ms")

    def case_data(self):
        return {"version": 1, "source": self.source.toPlainText(),
                "controls": self.control_values(), "line": self.line_choice.currentIndex(),
                "position": self.position.value()}

    def restore_case(self, data):
        if not isinstance(data, dict) or data.get("version") != 1 or not isinstance(data.get("source"), str):
            raise ValueError("Expected a version 1 renderer case with LRCX source.")
        values = data.get("controls", {})
        if not isinstance(values, dict):
            raise ValueError("Case controls must be an object.")
        for key, value in values.items():
            control = self.controls.get(key)
            if control is None:
                continue
            control.blockSignals(True)
            try:
                if isinstance(control, QCheckBox):
                    control.setChecked(bool(value))
                elif isinstance(control, QComboBox):
                    if control.findText(str(value)) >= 0:
                        control.setCurrentText(str(value))
                elif isinstance(control, QLineEdit):
                    control.setText(str(value))
                else:
                    control.setValue(int(value))
            finally:
                control.blockSignals(False)
        self.apply_controls()
        self.source.setPlainText(data["source"])
        self.parse_source()
        index = int(data.get("line", 0))
        self.line_choice.setCurrentIndex(max(0, min(index, len(self.lines) - 1)))
        self.position.setValue(float(data.get("position", 0)))

    def save_case(self):
        path, _ = QFileDialog.getSaveFileName(self, "Save renderer case", "renderer-case.json", "JSON (*.json)")
        if path:
            try:
                Path(path).write_text(json.dumps(self.case_data(), ensure_ascii=False, indent=2), encoding="utf-8")
            except OSError as error:
                QMessageBox.warning(self, "Save failed", str(error))

    def load_case(self):
        path, _ = QFileDialog.getOpenFileName(self, "Load renderer case", "", "JSON (*.json)")
        if path:
            previous = self.case_data()
            try:
                self.restore_case(json.loads(Path(path).read_text(encoding="utf-8")))
            except (OSError, ValueError, TypeError, OverflowError) as error:
                self.restore_case(previous)
                QMessageBox.warning(self, "Load failed", str(error))

    def render_image(self):
        image = QImage(self.row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(QColor("#18202c"))
        self.row.render(image)
        return image

    def export_png(self):
        path, _ = QFileDialog.getSaveFileName(self, "Export preview", "renderer-preview.png", "PNG (*.png)")
        if path and not self.render_image().save(path, "PNG"):
            QMessageBox.warning(self, "Export failed", "Could not save the preview image.")


def main():
    app = QApplication(sys.argv)
    window = RendererPlayground()
    window.show()
    return app.exec()


if __name__ == "__main__":
    raise SystemExit(main())
