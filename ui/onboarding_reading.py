"""A local lyric sample and draft preferences for the reading setup step."""

from PySide6.QtGui import QColor, QPalette
from PySide6.QtWidgets import QComboBox, QFormLayout, QLabel, QSpinBox, QVBoxLayout, QWidget

from core.lyrics_settings import normalize_lyrics_settings
from lyrics.furigana_widget import FuriganaWidget


class ReadingPreferences(QWidget):
    SAMPLE = "歌"

    def __init__(self, values, parent=None):
        super().__init__(parent)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        sample_label = QLabel("Lyric preview")
        layout.addWidget(sample_label)
        self.preview = FuriganaWidget()
        self.preview.setAccessibleName("Sample lyric: song")
        self.preview.setAutoFillBackground(True)
        palette = self.preview.palette()
        palette.setColor(QPalette.ColorRole.Window, QColor("#18202c"))
        self.preview.setPalette(palette)
        self.preview.set_colors("#f5f7fb", "#f5f7fb")
        self.preview.set_highlight_current(False)
        # Supply readings directly. No worker, dictionary, or network is needed.
        self.preview.set_lyric(self.SAMPLE, generate=False)
        self.preview.furigana_ready(self.SAMPLE, [
            {"text": "歌", "reading": "うた"},
        ])
        layout.addWidget(self.preview)
        form = QFormLayout()
        self.font_size = QSpinBox()
        self.font_size.setRange(10, 96)
        self.font_size.setSuffix(" pt")
        self.font_size.setAccessibleName("Lyric font size")
        form.addRow("Lyric font size", self.font_size)
        self.reading_mode = QComboBox()
        self.reading_mode.setAccessibleName("Reading aids")
        for title, mode in (
            ("No readings", "none"), ("Furigana", "furigana"),
            ("Romaji", "romaji"), ("Replace lyrics with romaji", "all_romaji"),
        ):
            self.reading_mode.addItem(title, mode)
        form.addRow("Reading aids", self.reading_mode)
        layout.addLayout(form)
        note = QLabel("Apply and Next saves your default lyric preferences. Do this later keeps your current settings.")
        note.setWordWrap(True)
        layout.addWidget(note)
        self.font_size.valueChanged.connect(self.update_preview)
        self.reading_mode.currentIndexChanged.connect(self.update_preview)
        self.set_settings(values)

    def set_settings(self, values):
        self.initial = normalize_lyrics_settings(values)
        mode = ("all_romaji" if self.initial["all_romaji"] else
                "romaji" if self.initial["show_romaji"] else
                "furigana" if self.initial["show_ruby"] else "none")
        self.font_size.blockSignals(True)
        self.reading_mode.blockSignals(True)
        self.font_size.setValue(self.initial["font_size"])
        self.reading_mode.setCurrentIndex(self.reading_mode.findData(mode))
        self.font_size.blockSignals(False)
        self.reading_mode.blockSignals(False)
        self.update_preview()

    def values(self):
        mode = self.reading_mode.currentData()
        return {"font_size": self.font_size.value(), "show_ruby": mode == "furigana",
                "show_romaji": mode == "romaji", "all_romaji": mode == "all_romaji"}

    def changes(self):
        return {key: value for key, value in self.values().items() if value != self.initial[key]}

    def update_preview(self):
        values = self.values()
        self.preview.set_font(self.initial["font_family"], values["font_size"], self.initial["ruby_font_size"])
        self.preview.set_ruby_padding(self.initial["ruby_padding"])
        self.preview.set_ruby_position(self.initial["ruby_position"])
        self.preview.set_all_romaji_visible(False)
        self.preview.set_ruby_visible(values["show_ruby"])
        self.preview.set_romaji_visible(values["show_romaji"])
        self.preview.set_all_romaji_visible(values["all_romaji"])
