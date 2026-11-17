"""A local lyric sample and draft preferences for the lyric-layer setup step."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import (
    QCheckBox, QFormLayout, QHBoxLayout, QLabel, QPushButton, QScrollArea,
    QSpinBox, QVBoxLayout, QWidget,
)

from core.lyrics_settings import normalize_lyrics_settings
from lyrics.furigana_widget import FuriganaWidget
from lyrics.line_breaks import reading_source
from ui.lyric_layers import LyricLayerOrder


LAYER_KEYS = {
    "lyrics": "show_lyrics", "romaji": "show_romaji",
    "translation": "show_translation", "furigana": "show_ruby",
}


class ReadingPreferences(QWidget):
    SAMPLE = "歌♪\nSong"

    def __init__(self, values, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        instruction = QLabel(
            "Check the layers you want to see. Drag them or use Move up / Move down "
            "to change their order. Keep at least one checked."
        )
        instruction.setWordWrap(True)
        layout.addWidget(instruction)
        form = QFormLayout()
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 96)
        self.font_size.setSuffix(" pt")
        self.font_size.setAccessibleName("Lyric font size")
        form.addRow("Lyric font size", self.font_size)
        layout.addLayout(form)

        body = QHBoxLayout()
        preview_layout = QVBoxLayout()
        preview_layout.addWidget(QLabel("Lyric preview"))
        self.preview = FuriganaWidget()
        self.preview.setAccessibleName("Sample lyric: song, with a music note and an English translation")
        self.preview.setAutoFillBackground(True)
        palette = self.preview.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#18202c"))
        self.preview.setPalette(palette)
        self.preview.set_colors("#f5f7fb", "#f5f7fb")
        self.preview.set_highlight_current(False)
        self.preview.set_text_wrapping(True, -1)
        # Supply all sample readings locally; never load a provider or dictionary.
        self.preview.set_lyric(self.SAMPLE, generate=False, translation_lines=[1])
        self.preview.furigana_ready(reading_source(self.SAMPLE, [1]), [
            {"text": "歌", "reading": "うた"},
            {"text": "♪\nSong", "reading": None},
        ])
        self.preview_scroll = QScrollArea()
        self.preview_scroll.setAccessibleName("Scrollable lyric preview")
        self.preview_scroll.setWidgetResizable(True)
        self.preview_scroll.setFixedHeight(200)
        self.preview_scroll.setWidget(self.preview)
        preview_layout.addWidget(self.preview_scroll)
        body.addLayout(preview_layout, 3)

        layers_layout = QVBoxLayout()
        layers_layout.addWidget(QLabel("Layers"))
        self.layer_order = LyricLayerOrder()
        self.layer_order.setFixedHeight(112)
        layers_layout.addWidget(self.layer_order)
        self.layer_controls = {layer: QCheckBox(layer.capitalize(), self) for layer in LAYER_KEYS}
        for control in self.layer_controls.values():
            control.hide()
        self.layer_order.bind_visibility(self.layer_controls)
        moves = QHBoxLayout()
        for title, delta in (("Move up", -1), ("Move down", 1)):
            button = QPushButton(title)
            button.clicked.connect(lambda _checked=False, delta=delta: self.layer_order.move_current(delta))
            moves.addWidget(button)
        layers_layout.addLayout(moves)
        layers_layout.addStretch(1)
        body.addLayout(layers_layout, 2)
        layout.addLayout(body)

        explanation = QLabel(
            "Romaji is smaller alongside Lyrics and full-size on its own. "
            "Translation uses translated lines saved in the lyric file. "
            "These defaults also apply to Floating Lyrics."
        )
        explanation.setWordWrap(True)
        layout.addWidget(explanation)
        note = QLabel(
            "Apply and Next saves these defaults. Do this later keeps your current settings. "
            "You can change layers in Settings > Lyrics > Readings."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        self.font_size.valueChanged.connect(self.update_preview)
        for control in self.layer_controls.values():
            control.toggled.connect(self.update_preview)
        for signal in (self.layer_order.model().rowsMoved, self.layer_order.model().rowsInserted,
                       self.layer_order.model().rowsRemoved):
            signal.connect(self.update_preview)
        self.set_settings(values)

    def set_settings(self, values):
        self.initial = normalize_lyrics_settings(values)
        # Preview the effective layers of an older replacement-mode preference.
        if self.initial["all_romaji"]:
            self.initial.update(show_lyrics=False, show_romaji=True, show_ruby=False)
        self.font_size.blockSignals(True)
        self.font_size.setValue(self.initial["font_size"])
        self.font_size.blockSignals(False)
        for layer, key in LAYER_KEYS.items():
            control = self.layer_controls[layer]
            control.blockSignals(True)
            control.setChecked(self.initial[key])
            control.blockSignals(False)
        self.layer_order.setValue(self.initial["lyric_layer_order"])
        self.update_preview()

    def values(self):
        return {
            "font_size": self.font_size.value(),
            "lyric_layer_order": self.layer_order.value(),
            **{key: self.layer_controls[layer].isChecked() for layer, key in LAYER_KEYS.items()},
        }

    def changes(self):
        return {key: value for key, value in self.values().items() if value != self.initial[key]}

    def update_preview(self, *_args):
        values = self.values()
        self.preview.set_font(self.initial["font_family"], values["font_size"], self.initial["ruby_font_size"])
        self.preview.set_ruby_padding(self.initial["ruby_padding"])
        self.preview.set_lyric_layers(
            values["lyric_layer_order"], values["show_lyrics"], values["show_romaji"],
            values["show_translation"], values["show_ruby"],
        )
