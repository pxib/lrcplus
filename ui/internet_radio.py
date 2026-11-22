"""Browse Radio Browser and tune stations through the main player."""

from html import escape

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.background_job import BackgroundJob
from core.radio_browser import PAGE_SIZE, search_stations


class InternetRadioPage(QWidget):
    def __init__(self, parent, play_stream):
        super().__init__(parent)
        self._play_stream = play_stream
        self._job = BackgroundJob(self, "radio-directory")
        self._job.finished.connect(self._show_results)
        self._job.failed.connect(self._show_error)
        self._stations = ()
        self._query = ("", "", "")
        self._offset = 0
        self._started = False

        layout = QVBoxLayout(self)
        filters = QFormLayout()
        self.name = QLineEdit(self)
        self.country = QLineEdit(self)
        self.tag = QLineEdit(self)
        for label, field in (("Station name", self.name), ("Country", self.country), ("Genre or tag", self.tag)):
            field.setMaxLength(120)
            field.returnPressed.connect(self._search)
            filters.addRow(label, field)
        layout.addLayout(filters)

        actions = QHBoxLayout()
        self.status = QLabel(self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        actions.addWidget(self.status, 1)
        self.search_button = QPushButton("Search", self)
        self.search_button.setAutoDefault(False)
        self.search_button.clicked.connect(self._search)
        actions.addWidget(self.search_button)
        layout.addLayout(actions)

        self.results = QTreeWidget(self)
        self.results.setAccessibleName("Internet radio stations")
        self.results.setHeaderLabels(("Station", "Country", "Genre or tags", "Format"))
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        header = self.results.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(1, 115)
        header.resizeSection(2, 165)
        header.resizeSection(3, 110)
        header.setStretchLastSection(False)
        self.results.itemSelectionChanged.connect(self._update_play_button)
        self.results.itemActivated.connect(self._play_selected)
        layout.addWidget(self.results, 1)

        buttons = QHBoxLayout()
        self.previous_button = QPushButton("Previous page", self)
        self.previous_button.clicked.connect(lambda: self._load_page(self._offset - PAGE_SIZE))
        self.next_button = QPushButton("Next page", self)
        self.next_button.clicked.connect(lambda: self._load_page(self._offset + PAGE_SIZE))
        buttons.addWidget(self.previous_button)
        buttons.addWidget(self.next_button)
        buttons.addStretch()
        self.play_button = QPushButton("Play Station", self)
        self.play_button.clicked.connect(self._play_selected)
        buttons.addWidget(self.play_button)
        for button in (self.previous_button, self.next_button, self.play_button):
            button.setEnabled(False)
            button.setAutoDefault(False)
        layout.addLayout(buttons)

    def start(self):
        if not self._started:
            self._started = True
            self._load_page(0)

    def _search(self):
        self._query = tuple(field.text().strip() for field in (self.name, self.country, self.tag))
        self._load_page(0)

    def _load_page(self, offset):
        self._offset = offset
        self.results.clear()
        self._stations = ()
        self._update_play_button()
        self.previous_button.setEnabled(False)
        self.next_button.setEnabled(False)
        self.status.setText("Loading stations from Radio Browser…")
        self._job.start(search_stations, *self._query, offset)

    def _show_results(self, page):
        self._stations = page.stations
        for index, station in enumerate(page.stations):
            bitrate = f"{station.bitrate} kbps" if station.bitrate else ""
            item = QTreeWidgetItem((station.name, station.country, station.tags,
                                    " ".join(filter(None, (station.codec, bitrate)))))
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            if station.tags:
                item.setToolTip(2, f"<qt>{escape(station.tags)}</qt>")
            self.results.addTopLevelItem(item)
        if page.stations:
            self.results.setCurrentItem(self.results.topLevelItem(0))
            page_number = self._offset // PAGE_SIZE + 1
            label = "Search results" if any(self._query) else "Popular stations"
            self.status.setText(f"{label} · Page {page_number} · Radio Browser")
        else:
            self.status.setText("No playable stations on this page.")
        self.previous_button.setEnabled(self._offset > 0)
        self.next_button.setEnabled(page.has_next)

    def _show_error(self, message):
        self.status.setText(message)
        self.previous_button.setEnabled(self._offset > 0)

    def _update_play_button(self):
        self.play_button.setEnabled(bool(self.results.selectedItems()))

    def _play_selected(self):
        selected = self.results.selectedItems()
        if selected:
            station = self._stations[selected[0].data(0, Qt.ItemDataRole.UserRole)]
            self._play_stream(station.url, title=station.name)
            self.status.setText(f"Selected station · {station.name}")

    def close_search(self):
        self._job.close()
