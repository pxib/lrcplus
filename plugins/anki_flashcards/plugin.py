import csv
from pathlib import Path

from PySide6.QtCore import QThread, Signal, Qt
from PySide6.QtWidgets import (
    QAbstractItemView,
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QLabel,
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


class FlashcardDialog(QDialog):
    def __init__(self, api, track_path, lyrics, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Anki Flashcards")
        self.resize(820, 480)

        layout = QVBoxLayout(self)
        self.status_label = QLabel("Analyzing lyric words...")
        layout.addWidget(self.status_label)

        self.table = QTableWidget(0, 4, self)
        self.table.setHorizontalHeaderLabels(["", "Word", "Reading", "Lyric context"])
        self.table.setSelectionBehavior(
            QAbstractItemView.SelectionBehavior.SelectRows
        )
        self.table.setEditTriggers(
            QAbstractItemView.EditTrigger.NoEditTriggers
        )
        self.table.horizontalHeader().setStretchLastSection(True)
        self.table.setColumnWidth(0, 36)
        self.table.setColumnWidth(1, 180)
        self.table.setColumnWidth(2, 180)
        self.table.setEnabled(False)
        layout.addWidget(self.table, 1)

        controls = QHBoxLayout()
        self.select_all_button = QPushButton("Select all")
        self.select_all_button.clicked.connect(self.select_all)
        self.export_button = QPushButton("Export TSV")
        self.export_button.setEnabled(False)
        self.export_button.clicked.connect(
            lambda: self.export_selected(track_path)
        )
        controls.addWidget(self.select_all_button)
        controls.addStretch()
        controls.addWidget(self.export_button)
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
        unique_words = []
        seen = set()
        for word in words:
            text = str(word.get("text", "") or "").strip()
            reading = str(word.get("reading", "") or "").strip()
            key = (text, reading)
            if not text or key in seen:
                continue
            seen.add(key)
            unique_words.append({
                "text": text,
                "reading": reading,
                "context": str(word.get("context", "") or ""),
            })

        self.table.setRowCount(len(unique_words))
        for row, word in enumerate(unique_words):
            selection = QTableWidgetItem()
            selection.setFlags(
                Qt.ItemFlag.ItemIsEnabled | Qt.ItemFlag.ItemIsUserCheckable
            )
            selection.setCheckState(Qt.CheckState.Checked)
            self.table.setItem(row, 0, selection)
            for column, key in enumerate(("text", "reading", "context"), start=1):
                item = QTableWidgetItem(word[key])
                item.setToolTip(word[key])
                self.table.setItem(row, column, item)

        self.table.setEnabled(True)
        self.status_label.setText(
            f"{len(unique_words)} unique words found. Choose cards to export."
            if unique_words
            else "No words were found in the current lyrics."
        )
        self.export_button.setEnabled(bool(unique_words))

    def show_analysis_error(self, message):
        self.status_label.setText(f"Word analysis failed: {message}")

    def select_all(self):
        state = (
            Qt.CheckState.Unchecked
            if all(
                self.table.item(row, 0).checkState() == Qt.CheckState.Checked
                for row in range(self.table.rowCount())
            )
            else Qt.CheckState.Checked
        )
        for row in range(self.table.rowCount()):
            self.table.item(row, 0).setCheckState(state)
        self.select_all_button.setText(
            "Select all" if state == Qt.CheckState.Unchecked else "Select none"
        )

    def export_selected(self, track_path):
        rows = []
        for row in range(self.table.rowCount()):
            if self.table.item(row, 0).checkState() != Qt.CheckState.Checked:
                continue
            rows.append([
                self.table.item(row, column).text()
                for column in (1, 2, 3)
            ])

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