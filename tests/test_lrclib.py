from datetime import datetime, timedelta, timezone
from email.utils import format_datetime

import pytest
import requests

from core import lrclib
from core.app_info import APP_VERSION
from lyrics.lrcx_parser import parse_lrcx


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


def test_structured_song_search_passes_artist_and_title(monkeypatch):
    calls = []
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs:
                        calls.append(kwargs["params"]) or response())
    assert lrclib.search("Song", Cancel(), artist="Artist") == []
    assert calls == [{"track_name": "Song", "artist_name": "Artist"}]


def test_lyricsfile_keeps_word_boundaries_and_overlapping_line_ends(monkeypatch):
    source = """\
version: '1.0'
metadata: {title: Song, artist: Artist}
lines:
  - text: Yes
    start_ms: 950
    end_ms: 4000
  - text: 'Go now!'
    start_ms: 1001
    end_ms: 3500
    words:
      - {text: 'Go ', start_ms: 1101, end_ms: 1302}
      - {text: now!, start_ms: 1503}
"""
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: response(payload=[{
        "trackName": "Song", "lyricsfile": source, "syncedLyrics": "[00:01]Legacy",
    }]))
    record, = lrclib.search("Song")
    assert record["lyricsFormat"] == "lrcx"
    assert record["hasWordTiming"] is True
    assert record["plainLyrics"] == "Yes\nGo now!"
    line, karaoke = parse_lrcx(record["syncedLyrics"], infer_ends=False)
    assert (line.text, line.start, line.end, line.has_karaoke) == ("Yes", 0.95, 4.0, False)
    assert (karaoke.text, karaoke.start, karaoke.end) == ("Go now!", 1.001, 3.5)
    assert [(word.text, word.start, word.end) for word in karaoke.segments] == [
        ("Go ", 1.101, 1.302), ("now!", 1.503, 3.5),
    ]


def test_lyricsfile_plain_lyrics_keep_line_breaks(monkeypatch):
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: response(payload=[{
        "lyricsfile": "version: '1.0'\nmetadata: {}\nplain: |\n  First line\n\n  Second line\n",
        "plainLyrics": "Legacy plain lyrics",
    }]))
    record, = lrclib.search("Song")
    assert record["plainLyrics"] == "First line\n\nSecond line\n"
    assert not record.get("syncedLyrics")


def test_lyricsfile_keeps_an_instant_word_at_the_line_end(monkeypatch):
    source = """\
version: '1.0'
metadata: {}
lines:
  - text: Wait!
    start_ms: 500
    end_ms: 1200
    words: [{text: Wait!, start_ms: 1200, end_ms: 1200}]
"""
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: response(payload=[{"lyricsfile": source}]))
    record, = lrclib.search("Song")
    line, = parse_lrcx(record["syncedLyrics"], infer_ends=False)
    word, = line.segments
    assert (line.start, line.end, word.start, word.end, word.instant) == (0.5, 1.2, 1.2, 1.2, True)


@pytest.mark.parametrize("lyricsfile", [
    "version: '2.0'\nmetadata: {}\nplain: Future format",
    "version: '1.0'\nmetadata: [",
    "version: '1.0'\nmetadata: {}\nplain: duplicate\nplain: also duplicate",
    "version: '1.0'\nmetadata: {}\nplain: !custom value",
    "version: '1.0'\nmetadata: &loop {recursive: *loop}",
    "version: '1.0'\nmetadata: !!map [invalid]",
    'version: "1.0"\nmetadata: {}\nplain: "\\uD800"',
])
def test_unreadable_lyricsfile_keeps_legacy_lyrics(monkeypatch, lyricsfile):
    record = {"lyricsfile": lyricsfile, "syncedLyrics": "[00:01]Legacy", "plainLyrics": "Legacy"}
    monkeypatch.setattr(lrclib.requests, "get", lambda *args, **kwargs: response(payload=[record]))
    assert lrclib.search("Song") == [record]


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
