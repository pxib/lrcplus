import base64
import math
from pathlib import Path
import time
from urllib.parse import urlsplit

from PySide6.QtCore import QDate, QEvent, QLocale, QSize, Qt
from PySide6.QtGui import QIcon, QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QListWidget, QListWidgetItem, QMenu, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
    QAbstractItemView, QStyle, QStyleOptionTabWidgetFrame, QTabWidget, QToolButton, QWidget,
)

from core.dependencies import DependencyError
from plugins.community.ytdlp_import.job import ImportJob, is_youtube_age_restriction
from plugins.community.ytdlp_import.session_dialog import YouTubeSessionDialog
from plugins.community.ytdlp_import.worker import (
    spotify_playlist_url, spotify_track_url, validate_url, validate_youtube_session_url,
)
from ui.dependency_settings import DependencyManagerDialog
from ui.search_history import SearchHistoryLineEdit
from plugins.community.ytdlp_import.queue_view import DownloadQueueView


def album_details(info, locale=None):
    parts = []
    album = str(info.get("album") or (info.get("musicbrainz_cover") or {}).get("album") or "").strip()
    if album:
        parts.append("Album: " + album)
    for key, label in (("release_date", "Released"), ("upload_date", "Uploaded")):
        value = str(info.get(key) or "")
        date = QDate.fromString(value, "yyyyMMdd") if len(value) == 8 else QDate()
        if date.isValid():
            parts.append(label + ": " + (locale or QLocale.system()).toString(date, QLocale.FormatType.ShortFormat))
            break
    return " · ".join(parts)


def audio_quality(info):
    parts = []
    codec = str(info.get("acodec") or "").strip()
    if codec and codec != "none":
        parts.append({"mp4a": "AAC", "opus": "Opus", "vorbis": "Vorbis"}.get(codec.split(".")[0], codec.upper()))
    for key, scale, unit in (("abr", 1, "kbps"), ("asr", 1000, "kHz")):
        value = info.get(key)
        if isinstance(value, (int, float)) and math.isfinite(value) and value > 0:
            parts.append(f"{value / scale:g} {unit}")
    return "Source audio: " + " · ".join(parts) if parts else ""


def estimated_download_bytes(info, codec):
    source = (info.get("audio_formats") or {}).get(codec) or {}
    for key in ("filesize", "filesize_approx"):
        size = source.get(key)
        if isinstance(size, (int, float)) and math.isfinite(size) and size > 0:
            return size
    duration = info.get("duration")
    if isinstance(duration, (int, float)) and math.isfinite(duration) and duration > 0:
        for key in ("tbr", "abr"):
            bitrate = source.get(key)
            if isinstance(bitrate, (int, float)) and math.isfinite(bitrate) and bitrate > 0:
                size = duration * bitrate * 1000 / 8
                if math.isfinite(size):
                    return size
    return None


class _ImportTabs(QTabWidget):
    """Fit the current page so an idle search can use less space than Downloads."""

    def _frame_size(self):
        option = QStyleOptionTabWidgetFrame()
        self.initStyleOption(option)
        padding = self.style().sizeFromContents(QStyle.ContentsType.CT_TabWidget, option, QSize(0, 0), self)
        return padding + QSize(0, self.tabBar().sizeHint().height())

    def sizeHint(self):
        size = super().sizeHint()
        page = self.currentWidget()
        if page is not None:
            size.setHeight(page.sizeHint().height() + self._frame_size().height())
        return size

    def minimumSizeHint(self):
        size = super().minimumSizeHint()
        page = self.currentWidget()
        if page is not None:
            size.setHeight(page.minimumSizeHint().height() + self._frame_size().height())
        return size

    def heightForWidth(self, width):
        page = self.currentWidget()
        if page is None:
            return super().heightForWidth(width)
        frame = self._frame_size()
        height = page.heightForWidth(max(0, width - frame.width()))
        return (height if height >= 0 else page.sizeHint().height()) + frame.height()


