"""Open a direct audio URL or browse internet radio in one window."""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QFormLayout, QHBoxLayout, QLabel, QLineEdit, QPushButton,
    QTabWidget, QVBoxLayout, QWidget,
)

from ui.internet_radio import InternetRadioPage, StationFavorites, StationFavoritesPage, StationHistoryPage


class NetworkPlaybackDialog(QDialog):
    def __init__(self, player):
        super().__init__(player)
        self._player = player
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("Network Playback")
        self.resize(740, 490)

        layout = QVBoxLayout(self)
        self.tabs = QTabWidget(self)
        layout.addWidget(self.tabs, 1)
        direct = QWidget(self.tabs)
        direct_layout = QVBoxLayout(direct)
        form = QFormLayout()
        self.url = QLineEdit(direct)
        self.url.setPlaceholderText("https://…")
        self.url.returnPressed.connect(self._play_url)
        form.addRow("Audio stream URL", self.url)
        direct_layout.addLayout(form)

        actions = QHBoxLayout()
        self.url_status = QLabel(direct)
        self.url_status.setWordWrap(True)
        self.url_status.setTextFormat(Qt.TextFormat.PlainText)
        actions.addWidget(self.url_status, 1)
        self.play_button = QPushButton("Play", direct)
        self.play_button.setAutoDefault(False)
        self.play_button.setEnabled(False)
        self.play_button.clicked.connect(self._play_url)
        self.url.textChanged.connect(self._url_changed)
        actions.addWidget(self.play_button)
        direct_layout.addLayout(actions)
        direct_layout.addStretch()
        self.tabs.addTab(direct, "Direct URL")

        favorites = StationFavorites(self)
        self.radio = InternetRadioPage(self.tabs, player, favorites)
        self.tabs.addTab(self.radio, "Internet Radio")
        self.favorites = StationFavoritesPage(self.tabs, player.play_radio_station, favorites)
        self.tabs.addTab(self.favorites, "Favorites")
        self.history = StationHistoryPage(self.tabs, player.play_radio_station, favorites)
        self.tabs.addTab(self.history, "Station History")
        player.station_history_changed.connect(self.history.refresh)
        self.tabs.currentChanged.connect(self._start_radio_if_selected)

        footer = QHBoxLayout()
        self.auto_lyrics_checkbox = QCheckBox("Find lyrics for live streams", self)
        self.auto_lyrics_checkbox.setToolTip(
            "Read song metadata over a second stream connection and look for plain lyrics on LRCLIB."
        )
        self.auto_lyrics_checkbox.toggled.connect(player.set_radio_auto_lyrics)
        footer.addWidget(self.auto_lyrics_checkbox)
        self.keep_live_buffer_checkbox = QCheckBox("Keep buffered audio on resume", self)
        self.keep_live_buffer_checkbox.setToolTip(
            "Resume from the paused position when buffered audio is available. "
            "Automatic lyrics resume when you choose Go Live."
        )
        self.keep_live_buffer_checkbox.toggled.connect(player.set_resume_live_from_buffer)
        footer.addWidget(self.keep_live_buffer_checkbox)
        self.sync_playback_preferences()
        footer.addStretch()
        close_button = QPushButton("Close", self)
        close_button.setAutoDefault(False)
        close_button.clicked.connect(self.reject)
        footer.addWidget(close_button)
        layout.addLayout(footer)
        self.url.setFocus(Qt.FocusReason.OtherFocusReason)

    def sync_playback_preferences(self):
        settings = self._player.playback_settings
        with QSignalBlocker(self.auto_lyrics_checkbox), QSignalBlocker(self.keep_live_buffer_checkbox):
            self.auto_lyrics_checkbox.setChecked(settings.get("radio_auto_lyrics", False))
            self.keep_live_buffer_checkbox.setChecked(settings.get("resume_live_from_buffer", False))

    def _url_changed(self, text):
        self.url_status.clear()
        self.play_button.setEnabled(bool(text.strip()))

    def _play_url(self):
        url = self.url.text().strip()
        if url:
            try:
                self._player.load_stream(url)
            except ValueError as error:
                self.url_status.setText(str(error))
                self.url.setFocus(Qt.FocusReason.OtherFocusReason)
            else:
                self.accept()

    def _start_radio_if_selected(self):
        if self.tabs.currentWidget() is self.radio:
            self.radio.start()

    def done(self, result):
        self.radio.close_search()
        super().done(result)
