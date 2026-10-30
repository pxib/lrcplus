import base64
import math
from pathlib import Path
import time

from PySide6.QtCore import QDate, QEvent, QLocale, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPlainTextEdit, QProgressBar, QPushButton, QVBoxLayout,
)

from plugins.community.ytdlp_import.job import ImportJob
from plugins.community.ytdlp_import.worker import validate_url


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


class ImportDialog(QDialog):
    _artwork_available_at = 0.0

    def __init__(self, api, settings, parent=None):
        super().__init__(parent)
        self.api = api
        self.config = dict(settings)
        self.job = None
        self.info = None
        self.preview_url = None
        self._last_action = None
        self._closing = False
        self._existing = None
        self._existing_entry = {}
        self._caption_warning = ""
        self.setWindowTitle("Import Audio from URL")
        self.resize(740, 520)

        self.url = QLineEdit()
        self.url.setPlaceholderText("Paste a single video or audio URL…")
        self.url.textChanged.connect(self._url_changed)
        self.url.returnPressed.connect(self.preview)
        self.preview_button = QPushButton("Preview")
        self.preview_button.clicked.connect(self.preview)
        url_row = QHBoxLayout()
        url_row.addWidget(self.url, 1)
        url_row.addWidget(self.preview_button)
        self.thumbnail = QLabel()
        self.thumbnail.setFixedSize(140, 100)
        self.thumbnail.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.thumbnail.setText("No artwork")
        self.title = QLabel("Preview a track to begin.")
        self.title.setTextFormat(Qt.TextFormat.PlainText)
        self.title.setWordWrap(True)
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
        text.addWidget(self.album_details)
        text.addWidget(self.audio_quality)
        text.addWidget(self.artist)
        info_row = QHBoxLayout()
        info_row.addWidget(self.thumbnail)
        info_row.addLayout(text, 1)

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
        self.setup_button = QPushButton("Check setup")
        self.setup_button.clicked.connect(lambda: self._start("check"))
        self.retry_button = QPushButton("Retry")
        self.retry_button.clicked.connect(self._retry)
        self.retry_button.hide()
        self.download_button = QPushButton("Download")
        self.download_button.clicked.connect(self.download)
        self.download_button.setEnabled(False)
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
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.reject)
        buttons = QHBoxLayout()
        for button in (self.setup_button, self.details_button, self.retry_button):
            buttons.addWidget(button)
        buttons.addStretch()
        for button in (self.again_button, self.download_size, self.download_button, self.cancel_button, close_button):
            buttons.addWidget(button)

        layout = QVBoxLayout(self)
        layout.addLayout(url_row)
        layout.addLayout(info_row)
        layout.addLayout(form)
        layout.addWidget(self.use_musicbrainz_cover)
        layout.addWidget(self.artwork_hint)
        layout.addWidget(self.subtitles_as_lyrics)
        layout.addWidget(self.subtitle_hint)
        layout.addWidget(self.subtitle_warning)
        layout.addWidget(self.add_to_queue)
        layout.addWidget(self.progress)
        layout.addWidget(self.status)
        layout.addWidget(self.details)
        layout.addLayout(buttons)
        self._fit_content()

    def _sync_details(self):
        has_details = bool(self.details.toPlainText().strip())
        self.details_button.setVisible(has_details)
        self.details.setVisible(has_details and self.details_button.isChecked())

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

    def apply_settings(self, settings):
        self.config.update(settings)
        if self.job is None:
            self.folder.setText(str(settings["download_folder"]))
            self.codec.setCurrentIndex(max(0, self.codec.findData(settings["codec"])))
            self.use_musicbrainz_cover.setChecked(bool(settings.get("use_musicbrainz_cover", False)))

    def _artwork_changed(self):
        self.config["use_musicbrainz_cover"] = self.use_musicbrainz_cover.isChecked()
        self._url_changed()
        self.status.setText("Preview the track to update artwork. Existing files keep their current cover; use Download again to change it.")

    def _url_changed(self):
        self.info = None
        self.preview_url = None
        self._existing = None
        self._caption_warning = ""
        self.title.setText("Preview a track to begin.")
        self._refresh_preview_metadata()
        self.artist.clear()
        self.artist.hide()
        self.thumbnail.setText("No artwork")
        self.artwork_hint.clear()
        self.artwork_hint.hide()
        self.progress.hide()
        self.download_button.setText("Download")
        self.download_button.setEnabled(False)
        self.again_button.hide()
        self.retry_button.hide()
        self._populate_subtitles([])

    def _browse(self):
        folder = QFileDialog.getExistingDirectory(self, "Download Folder", self.folder.text())
        if folder:
            self.folder.setText(folder)

    def preview(self):
        if self.job is not None:
            return
        try:
            self.preview_url = validate_url(self.url.text())
        except ValueError as error:
            self.status.setText(str(error))
            return
        self.info = None
        self._existing = None
        self._refresh_preview_metadata()
        self._start("preview")

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
        return not self.subtitles_as_lyrics.isChecked() or Path(entry.get("lyrics_path") or "").is_file()

    def _check_existing(self, *_args):
        history = self.api.get_setting("downloads", {})
        entry = history.get(self._history_key(), {}) if isinstance(history, dict) else {}
        self._existing_entry = entry if isinstance(entry, dict) else {}
        value = entry.get("path") if isinstance(entry, dict) else None
        self._existing = str(value) if value and Path(value).is_file() else None
        self.download_button.setText("Add existing" if self._existing else "Download")
        if self._existing and not self._has_selected_subtitles(self._existing_entry):
            self.download_button.setText("Get subtitles")
        self.download_button.setEnabled(self.info is not None and self.job is None)
        self.again_button.setVisible(bool(self._existing))
        self._refresh_download_size()
        if self._existing:
            self.status.setText("Already downloaded: " + self._existing)
            if self._existing_entry.get("subtitle") == self.subtitle_combo.currentData():
                self._caption_warning = self._existing_entry.get("subtitle_warning", "")
        self._refresh_subtitle_warning()

    def download(self, _checked=False, *, force=False):
        if self.job is not None or self.info is None:
            return
        self._check_existing()
        if (force or not self._existing) and not self.folder.text().strip():
            self.status.setText("Choose a download folder first.")
            return
        try:
            selected = self.subtitle_combo.currentData() or {}
            for key, value in (("download_folder", self.folder.text().strip()), ("codec", self.codec.currentData()),
                               ("add_to_queue", self.add_to_queue.isChecked()),
                               ("use_musicbrainz_cover", self.use_musicbrainz_cover.isChecked()),
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
            else:
                lyrics = self._existing_entry.get("lyrics_path") if selected else None
                self._import_file(self._existing, lyrics_path=lyrics)
            return
        self._start("download")

    def _start(self, action):
        if self.job is not None:
            return
        self._last_action = action
        request = {"action": action, "config": dict(self.config), "url": self.preview_url}
        request["config"]["use_musicbrainz_cover"] = self.use_musicbrainz_cover.isChecked()
        if action == "preview":
            request["musicbrainz_not_before"] = ImportDialog._artwork_available_at
        if action in {"download", "subtitles"}:
            request.update(folder=self.folder.text().strip(), codec=self.codec.currentData(), identity=self.info["identity"],
                           subtitle=self.subtitle_combo.currentData(), subtitles_as_lyrics=self.subtitles_as_lyrics.isChecked())
            if action == "subtitles":
                request.update(audio_path=self._existing, folder=str(Path(self._existing).parent))
            elif self.use_musicbrainz_cover.isChecked() and self.info.get("musicbrainz_cover"):
                request["musicbrainz_cover"] = dict(self.info["musicbrainz_cover"], data=self.info["thumbnail"])
        self.details.clear()
        if action == "preview":
            self._caption_warning = ""
        self._refresh_subtitle_warning()
        self.retry_button.hide()
        self.progress.setRange(0, 0)
        self.status.setText({"preview": "Reading track information…", "download": "Starting download…",
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
        for widget in (self.url, self.folder, self.browse_button, self.codec, self.preview_button,
                       self.setup_button, self.add_to_queue, self.again_button, self.use_musicbrainz_cover):
            widget.setEnabled(not busy)
        self.cancel_button.setEnabled(busy)
        self.progress.setVisible(busy)
        self.download_button.setEnabled(not busy and self.info is not None)
        self._sync_subtitle_controls()

    def _event(self, event):
        kind = event.get("event")
        if kind == "log":
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
        if action == "check":
            self.status.setText(str(result.get("message", "Setup check complete.")))
        elif action == "preview":
            self.info = result
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
            self.thumbnail.setText("No artwork")
            try:
                pixmap = QPixmap()
                if pixmap.loadFromData(base64.b64decode(result.get("thumbnail", ""))):
                    self.thumbnail.setPixmap(pixmap.scaled(self.thumbnail.size(), Qt.AspectRatioMode.KeepAspectRatio,
                                                          Qt.TransformationMode.SmoothTransformation))
            except (ValueError, TypeError):
                pass
            self.status.setText("Ready to download.")
            self._populate_subtitles(result.get("subtitle_tracks", []))
            self._check_existing()
        elif action in {"download", "subtitles"}:
            path = result.get("path")
            if not path or not Path(path).is_file():
                self._failed("The downloaded audio file could not be found.")
                return
            history = self.api.get_setting("downloads", {})
            history = dict(history) if isinstance(history, dict) else {}
            history[self._history_key()] = {key: result[key] for key in
                                           ("path", "url", "title", "subtitle", "subtitle_path", "lyrics_path", "subtitle_warning", "musicbrainz_cover") if key in result}
            if action == "subtitles":
                history[self._history_key()] = dict(self._existing_entry, **history[self._history_key()])
            try:
                self.api.set_setting("downloads", dict(list(history.items())[-1000:]))
            except OSError as error:
                self.details.appendPlainText("Could not remember the source URL: " + str(error))
            self._existing = path
            self._refresh_download_size()
            self.download_button.setText("Add existing")
            self.again_button.show()
            self._existing_entry = history[self._history_key()]
            self._caption_warning = result.get("subtitle_warning", "")
            self._refresh_subtitle_warning()
            added = self._import_file(path, lyrics_path=result.get("lyrics_path"))
            if result.get("warning"):
                self.details.appendPlainText(result["warning"])
                if added:
                    self.status.setText(self.status.text() + "\n" + result["warning"])

    def _import_file(self, path, *, lyrics_path=None):
        try:
            if lyrics_path and self.subtitles_as_lyrics.isChecked():
                self.api.attach_lyrics(str(path), str(lyrics_path))
            self.api.import_audio_files([str(path)], add_to_queue=self.add_to_queue.isChecked())
            destination = "library and queue" if self.add_to_queue.isChecked() else "library"
            self.status.setText(f"Added to {destination}: {Path(path).name}")
            if lyrics_path and self.subtitles_as_lyrics.isChecked():
                self.status.setText(self.status.text() + "\nSynced subtitle lyrics attached.")
            return True
        except (OSError, ValueError, RuntimeError) as error:
            self.status.setText(f"Audio saved at {path}. Could not add it to the library/queue: {error}. Use Add existing to retry.")
            return False

    def _failed(self, message):
        self.progress.setRange(0, 100)
        self.progress.setValue(0)
        self.status.setText(str(message))
        self.details.appendPlainText(str(message))
        self.retry_button.show()

    def _finished(self):
        job = self.job
        if (job is not None and job.request.get("action") == "preview"
                and job.request.get("config", {}).get("use_musicbrainz_cover")):
            # Workers are short-lived, so keep request pacing across previews.
            ImportDialog._artwork_available_at = time.time() + 1.1
        self.job = None
        self._set_busy(False)
        if self.progress.maximum() == 0:
            self.progress.setRange(0, 100)
            self.progress.setValue(0)
        if job is not None:
            job.deleteLater()

    def _retry(self):
        if self._last_action == "download":
            self.download(force=True)
        elif self._last_action == "subtitles":
            self.download()
        elif self._last_action == "preview":
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