class ImportDialog(QDialog):
    _artwork_available_at = 0.0

    def __init__(self, api, settings, parent=None, *, download_queue=None, plugin_manager=None):
        super().__init__(parent)
        self.api = api
        self.download_queue = download_queue
        self.plugin_manager = plugin_manager
        self._youtube_session_source = None
        self._playlist_url = None
        self._playlist_next = None
        self._spotify_entries = []
        self.config = dict(settings)
        self.job = None
        self.info = None
        self.preview_url = None
        self._last_action = None
        self._closing = False
        self._existing = None
        self._existing_entry = {}
        self._caption_warning = ""
        self.setWindowTitle("Search and Import Audio")
        self.resize(740, 520)

        self.url = SearchHistoryLineEdit(
            "ytdlp",
            load=lambda: self.api.get_setting("search_history", []),
            save=lambda values: self.api.set_setting("search_history", values),
        )
        self.url.setPlaceholderText("Search YouTube or paste a URL")
        self.url.setAccessibleName("Search YouTube or paste a URL")
        self.url.textChanged.connect(self._input_changed)
        self.url.returnPressed.connect(self.preview)
        self.preview_button = QPushButton("Search")
        self.preview_button.clicked.connect(self.preview)
        url_row = QHBoxLayout()
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.preview_button)
        self.actions_button = QToolButton()
        self.actions_button.setText("More")
        self.actions_button.setAccessibleName("More import actions")
        self.actions_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        actions = QMenu(self.actions_button)
        if self.download_queue is not None:
            actions.addAction("Load playlist", self._load_playlist)
            actions.addAction("Paste batch", self._paste_batch)
            actions.addAction("Download with YouTube session…", self._download_with_youtube_session)
            actions.addSeparator()
        actions.addAction("Check setup", lambda: self._start("check"))
        self.actions_button.setMenu(actions)
        url_row.addWidget(self.actions_button)
        self.search_results = QListWidget()
        self.search_results.setAccessibleName("Tracks. Select tracks to download; double-click or press Enter to preview.")
        self.search_results.setIconSize(QSize(80, 50))
        self.search_results.setFixedHeight(210)
        self.search_results.setWordWrap(True)
        if self.download_queue is not None:
            self.search_results.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        if self.download_queue is None:
            self.search_results.itemClicked.connect(self._select_result)
        else:
            self.search_results.itemDoubleClicked.connect(self._select_result)
        self.search_results.itemActivated.connect(self._select_result)
        self.search_results.hide()
        self.spotify_playlist_toggle = QPushButton("Show Spotify playlist")
        self.spotify_playlist_toggle.setCheckable(True)
        self.spotify_playlist_toggle.setAutoDefault(False)
        self.spotify_playlist_toggle.toggled.connect(self._show_spotify_playlist)
        self.spotify_playlist_toggle.hide()
        self.spotify_results = QListWidget()
        self.spotify_results.setAccessibleName("Spotify playlist. Double-click or press Enter on a song to fill search.")
        self.spotify_results.setMaximumHeight(165)
        self.spotify_results.itemDoubleClicked.connect(self._fill_spotify_search)
        self.spotify_results.itemActivated.connect(self._fill_spotify_search)
        self.spotify_results.installEventFilter(self)
        self.spotify_results.hide()
        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(140, 100)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.hide()
        self.title = QLabel()
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
        title_font = self.title.font()
        title_font.setBold(True)
        self.title.setFont(title_font)
        self.album_details = QLabel()
        self.album_details.setTextFormat(Qt.TextFormat.PlainText)
        self.album_details.setWordWrap(True)
        self.album_details.hide()
        self.audio_quality = QLabel()
        self.audio_quality.setTextFormat(Qt.TextFormat.PlainText)
        self.audio_quality.setWordWrap(True)
        self.audio_quality.hide()
        self.artist = QLabel()
        self.artist.setTextFormat(Qt.TextFormat.PlainText)
        self.artist.setWordWrap(True)
        self.artist.hide()
        text = QVBoxLayout()
        text.setSpacing(3)
        text.setAlignment(Qt.AlignmentFlag.AlignVCenter)
        text.addWidget(self.title)
        text.addWidget(self.artist)
        text.addWidget(self.album_details)
        self.track_preview = QWidget()
        info_row = QHBoxLayout(self.track_preview)
        info_row.setContentsMargins(0, 4, 0, 4)
        info_row.addWidget(self.thumbnail)
        info_row.addLayout(text, 1)
        self.track_preview.hide()

        self.folder = QLineEdit(str(settings["download_folder"]))
        self.browse_button = QPushButton("Browse…")
        self.browse_button.clicked.connect(self._browse)
        folder_row = QHBoxLayout()
        folder_row.addWidget(self.folder)
        folder_row.addWidget(self.browse_button)
        self.codec = QComboBox()
        self.codec.addItem("M4A", "m4a")
        self.codec.addItem("MP3 (192 kbps)", "mp3")
        self.codec.setCurrentIndex(max(0, self.codec.findData(settings.get("codec", "m4a"))))
        self.codec.currentIndexChanged.connect(self._check_existing)
        self.codec.currentIndexChanged.connect(self._refresh_preview_metadata)
        form = QFormLayout()
        form.addRow("Save to:", folder_row)
        form.addRow("Audio format:", self.codec)
        self.use_musicbrainz_cover = QCheckBox("Use MusicBrainz for album cover")
        self.use_musicbrainz_cover.setChecked(bool(settings.get("use_musicbrainz_cover", False)))
        self.use_musicbrainz_cover.setToolTip(
            "Matches the title, artist and album when available. Uses source artwork if no reliable cover is found."
        )
        self.use_musicbrainz_cover.toggled.connect(self._artwork_changed)
        self.artwork_hint = QLabel()
        self.artwork_hint.setTextFormat(Qt.TextFormat.PlainText)
        self.artwork_hint.setWordWrap(True)
        self.artwork_hint.hide()
        self.subtitle_combo = QComboBox()
        self.subtitle_combo.addItem("None — audio only", None)
        self.subtitle_combo.setEnabled(False)
        self.subtitle_combo.currentIndexChanged.connect(self._subtitle_changed)
        form.addRow("Subtitles:", self.subtitle_combo)
        self.subtitles_as_lyrics = QCheckBox("Use subtitles as synced lyrics (.lrc)")
        self.subtitles_as_lyrics.setChecked(bool(settings.get("subtitles_as_lyrics", True)))
        self.subtitles_as_lyrics.setEnabled(False)
        self.subtitles_as_lyrics.toggled.connect(self._check_existing)
        self.automatically_find_lyrics = QCheckBox("Automatically find lyrics")
        self.automatically_find_lyrics.setChecked(bool(settings.get("automatically_find_lyrics", False)))
        self.automatically_find_lyrics.setToolTip(
            "Find matching lyrics on LRCLIB. Prefers synced lyrics, then plain text. Selected subtitle lyrics take precedence."
        )
        self.automatically_find_lyrics.toggled.connect(self._check_existing)
        self.subtitle_hint = QLabel("Preview a track to see available subtitle languages.")
        self.subtitle_hint.setWordWrap(True)
        self.subtitle_warning = QLabel()
        self.subtitle_warning.setTextFormat(Qt.TextFormat.PlainText)
        self.subtitle_warning.setWordWrap(True)
        self.subtitle_warning.setStyleSheet(
            "background: #fff3cd; color: #664d03; border: 1px solid #e0c46c; border-radius: 4px; padding: 8px;"
        )
        self.subtitle_warning.hide()
        self.add_to_queue = QCheckBox("Also add to the playback queue")
        self.add_to_queue.setChecked(bool(settings.get("add_to_queue", True)))
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.progress.hide()
        self.status = QLabel("Downloads are added to your library when complete.")
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        self.details = QPlainTextEdit()
        self.details.setReadOnly(True)
        self.details.setMaximumBlockCount(150)
        self.details.setMaximumHeight(110)
        self.details.hide()
        self.details_button = QPushButton("Details")
        self.details_button.setCheckable(True)
        self.details_button.toggled.connect(self._sync_details)
        self.details.textChanged.connect(self._sync_details)
        self.details_button.hide()
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self._retry)
        self.retry_button.hide()
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.download)
        self.download_button.setEnabled(False)
        self.download_button.hide()
        self.download_size = QLabel()
        self.download_size.setToolTip(
            "Estimated source download size before audio conversion. Artwork and subtitles are extra."
        )
        self.download_size.hide()
        self.again_button = QPushButton("Download again")
        self.again_button.clicked.connect(lambda: self.download(force=True))
        self.again_button.hide()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel)
        self.cancel_button.setEnabled(False)
        self.cancel_button.hide()
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        self.options_button = QToolButton()
        self.options_button.setText("Download options")
        self.options_button.setCheckable(True)
        self.options_button.setToolButtonStyle(Qt.ToolButtonStyle.ToolButtonTextBesideIcon)
        self.options_button.setArrowType(Qt.ArrowType.RightArrow)
        self.options_panel = QWidget()
        options = QVBoxLayout(self.options_panel)
        options.setContentsMargins(12, 2, 0, 6)
        options.addLayout(form)
        for widget in (self.subtitles_as_lyrics, self.subtitle_hint, self.automatically_find_lyrics,
                       self.use_musicbrainz_cover, self.artwork_hint, self.add_to_queue,
                       self.audio_quality, self.download_size):
            options.addWidget(widget)
        self.options_panel.hide()
        self.options_button.toggled.connect(self._show_download_options)
        buttons = QHBoxLayout()
        for button in (self.options_button, self.details_button, self.retry_button):
            buttons.addWidget(button)
        buttons.addStretch()
        for button in (self.again_button, self.download_button, self.cancel_button, close_button):
            buttons.addWidget(button)

        outer = QVBoxLayout(self)
        if self.download_queue is not None:
            self.tabs = _ImportTabs()
            outer.addWidget(self.tabs)
            search_page = QWidget()
            layout = QVBoxLayout(search_page)
            self.tabs.addTab(search_page, "Search / Import")
            self.queue_view = DownloadQueueView(self.download_queue, retry_with_session=self._retry_with_youtube_session)
            self.tabs.addTab(self.queue_view, "Downloads")
        else:
            layout = outer
        layout.addLayout(url_row)
        layout.addWidget(self.spotify_playlist_toggle, alignment=Qt.AlignmentFlag.AlignLeft)
        layout.addWidget(self.spotify_results)
        layout.addWidget(self.search_results)
        if self.download_queue is not None:
            self.result_actions = QWidget()
            selection_row = QHBoxLayout(self.result_actions)
            selection_row.setContentsMargins(0, 0, 0, 0)
            self.enqueue_selected_button = QPushButton("Add selected to downloads")
            self.enqueue_selected_button.clicked.connect(self._enqueue_selected)
            selection_row.addWidget(self.enqueue_selected_button)
            self.select_all_button = QPushButton("Select all")
            self.select_all_button.clicked.connect(self.search_results.selectAll)
            selection_row.addWidget(self.select_all_button)
            self.more_button = QPushButton("Load more playlist tracks")
            self.more_button.clicked.connect(lambda: self._load_playlist(more=True))
            self.more_button.hide()
            selection_row.addWidget(self.more_button)
            selection_row.addStretch()
            self.result_actions.hide()
            layout.addWidget(self.result_actions)
        layout.addWidget(self.track_preview)
        layout.addWidget(self.subtitle_warning)
        layout.addWidget(self.progress)
        if self.download_queue is None:
            layout.addWidget(self.status)
        else:
            status_row = QHBoxLayout()
            status_row.addWidget(self.status, 1)
            self.view_downloads_button = QPushButton("View downloads")
            self.view_downloads_button.setAutoDefault(False)
            self.view_downloads_button.clicked.connect(lambda: self.tabs.setCurrentWidget(self.queue_view))
            self.view_downloads_button.hide()
            status_row.addWidget(self.view_downloads_button)
            layout.addLayout(status_row)
            self.download_queue.changed.connect(self._update_downloads_tab)
            self._update_downloads_tab()
        layout.addWidget(self.details)
        layout.addLayout(buttons)
        layout.addWidget(self.options_panel)
        self.nightly_prompt = QWidget()
        nightly_row = QHBoxLayout(self.nightly_prompt)
        nightly_row.setContentsMargins(0, 0, 0, 0)
        nightly_hint = QLabel("A newer yt-dlp build may fix this download.")
        nightly_hint.setWordWrap(True)
        nightly_row.addWidget(nightly_hint, 1)
        self.try_nightly_button = QPushButton("Try Nightly")
        self.try_nightly_button.setAutoDefault(False)
        self.try_nightly_button.clicked.connect(self._open_nightly_setup)
        nightly_row.addWidget(self.try_nightly_button)
        dismiss = QPushButton("Dismiss")
        dismiss.setAutoDefault(False)
        dismiss.clicked.connect(self.nightly_prompt.hide)
        nightly_row.addWidget(dismiss)
        outer.addWidget(self.nightly_prompt)
        self.nightly_prompt.hide()
        if self.download_queue is not None:
            self.download_queue.nightly_suggested.connect(self._offer_nightly)
            self.tabs.currentChanged.connect(self.tabs.updateGeometry)
        self._fit_content()

    def _sync_details(self):
        has_details = bool(self.details.toPlainText().strip())
        self.details_button.setVisible(has_details)
        self.details.setVisible(has_details and self.details_button.isChecked())

    def _show_download_options(self, visible):
        self.options_button.setArrowType(Qt.ArrowType.DownArrow if visible else Qt.ArrowType.RightArrow)
        self.options_panel.setVisible(visible)

    def _refresh_preview_metadata(self):
        info = self.info or {}
        self.album_details.setText(album_details(info))
        self.album_details.setVisible(bool(self.album_details.text()))
        source = (info.get("audio_formats") or {}).get(self.codec.currentData()) or {}
        self.audio_quality.setText(audio_quality(source))
        self.audio_quality.setVisible(bool(self.audio_quality.text()))
        self._refresh_download_size()

    def _refresh_download_size(self):
        size = estimated_download_bytes(self.info or {}, self.codec.currentData())
        if size is not None and not self._existing:
            mb = size / 1_000_000
            self.download_size.setText((f"Estimated size: ~{mb:.1f}MB" if mb >= 1 else f"Estimated size: ~{mb:.2f}MB") + "")
            self.download_size.show()
        else:
            self.download_size.clear()
            self.download_size.hide()

    def _fit_content(self):
        layout = self.layout()
        if layout is not None:
            layout.activate()
            height = layout.totalHeightForWidth(self.width())
            self.resize(self.width(), height if height >= 0 else layout.sizeHint().height())

    def event(self, event):
        handled = super().event(event)
        if event.type() == QEvent.Type.LayoutRequest:
            self._fit_content()
        return handled

    def eventFilter(self, watched, event):
        if (watched is self.spotify_results and event.type() == QEvent.Type.KeyPress
                and event.key() in (Qt.Key.Key_Return, Qt.Key.Key_Enter)):
            item = self.spotify_results.currentItem()
            if item is not None:
                self._fill_spotify_search(item)
            return True
        return super().eventFilter(watched, event)

    def apply_settings(self, settings):
        self.config.update(settings)
        if self.job is None:
            self.folder.setText(str(settings["download_folder"]))
            self.codec.setCurrentIndex(max(0, self.codec.findData(settings["codec"])))
            self.use_musicbrainz_cover.setChecked(bool(settings.get("use_musicbrainz_cover", False)))
            self.automatically_find_lyrics.setChecked(bool(settings.get("automatically_find_lyrics", False)))

    def _artwork_changed(self):
        self.config["use_musicbrainz_cover"] = self.use_musicbrainz_cover.isChecked()
        self._url_changed()
        self.status.setText("Preview the track to update artwork. Existing files keep their current cover; use Download again to change it.")

    def _input_changed(self):
        self._playlist_url = None
        self._playlist_next = None
        if hasattr(self, "more_button"):
            self.more_button.hide()
        self.search_results.clear()
        self._show_search_results(False)
        if spotify_track_url(self.url.text()):
            self.preview_button.setText("Fill search")
        elif self._is_playlist_link():
            self.preview_button.setText("Load playlist")
        else:
            self.preview_button.setText("Preview" if self._is_url() else "Search")
        self._url_changed()

    def _is_url(self):
        value = self.url.text().strip().lower()
        return value.startswith(("http:", "https:")) or "://" in value

    def _is_playlist_link(self):
        value = self.url.text().strip()
        if spotify_playlist_url(value):
            return True
        if self.download_queue is None or not self._is_url():
            return False
        try:
            url = urlsplit(value)
        except ValueError:
            return False
        return (url.scheme in {"http", "https"} and url.path.rstrip("/") == "/playlist"
                and url.hostname in {"youtube.com", "www.youtube.com", "m.youtube.com", "music.youtube.com"})

    def _show_search_results(self, visible):
        self.search_results.setVisible(visible)
        if self.download_queue is not None:
            self.result_actions.setVisible(visible)

    def _show_spotify_playlist(self, visible):
        self.spotify_playlist_toggle.setText("Hide Spotify playlist" if visible else "Show Spotify playlist")
        self.spotify_results.setVisible(visible)

    def _fill_spotify_search(self, item):
        query = item.data(Qt.ItemDataRole.UserRole)
        if self.job is None and query:
            self.url.setText(query)
            self.url.setFocus()
            self.status.setText("Search text filled in from Spotify. Edit it or select Search.")

    def _add_search_result(self, result):
        parts = [str(result.get("artist") or "")]
        duration = result.get("duration")
        if isinstance(duration, (int, float)) and math.isfinite(duration):
            seconds = max(0, round(duration))
            parts.append(f"{seconds // 60}:{seconds % 60:02d}")
        item = QListWidgetItem(str(result.get("title") or "Untitled") + "\n" + " · ".join(filter(None, parts)))
        item.setData(Qt.ItemDataRole.UserRole, result.get("url", ""))
        item.setData(Qt.ItemDataRole.UserRole + 1, result)
        if result.get("available") is False:
            item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
        item.setToolTip(result.get("url", "Unavailable track"))
        item.setSizeHint(QSize(0, 62))
        try:
            pixmap = QPixmap()
            if pixmap.loadFromData(base64.b64decode(result.get("thumbnail", ""))):
                item.setIcon(QIcon(pixmap))
        except (ValueError, TypeError):
            pass
        self.search_results.addItem(item)
        self._show_search_results(True)

    def _select_result(self, item):
        if self.download_queue is not None and len(self.search_results.selectedItems()) > 1:
            return
        if self.job is not None:
            return
        self.url.blockSignals(True)
        self.url.setText(item.data(Qt.ItemDataRole.UserRole))
        self.url.blockSignals(False)
        self.preview_button.setText("Preview")
        self.preview()

    def _url_changed(self):
        self.info = None
        self.preview_url = None
        self._existing = None
        self._caption_warning = ""
        self.track_preview.hide()
        self.title.clear()
        self._refresh_preview_metadata()
        self.artist.clear()
        self.artist.hide()
        self.thumbnail.clear()
        self.thumbnail.hide()
        self.artwork_hint.clear()
        self.artwork_hint.hide()
        self.progress.hide()
        self.download_button.setText("Download")
        self.download_button.setEnabled(False)
        self.download_button.hide()
        self.again_button.hide()
        self.retry_button.hide()
        self._populate_subtitles([])

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Download Folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def _queue_options(self):
        folder = self.folder.text().strip()
        if not folder:
            self.options_button.setChecked(True)
            self.folder.setFocus()
            raise ValueError("Choose a download folder first.")
        selected = self.subtitle_combo.currentData()
        values = dict(self.config, download_folder=folder, codec=self.codec.currentData(),
                      add_to_queue=self.add_to_queue.isChecked(),
                      use_musicbrainz_cover=self.use_musicbrainz_cover.isChecked(),
                      automatically_find_lyrics=self.automatically_find_lyrics.isChecked(),
                      subtitles_as_lyrics=self.subtitles_as_lyrics.isChecked(),
                      subtitle_language=(selected or {}).get("language", ""),
                      subtitle_kind=(selected or {}).get("kind", "manual"))
        for key, value in values.items():
            if key != "concurrent_downloads":
                self.api.set_setting(key, value)
        return {"config": values, "folder": folder, "codec": self.codec.currentData(),
                "subtitle": selected, "subtitles_as_lyrics": self.subtitles_as_lyrics.isChecked(),
                "add_to_queue": self.add_to_queue.isChecked()}

    def _enqueue_entries(self, entries, *, force=False, youtube_session=None):
        try:
            count = self.download_queue.enqueue(entries, self._queue_options(), force=force, youtube_session=youtube_session)
        except (OSError, ValueError) as error:
            self.status.setText(str(error))
            return False
        if count:
            message = "Added 1 track to downloads." if count == 1 else f"Added {count} tracks to downloads."
            if count < len(entries):
                message += " Skipped tracks already in the queue."
            if self.download_queue.paused:
                message += " The download queue is paused."
        else:
            message = "The selected tracks are already in the download queue."
        self.status.setText(message)
        self.view_downloads_button.setVisible(bool(self.download_queue.items))
        return True

    def _choose_youtube_session(self, url, config):
        dialog = YouTubeSessionDialog(self, url=url, config=config, source=self._youtube_session_source)
        try:
            if dialog.exec() == QDialog.DialogCode.Accepted:
                self._youtube_session_source = dialog.source
                return dialog.source
        finally:
            dialog.deleteLater()

    def _download_with_youtube_session(self):
        try:
            url = validate_youtube_session_url(self.url.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        source = self._choose_youtube_session(url, self.config)
        if source is not None and self._enqueue_entries([{"url": url}], youtube_session=source):
            self.tabs.setCurrentWidget(self.queue_view)

    def _retry_with_youtube_session(self, item):
        if item is None:
            return
        request = item["request"]
        source = self._choose_youtube_session(request["url"], request["config"])
        if source is not None:
            self.download_queue.retry_with_youtube_session(item, source)

    def _update_downloads_tab(self):
        queue = self.download_queue
        pending = len(queue.jobs) + sum(item["state"] == "waiting" and item["id"] not in queue.jobs
                                        for item in queue.items)
        title = f"Downloads ({pending})" if pending else "Downloads"
        self.tabs.setTabText(self.tabs.indexOf(self.queue_view), title)
        if not queue.items:
            self.view_downloads_button.hide()

    def _enqueue_selected(self):
        entries = [self.search_results.item(index).data(Qt.ItemDataRole.UserRole + 1)
                   for index in range(self.search_results.count())
                   if self.search_results.item(index).isSelected()]
        if not entries:
            self.status.setText("Select tracks in the result list first. Double-click a track to preview it.")
            return
        self._enqueue_entries(entries)

    def _paste_batch(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Add multiple track URLs")
        dialog.resize(580, 350)
        layout = QVBoxLayout(dialog)
        layout.addWidget(QLabel("Paste one track URL per line. Use Load playlist for a playlist URL."))
        urls = QPlainTextEdit()
        urls.setAccessibleName("Batch track URLs")
        layout.addWidget(urls)
        error_label = QLabel()
        error_label.setWordWrap(True)
        layout.addWidget(error_label)
        add = QPushButton("Add to downloads")
        layout.addWidget(add)

        def submit():
            try:
                entries = [{"url": validate_url(line)} for line in urls.toPlainText().splitlines() if line.strip()]
                if not entries:
                    raise ValueError("Paste at least one track URL.")
                if len(entries) > 1000:
                    raise ValueError("Add at most 1000 URLs at a time.")
            except ValueError as error:
                error_label.setText(str(error))
                return
            if self._enqueue_entries(entries):
                dialog.accept()
            else:
                error_label.setText(self.status.text())

        add.clicked.connect(submit)
        dialog.exec()

    def _load_playlist(self, _checked=False, *, more=False):
        if self.job is not None:
            return
        if not more:
            if spotify_playlist_url(self.url.text()):
                self._url_changed()
                self.url.remember_search()
                self._start("spotify_playlist")
                return
            try:
                self._playlist_url = validate_url(self.url.text())
            except ValueError as error:
                self.status.setText(str(error))
                return
            self._playlist_next = 1
            self.spotify_playlist_toggle.setChecked(False)
            self._url_changed()
            self.search_results.clear()
            self._show_search_results(False)
            self.more_button.hide()
            self.url.remember_search()
        if self._playlist_url:
            self._start("playlist")

    def preview(self):
        if self.job is not None:
            return
        if self._is_playlist_link():
            self._load_playlist()
            return
        spotify_url = spotify_track_url(self.url.text())
        if spotify_url:
            self._url_changed()
            self._start("spotify_search_query")
            return
        if not self._is_url():
            self.search_youtube(self.url.text())
            return
        try:
            url = validate_url(self.url.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self._url_changed()
        self.preview_url = url
        self.url.remember_search()
        self._start("preview")

    def search_youtube(self, query):
        if self.job is not None:
            return False
        query = query.strip()
        if not query or len(query) > 500:
            self.status.setText("Enter a YouTube search of 1 to 500 characters, or paste a URL.")
            return False
        if self.download_queue is not None:
            self.tabs.setCurrentIndex(0)
        self.spotify_playlist_toggle.setChecked(False)
        self.url.setText(query)
        self.preview_button.setText("Search")
        self._url_changed()
        self.search_results.clear()
        self._show_search_results(False)
        self.url.remember_search()
        self._start("search")
        return True

    def _history_key(self):
        return self.info["identity"] + ":" + self.codec.currentData() if self.info else None

    def _populate_subtitles(self, tracks):
        self.subtitle_combo.blockSignals(True)
        self.subtitle_combo.clear()
        self.subtitle_combo.addItem("None — audio only", None)
        preferred = 0
        for track in tracks:
            kind = "Uploaded" if track["kind"] == "manual" else "Automatic"
            label = f'{track["name"]} [{track["language"]}] · {kind}'
            selected = {"language": track["language"], "kind": track["kind"]}
            self.subtitle_combo.addItem(label, selected)
            if (track["language"] == self.config.get("subtitle_language")
                    and track["kind"] == self.config.get("subtitle_kind", "manual")):
                preferred = self.subtitle_combo.count() - 1
        self.subtitle_combo.setCurrentIndex(preferred)
        self.subtitle_combo.blockSignals(False)
        self.subtitle_hint.setText(
            "Keeps the original VTT/SRT file. Lyric conversion preserves line timing and existing lyric files."
            if tracks else ("No VTT/SRT subtitles are available for this track." if self.info
                            else "Preview a track to see available subtitle languages.")
        )
        self._sync_subtitle_controls()
        self._refresh_subtitle_warning()

    def _sync_subtitle_controls(self):
        available = self.job is None and self.info is not None
        self.subtitle_combo.setEnabled(available and self.subtitle_combo.count() > 1)
        self.subtitles_as_lyrics.setEnabled(available and self.subtitle_combo.currentData() is not None)

    def _subtitle_changed(self, *_args):
        self._caption_warning = ""
        self._sync_subtitle_controls()
        self._check_existing()

    def _refresh_subtitle_warning(self):
        selected = self.subtitle_combo.currentData()
        tracks = (self.info or {}).get("ass_subtitle_tracks", [])
        message = self._caption_warning
        if not message and any(selected is None or selected == track for track in tracks):
            message = ("ASS/SSA subtitles detected. This importer uses VTT/SRT alternatives when available. "
                       "Conversion to synced lyrics removes styling, positioning and karaoke effects; review the result.")
        self.subtitle_warning.setText("Warning: " + message if message else "")
        self.subtitle_warning.setVisible(bool(message))

    def _has_selected_subtitles(self, entry):
        selected = self.subtitle_combo.currentData()
        if selected is None:
            return True
        if entry.get("subtitle") != selected or not Path(entry.get("subtitle_path") or "").is_file():
            return False
        return not self.subtitles_as_lyrics.isChecked() or (
            entry.get("lyrics_source") not in {"LRCLIB", "existing"}
            and Path(entry.get("lyrics_path") or "").is_file()
        )

    def _check_existing(self, *_args):
        history = self.api.get_setting("downloads", {})
        entry = history.get(self._history_key(), {}) if isinstance(history, dict) else {}
        self._existing_entry = entry if isinstance(entry, dict) else {}
        value = entry.get("path") if isinstance(entry, dict) else None
        self._existing = str(value) if value and Path(value).is_file() else None
        self.download_button.setText("Add existing" if self._existing else "Download")
        if self._existing and not self._has_selected_subtitles(self._existing_entry):
            self.download_button.setText("Get subtitles")
        elif self._existing and self.automatically_find_lyrics.isChecked() and not self._saved_lyrics():
            self.download_button.setText("Find lyrics")
        self.download_button.setEnabled(self.info is not None and self.job is None)
        self.download_button.setVisible(self.info is not None)
        self.again_button.setVisible(bool(self._existing))
        self._refresh_download_size()
        if self._existing:
            self.status.setText("Already downloaded: " + self._existing)
            if self._existing_entry.get("subtitle") == self.subtitle_combo.currentData():
                self._caption_warning = self._existing_entry.get("subtitle_warning", "")
        self._refresh_subtitle_warning()

    def _saved_lyrics(self):
        value = self._existing_entry.get("lyrics_path")
        return value if value and Path(value).is_file() else None

    def download(self, _checked=False, *, force=False):
        if self.job is not None or self.info is None:
            return
        if self.download_queue is not None:
            self._enqueue_entries([{"url": self.preview_url, "title": self.info.get("title")}], force=force)
            return
        self._check_existing()
        if (force or not self._existing) and not self.folder.text().strip():
            self.options_button.setChecked(True)
            self.folder.setFocus()
            self.status.setText("Choose a download folder first.")
            return
        try:
            selected = self.subtitle_combo.currentData() or {}
            for key, value in (("download_folder", self.folder.text().strip()), ("codec", self.codec.currentData()),
                               ("add_to_queue", self.add_to_queue.isChecked()),
                               ("use_musicbrainz_cover", self.use_musicbrainz_cover.isChecked()),
                               ("automatically_find_lyrics", self.automatically_find_lyrics.isChecked()),
                               ("subtitle_language", selected.get("language", "")),
                               ("subtitle_kind", selected.get("kind", "manual")),
                               ("subtitles_as_lyrics", self.subtitles_as_lyrics.isChecked())):
                self.api.set_setting(key, value)
                self.config[key] = value
        except OSError as error:
            self.status.setText("Could not save download preferences: " + str(error))
            return
        if self._existing and not force:
            if not self._has_selected_subtitles(self._existing_entry):
                self._start("subtitles")
            elif self.automatically_find_lyrics.isChecked() and not self._saved_lyrics():
                self._start("lyrics")
            else:
                use_saved = self.automatically_find_lyrics.isChecked() or (
                    selected and self.subtitles_as_lyrics.isChecked()
                    and self._existing_entry.get("lyrics_source") not in {"LRCLIB", "existing"}
                )
                lyrics = self._saved_lyrics() if use_saved else None
                source = ("existing" if self.automatically_find_lyrics.isChecked()
                          else self._existing_entry.get("lyrics_source"))
                self._import_file(self._existing, lyrics_path=lyrics, lyrics_source=source)
            return
        self._start("download")

    def _start(self, action):
        if self.job is not None:
            return
        self._last_action = action
        request = {"action": action, "config": dict(self.config), "url": self.preview_url}
        if action == "playlist":
            request.update(url=self._playlist_url, start=self._playlist_next or 1)
        if action == "search":
            request["query"] = self.url.text().strip()
        if action == "spotify_search_query":
            request["url"] = spotify_track_url(self.url.text())
        if action == "spotify_playlist":
            request["url"] = spotify_playlist_url(self.url.text())
        request["config"]["use_musicbrainz_cover"] = self.use_musicbrainz_cover.isChecked()
        request["config"]["automatically_find_lyrics"] = self.automatically_find_lyrics.isChecked()
        if action == "preview":
            request["musicbrainz_not_before"] = ImportDialog._artwork_available_at
        if action in {"download", "subtitles", "lyrics"}:
            request.update(folder=self.folder.text().strip(), codec=self.codec.currentData(), identity=self.info["identity"],
                           subtitle=self.subtitle_combo.currentData(), subtitles_as_lyrics=self.subtitles_as_lyrics.isChecked())
            if action in {"subtitles", "lyrics"}:
                request.update(audio_path=self._existing, folder=str(Path(self._existing).parent),
                               info=dict(self.info), existing_lyrics_path=self._saved_lyrics())
            elif self.use_musicbrainz_cover.isChecked() and self.info.get("musicbrainz_cover"):
                request["musicbrainz_cover"] = dict(self.info["musicbrainz_cover"], data=self.info["thumbnail"])
        self.details.clear()
        if action == "download":
            self.nightly_prompt.hide()
        if action == "preview":
            self._caption_warning = ""
        self._refresh_subtitle_warning()
        self.retry_button.hide()
        self.progress.setRange(0, 0)
        self.status.setText({"preview": "Reading track information…", "download": "Starting download…",
                             "playlist": "Loading playlist tracks…",
                             "search": "Searching YouTube…",
                             "spotify_search_query": "Reading Spotify track information…",
                             "spotify_playlist": "Loading Spotify playlist…",
                             "lyrics": "Looking for lyrics for the existing audio…",
                             "subtitles": "Downloading subtitles for the existing audio…", "check": "Checking tools…"}[action])
        job = ImportJob(request, self)
        self.job = job
        job.event_received.connect(self._event)
        job.succeeded.connect(lambda result: self._succeeded(action, result))
        job.failed.connect(self._failed)
        job.cancelled.connect(lambda: self.status.setText("Cancelled. You can retry when ready."))
        job.finished.connect(self._finished)
        self._set_busy(True)
        job.start()

    def _set_busy(self, busy):
        if self.download_queue is not None:
            for widget in (self.enqueue_selected_button, self.select_all_button, self.more_button):
                widget.setEnabled(not busy)
        for widget in (self.url, self.folder, self.browse_button, self.codec, self.preview_button,
                       self.actions_button, self.add_to_queue, self.again_button, self.use_musicbrainz_cover,
                       self.automatically_find_lyrics, self.search_results, self.spotify_playlist_toggle,
                       self.spotify_results):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.cancel_button.setVisible(busy)
        self.progress.setVisible(busy)
        self.download_button.setEnabled(not busy and self.info is not None)
        self._sync_subtitle_controls()

    def _event(self, event):
        kind = event.get("event")
        if kind == "search_result":
            self._add_search_result(event["result"])
        elif kind == "spotify_playlist_page":
            self._spotify_entries.extend(event["entries"])
            loaded, total = event["loaded"], event["total"]
            count = f"{loaded} of {total}" if total else str(loaded)
            self.status.setText(f"Read {count} Spotify playlist entries…")
        elif kind == "log":
            self.details.appendPlainText(str(event.get("message", "")))
        elif kind == "subtitle_warning":
            self._caption_warning = str(event.get("message", ""))
            self._refresh_subtitle_warning()
        elif kind == "processing":
            self.progress.setRange(0, 0)
            self.status.setText(str(event.get("message", "Processing audio…")))
        elif kind == "progress":
            total, received = event.get("total"), event.get("downloaded", 0)
            if isinstance(total, (int, float)) and math.isfinite(total) and total > 0:
                self.progress.setRange(0, 100)
                self.progress.setValue(max(0, min(99, round(received / total * 100))))
            speed, eta = event.get("speed"), event.get("eta")
            text = "Downloading audio"
            if isinstance(speed, (int, float)) and math.isfinite(speed):
                text += f" · {speed * 8 / 1_000_000:.2f} Mbps"
            if isinstance(eta, (int, float)) and math.isfinite(eta):
                text += f" · {eta:.0f}s remaining"
            self.status.setText(text)

    def _succeeded(self, action, result):
        if self._closing:
            return
        self.progress.setRange(0, 100)
        self.progress.setValue(100)
        if action == "playlist":
            for entry in result.get("results", []):
                self._add_search_result(entry)
            self._playlist_next = result.get("next_start")
            self.more_button.setVisible(self._playlist_next is not None)
            self.status.setText(f'{result.get("title", "Playlist")}: {self.search_results.count()} tracks loaded. Select tracks to download.')
        elif action == "search":
            self.search_results.clear()
            for entry in result.get("results", []):
                self._add_search_result(entry)
            count = self.search_results.count()
            self._show_search_results(bool(count))
            self.status.setText(f"{count} results. Select a track to preview." if count
                                else "No finished tracks found. Try another search.")
        elif action == "spotify_search_query":
            self.url.setText(result["query"])
            self.status.setText("Search text filled in from Spotify. Edit it or select Search.")
        elif action == "spotify_playlist":
            self.spotify_results.clear()
            for entry in self._spotify_entries:
                text = entry["title"]
                if entry.get("artist"):
                    text += " — " + entry["artist"]
                item = QListWidgetItem(text)
                item.setData(Qt.ItemDataRole.UserRole, entry.get("query"))
                if not entry.get("query"):
                    item.setFlags(item.flags() & ~Qt.ItemFlag.ItemIsEnabled & ~Qt.ItemFlag.ItemIsSelectable)
                self.spotify_results.addItem(item)
            self.spotify_playlist_toggle.setVisible(bool(result["loaded"]))
            self.spotify_playlist_toggle.setChecked(bool(result["loaded"]))
            title, count = result["title"], result["searchable"]
            if count:
                self.status.setText(f"{title} · {count} searchable songs. Double-click a song or press Enter to fill search.")
            else:
                self.status.setText(f"{title} has no searchable songs.")
        elif action == "check":
            self.status.setText(str(result.get("message", "Setup check complete.")))
        elif action == "preview":
            self.info = result
            self.track_preview.show()
            self._refresh_preview_metadata()
            self.title.setText(str(result.get("title", "")))
            duration = result.get("duration")
            detail = str(result.get("artist", ""))
            if isinstance(duration, (int, float)) and math.isfinite(duration):
                seconds = max(0, round(duration))
                detail += f" · {seconds // 60}:{seconds % 60:02d}"
            self.artist.setText(detail)
            self.artist.setVisible(bool(detail.strip()))
            cover = result.get("musicbrainz_cover")
            self.artwork_hint.setText(
                "MusicBrainz / Cover Art Archive: " + str(cover.get("album", ""))
                if cover else str(result.get("artwork_message", ""))
            )
            self.artwork_hint.setVisible(bool(self.artwork_hint.text().strip()))
            try:
                pixmap = QPixmap()
                if pixmap.loadFromData(base64.b64decode(result.get("thumbnail", ""))):
                    self.thumbnail.setPixmap(pixmap.scaled(self.thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
                    self.thumbnail.show()
            except (ValueError, TypeError):
                pass
            self.status.setText("Ready to download.")
            self._populate_subtitles(result.get("subtitle_tracks", []))
            self._check_existing()
        elif action in {"download", "subtitles", "lyrics"}:
            path = result.get("path")
            if not path or not Path(path).is_file():
                self._failed("The downloaded audio file could not be found.")
                return
            history = self.api.get_setting("downloads", {})
            history = dict(history) if isinstance(history, dict) else {}
            history[self._history_key()] = {key: result[key] for key in
                                           ("path", "url", "title", "subtitle", "subtitle_path", "lyrics_path", "lyrics_source", "subtitle_warning", "musicbrainz_cover") if key in result}
            if action in {"subtitles", "lyrics"}:
                history[self._history_key()] = dict(self._existing_entry, **history[self._history_key()])
                if action == "subtitles" and "lyrics_path" in result and "lyrics_source" not in result:
                    history[self._history_key()].pop("lyrics_source", None)
            try:
                self.api.set_setting("downloads", dict(list(history.items())[-1000:]))
            except OSError as error:
                self.details.appendPlainText("Could not remember the source URL: " + str(error))
            self._existing = path
            self._refresh_download_size()
            self.download_button.setText("Add existing")
            self.again_button.show()
            self._existing_entry = history[self._history_key()]
            if self.automatically_find_lyrics.isChecked() and not self._saved_lyrics():
                self.download_button.setText("Find lyrics")
            self._caption_warning = result.get("subtitle_warning", "")
            self._refresh_subtitle_warning()
            added = self._import_file(path, lyrics_path=result.get("lyrics_path"), lyrics_source=result.get("lyrics_source"))
            if result.get("lyrics_message"):
                self.details.appendPlainText(result["lyrics_message"])
                if added:
                    self.status.setText(self.status.text() + "\n" + result["lyrics_message"])
            if result.get("warning"):
                self.details.appendPlainText(result["warning"])
                if added:
                    self.status.setText(self.status.text() + "\n" + result["warning"])

    def _import_file(self, path, *, lyrics_path=None, lyrics_source=None):
        try:
            attach = bool(lyrics_path and (lyrics_source in {"LRCLIB", "existing"} or self.subtitles_as_lyrics.isChecked()))
            if attach:
                self.api.attach_lyrics(str(path), str(lyrics_path))
            self.api.import_audio_files([str(path)], add_to_queue=self.add_to_queue.isChecked())
            destination = "library and queue" if self.add_to_queue.isChecked() else "library"
            self.status.setText(f"Added to {destination}: {Path(path).name}")
            if attach:
                self.status.setText(self.status.text() + ("\nLyrics attached." if lyrics_source else "\nSynced subtitle lyrics attached."))
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(f"Audio saved at {path}. Could not add it to the library/queue: {error}. Use Add existing to retry.")
            return False

    def _failed(self, message):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(str(message))
        self.details.appendPlainText(str(message))
        if (self.download_queue is not None and self._last_action == "preview"
                and is_youtube_age_restriction(message)):
            self.status.setText("YouTube reported an age restriction. Choose More → Download with YouTube session… to test access.")
        self.retry_button.show()

    def _can_offer_nightly(self):
        if self.plugin_manager is None:
            return False
        manager = self.plugin_manager.dependency_manager
        try:
            state = manager.state()
            status = manager.status("yt-dlp", state=state)
            if status["source"] != "managed" or status.get("channel") != "stable":
                return False
            # Downloads use the environment activated at startup until LRCPlus restarts.
            if state["environment"] != manager.start_environment:
                status = manager.status("yt-dlp", state={**state, "environment": manager.start_environment})
            return status.get("channel") == "stable"
        except (DependencyError, KeyError):
            return False

    def _offer_nightly(self):
        self.nightly_prompt.setVisible(self._can_offer_nightly())

    def _open_nightly_setup(self):
        if self._can_offer_nightly():
            dialog = DependencyManagerDialog(self.plugin_manager, self)
            dialog.dependencies.select_yt_dlp_build("nightly")
            try:
                dialog.exec()
            finally:
                dialog.deleteLater()
        self._offer_nightly()

    def _finished(self):
        job = self.job
        if (job is not None and job.request.get("action") == "preview"
                and job.request.get("config", {}).get("use_musicbrainz_cover")):
            # Workers are short-lived, so keep request pacing across previews.
            ImportDialog._artwork_available_at = time.time() + 1.1
        self.job = None
        self._spotify_entries.clear()
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        if job is not None:
            if job.request["action"] == "download" and getattr(job, "nightly_candidate", False):
                self._offer_nightly()
            job.deleteLater()

    def _retry(self):
        if self._last_action == "download":
            self.download(force=True)
        elif self._last_action in {"subtitles", "lyrics"}:
            self.download()
        elif self._last_action == "playlist":
            self._load_playlist(more=True)
        elif self._last_action in {"preview", "search", "spotify_search_query", "spotify_playlist"}:
            self.preview()
        else:
            self._start("check")

    def cancel(self):
        if self.job is not None:
            self.status.setText("Cancelling…")
            self.job.cancel()

    def shutdown(self):
        self._closing = True
        if self.job is not None:
            self.job.shutdown()

    def reject(self):
        self.shutdown()
        super().reject()

    def showEvent(self, event):
        self._closing = False
        super().showEvent(event)

    def closeEvent(self, event):
        self.shutdown()
        event.accept()
