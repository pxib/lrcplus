"""Search and navigate settings without changing their values."""
from dataclasses import dataclass
import re

from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtGui import QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QScrollArea,
    QStackedWidget, QTabWidget, QVBoxLayout, QWidget,
)


ALIASES = (
    {"ruby", "furigana", "readings"},
    {"romaji", "romanization", "romanisation"},
    {"background", "artwork", "wallpaper"},
    {"font", "typography", "typeface"},
    {"audio", "sound"},
)


def words(text):
    return re.findall(r"\w+", text.casefold().replace("_", " "))


@dataclass
class SearchEntry:
    title: str
    path: str
    widget: QWidget
    keywords: str = ""
    fallback: QWidget | None = None
    availability: str = ""
    focus_widget: QWidget | None = None


class SettingsSearch(QWidget):
    def __init__(self, pages, categories, parent=None):
        super().__init__(parent)
        self.pages = pages
        self.entries = []
        self._highlighted = None
        self._highlight_timer = QTimer(self)
        self._highlight_timer.setSingleShot(True)
        self._highlight_timer.timeout.connect(self._clear_highlight)

        self.search_edit = QLineEdit()
        self.search_edit.setPlaceholderText("Search settings...")
        self.search_edit.setAccessibleName("Search settings")
        self.search_edit.setClearButtonEnabled(True)
        self.search_edit.installEventFilter(self)
        self.search_shortcut = QShortcut(QKeySequence.StandardKey.Find, self)
        self.search_shortcut.activated.connect(self._focus_search)
        self.search_edit.textChanged.connect(self.search)
        self.search_edit.returnPressed.connect(self._activate_current)

        self.results = QListWidget()
        self.results.setObjectName("settingsSearchResults")
        self.results.setAccessibleName("Settings search results")
        self.results.setWordWrap(True)
        self.results.itemClicked.connect(self.activate)
        self.results.itemActivated.connect(self.activate)
        self.results.installEventFilter(self)
        self.summary = QLabel()
        self.summary.setTextFormat(Qt.TextFormat.PlainText)
        result_page = QWidget()
        result_layout = QVBoxLayout(result_page)
        result_layout.addWidget(self.summary)
        result_layout.addWidget(self.results, 1)

        self.notice = QLabel()
        self.notice.setWordWrap(True)
        self.notice.setTextFormat(Qt.TextFormat.PlainText)
        self.notice.hide()
        self.stack = QStackedWidget()
        self.stack.addWidget(pages)
        self.stack.addWidget(result_page)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.notice)
        layout.addWidget(self.stack, 1)
        categories.itemClicked.connect(lambda _item: self.search_edit.clear())
        categories.currentRowChanged.connect(lambda _row: self.search_edit.clear())

    def add(self, widget, title, keywords="", **options):
        # Derive the breadcrumb from actual tab ownership, including hidden scopes.
        route = []
        current = widget
        while current is not None:
            parent = current.parentWidget()
            if isinstance(parent, QStackedWidget) and isinstance(parent.parentWidget(), QTabWidget):
                tabs = parent.parentWidget()
                index = tabs.indexOf(current)
                if index >= 0:
                    route.append(tabs.tabText(index))
            current = parent
        path = " → ".join(reversed(route))
        self.entries.append(SearchEntry(title.rstrip(":"), path, widget, keywords, **options))

    def search(self, query):
        tokens = words(query)
        self.results.clear()
        self.notice.hide()
        if not tokens:
            self.stack.setCurrentIndex(0)
            return
        self._clear_highlight()
        self.stack.setCurrentIndex(1)
        matches = []
        for index, entry in enumerate(self.entries):
            title = entry.title.casefold()
            haystack = " ".join((title, entry.path, entry.keywords, entry.widget.toolTip())).casefold()
            score = 0
            for token in tokens:
                alternatives = {token}
                for group in ALIASES:
                    if token in group:
                        alternatives.update(group)
                if not any(term in haystack for term in alternatives):
                    break
                score += 2 if token in title else 0
            else:
                matches.append((-score, "Local (File)" in entry.path, index, entry))
        for _score, _local, index, entry in sorted(matches):
            item = QListWidgetItem(f"{entry.title}\n{entry.path}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(entry.widget.toolTip() or entry.path)
            self.results.addItem(item)
        count = len(matches)
        self.summary.setText(f"{count} setting{'s' if count != 1 else ''} found" if count else "No settings found. Try another word.")
        if count:
            self.results.setCurrentRow(0)

    def _focus_search(self):
        self.search_edit.setFocus()
        self.search_edit.selectAll()

    def _activate_current(self):
        item = self.results.currentItem()
        if item is not None:
            self.activate(item)

    def activate(self, item):
        entry = self.entries[item.data(Qt.ItemDataRole.UserRole)]
        self.search_edit.clear()
        target = entry.widget
        # Open every owning tab from the outside in before scrolling.
        route = []
        current = target
        while current is not None:
            parent = current.parentWidget()
            if isinstance(parent, QStackedWidget) and isinstance(parent.parentWidget(), QTabWidget):
                route.append((parent.parentWidget(), current))
            current = parent
        for tabs, page in reversed(route):
            tabs.setCurrentWidget(page)
        if not target.isVisible() and entry.fallback is not None:
            target = entry.fallback
            self.notice.setText(entry.availability)
            self.notice.show()
        elif not target.isEnabled():
            message = (
                f"{entry.title} inherits its Global value. Enable Override to edit it for this file."
                if entry.focus_widget is not None else
                f"{entry.title} is currently unavailable. Enable its related option to edit it."
            )
            self.notice.setText(message)
            self.notice.show()
        self._clear_highlight()
        self._highlighted = target
        target.setProperty("settingsSearchHighlight", True)
        self._repolish(target)
        focus = entry.focus_widget if entry.focus_widget is not None else target
        focus.setFocus(Qt.FocusReason.OtherFocusReason)
        # Layout and scroll ranges settle after selecting hidden nested tabs.
        QTimer.singleShot(0, self._scroll_to_highlight)
        self._highlight_timer.start(1800)

    def _scroll_to_highlight(self):
        target = self._highlighted
        if target is None:
            return
        parent = target.parentWidget()
        while parent is not None:
            if isinstance(parent, QScrollArea):
                parent.ensureWidgetVisible(target, 24, 40)
            parent = parent.parentWidget()

    @staticmethod
    def _repolish(widget):
        widget.style().unpolish(widget)
        widget.style().polish(widget)
        widget.update()

    def _clear_highlight(self):
        self._highlight_timer.stop()
        if self._highlighted is not None:
            self._highlighted.setProperty("settingsSearchHighlight", False)
            self._repolish(self._highlighted)
            self._highlighted = None

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.KeyPress:
            if event.key() in {Qt.Key.Key_Return, Qt.Key.Key_Enter}:
                self._activate_current()
                return True
            if event.key() == Qt.Key.Key_Escape and self.search_edit.text():
                self.search_edit.clear()
                self.search_edit.setFocus()
                return True
            if watched is self.search_edit and event.key() == Qt.Key.Key_Down and self.results.count():
                self.results.setFocus()
                return True
        return super().eventFilter(watched, event)
