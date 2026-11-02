"""Small Qt dialogs and workers for non-blocking application updates."""
from __future__ import annotations

from datetime import datetime
import re
import threading
import time
from pathlib import Path

from PySide6.QtCore import QThread, Qt, QUrl, Signal
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QPlainTextEdit,
    QProgressBar,
    QPushButton,
    QVBoxLayout,
)

from core.app_info import APP_VERSION
from core.updater import Release, UpdateError, download_asset, extract_update_archive, find_update


class UpdateCheckWorker(QThread):
    checked = Signal(object)
    failed = Signal(str)

    def __init__(self, channel: str, parent=None):
        super().__init__(parent)
        self.channel = channel

    def run(self):
        try:
            self.checked.emit(find_update(APP_VERSION, self.channel))
        except UpdateError as error:
            self.failed.emit(str(error))


class UpdateDownloadWorker(QThread):
    progress = Signal(int, int)
    stage_changed = Signal(str)
    prepared = Signal(str)
    failed = Signal(str)
    cancelled = Signal()

    def __init__(self, release: Release, transaction: Path, parent=None):
        super().__init__(parent)
        self.release = release
        self.transaction = Path(transaction)
        self.cancel_event = threading.Event()
        match = re.search(r"-Windows-(x64|arm64)\.zip$", release.asset.name, re.IGNORECASE)
        if match is None:
            raise UpdateError("The release archive has an unsupported platform name.")
        self.architecture = match.group(1).lower()
        self.version_text = release.tag[1:] if release.tag[:1].lower() == "v" else release.tag

    def cancel(self):
        self.cancel_event.set()

    def run(self):
        try:
            archive = download_asset(
                self.release.asset,
                self.transaction / "release.zip",
                progress=lambda done, total: self.progress.emit(done, total or -1),
                cancel_event=self.cancel_event,
            )
            if self.cancel_event.is_set():
                raise UpdateError("The update download was cancelled.")
            self.stage_changed.emit("Verifying package contents...")
            staged = extract_update_archive(
                archive,
                self.transaction / "preflight",
                expected_version=self.version_text,
                architecture=self.architecture,
            )
            self.prepared.emit(str(staged))
        except UpdateError as error:
            if self.cancel_event.is_set():
                self.cancelled.emit()
            else:
                self.failed.emit(str(error))


class UpdateAvailableDialog(QDialog):
    update_requested = Signal()
    skip_requested = Signal()

    def __init__(self, release: Release, parent=None):
        super().__init__(parent)
        self.release = release
        self.setWindowTitle("LRCPlus Update Available")
        self.setMinimumSize(520, 440)
        self.setModal(False)

        layout = QVBoxLayout(self)
        title = QLabel(f"LRCPlus {release.tag} is available")
        title.setStyleSheet("font-weight: 600; font-size: 15px;")
        layout.addWidget(title)
        current = QLabel(f"Current version: {APP_VERSION}    Available version: {release.tag}")
        current.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        layout.addWidget(current)
        published = QLabel(f"Published: {release.published_at}")
        layout.addWidget(published)
        if release.prerelease:
            warning = QLabel("Prerelease build: experimental software that may contain bugs.")
            warning.setWordWrap(True)
            layout.addWidget(warning)
        notes = QPlainTextEdit()
        notes.setReadOnly(True)
        notes.setPlainText(release.body)
        notes.setPlaceholderText("No release notes were provided.")
        layout.addWidget(notes, 1)

        buttons = QHBoxLayout()
        update = QPushButton("Update Now")
        update.clicked.connect(self._request_update)
        buttons.addWidget(update)
        later = QPushButton("Remind Me Later")
        later.clicked.connect(self.close)
        buttons.addWidget(later)
        skip = QPushButton("Skip This Version")
        skip.clicked.connect(self._skip_version)
        buttons.addWidget(skip)
        view = QPushButton("View on GitHub")
        view.clicked.connect(lambda: QDesktopServices.openUrl(QUrl(release.html_url)))
        buttons.addWidget(view)
        layout.addLayout(buttons)

    def _request_update(self):
        self.update_requested.emit()
        self.close()

    def _skip_version(self):
        self.skip_requested.emit()
        self.close()


class UpdateDownloadDialog(QDialog):
    cancel_requested = Signal()

    def __init__(self, version: str, parent=None):
        super().__init__(parent)
        self.setWindowTitle(f"Downloading LRCPlus {version}")
        self.setMinimumWidth(420)
        self.setModal(False)
        self._started = time.monotonic()
        self._downloaded = 0
        self._total = -1

        layout = QVBoxLayout(self)
        self.stage_label = QLabel("Connecting to GitHub...")
        layout.addWidget(self.stage_label)
        self.progress_bar = QProgressBar()
        self.progress_bar.setRange(0, 0)
        layout.addWidget(self.progress_bar)
        self.detail_label = QLabel("Preparing download")
        self.detail_label.setWordWrap(True)
        layout.addWidget(self.detail_label)
        buttons = QHBoxLayout()
        self.cancel_button = QPushButton("Cancel")
        self.cancel_button.clicked.connect(self.cancel_requested.emit)
        buttons.addWidget(self.cancel_button)
        hide_button = QPushButton("Hide")
        hide_button.clicked.connect(self.hide)
        buttons.addWidget(hide_button)
        layout.addLayout(buttons)

    def set_stage(self, text: str):
        self.stage_label.setText(text)
        if text != "Downloading update...":
            self.progress_bar.setRange(0, 0)

    def set_progress(self, downloaded: int, total: int):
        self._downloaded = downloaded
        self._total = total
        if total > 0:
            self.progress_bar.setRange(0, 1000)
            self.progress_bar.setValue(min(1000, downloaded * 1000 // total))
            percent = downloaded * 100 // total
            remaining = max(0, total - downloaded)
            elapsed = max(0.001, time.monotonic() - self._started)
            speed = downloaded / elapsed
            eta = remaining / speed if speed > 0 else None
            eta_text = f"\nRemaining: about {eta:.0f} seconds" if eta is not None else ""
            self.detail_label.setText(
                f"Progress: {percent}%\nDownloaded: {_format_bytes(downloaded)} / {_format_bytes(total)}"
                f"\nSpeed: {_format_bytes(speed)}/s{eta_text}"
            )
        else:
            self.progress_bar.setRange(0, 0)
            self.detail_label.setText(f"Downloaded: {_format_bytes(downloaded)}")

    def set_finished(self):
        self.cancel_button.setEnabled(False)
        self.stage_label.setText("Preparing installation...")
        self.progress_bar.setRange(0, 1)
        self.progress_bar.setValue(1)
        self.detail_label.setText("The verified update is ready to install.")


def _format_bytes(value: float) -> str:
    amount = float(value)
    for unit in ("B", "KB", "MB", "GB"):
        if amount < 1024 or unit == "GB":
            return f"{amount:.1f} {unit}"
        amount /= 1024
    return f"{amount:.1f} GB"
