"""Find lyric lines while preserving the editor's current sidebar order."""

from PySide6.QtCore import Qt
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import QHBoxLayout, QLabel, QLineEdit, QPushButton


class LyricsEditorSearchMixin:
    def _build_lyric_search(self, layout):
        row = QHBoxLayout()
        self.lyric_search = QLineEdit()
        self.lyric_search.setPlaceholderText("Find lyrics (Ctrl+F)")
        self.lyric_search.setClearButtonEnabled(True)
        self.lyric_search.setAccessibleName("Find lyric lines")
        self.lyric_search.textChanged.connect(self._update_lyric_search)
        self.lyric_search.returnPressed.connect(self.find_next_lyric)
        layout.addWidget(self.lyric_search)
        for label, handler in (("Previous", self.find_previous_lyric), ("Next", self.find_next_lyric)):
            button = QPushButton(label)
            button.setAutoDefault(False)
            button.clicked.connect(handler)
            row.addWidget(button)
        layout.addLayout(row)
        self.lyric_search_status = QLabel()
        self.lyric_search_status.setWordWrap(True)
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
        query = self.lyric_search.text().casefold().strip()
        return [index for index in self._view_line_indices
                if query and query in self.lines[index].text.casefold()]

    def _update_lyric_search(self):
        if not hasattr(self, "lyric_search_status"):
            return
        matches = self._matching_lyric_indices()
        current = self._current_line_index()
        if not self.lyric_search.text().strip():
            message = ""
        elif not matches:
            message = "No matching lyrics."
        elif current in matches:
            message = f"Match {matches.index(current) + 1} of {len(matches)}"
        else:
            message = f"{len(matches)} matching line(s)"
        self.lyric_search_status.setText(message)

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
            self._select_line_index(target)
            self.line_list.scrollToItem(self.line_list.currentItem())
        self._update_lyric_search()

    def find_next_lyric(self):
        self._find_lyric(1)

    def find_previous_lyric(self):
        self._find_lyric(-1)
