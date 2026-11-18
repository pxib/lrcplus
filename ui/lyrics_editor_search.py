"""Find lyric lines while preserving the editor's current sidebar order."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut, QTextCursor, QColor
from PySide6.QtWidgets import (
    QAbstractItemView, QCheckBox, QComboBox, QDialog, QHBoxLayout,
    QHeaderView, QLabel, QLineEdit, QPushButton, QTableWidget,
    QTableWidgetItem, QTextEdit, QToolButton, QVBoxLayout, QWidget,
)

from lyrics.editor_search import find_text_matches, replace_lyric_matches, visible_text_ranges
from lyrics.writer import format_timestamp


class LyricsEditorSearchMixin:
    def _build_lyric_search(self, layout):
        self.lyric_search = QLineEdit()
        self.lyric_search.setPlaceholderText("Find lyrics (Ctrl+F)")
        self.lyric_search.setClearButtonEnabled(True)
        self.lyric_search.setAccessibleName("Find lyric lines")
        self.lyric_search.textChanged.connect(self._update_lyric_search)
        self.lyric_search.returnPressed.connect(self.find_next_lyric)
        layout.addWidget(self.lyric_search)
        self.lyric_search_tools = QWidget()
        tools = QVBoxLayout(self.lyric_search_tools)
        tools.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        for label, handler in (("Previous", self.find_previous_lyric), ("Next", self.find_next_lyric)):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(handler)
            row.addWidget(button)
        tools.addLayout(row)
        self.lyric_replace_toggle = QToolButton()
        self.lyric_replace_toggle.setText("Find && Replace")
        self.lyric_replace_toggle.setAccessibleName("Find and replace options")
        self.lyric_replace_toggle.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.lyric_replace_toggle.setCheckable(True)
        self.lyric_replace_toggle.setArrowType(Qt.ArrowType.RightArrow)
        self.lyric_replace_toggle.setToolTip("Expand or collapse find and replace options.")
        tools.addWidget(self.lyric_replace_toggle)
        self.lyric_replace_panel = QWidget()
        replace_layout = QVBoxLayout(self.lyric_replace_panel)
        replace_layout.setContentsMargins(0, 0, 0, 0)
        self.lyric_search_case = QCheckBox("Match case")
        self.lyric_search_case.toggled.connect(self._update_lyric_search)
        replace_layout.addWidget(self.lyric_search_case)
        self.lyric_search_scope = QComboBox()
        self.lyric_search_scope.setAccessibleName("Find and replace scope")
        self.lyric_search_scope.addItem("All lyrics", "all")
        self.lyric_search_scope.addItem("Selected lines", "selected")
        self.lyric_search_scope.currentIndexChanged.connect(self._update_lyric_search)
        replace_layout.addWidget(self.lyric_search_scope)
        self.lyric_replace = QLineEdit()
        self.lyric_replace.setAccessibleName("Replacement lyric text")
        self.lyric_replace.setPlaceholderText("Replace with (empty deletes matches)")
        self.lyric_replace.setClearButtonEnabled(True)
        replace_layout.addWidget(self.lyric_replace)
        replacements = QHBoxLayout()
        self.replace_line_button = QPushButton("Replace line…")
        self.replace_line_button.setToolTip("Preview replacing matches in the current lyric line.")
        self.replace_line_button.clicked.connect(lambda: self.preview_lyric_replacements(current_only=True))
        self.replace_all_button = QPushButton("Replace all…")
        self.replace_all_button.setToolTip("Preview replacing every match within the chosen scope.")
        self.replace_all_button.clicked.connect(self.preview_lyric_replacements)
        for button in (self.replace_line_button, self.replace_all_button):
            button.setAutoDefault(False)
            replacements.addWidget(button)
        replace_layout.addLayout(replacements)
        self.lyric_replace_panel.hide()
        self.lyric_replace_toggle.toggled.connect(self.lyric_replace_panel.setVisible)
        self.lyric_replace_toggle.toggled.connect(lambda expanded: self.lyric_replace_toggle.setArrowType(
            Qt.ArrowType.DownArrow if expanded else Qt.ArrowType.RightArrow))
        tools.addWidget(self.lyric_replace_panel)
        self.lyric_search_tools.hide()
        layout.addWidget(self.lyric_search_tools)
        self.lyric_search_status = QLabel()
        self.lyric_search_status.setWordWrap(True)
        # An empty QLabel still reserves a line in the sidebar layout. Keep it
        # hidden until a search has something useful to report.
        self.lyric_search_status.hide()
        layout.addWidget(self.lyric_search_status)
        self.shortcut_find = QShortcut(QKeySequence.StandardKey.Find, self)
        self.shortcut_find.activated.connect(self.focus_lyric_search)
        self.shortcut_find_next = QShortcut(QKeySequence("F3"), self)
        self.shortcut_find_next.activated.connect(self.find_next_lyric)
        self.shortcut_find_previous = QShortcut(QKeySequence("Shift+F3"), self)
        self.shortcut_find_previous.activated.connect(self.find_previous_lyric)

    def focus_lyric_search(self):
        if not self._commit_pending_source():
            return
        self.tabs.setCurrentIndex(0)
        self.lyric_search.setFocus(Qt.FocusReason.ShortcutFocusReason)
        self.lyric_search.selectAll()

    def _matching_lyric_indices(self):
        selected = set(self._selected_line_indices()) if self.lyric_search_scope.currentData() == "selected" else None
        return [index for index in self._view_line_indices
                if (selected is None or index in selected) and self._lyric_matches(self.lines[index].text)]

    def _lyric_matches(self, text):
        return find_text_matches(text, self.lyric_search.text(), case_sensitive=self.lyric_search_case.isChecked())

    def _update_lyric_search(self):
        if not hasattr(self, "lyric_search_status"):
            return
        matches = self._matching_lyric_indices()
        current = self._current_line_index()
        searching = bool(self.lyric_search.text())
        if not searching:
            message = ""
        elif not matches:
            message = "No matching lyrics."
        elif current in matches:
            message = f"Match {matches.index(current) + 1} of {len(matches)}"
        else:
            message = f"{len(matches)} matching line(s)"
        self.lyric_search_status.setText(message)
        self.lyric_search_status.setVisible(bool(message))
        self.lyric_search_tools.setVisible(searching)
        self.replace_line_button.setEnabled(current in matches)
        self.replace_all_button.setEnabled(bool(matches))
        self._highlight_lyric_matches(current in matches)

    def _highlight_lyric_matches(self, matching):
        selections = []
        index = self._current_line_index()
        if matching and index is not None:
            ranges = visible_text_ranges(self.line_text.toPlainText())
            for start, end in self._lyric_matches(self.lines[index].text):
                # While a new line's widgets are loading, the raw field may
                # still contain the preceding lyric. The final load refreshes it.
                if end > len(ranges):
                    continue
                selection = QTextEdit.ExtraSelection()
                selection.cursor = QTextCursor(self.line_text.document())
                selection.cursor.setPosition(ranges[start][0])
                selection.cursor.setPosition(ranges[end - 1][1], QTextCursor.MoveMode.KeepAnchor)
                selection.format.setBackground(QColor("#ffdc73"))
                selection.format.setForeground(QColor("#202020"))
                selections.append(selection)
        self.line_text.setExtraSelections(selections)

    def preview_lyric_replacements(self, _checked=False, *, current_only=False):
        if not self._commit_pending_source():
            return
        indices = self._matching_lyric_indices()
        current = self._current_line_index()
        if current_only:
            indices = [current] if current in indices else []
        changes = []
        count = 0
        for index in sorted(indices):
            line = self.lines[index]
            matches = self._lyric_matches(line.text)
            corrected = replace_lyric_matches(line, matches, self.lyric_replace.text())
            if corrected.text != line.text:
                changes.append((index, corrected))
                count += len(matches)
        if not changes:
            return
        selected = self._selected_line_indices()
        dialog = QDialog(self)
        dialog.setWindowTitle("Preview Lyric Replacements")
        layout = QVBoxLayout(dialog)
        summary = QLabel(f"Replace {count} match(es) in {len(changes)} line(s). Undo restores this entire change.")
        summary.setWordWrap(True)
        layout.addWidget(summary)
        preview = QTableWidget(len(changes), 3)
        preview.setAccessibleName("Replacement preview: original and replacement lyrics")
        preview.setHorizontalHeaderLabels(["Line", "Before", "After"])
        preview.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        preview.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeMode.ResizeToContents)
        preview.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeMode.Stretch)
        preview.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeMode.Stretch)
        preview.verticalHeader().hide()
        for row, (index, corrected) in enumerate(changes):
            values = (f"{index + 1} · {format_timestamp(corrected.start)}", self.lines[index].text, corrected.text)
            for column, value in enumerate(values):
                preview.setItem(row, column, QTableWidgetItem(value))
        preview.resizeRowsToContents()
        layout.addWidget(preview, 1)

        def apply():
            self._push_undo_state()
            for index, corrected in changes:
                # Retain line identity for checked Share selections.
                line = self.lines[index]
                for name in ("text", "segments", "reading_groups", "style_spans", "translation_lines", "has_karaoke"):
                    setattr(line, name, getattr(corrected, name))
            self._finish_line_operation(selected, current)
            dialog.accept()

        self._operation_dialog_buttons(dialog, layout, apply, label="Replace")
        dialog.resize(760, 420)
        self._exec_operation_dialog(dialog)

    def _find_lyric(self, direction):
        if not self._commit_pending_source():
            return
        matches = self._matching_lyric_indices()
        if matches:
            current = self._current_line_index()
            if current in matches:
                target = matches[(matches.index(current) + direction) % len(matches)]
            else:
                row = self.line_list.currentRow()
                candidates = [index for index in matches
                              if (self._view_line_indices.index(index) - row) * direction > 0]
                if candidates:
                    target = candidates[0] if direction > 0 else candidates[-1]
                else:
                    target = matches[0] if direction > 0 else matches[-1]
            self._select_line_index(target, preserve_selection=self.lyric_search_scope.currentData() == "selected")
            self.line_list.scrollToItem(self.line_list.currentItem())
        self._update_lyric_search()

    def find_next_lyric(self):
        self._find_lyric(1)

    def find_previous_lyric(self):
        self._find_lyric(-1)
