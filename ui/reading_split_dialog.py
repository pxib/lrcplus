"""Choose a word boundary and review the two readings without changing timing."""

from PySide6.QtCore import Qt, QTextBoundaryFinder
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QFormLayout, QLabel, QLineEdit, QVBoxLayout

from core.fonts import content_font
from ui.settings_controls import ThemedComboBox


def reading_split_points(text):
    """Return Python string offsets at complete Unicode grapheme boundaries."""
    offsets = {0: 0}
    position = 0
    for index, char in enumerate(text, 1):
        position += 2 if ord(char) > 0xffff else 1
        offsets[position] = index
    finder = QTextBoundaryFinder(QTextBoundaryFinder.BoundaryType.Grapheme, text)
    finder.toStart()
    result = []
    while (boundary := finder.toNextBoundary()) >= 0:
        index = offsets.get(boundary)
        if index is not None and 0 < index < len(text):
            result.append(index)
    return result


class SplitReadingDialog(QDialog):
    def __init__(self, text, reading, parent=None, *, reading_range=None):
        super().__init__(parent)
        self.setWindowTitle("Split reading")
        self._text = text
        self._reading = reading or ""
        self._reading_range = reading_range or (0, len(text))
        layout = QVBoxLayout(self)
        layout.addWidget(QLabel("Choose where to split the word, then review both readings."))
        form = QFormLayout()
        self.boundary = ThemedComboBox()
        self.boundary.setAccessibleName("Reading split boundary")
        self.boundary.setFont(content_font(self.font(), text))
        for position in reading_split_points(text):
            self.boundary.addItem(text[:position] + " | " + text[position:], position)
        form.addRow("Split between:", self.boundary)
        self.left_label = QLabel()
        self.right_label = QLabel()
        self.left_reading = QLineEdit()
        self.right_reading = QLineEdit()
        font = content_font(self.font(), text + self._reading)
        for label, field in ((self.left_label, self.left_reading), (self.right_label, self.right_reading)):
            label.setFont(font)
            label.setTextFormat(Qt.TextFormat.PlainText)
            field.setFont(font)
            field.setPlaceholderText("Reading (optional)")
            form.addRow(label, field)
        layout.addLayout(form)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        buttons.button(QDialogButtonBox.StandardButton.Ok).setEnabled(self.boundary.count() > 0)
        layout.addWidget(buttons)
        self.boundary.currentIndexChanged.connect(self._suggest_readings)
        self._suggest_readings()

    @property
    def split_position(self):
        return self.boundary.currentData()

    def _suggest_readings(self):
        from lyrics.furigana import split_reading_by_text_ranges

        cut = self.split_position
        if cut is None:
            return
        a, b = self._reading_range
        for start, end, label, field in ((0, cut, self.left_label, self.left_reading),
                                       (cut, len(self._text), self.right_label, self.right_reading)):
            base = self._text[start:end]
            label.setText(base + ":")
            field.setAccessibleName("Reading for " + base)
            first, last = max(a, start), min(b, end)
            value = ""
            if first < last and self._reading:
                value = (self._reading if (first, last) == (a, b) else
                         split_reading_by_text_ranges(self._text[a:b], self._reading,
                                                      [(first - a, last - a)])[0] or "")
            field.setText(value)
