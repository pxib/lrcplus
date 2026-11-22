"""Browse Radio Browser and tune stations through the main player."""

from html import escape
from threading import Event

from PySide6.QtCore import Qt, QTimer
from PySide6.QtWidgets import (
    QFormLayout, QHBoxLayout, QHeaderView, QLabel, QLineEdit, QMessageBox, QPushButton,
    QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget,
)

from core.background_job import BackgroundJob
from core.radio_browser import PAGE_SIZE, search_stations
from core.radio_history import clear_station_history, load_station_history
from core.stream_lyrics import watch_icy_titles


class InternetRadioPage(QWidget):
    def __init__(self, parent, player):
        super().__init__(parent)
        self._player = player
        self._job = BackgroundJob(self, "radio-directory")
        self._job.finished.connect(self._show_results)
        self._job.failed.connect(self._show_error)
        self._stations = ()
        self._query = ("", "", "")
        self._offset = 0
        self._started = False
        self._metadata_cancel = None
        self._metadata_job = BackgroundJob(self, "radio-import-metadata")
        self._metadata_job.finished.connect(self._open_import_search)
        self._metadata_job.failed.connect(self._metadata_failed)
        self._metadata_timeout = QTimer(self)
        self._metadata_timeout.setSingleShot(True)
        self._metadata_timeout.setInterval(8000)
        self._metadata_timeout.timeout.connect(self._metadata_failed)
        player.media_player.sourceChanged.connect(self._cancel_metadata_on_source_change)
        player.media_player.playbackStateChanged.connect(self._cancel_metadata_on_pause)

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
        self.download_button = QPushButton("Download audio from metadata…", self)
        self.download_button.setAutoDefault(False)
        self.download_button.setToolTip("Search YouTube for the song on the station currently playing")
        self.download_button.clicked.connect(self._search_current_song)
        buttons.addWidget(self.download_button)
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
            self._player.play_radio_station(station)
            self.status.setText(f"Selected station · {station.name}")

    def _import_search(self):
        manager = self._player.plugin_manager
        return manager.get_service("ytdlp_import.search") if manager is not None else None

    def _search_current_song(self):
        if not callable(self._import_search()):
            self.status.setText("Enable yt-dlp Audio Import in Settings → Plugins to search for this song.")
        elif self._player._live_playback_delayed:
            self.status.setText("Choose Go Live to search for the song currently on air.")
        elif not self._player._is_live_stream() or not self._player.media_player.isPlaying():
            self.status.setText("Play a live station to search from its song metadata.")
        elif self._player._stream_lyrics.current_song:
            self._open_import_search(self._player._stream_lyrics.current_song)
        else:
            self._metadata_cancel = Event()
            self.download_button.setEnabled(False)
            self.status.setText("Reading the current song title from the station…")
            self._metadata_job.start(watch_icy_titles, self._player._stream_url.toString(), self._metadata_cancel)
            self._metadata_timeout.start()

    def _open_import_search(self, song):
        self._cancel_metadata()
        if not song:
            self.status.setText("The station did not send an artist and song title.")
            return
        search = self._import_search()
        if not callable(search):
            self.status.setText("Enable yt-dlp Audio Import in Settings → Plugins to search for this song.")
        elif search(" ".join(song)):
            self.status.setText(f"Searching yt-dlp for {song[0]} — {song[1]}")
        else:
            self.status.setText("The yt-dlp importer is busy. Try again when it finishes.")

    def _metadata_failed(self, *_):
        self._cancel_metadata()
        self.status.setText("Could not read the current song from the station.")

    def _cancel_metadata_on_source_change(self):
        if self._metadata_cancel is not None:
            self._cancel_metadata()
            self.status.setText("The stream changed. Search again to use its current song.")

    def _cancel_metadata_on_pause(self):
        if not self._player.media_player.isPlaying():
            self._cancel_metadata_on_source_change()

    def _cancel_metadata(self):
        self._metadata_timeout.stop()
        if self._metadata_cancel is not None:
            self._metadata_cancel.set()
            self._metadata_cancel = None
        self._metadata_job.cancel()
        self.download_button.setEnabled(True)

    def close_search(self):
        self._cancel_metadata()
        self._metadata_job.close()
        self._job.close()


class StationHistoryPage(QWidget):
    def __init__(self, parent, play_station):
        super().__init__(parent)
        self._play_station = play_station
        self._history = []
        layout = QVBoxLayout(self)
        self.results = QTreeWidget(self)
        self.results.setAccessibleName("Recently played radio stations")
        self.results.setHeaderLabels(("Station", "Country", "Last played"))
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        header = self.results.header()
        header.setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        header.resizeSection(1, 115)
        header.resizeSection(2, 155)
        header.setStretchLastSection(False)
        self.results.itemSelectionChanged.connect(self._update_play_button)
        self.results.itemActivated.connect(self._play_selected)
        layout.addWidget(self.results, 1)

        buttons = QHBoxLayout()
        self.status = QLabel(self)
        buttons.addWidget(self.status, 1)
        self.clear_button = QPushButton("Clear History…", self)
        self.clear_button.clicked.connect(self._clear_history)
        buttons.addWidget(self.clear_button)
        self.play_button = QPushButton("Play Station", self)
        self.play_button.clicked.connect(self._play_selected)
        buttons.addWidget(self.play_button)
        for button in (self.clear_button, self.play_button):
            button.setAutoDefault(False)
        layout.addLayout(buttons)
        self.refresh()

    def refresh(self):
        self._history = load_station_history()
        self.results.clear()
        for index, (station, played) in enumerate(self._history):
            item = QTreeWidgetItem((station.name, station.country, played.astimezone().strftime("%d %b %Y, %H:%M")))
            item.setData(0, Qt.ItemDataRole.UserRole, index)
            self.results.addTopLevelItem(item)
        if self._history:
            self.results.setCurrentItem(self.results.topLevelItem(0))
        self.status.setText("" if self._history else "No stations played yet.")
        self.clear_button.setEnabled(bool(self._history))
        self._update_play_button()

    def _update_play_button(self):
        self.play_button.setEnabled(bool(self.results.selectedItems()))

    def _play_selected(self):
        selected = self.results.selectedItems()
        if selected:
            station, _ = self._history[selected[0].data(0, Qt.ItemDataRole.UserRole)]
            self._play_station(station)

    def _clear_history(self):
        answer = QMessageBox.question(
            self, "Clear station history", "Delete the list of recently played stations?",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        )
        if answer == QMessageBox.StandardButton.Yes:
            try:
                clear_station_history()
            except OSError:
                self.status.setText("Could not clear station history.")
            else:
                self.refresh()
