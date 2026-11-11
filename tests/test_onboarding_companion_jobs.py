import json

from PySide6.QtCore import QObject, Signal
from PySide6.QtNetwork import QNetworkRequest

from core.companion_job import CompanionJob


class Reply(QObject):
    finished = Signal()
    readyRead = Signal()

    def __init__(self, content=b"", status=200):
        super().__init__()
        self.content, self.status, self.aborted = content, status, False

    def setReadBufferSize(self, size):
        pass

    def readAll(self):
        content, self.content = self.content, b""
        return content

    def attribute(self, attribute):
        return self.status

    def abort(self):
        self.aborted = True
        self.finished.emit()


def test_stop_aborts_local_transport_and_ignores_stale_completion(qapp):
    job = CompanionJob()
    old = Reply()
    current = Reply(json.dumps({"choices": [{"message": {"content": "Current"}}]}).encode())
    replies = [old, current]
    queries = []

    class Network:
        def post(self, query, body):
            queries.append((query, json.loads(bytes(body))))
            return replies.pop(0)

    job.network = Network()
    completed, failures = [], []
    job.finished.connect(completed.append)
    job.failed.connect(failures.append)
    args = (None, "https://example.com/v1", "model", "key", [{"role": "user", "content": "Hi"}])
    job.start(*args)
    job.cancel()
    assert old.aborted
    job.start(*args)
    old.finished.emit()
    assert completed == [] and failures == []
    current.finished.emit()
    assert [reply.text for reply in completed] == ["Current"]
    assert queries[0][0].attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    job.close()


def test_oversized_reply_is_aborted(qapp):
    job = CompanionJob()
    reply = Reply(b"x" * (1024 * 1024 + 1))

    class Network:
        def post(self, *args):
            return reply

    job.network = Network()
    failures = []
    job.failed.connect(failures.append)
    job.start(None, "https://example.com/v1", "model", "key", [])
    reply.readyRead.emit()
    assert reply.aborted
    assert failures == ["The chat provider returned an oversized response."]
