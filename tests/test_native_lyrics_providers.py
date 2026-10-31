import base64
from threading import Event
from types import SimpleNamespace
import zlib

import pytest
import requests

from core import chinese_lyrics
from lyrics.loader import load_lyrics


def encoded_krc(text):
    key = b"@Gaw^2tGQ61-\xce\xd2ni"
    raw = zlib.compress(text.encode())
    return base64.b64encode(b"krc1" + bytes(value ^ key[i % 16] for i, value in enumerate(raw))).decode()


def replies(monkeypatch, *payloads):
    calls, closed = [], []
    queue = iter(payloads)
    def request(url, **kwargs):
        calls.append((url, kwargs))
        payload = next(queue)
        if isinstance(payload, int):
            return SimpleNamespace(status_code=payload, headers={}, close=lambda: closed.append(True))
        return SimpleNamespace(status_code=200, headers={}, json=lambda: payload, close=lambda: closed.append(True))
    monkeypatch.setattr(chinese_lyrics.requests, "get", request)
    monkeypatch.setattr(chinese_lyrics.requests, "post", request)
    return calls, closed


def test_netease_prefers_yrc_and_filters_credits_before_word_markup(monkeypatch, tmp_path):
    payload = {"code": 200, "yrc": {"lyric": (
        '{"t":0,"c":[{"tx":"作曲：Name"}]}\n'
        '[0,1000](0,200,0)作(200,200,0)词：(400,600,0)Name\n'
        '[1000,2000](1100,250,0)君(1600,300,0)の'
    )}, "lrc": {"lyric": "[00:01]Fallback"}}
    calls, closed = replies(monkeypatch, payload)
    result = chinese_lyrics.fetch_lyrics({"id": 42, "source": "NetEase Cloud Music"})
    assert result["lyricsFormat"] == "lrcx"
    assert result["nativeFormat"] == "YRC"
    assert result["timing"] == "word"
    assert "Name" not in result["syncedLyrics"]
    assert "Fallback" not in result["syncedLyrics"]
    path = tmp_path / "song.lrcx"
    path.write_text(result["syncedLyrics"], encoding="utf-8")
    assert load_lyrics(path)[0].text == "君の"
    assert calls[0][0].endswith("/lyric/v1")
    assert calls[0][1]["params"]["yv"] == -1
    assert len(calls) == len(closed) == 1


@pytest.mark.parametrize("yrc", [None, {}, {"lyric": ""}, {"lyric": "broken"}, {"lyric": "[1000,500](500,500,0)invalid"}, {"lyric": "[1000,500](1000,500,0)作曲：Name"}])
def test_netease_falls_back_to_lrc_without_an_extra_request(monkeypatch, yrc):
    calls, closed = replies(monkeypatch, {"code": 200, "yrc": yrc, "lrc": {"lyric": "[00:01.234]Song"}})
    result = chinese_lyrics.fetch_lyrics({"id": 42, "source": "NetEase Cloud Music"})
    assert result["lyricsFormat"] == "lrc"
    assert result["syncedLyrics"] == "[00:01.234]Song"
    assert len(calls) == 1


@pytest.mark.parametrize("failure", [404, {"code": 404}])
def test_netease_legacy_fallback_when_v1_is_unavailable(monkeypatch, failure):
    calls, closed = replies(monkeypatch, failure, {"code": 200, "lrc": {"lyric": "[00:01]Song"}})
    result = chinese_lyrics.fetch_lyrics({"id": 42, "source": "NetEase Cloud Music"})
    assert result["lyricsFormat"] == "lrc"
    assert len(calls) == len(closed) == 2
    assert calls[-1][0].endswith("/song/lyric")


def test_netease_rate_limit_does_not_start_fallback_request(monkeypatch):
    calls, closed = replies(monkeypatch, 429)
    with pytest.raises(chinese_lyrics.LyricsProviderError) as error:
        chinese_lyrics.fetch_lyrics({"id": 42, "source": "NetEase Cloud Music"})
    assert error.value.retry_after == 60
    assert len(calls) == 1


