from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from core import lrclib
from core.app_info import APP_VERSION


class Cancel:
    def __init__(self):
        self.delays = []
        self.cancelled = False

    def is_set(self):
        return self.cancelled

    def wait(self, delay):
        self.delays.append(delay)
        return self.cancelled


def response(status=200, headers=None, payload=None):
    result = requests.Response()
    result.status_code = status
    result.headers.update(headers or {})
    result._content = b"[]"
    if payload is not None:
        result.json = lambda: payload
    result.close = lambda: None
    return result


def test_503_retries_after_server_delay_then_returns_lyrics(monkeypatch):
    calls = []
    replies = iter([response(503, {"Retry-After": "3"}), response(payload=[{"trackName": "Song"}])])

    def get(*args, **kwargs):
        calls.append(kwargs)
        return next(replies)

    monkeypatch.setattr(lrclib.requests, "get", get)
    cancel = Cancel()
    assert lrclib.search("Song", cancel) == [{"trackName": "Song"}]
    assert cancel.delays == [3]
    assert len(calls) == 2
    assert APP_VERSION in calls[0]["headers"]["User-Agent"]


@pytest.mark.parametrize("status", [429, 500, 502, 503, 504])
def test_transient_failures_stop_after_three_attempts(monkeypatch, status):
    calls = []
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: calls.append(1) or response(status))
    cancel = Cancel()
    with pytest.raises(lrclib.LRCLibError, match=f"HTTP {status}") as error:
        lrclib.search("Song", cancel)
    assert len(calls) == 3
    assert cancel.delays == [1, 2]
    assert error.value.retry_after == 4


def test_long_retry_after_is_returned_to_ui_without_retrying_early(monkeypatch):
    calls = []
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs:
                        calls.append(1) or response(429, {"Retry-After": "120"}))
    cancel = Cancel()
    with pytest.raises(lrclib.LRCLibError) as error:
        lrclib.search("Song", cancel)
    assert error.value.retry_after == 120
    assert calls == [1]
    assert not cancel.delays


def test_cancel_during_backoff_stops_further_requests(monkeypatch):
    cancel = Cancel()
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        cancel.cancelled = True
        return response(503)

    monkeypatch.setattr(lrclib.requests, "get", get)
    assert lrclib.search("Song", cancel) == []
    assert calls == [1]


def test_http_date_retry_after():
    date = datetime.now(timezone.utc) + timedelta(seconds=30)
    assert 28 <= lrclib._retry_after(format_datetime(date)) <= 30


def test_permanent_http_error_is_not_retried(monkeypatch):
    calls = []
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs:
                        calls.append(1) or response(400))
    with pytest.raises(requests.HTTPError):
        lrclib.search("Song", Cancel())
    assert calls == [1]


def test_network_timeouts_are_retried_without_blocking_forever(monkeypatch):
    calls = []

    def get(*args, **kwargs):
        calls.append(1)
        raise requests.Timeout()

    monkeypatch.setattr(lrclib.requests, "get", get)
    with pytest.raises(lrclib.LRCLibError, match="Could not reach LRCLIB"):
        lrclib.search("Song", Cancel())
    assert len(calls) == 3
