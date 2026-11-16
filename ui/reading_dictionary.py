"""Dictionary-list and editable-table manager for reusable lyric readings."""

from copy import deepcopy
from pathlib import Path
import uuid

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QDialogButtonBox, QFileDialog,
    QHBoxLayout, QHeaderView, QInputDialog, QLabel, QLineEdit, QListWidget,
    QListWidgetItem, QMenu, QMessageBox, QPushButton, QSplitter, QTableWidget,
    QTableWidgetItem, QToolButton, QVBoxLayout, QWidget,
)

from core.reading_dictionary import (
    READING_TYPES, MATCH_TYPES, export_dictionary_file, get_factory_dictionaries, import_dictionary_file,
    new_dictionary, validate_dictionaries,
)
from ui.settings_controls import ThemedComboBox


class ReadingDictionaryDialog(QDialog):
    def __init__(self, data, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Reading Dictionaries")
        self.resize(1020, 520)
        self.data = deepcopy(data)
        self._loading = False
        self.dictionary_list = QListWidget()
        self.dictionary_list.setAccessibleName("Reading dictionaries; checked dictionaries are enabled")
        self.dictionary_list.setMinimumWidth(170)
        self.dictionary_list.currentRowChanged.connect(self._show_dictionary)
        self.dictionary_list.itemChanged.connect(self._dictionary_enabled)

        self.manage_button = QToolButton()
        self.manage_button.setText("Manage")
        self.manage_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.manage_menu = QMenu(self.manage_button)
        self.manage_menu.addAction("New dictionary…", self._new_dictionary)
        self.rename_action = self.manage_menu.addAction("Rename dictionary…", self._rename_dictionary)
        self.delete_action = self.manage_menu.addAction("Delete dictionary…", self._delete_dictionary)
        self.manage_menu.addSeparator()
        self.move_up_action = self.manage_menu.addAction("Move dictionary up", lambda: self._move_dictionary(-1))
        self.move_down_action = self.manage_menu.addAction("Move dictionary down", lambda: self._move_dictionary(1))
        self.manage_menu.addSeparator()
        self.manage_menu.addAction("Import dictionaries…", self._import_dictionaries)
        self.export_action = self.manage_menu.addAction("Export selected dictionary…", self._export_dictionary)
        self.manage_menu.addSeparator()
        self.manage_menu.addAction("Restore factory dictionaries…", self._restore_factory_dictionaries)
        self.manage_button.setMenu(self.manage_menu)
        self.add_button = QPushButton("Add entry")
        self.add_button.clicked.connect(self._add_entry)
        self.remove_button = QPushButton("Delete entries")
        self.remove_button.clicked.connect(self._delete_entries)
        self.search = QLineEdit()
        self.search.setPlaceholderText("Find a word, reading or comment…")
        self.search.setClearButtonEnabled(True)
        self.search.setAccessibleName("Search selected dictionary")
        self.search.textChanged.connect(self._filter_entries)
        toolbar = QHBoxLayout()
        toolbar.addWidget(self.manage_button)
        toolbar.addWidget(self.add_button)
        toolbar.addWidget(self.remove_button)
        toolbar.addStretch()
        toolbar.addWidget(self.search, 1)

        self.table = QTableWidget(0, 5)
        self.table.setAccessibleName("Dictionary entries")
        self.table.setHorizontalHeaderLabels(["Reading", "Word", "Reading type", "Comment", "Match"])
        self.table.setSelectionBehavior(QAbstractItemView.SelectionBehavior.SelectRows)
        self.table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        # The Settings theme supplies the table surface; native alternate-row
        # colors can stay white under a dark stylesheet.
        self.table.setAlternatingRowColors(False)
        self.table.horizontalHeader().setSectionResizeMode(QHeaderView.ResizeMode.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeMode.ResizeToContents)
        self.table.itemChanged.connect(self._entry_changed)
        self.table.itemSelectionChanged.connect(self._update_actions)
        self.table.verticalHeader().setVisible(False)
        self.splitter = QSplitter()
        self.splitter.addWidget(self.dictionary_list)
        self.splitter.addWidget(self.table)
        self.splitter.setStretchFactor(1, 1)
        self.splitter.setSizes([190, 650])
        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        hint = QLabel("Whole word matches follow tokenizer word boundaries, so 私 does not replace part of 私立. "
                      "Use Inside words only for intentional partial matches. Longest eligible matches win, then dictionary order. "
                      "Song-specific readings keep priority.")
        hint.setWordWrap(True)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Ok | QDialogButtonBox.StandardButton.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        layout = QVBoxLayout(self)
        layout.addLayout(toolbar)
        layout.addWidget(self.splitter, 1)
        layout.addWidget(self.status)
        layout.addWidget(hint)
        layout.addWidget(buttons)
        self._populate_dictionaries()

    def _current_dictionary(self):
        row = self.dictionary_list.currentRow()
        return self.data["dictionaries"][row] if 0 <= row < len(self.data["dictionaries"]) else None

    def _populate_dictionaries(self, selected=0):
        blocker = QSignalBlocker(self.dictionary_list)
        self.dictionary_list.clear()
        for dictionary in self.data["dictionaries"]:
            item = QListWidgetItem(dictionary["name"])
            item.setFlags(item.flags() | Qt.ItemFlag.ItemIsUserCheckable)
            item.setCheckState(Qt.CheckState.Checked if dictionary["enabled"] else Qt.CheckState.Unchecked)
            self.dictionary_list.addItem(item)
        self.dictionary_list.setCurrentRow(min(selected, self.dictionary_list.count() - 1))
        del blocker
        self._show_dictionary()

    def _show_dictionary(self, *_args):
        self._loading = True
        blocker = QSignalBlocker(self.table)
        try:
            self.table.setRowCount(0)
            dictionary = self._current_dictionary()
            for entry in dictionary["entries"] if dictionary else []:
                row = self.table.rowCount()
                self.table.insertRow(row)
                for column, key in ((0, "reading"), (1, "word"), (3, "comment")):
                    self.table.setItem(row, column, QTableWidgetItem(entry[key]))
                kind = ThemedComboBox()
                for key, label in READING_TYPES.items():
                    kind.addItem(label, key)
                kind.setCurrentIndex(max(0, kind.findData(entry["type"])))
                kind.currentIndexChanged.connect(lambda _index, entry=entry, kind=kind: entry.update(type=kind.currentData()))
                self.table.setCellWidget(row, 2, kind)
                matching = ThemedComboBox()
                for key, label in MATCH_TYPES.items():
                    matching.addItem(label, key)
                matching.setCurrentIndex(max(0, matching.findData(entry.get("match", "word"))))
                matching.setToolTip("Whole word uses tokenizer boundaries. Without them, only complete runs between spaces or punctuation match. "
                                    "Inside words also replaces text within larger words.")
                matching.currentIndexChanged.connect(
                    lambda _index, entry=entry, matching=matching: entry.update(match=matching.currentData()))
                self.table.setCellWidget(row, 4, matching)
                kind.ensurePolished()
                matching.ensurePolished()
            # Installing index widgets does not invalidate the header's cached
            # content widths. Refresh them after rows change, including when
            # the first entry is added to an already-visible empty dictionary.
            for column in (2, 4):
                self.table.resizeColumnToContents(column)
        finally:
            del blocker
            self._loading = False
        self._filter_entries()
        self._update_actions()

    def _dictionary_enabled(self, item):
        self.data["dictionaries"][self.dictionary_list.row(item)]["enabled"] = item.checkState() == Qt.CheckState.Checked

    def _entry_changed(self, item):
        if self._loading:
            return
        dictionary = self._current_dictionary()
        key = {0: "reading", 1: "word", 3: "comment"}.get(item.column())
        if dictionary is not None and key:
            dictionary["entries"][item.row()][key] = item.text()
        self._filter_entries()

    def _filter_entries(self):
        query = self.search.text().casefold().strip()
        dictionary = self._current_dictionary()
        entries = dictionary["entries"] if dictionary else []
        visible = 0
        for row, entry in enumerate(entries):
            match = not query or query in " ".join(entry.values()).casefold()
            self.table.setRowHidden(row, not match)
            visible += match
        name = dictionary["name"] if dictionary else "No dictionary selected"
        self.status.setText(f"{name}: {len(entries)} entries" + (f" · {visible} shown" if query else ""))

    def _update_actions(self):
        dictionary = self._current_dictionary()
        for widget in (self.add_button, self.rename_action, self.delete_action, self.export_action):
            widget.setEnabled(dictionary is not None)
        self.remove_button.setEnabled(bool(self.table.selectionModel().selectedRows()))
        row = self.dictionary_list.currentRow()
        self.move_up_action.setEnabled(row > 0)
        self.move_down_action.setEnabled(0 <= row < self.dictionary_list.count() - 1)

    def _ask_name(self, title, current=""):
        name, accepted = QInputDialog.getText(self, title, "Dictionary name:", text=current)
        if not accepted:
            return None
        name = name.strip()
        if not name or len(name) > 100 or any(d["name"].casefold() == name.casefold() and d is not self._current_dictionary()
                                              for d in self.data["dictionaries"]):
            QMessageBox.warning(self, title, "Enter a unique dictionary name of 1–100 characters.")
            return None
        return name

    def _new_dictionary(self):
        name = self._ask_name("New Dictionary")
        if name is not None:
            if any(d["name"].casefold() == name.casefold() for d in self.data["dictionaries"]):
                QMessageBox.warning(self, "New Dictionary", "A dictionary already has that name.")
                return
            self.data["dictionaries"].append(new_dictionary(name))
            self._populate_dictionaries(len(self.data["dictionaries"]) - 1)

    def _rename_dictionary(self):
        dictionary = self._current_dictionary()
        if dictionary is None:
            return
        name = self._ask_name("Rename Dictionary", dictionary["name"])
        if name is not None:
            dictionary["name"] = name
            self._populate_dictionaries(self.dictionary_list.currentRow())

    def _delete_dictionary(self):
        dictionary = self._current_dictionary()
        if dictionary is not None and QMessageBox.question(
            self, "Delete Dictionary", f'Delete "{dictionary["name"]}" and its {len(dictionary["entries"])} entries?',
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No, QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            row = self.dictionary_list.currentRow()
            del self.data["dictionaries"][row]
            self._populate_dictionaries(row)

    def _move_dictionary(self, direction):
        row = self.dictionary_list.currentRow()
        other = row + direction
        dictionaries = self.data["dictionaries"]
        if 0 <= row < len(dictionaries) and 0 <= other < len(dictionaries):
            dictionaries[row], dictionaries[other] = dictionaries[other], dictionaries[row]
            self._populate_dictionaries(other)

    def _restore_factory_dictionaries(self):
        if QMessageBox.question(
            self, "Restore Factory Dictionaries",
            "Replace all dictionaries with the starter Japanese song readings? "
            "Your current entries will be removed when you save Settings.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes:
            self.data = get_factory_dictionaries()
            self.search.clear()
            self._populate_dictionaries()

    def _add_entry(self):
        dictionary = self._current_dictionary()
        if dictionary is None:
            return
        self.search.clear()
        dictionary["entries"].append({"reading": "", "word": "", "type": "furigana", "comment": "", "match": "word"})
        self._show_dictionary()
        row = self.table.rowCount() - 1
        self.table.setCurrentCell(row, 0)
        self.table.editItem(self.table.item(row, 0))

    def _delete_entries(self):
        dictionary = self._current_dictionary()
        if dictionary is None:
            return
        rows = sorted({index.row() for index in self.table.selectionModel().selectedRows()
                       if not self.table.isRowHidden(index.row())}, reverse=True)
        for row in rows:
            del dictionary["entries"][row]
        self._show_dictionary()

    def _import_dictionaries(self):
        filename, _ = QFileDialog.getOpenFileName(self, "Import Dictionaries", "", "Dictionary files (*.json *.tsv *.txt)")
        if not filename:
            return
        try:
            dictionaries = import_dictionary_file(filename)
            names = {d["name"].casefold() for d in self.data["dictionaries"]}
            for dictionary in dictionaries:
                base, number = dictionary["name"], 2
                while dictionary["name"].casefold() in names:
                    dictionary["name"] = f"{base[:90]} ({number})"
                    number += 1
                names.add(dictionary["name"].casefold())
                dictionary["id"] = uuid.uuid4().hex
            self.data["dictionaries"].extend(dictionaries)
            self._populate_dictionaries(len(self.data["dictionaries"]) - len(dictionaries))
        except (OSError, ValueError, UnicodeError) as error:
            QMessageBox.warning(self, "Import Dictionaries", str(error))

    def _export_dictionary(self):
        dictionary = self._current_dictionary()
        if dictionary is None:
            return
        filename, selected_filter = QFileDialog.getSaveFileName(
            self, "Export Dictionary", "dictionary.json", "LRCPlus dictionary (*.json);;Tab-separated dictionary (*.tsv)",
        )
        if filename:
            if not Path(filename).suffix:
                filename += ".tsv" if "*.tsv" in selected_filter else ".json"
            try:
                export_dictionary_file(filename, dictionary)
            except (OSError, ValueError) as error:
                QMessageBox.warning(self, "Export Dictionary", str(error))

    def accept(self):
        self.table.setFocus()
        try:
            self.data = validate_dictionaries(self.data)
        except ValueError as error:
            QMessageBox.warning(self, "Reading Dictionaries", str(error))
            return
        super().accept()