def test_kugou_search_uses_returned_candidate_key_and_preserves_duration(monkeypatch):
    calls, closed = replies(monkeypatch,
        {"status": 200, "candidates": [{"id": "42", "accesskey": "candidate-key", "song": "Song", "singer": "Artist", "duration": 247510}]},
        {"status": 200, "content": encoded_krc("[ti:Song]\n[ar:Artist]\n[0,500]<0,500,0>Song - Artist\n[1000,500]<0,500,0>歌")},
    )
    records = chinese_lyrics.search("KuGou", "Artist - Song")
    assert records[0]["duration"] == 247.51
    assert "candidate-key" not in records[0]["sourceUrl"]
    result = chinese_lyrics.fetch_lyrics(records[0])
    assert result["lyricsFormat"] == "lrcx"
    assert result["nativeFormat"] == "KRC"
    assert "Song - Artist" not in result["syncedLyrics"]
    assert "candidate-key" not in result["syncedLyrics"]
    assert calls[1][1]["params"]["accesskey"] == "candidate-key"
    assert len(closed) == 2


def test_kugou_rejects_invalid_krc(monkeypatch):
    replies(monkeypatch, {"status": 200, "content": "not a KRC file"})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="invalid or oversized"):
        chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "42", "accessKey": "key"})


def test_candidate_keys_are_redacted_from_network_errors(monkeypatch):
    def request(*args, **kwargs):
        raise requests.ConnectionError("https://lyrics.kugou.com/download?accesskey=private-key")
    monkeypatch.setattr(chinese_lyrics.requests, "get", request)
    with pytest.raises(chinese_lyrics.LyricsProviderError) as error:
        chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "42", "accessKey": "private-key"})
    assert "private-key" not in str(error.value)


def test_kuwo_song_ids_decimal_timestamps_and_duplicate_rows(monkeypatch):
    calls, closed = replies(monkeypatch,
        {"TOTAL": "1", "abslist": [{"MUSICRID": "MUSIC_42", "NAME": "Song", "ARTIST": "Artist", "ALBUM": "Album", "DURATION": "123"}]},
        {"status": 200, "data": {"lrclist": [
            {"time": "0", "lineLyric": "作词：Name"},
            {"time": "3.827", "lineLyric": "Later"},
            {"time": "1.234", "lineLyric": "歌詞"},
            {"time": "1.234", "lineLyric": "Translated line"},
        ]}},
    )
    records = chinese_lyrics.search("Kuwo", "Artist - Song")
    assert records[0]["id"] == "42"
    assert records[0]["albumName"] == "Album"
    assert records[0]["duration"] == 123
    result = chinese_lyrics.fetch_lyrics(records[0])
    assert result["lyricsFormat"] == "lrc"
    assert result["syncedLyrics"] == "[00:01.234]歌詞\n[00:01.234]Translated line\n[00:03.827]Later"
    assert calls[-1][1]["params"] == {"musicId": "42"}


@pytest.mark.parametrize("value", ["NaN", "Infinity", "-0.1", "1e10000000", "not a time"])
def test_kuwo_rejects_invalid_timestamps(monkeypatch, value):
    replies(monkeypatch, {"status": 200, "data": {"lrclist": [{"time": value, "lineLyric": "Song"}]}})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="invalid lyric timestamps"):
        chinese_lyrics.fetch_lyrics({"source": "Kuwo", "id": "42"})


@pytest.mark.parametrize("source, payload", [("KuGou", {"status": 200, "candidates": []}), ("Kuwo", {"TOTAL": "0", "abslist": []})])
def test_new_sources_empty_results(monkeypatch, source, payload):
    replies(monkeypatch, payload)
    assert chinese_lyrics.search(source, "missing song") == []


@pytest.mark.parametrize("source", ["KuGou", "Kuwo"])
def test_cancel_before_new_source_request(monkeypatch, source):
    calls, closed = replies(monkeypatch)
    cancel = Event()
    cancel.set()
    assert chinese_lyrics.search(source, "Song", cancel) == []
    record = {"source": source, "id": "42"}
    assert chinese_lyrics.fetch_lyrics(record, cancel) == record
    assert not calls
