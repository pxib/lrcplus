"""Cancellable Qt HTTP transport. Requests happen only after explicit Translate."""
import json

from PySide6.QtCore import QObject, Signal, QTimer, QUrl, QByteArray
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from plugins.community.lyrics_translator import cloud_providers as providers


class CloudTranslationJob(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    finished = Signal()
    usage_changed = Signal()

    def __init__(self, provider, parent=None):
        super().__init__(parent)
        self.provider = provider
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self._running = False
        self._epoch = 0
        self.estimated_characters_sent = 0

    def start(self, request):
        if self._running:
            raise RuntimeError("A translation operation is already running.")
        self._running = True
        self._epoch += 1
        epoch = self._epoch
        self.request = request
        if request["action"] == "catalog":
            QTimer.singleShot(0, lambda: self._complete(providers.catalog()) if self._running and self._epoch == epoch else None)
            return
        try:
            self.groups = providers.batches(self.provider, request["rows"])
            self.values, self.completed, self.total = {}, 0, len(request["rows"])
            self._next()
        except (ValueError, KeyError, TypeError) as error:
            self._fail(str(error))

    def _next(self):
        if not self.groups:
            self._complete({"translations": self.values})
            return
        self.group = self.groups.pop(0)
        method, url, headers, body = providers.request(
            self.provider, self.request["source"], self.request["target"],
            [text for text, _ in self.group], self.request.get("settings", {}))
        query = QNetworkRequest(QUrl(url))
        query.setTransferTimeout(30000)
        query.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                           QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
        for name, value in headers.items():
            query.setRawHeader(QByteArray(name.encode("ascii")), QByteArray(value.encode("utf-8")))
        self.reply = self.network.get(query) if method == "GET" else self.network.post(query, QByteArray(body))
        reply = self.reply
        reply.finished.connect(lambda: self._received(reply))
        self.estimated_characters_sent += sum(len(text) for text, _ in self.group)
        self.usage_changed.emit()

    def _received(self, reply):
        if self.reply is not reply:
            return
        self.reply = None
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status != 200:
                labels = {401: "authentication failed", 403: "access denied", 429: "rate limit reached", 456: "quota exhausted"}
                self._fail("Translation request failed: " + labels.get(status, f"HTTP {status or 'connection error'}") + ".")
                return
            values = providers.response(self.provider, json.loads(bytes(reply.readAll())), len(self.group))
            for (_, indices), text in zip(self.group, values):
                for index in indices:
                    self.values[str(index)] = text
                self.completed += len(indices)
            self.progress.emit(self.completed, self.total)
            if self._running:
                self._next()
        except (ValueError, KeyError, TypeError):
            # No raw server body, URL, API key or lyric text in UI diagnostics.
            self._fail("Translation failed. Check the selected provider's credentials, quota, connection and language pair.")
        finally:
            reply.deleteLater()

    def _complete(self, result):
        self._running = False
        self.succeeded.emit(result)
        self.finished.emit()

    def _fail(self, message):
        self._running = False
        self.values = {}
        self.failed.emit(message)
        self.finished.emit()

    def cancel(self):
        if not self._running:
            return
        self._running = False
        self.values = {}
        reply, self.reply = self.reply, None
        if reply is not None:
            reply.abort()
            reply.deleteLater()
        self.finished.emit()

    def shutdown(self):
        self.cancel()
