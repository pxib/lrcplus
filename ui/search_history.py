"""Search input with autocomplete and a menu of recent searches."""

from PySide6.QtCore import QModelIndex, QPoint, Qt, QTimer, Signal
from PySide6.QtGui import QStandardItem, QStandardItemModel
from PySide6.QtWidgets import QCompleter, QLineEdit, QMenu, QStyle

from core.search_history import (
    HISTORY_LIMITS, clean_history, load_search_history, save_search_history,
)

_ENTRY_ROLE = Qt.ItemDataRole.UserRole
_COMPLETION_ROLE = _ENTRY_ROLE + 1


class SearchHistoryLineEdit(QLineEdit):
    entry_recalled = Signal(object)

    def __init__(
        self, category, parent=None, *, load=None, save=None, remember_idle=False,
        clean=None, label=str, completion_text=None,
    ):
        super().__init__(parent)
        self._limit = HISTORY_LIMITS[category]
        self._load_history = load or (lambda: load_search_history(category))
        self._save_history = save or (lambda values: save_search_history(category, values))
        self._clean_entries = clean or (lambda values: clean_history(values, self._limit))
        self._entry_label = label
        self._completion_text = completion_text or label
        self._history_model = QStandardItemModel(self)
        completer = QCompleter(self._history_model, self)
        completer.setCaseSensitivity(Qt.CaseSensitivity.CaseInsensitive)
        completer.setCompletionRole(_COMPLETION_ROLE)
        self.setCompleter(completer)
        if clean is not None:
            completer.activated[QModelIndex].connect(
                lambda index: self._recall(index.data(_ENTRY_ROLE))
            )
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
        values = self._clean_entries(self._load_history())
        self._set_completions(values)
        return values

    def _set_completions(self, values):
        self._history_model.clear()
        for value in values:
            item = QStandardItem(self._entry_label(value))
            item.setData(value, _ENTRY_ROLE)
            item.setData(self._completion_text(value), _COMPLETION_ROLE)
            self._history_model.appendRow(item)

    def remember_search(self, value=None):
        self._history_timer.stop()
        if value is None:
            value = self.text().strip()
        if not value:
            return
        previous = self._refresh_history()
        values = self._clean_entries([value, *previous])
        if values != previous:
            self._save_history(values)
            self._set_completions(values)

    def clear_history(self):
        self._history_timer.stop()
        self._save_history([])
        self._history_model.clear()

    def _show_history(self):
        menu = QMenu(self)
        values = self._refresh_history()
        for value in values:
            action = menu.addAction(self._entry_label(value).replace("&", "&&"))
            action.triggered.connect(lambda _checked=False, text=value: self._recall(text))
        if values:
            menu.addSeparator()
            menu.addAction("Clear history", self.clear_history)
        else:
            menu.addAction("No recent searches").setEnabled(False)
        menu.exec(self.mapToGlobal(QPoint(0, self.height())))
        menu.deleteLater()

    def _recall(self, value):
        self.setText(self._completion_text(value))
        self.entry_recalled.emit(value)
        self.setFocus()
        self.setCursorPosition(len(self.text()))
