"""Abortable companion HTTP requests on Qt's asynchronous network transport."""

import json

from PySide6.QtCore import QByteArray, QObject, QUrl, Signal
from PySide6.QtNetwork import QNetworkAccessManager, QNetworkRequest

from core.companion_chat import build_request, parse_reply


class CompanionJob(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent=None, *, request_builder=build_request):
        super().__init__(parent)
        self.network = QNetworkAccessManager(self)
        self.reply = None
        self.buffer = bytearray()
        self.request_builder = request_builder

    def start(self, function, *args):
        # Same argument contract as BackgroundJob; transport is now abortable.
        self.cancel()
        try:
            url, headers, body = self.request_builder(*args)
            query = QNetworkRequest(QUrl(url))
            query.setTransferTimeout(70000)
            query.setAttribute(QNetworkRequest.Attribute.RedirectPolicyAttribute,
                               QNetworkRequest.RedirectPolicy.ManualRedirectPolicy)
            for name, value in headers.items():
                query.setRawHeader(QByteArray(name.encode("ascii")), QByteArray(value.encode("utf-8")))
            self.reply = self.network.post(query, QByteArray(json.dumps(body, ensure_ascii=False).encode("utf-8")))
            reply = self.reply
            reply.setReadBufferSize(65536)
            reply.readyRead.connect(lambda: self.read(reply))
            reply.finished.connect(lambda: self.received(reply))
        except (ValueError, TypeError, UnicodeError):
            self.failed.emit("Could not start the request. Check the AI settings and saved memories.")

    def read(self, reply):
        if reply is not self.reply:
            return
        self.buffer.extend(bytes(reply.readAll()))
        if len(self.buffer) > 1024 * 1024:
            self.cancel()
            self.failed.emit("The chat provider returned an oversized response.")

    def received(self, reply):
        if reply is not self.reply:
            reply.deleteLater()
            return
        self.read(reply)
        if reply is not self.reply:
            return
        self.reply = None
        try:
            status = reply.attribute(QNetworkRequest.Attribute.HttpStatusCodeAttribute)
            if status != 200:
                raise ValueError(f"Chat request failed (HTTP {status or 'connection error'}). Check credentials, model, quota, and connection.")
            try:
                data = json.loads(self.buffer)
            except (ValueError, UnicodeError):
                raise ValueError("The chat provider returned invalid JSON.") from None
            result = parse_reply(data)
        except (ValueError, TypeError, RecursionError) as error:
            self.failed.emit(str(error) if isinstance(error, ValueError) else "The chat provider returned an invalid reply.")
        else:
            self.finished.emit(result)
        finally:
            self.buffer.clear()
            reply.deleteLater()

    def cancel(self):
        reply, self.reply = self.reply, None
        self.buffer.clear()
        if reply is not None:
            reply.abort()
            reply.deleteLater()

    def close(self):
        self.cancel()
