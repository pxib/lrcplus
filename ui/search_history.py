"""Search input with autocomplete and a menu of recent searches."""

from PySide6.QtCore import QPoint, QStringListModel, Qt, QTimer
from PySide6.QtWidgets import QCompleter, QLineEdit, QMenu, QStyle

from core.search_history import (
    HISTORY_LIMITS, clean_history, load_search_history, save_search_history,
)


class SearchHistoryLineEdit(QLineEdit):
    def __init__(self, category, parent=None, *, load=None, save=None, remember_idle=False):
        super().__init__(parent)
        self._limit = HISTORY_LIMITS[category]
        self._load_history = load or (lambda: load_search_history(category))
        self._save_history = save or (lambda values: save_search_history(category, values))
        self._history_model = QStringListModel(self)
        completer = QCompleter(self._history_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        self.setCompleter(completer)
        self.history_action = self.addAction(
            self.style().standardIcon(QStyle.StandardPixmap.SP_FileDialogDetailedView),
            QLineEdit.ActionPosition.TrailingPosition,
        )
        self.history_action.setText("Search history")
        self.history_action.setToolTip("Search history")
        self.history_action.triggered.connect(self._show_history)
        self._refresh_history()
        self._history_timer = QTimer(self)
        self._history_timer.setSingleShot(True)
        self._history_timer.setInterval(1200)
        self._history_timer.timeout.connect(self.remember_search)
        if remember_idle:
            self.textChanged.connect(lambda _text: self._history_timer.start())
            self.editingFinished.connect(self.remember_search)

    def _refresh_history(self):
        values = clean_history(self._load_history(), self._limit)
        self._history_model.setStringList(values)
        return values

    def remember_search(self):
        self._history_timer.stop()
        value = self.text().strip()
        if not value:
            return
        previous = self._refresh_history()
        values = clean_history([value, *previous], self._limit)
        if values != previous:
            self._save_history(values)
            self._history_model.setStringList(values)

    def clear_history(self):
        self._history_timer.stop()
        self._save_history([])
        self._history_model.setStringList([])

    def _show_history(self):
        menu = QMenu(self)
        values = self._refresh_history()
        for value in values:
            action = menu.addAction(value.replace("&", "&&"))
            action.triggered.connect(lambda _checked=False, text=value: self._recall(text))
        if values:
            menu.addSeparator()
            menu.addAction("Clear history", self.clear_history)
        else:
            menu.addAction("No recent searches").setEnabled(False)
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))
        menu.deleteLater()

    def _recall(self, value):
        self.setText(value)
        self.setFocus()
        self.setCursorPosition(len(value))
