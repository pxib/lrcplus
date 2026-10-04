import csv
import html
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QComboBox,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)


class WordAnalysisWorker(QThread):
    completed = Signal(object)
    failed = Signal(str)

    def __init__(self, api, lyrics, parent=None):
        super().__init__(parent)
        self.api = api
        self.lyrics = lyrics

    def run(self):
        try:
            self.completed.emit(self.api.analyze_lyric_words(self.lyrics))
        except Exception as error:
            self.failed.emit(str(error))


def format_lyric_with_readings(text, words):
    parts = []
    source_position = 0
    for word in words:
        surface = str(word.get("text", "") or "")
        reading = str(word.get("reading", "") or "")
        if not surface:
            continue
        found_at = text.find(surface, source_position)
        if found_at < 0:
            continue
        parts.append(html.escape(text[source_position:found_at]))
        parts.append(html.escape(surface))
        if reading:
            parts.append(
                '<span style="font-size: 15px; color: #68716e;">'
                f"（{html.escape(reading)}）</span>"
            )
        source_position = found_at + len(surface)
    parts.append(html.escape(text[source_position:]))
    return "".join(parts)


class FlashcardDialog(QDialog):
    def __init__(self, api, track_path, lyrics, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anki Flashcards")
        self.resize(900, 660)
        self.lyrics = list(lyrics)
        self.words_by_line = [[] for _line in self.lyrics]
        self.selected_cards = {}
        self._updating_words = False

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Analyzing lyric words...")

        navigation = QHBoxLayout()
        self.previous_button = QPushButton("Previous Lyrics")
        self.previous_button.setEnabled(False)
        self.previous_button.clicked.connect(self.previous_line)
        self.line_selector = QComboBox()
        self.line_selector.setMinimumWidth(280)
        for index, line in enumerate(self.lyrics):
            text = str(line.get("text", "") or "")
            self.line_selector.addItem(f"{index + 1:03d}  {text}")
            self.line_selector.setItemData(
                index,
                text,
                Qt.ItemDataRole.ToolTipRole,
            )
        self.line_selector.setEnabled(False)
        self.line_selector.currentIndexChanged.connect(self.show_line)
        self.next_button = QPushButton("Next Lyrics")
        self.next_button.setEnabled(False)
        self.next_button.clicked.connect(self.next_line)
        navigation.addWidget(self.previous_button)
        navigation.addWidget(QLabel("Select Line"))
        navigation.addWidget(self.line_selector, 1)
        navigation.addWidget(self.next_button)
        layout.addLayout(navigation)

        self.lyric_label = QLabel("<i>Waiting for word analysis...</i>")
        self.lyric_label.setWordWrap(True)
        self.lyric_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.lyric_label.setMinimumHeight(100)
        layout.addWidget(self.lyric_label)

        self.line_reading_label = QLabel("")
        self.line_reading_label.setWordWrap(True)
        self.line_reading_label.setAlignment(Qt.AlignmentFlag.AlignCenter)
        layout.addWidget(self.line_reading_label)

        self.table = QTableWidget(0, 3, self)
        self.table.setHorizontalHeaderLabels(["", "Word", "Reading"])
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 180)
        self.table.setEnabled(False)
        self.table.itemChanged.connect(self.word_selection_changed)
        layout.addWidget(self.table, 1)

        self.all_lyrics_list = QListWidget(self)
        self.all_lyrics_list.setMaximumHeight(180)
        self.all_lyrics_list.setVisible(False)
        self.all_lyrics_list.itemClicked.connect(self.select_listed_line)
        layout.addWidget(self.all_lyrics_list)

        controls = QHBoxLayout()
        controls.addWidget(self.status_label, 1)
        self.export_button = QPushButton("Export...")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(
            lambda: self.export_selected(track_path)
        )
        controls.addStretch()
        controls.addWidget(self.export_button)
        self.show_all_button = QPushButton("Show All Lyrics")
        self.show_all_button.clicked.connect(self.toggle_all_lyrics)
        controls.addWidget(self.show_all_button)
        layout.addLayout(controls)

        self.worker = WordAnalysisWorker(api, lyrics, self)
        self.worker.completed.connect(self.populate_words)
        self.worker.failed.connect(self.show_analysis_error)
        self.worker.start()

    def closeEvent(self, event):
        if self.worker.isRunning():
            self.status_label.setText("Word analysis is still running...")
            event.ignore()
            return
        super().closeEvent(event)

    def populate_words(self, words):
        self.words_by_line = [[] for _line in self.lyrics]
        seen_by_line = [set() for _line in self.lyrics]
        for word in words:
            text = str(word.get("text", "") or "").strip()
            reading = str(word.get("reading", "") or "").strip()
            line_index = int(word.get("line_index", -1))
            key = (text, reading)
            if (
                not text
                or not 0 <= line_index < len(self.words_by_line)
                or key in seen_by_line[line_index]
            ):
                continue
            seen_by_line[line_index].add(key)
            self.words_by_line[line_index].append({
                "text": text,
                "reading": reading,
                "context": str(
                    word.get("context", "")
                    or self.lyrics[line_index].get("text", "")
                ),
            })

        self.all_lyrics_list.clear()
        for index, line in enumerate(self.lyrics):
            text = str(line.get("text", "") or "")
            reading = self.line_reading(index)
            label = f"{index + 1:03d}  {text}"
            if reading:
                label += f"    {reading}"
            self.all_lyrics_list.addItem(label)

        if self.lyrics:
            self.line_selector.setEnabled(True)
            self.line_selector.setCurrentIndex(0)
            self.show_line(0)
        self.table.setEnabled(True)
        self.status_label.setText("Select lyric words to add them to the export.")
        self.export_button.setEnabled(bool(self.selected_cards))

    def show_analysis_error(self, message):
        self.status_label.setText(f"Word analysis failed: {message}")

    def line_reading(self, line_index):
        return " ".join(
            word["reading"]
            for word in self.words_by_line[line_index]
            if word["reading"]
        )

    def show_line(self, line_index):
        if not 0 <= line_index < len(self.lyrics):
            return
        if self.line_selector.currentIndex() != line_index:
            self.line_selector.setCurrentIndex(line_index)

        text = str(self.lyrics[line_index].get("text", "") or "")
        words = self.words_by_line[line_index]
        self.lyric_label.setText(
            '<div style="font-size: 30px;">'
            + format_lyric_with_readings(text, words)
            + "</div>"
        )
        reading = self.line_reading(line_index)
        self.line_reading_label.setText(
            f'<div style="font-size: 20px;">{html.escape(reading)}</div>'
        )
        self.previous_button.setEnabled(line_index > 0)
        self.next_button.setEnabled(line_index < len(self.lyrics) - 1)

        self._updating_words = True
        self.table.blockSignals(True)
        self.table.setRowCount(len(words))
        for row, word in enumerate(words):
            key = (word["text"], word["reading"])
            selection = QTableWidgetItem()
            selection.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            selection.setCheckState(
                Qt.CheckState.Checked
                if key in self.selected_cards
                else Qt.CheckState.Unchecked
            )
            self.table.setItem(row, 0, selection)
            for column, value in enumerate((word["text"], word["reading"]), start=1):
                item = QTableWidgetItem(value)
                item.setData(Qt.ItemDataRole.UserRole, key)
                item.setToolTip(value)
                self.table.setItem(row, column, item)
        self.table.blockSignals(False)
        self._updating_words = False

    def previous_line(self):
        self.show_line(self.line_selector.currentIndex() - 1)

    def next_line(self):
        self.show_line(self.line_selector.currentIndex() + 1)

    def select_listed_line(self, item):
        self.show_line(self.all_lyrics_list.row(item))

    def toggle_all_lyrics(self):
        visible = not self.all_lyrics_list.isVisible()
        self.all_lyrics_list.setVisible(visible)
        self.show_all_button.setText(
            "Hide All Lyrics" if visible else "Show All Lyrics"
        )

    def word_selection_changed(self, item):
        if self._updating_words or item.column() != 0:
            return
        word_item = self.table.item(item.row(), 1)
        if word_item is None:
            return
        key = word_item.data(Qt.ItemDataRole.UserRole)
        line_index = self.line_selector.currentIndex()
        word = next(
            (
                candidate
                for candidate in self.words_by_line[line_index]
                if (candidate["text"], candidate["reading"]) == key
            ),
            None,
        )
        if word is None:
            return
        if item.checkState() == Qt.CheckState.Checked:
            self.selected_cards[key] = word
        else:
            self.selected_cards.pop(key, None)
        self.export_button.setEnabled(bool(self.selected_cards))
        self.status_label.setText(
            f"{len(self.selected_cards)} words selected for export."
        )

    def export_selected(self, track_path):
        rows = [
            [word["text"], word["reading"], word["context"]]
            for word in self.selected_cards.values()
        ]
        if not rows:
            QMessageBox.information(
                self,
                "Anki Flashcards",
                "Select at least one word to export.",
            )
            return

        track_name = Path(track_path).stem if track_path else "lyrics"
        default_name = f"{track_name}_anki.tsv"
        path, _selected_filter = QFileDialog.getSaveFileName(
            self,
            "Export Anki TSV",
            default_name,
            "TSV files (*.tsv)",
        )
        if not path:
            return
        if not path.lower().endswith(".tsv"):
            path += ".tsv"

        try:
            with open(path, "w", encoding="utf-8", newline="") as output:
                writer = csv.writer(
                    output,
                    delimiter="\t",
                    lineterminator="\n",
                    quoting=csv.QUOTE_MINIMAL,
                )
                writer.writerows(rows)
        except OSError as error:
            QMessageBox.critical(self, "Export failed", str(error))
            return

        self.status_label.setText(f"Exported {len(rows)} cards to {path}")


class AnkiFlashcardsPlugin:
    name = "Anki Flashcards"
    version = "1.0.0"
    description = "Select lyric words and export Anki-ready TSV flashcards."

    def on_load(self, context):
        self.context = context
        context.api.add_main_window_button("Flashcard", self.open_flashcards)

    def open_flashcards(self):
        api = self.context.api
        lyrics = api.get_current_lyrics()
        if not lyrics:
            QMessageBox.information(
                None,
                "Anki Flashcards",
                "Load a song with lyrics before creating flashcards.",
            )
            return

        dialog = FlashcardDialog(
            api,
            api.get_current_track(),
            lyrics,
        )
        dialog.exec()


PLUGIN_CLASS = AnkiFlashcardsPlugin