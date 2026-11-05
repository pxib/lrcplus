from threading import Event

import pytest
import requests

from core import synclrc
from lyrics.lrcx_parser import parse_lrcx


RECORD_ID = "a" * 32


def test_search_preserves_word_timing_and_literal_styles(monkeypatch):
    def request(path, params, cancel):
        assert path == "/search"
        assert params == {"q": "Artist Song", "limit": 10}
        return {"results": [{"id": RECORD_ID, "track": "Song", "artist": "Artist", "duration": 12,
                             "lyrics": {"karaoke": "[offset:100]\n[00:01.234]<00:01.334>**Hello** <00:01.834>world<00:02.134>",
                                        "synced": "[00:01.234]Hello world", "plain": "Hello world"}}]}
    monkeypatch.setattr(synclrc, "_request", request)
    record, = synclrc.search("Artist - Song")
    assert record["lyricsFormat"] == "lrcx"
    line, = parse_lrcx(record["syncedLyrics"])
    assert line.start == 1.134
    assert line.text == "**Hello** world"
    assert not line.style_spans
    assert [(seg.start, seg.end) for seg in line.segments] == [(1.234, None), (1.734, 2.034)]


@pytest.mark.parametrize("fields,expected,kind", [
    ({"synced": "[00:01.20][00:03.40]Hello"}, "[00:01.200]Hello\n[00:03.400]Hello", "lrc"),
    ({"plain": "Hello"}, "", "lrc"),
    ({"karaoke": "not timed", "synced": "[00:01.00]Hello"}, "[00:01.000]Hello", "lrc"),
    ({"karaoke": "[00:01.00]**Hello**"}, "[00:01.000]**Hello**", "lrc"),
])
def test_format_fallbacks(monkeypatch, fields, expected, kind):
    monkeypatch.setattr(synclrc, "_request", lambda *args: {"results": [{"id": RECORD_ID, "lyrics": fields}]})
    record, = synclrc.search("Song")
    assert record["syncedLyrics"] == expected
    assert record["lyricsFormat"] == kind


def test_empty_and_instrumental_search_results_are_excluded(monkeypatch):
    monkeypatch.setattr(synclrc, "_request", lambda *args: {"results": [
        {"id": RECORD_ID, "lyrics": {}},
        {"id": RECORD_ID, "instrumental": True, "lyrics": {"plain": "Hello"}},
        {"id": "invalid", "lyrics": {"plain": "Hello"}},
    ]})
    assert synclrc.search("Song") == []


def test_fetch_pins_recording_and_rejects_different_id(monkeypatch):
    def request(path, params, cancel):
        assert path == f"/lyrics/{RECORD_ID}"
        return {"id": "b" * 32, "plain": "Hello"}
    monkeypatch.setattr(synclrc, "_request", request)
    with pytest.raises(synclrc.SyncLRCError, match="different recording"):
        synclrc.fetch_lyrics({"id": RECORD_ID})


@pytest.mark.parametrize("status,payload", [(429, {}), (404, {}), (200, []), (200, None)])
def test_request_errors_close_response(monkeypatch, status, payload):
    class Response:
        status_code = status
        headers = {"Retry-After": "120"}
        closed = False

        def json(self):
            if payload is None:
                raise ValueError("Invalid JSON")
            return payload

        def close(self):
            self.closed = True
    response = Response()
    monkeypatch.setattr(synclrc.requests, "get", lambda *args, **kwargs: response)
    with pytest.raises(synclrc.SyncLRCError) as exc:
        synclrc.search("Song")
    assert response.closed
    if status == 429:
        assert exc.value.retry_after == 120


def test_cancelled_search_does_not_request(monkeypatch):
    def unexpected(*args, **kwargs):
        pytest.fail("Cancelled search made a request")
    monkeypatch.setattr(synclrc.requests, "get", unexpected)
    cancel = Event()
    cancel.set()
    assert synclrc.search("Song", cancel) == []


def test_network_failure(monkeypatch):
    def request(*args, **kwargs):
        raise requests.Timeout()
    monkeypatch.setattr(synclrc.requests, "get", request)
    with pytest.raises(synclrc.SyncLRCError, match="Could not reach"):
        synclrc.search("Song")


def test_search_preserves_empty_rows_and_offset(monkeypatch):
    monkeypatch.setattr(synclrc, "_request", lambda *args: {"results": [{
        "id": RECORD_ID,
        "lyrics": {"synced": "[offset:500]\n[00:01.00]Hello\n[00:02.00]"},
    }]})
    record, = synclrc.search("Song")
    assert record["syncedLyrics"] == "[00:00.500]Hello\n[00:01.500]"
    assert record["lyricsFormat"] == "lrc"
