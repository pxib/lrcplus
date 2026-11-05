"""Load track-specific album art off the UI thread, reusing resolved RPC covers."""

from dataclasses import dataclass, field
from pathlib import Path
import threading
from urllib.parse import urlparse
import weakref

import requests
from PySide6.QtCore import QByteArray, QBuffer, QIODevice, QObject, QSize, Qt, QTimer, Signal
from PySide6.QtGui import QImage, QImageReader

from ui.artwork_background import extract_embedded_cover


MAX_COVER_BYTES = 12_000_000


@dataclass
class AlbumCoverResult:
    image: QImage = field(default_factory=QImage)
    message: str = "No album cover available. You can export the card without one."
    pending: bool = False


def _decode_cover(data):
    if not data or len(data) > MAX_COVER_BYTES:
        return QImage()
    buffer = QBuffer()
    buffer.setData(QByteArray(data))
    buffer.open(QIODevice.OpenModeFlag.ReadOnly)
    reader = QImageReader(buffer)
    reader.setAutoTransform(True)
    size = reader.size()
    if size.isValid() and max(size.width(), size.height()) > 1024:
        reader.setScaledSize(size.scaled(QSize(1024, 1024), Qt.AspectRatioMode.KeepAspectRatio))
    return reader.read()


def _download_cover(url):
    if not isinstance(url, str) or urlparse(url).scheme not in {"http", "https"}:
        return QImage()
    with requests.get(url, stream=True, timeout=(5, 15),
                      headers={"User-Agent": "LRCPlus/1.0 (lyrics image cards)"}) as response:
        response.raise_for_status()
        data = bytearray()
        for chunk in response.iter_content(64 * 1024):
            data.extend(chunk)
            if len(data) > MAX_COVER_BYTES:
                return QImage()
        return _decode_cover(data)


def load_album_cover(path, provider=None):
    cached = {}
    failed_online = False
    if callable(provider):
        try:
            value = provider(path)
            cached = value if isinstance(value, dict) else {}
            if cached.get("url"):
                image = _download_cover(cached["url"])
                if not image.isNull():
                    return AlbumCoverResult(image, "Using the album cover resolved by Discord Rich Presence.")
                failed_online = True
        except Exception:
            # An optional plugin or an expired remote image must not prevent
            # the card from using artwork embedded in its own audio file.
            failed_online = True
    image = _decode_cover(extract_embedded_cover(path))
    if not image.isNull():
        return AlbumCoverResult(image, "Using the album cover embedded in the audio file.")
    if cached.get("pending"):
        return AlbumCoverResult(message="Waiting for album artwork to finish resolving…", pending=True)
    if failed_online:
        return AlbumCoverResult(message="Could not load the resolved cover; no embedded cover is available. Try again or export without it.")
    return AlbumCoverResult()


class CardAlbumArt(QObject):
    changed = Signal()
    _finished = Signal(int, object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.image = QImage()
        self.message = "Include the song's album cover when available."
        self.loading = False
        self._key = None
        self._token = 0
        self._path = None
        self._provider = None
        self._attempts = 0
        self._paused = False
        self._worker_active = False
        self._retry_timer = QTimer(self)
        self._retry_timer.setSingleShot(True)
        self._retry_timer.setInterval(750)
        self._retry_timer.timeout.connect(self._start)
        self._finished.connect(self._complete)

    def configure(self, path, provider, enabled):
        try:
            stat = Path(path).stat() if path else None
            key = (str(path), stat.st_size, stat.st_mtime_ns) if stat else None
        except OSError:
            key = None
        key = (key, provider, bool(enabled))
        if key == self._key:
            return
        self.shutdown()
        self._key = key
        self._path = Path(path) if path else None
        self._provider = provider
        self._attempts = 0
        self.image = QImage()
        if not enabled:
            self.message = "Include the song's album cover when available."
        elif key[0] is None:
            self.message = "Attach an audio file to these lyrics to use its album cover."
        else:
            self.loading = True
            self.message = "Loading album cover…"
            self._start()
        self.changed.emit()

    def retry(self):
        if self._key and self._key[0] and self._key[2] and not self._worker_active:
            self._attempts = 0
            self._retry_timer.stop()
            self._start()

    def _start(self):
        if self._paused or self._worker_active:
            return
        self._token += 1
        token = self._token
        path, provider = self._path, self._provider
        self._worker_active = True
        self.loading = True
        self.message = "Loading album cover…"
        self.changed.emit()
        owner = weakref.ref(self)

        def work():
            try:
                result = load_album_cover(path, provider)
            except Exception:
                result = AlbumCoverResult(message="Could not read this song's album cover. Try again or export without it.")
            target = owner()
            if target is not None:
                try:
                    target._finished.emit(token, result)
                except RuntimeError:
                    pass  # The editor was destroyed while the request finished.

        threading.Thread(target=work, name="LyricsCardCover", daemon=True).start()

    def _complete(self, token, result):
        if token != self._token:
            return
        self._worker_active = False
        self.image = result.image
        self.message = result.message
        self._attempts += 1
        self.loading = result.pending and self._attempts < 60
        if result.pending and not self.loading:
            self.message = "Album artwork is still resolving. Try again when it finishes."
        if self.loading and not self._paused:
            self._retry_timer.start()
        self.changed.emit()

    def pause(self):
        self._paused = True
        self._retry_timer.stop()

    def resume(self):
        self._paused = False
        if self.loading and not self._worker_active:
            self._retry_timer.start()

    def shutdown(self):
        self._token += 1
        self._retry_timer.stop()
        self._worker_active = False
        self.loading = False
