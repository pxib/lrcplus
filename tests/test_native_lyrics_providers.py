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


@pytest.mark.parametrize("separator", [" - ", " \u2013 ", " \u2014 "])
def test_kugou_search_uses_returned_candidate_key_and_preserves_duration(monkeypatch, separator):
    calls, closed = replies(monkeypatch,
        {"status": 200, "candidates": [{"id": "42", "accesskey": "candidate-key", "song": "Song", "singer": "Artist", "duration": 247510}]},
        {"status": 200, "content": encoded_krc("[ti:Song]\n[ar:Artist]\n[0,500]<0,500,0>Song - Artist\n[1000,500]<0,500,0>歌")},
    )
    records = chinese_lyrics.search("KuGou", f"Artist{separator}Song")
    assert calls[0][1]["params"]["keyword"] == "Artist-Song"
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


@pytest.mark.parametrize("query", ["\u3042", "\u4e16", "\u7b11"])
def test_kugou_partial_search_uses_song_catalog_and_resolves_lyrics_lazily(monkeypatch, query):
    song_hash = "abcdef0123456789abcdef0123456789"
    calls, closed = replies(monkeypatch,
        {"status": 200, "candidates": []},
        {"status": 1, "data": {"info": [{
            "hash": song_hash, "songname": query, "singername": "Artist", "album_name": "Album", "duration": 123,
        }]}},
        {"status": 200, "candidates": [{"id": "42", "accesskey": "candidate-key"}]},
        {"status": 200, "content": encoded_krc("[1000,500]<0,500,0>Lyrics")},
    )
    records = chinese_lyrics.search("KuGou", query)
    assert len(records) == 1
    record = records[0]
    assert record["trackName"] == query
    assert record["albumName"] == "Album"
    assert record["duration"] == 123
    assert record["hasSyncedLyrics"] is None
    assert "accessKey" not in record
    assert len(calls) == 2  # No lyric downloads until previewing a song.
    assert calls[1][0] == "https://mobileservice.kugou.com/api/v3/search/song"
    assert calls[1][1]["params"]["keyword"] == query
    result = chinese_lyrics.fetch_lyrics(record)
    assert result["lyricsFormat"] == "lrcx"
    assert result["trackName"] == query
    assert result["albumName"] == "Album"
    assert result["id"] == song_hash
    assert calls[2][1]["params"]["keyword"] == f"Artist-{query}"
    assert calls[2][1]["params"]["duration"] == 123000
    assert calls[2][1]["params"]["hash"] == song_hash
    assert calls[3][1]["params"]["id"] == "42"
    assert calls[3][1]["params"]["accesskey"] == "candidate-key"
    assert len(calls) == len(closed) == 4


def test_kugou_song_without_lyrics_does_not_attempt_download(monkeypatch):
    calls, closed = replies(monkeypatch, {"status": 200, "candidates": []})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="no lyrics available"):
        chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "a" * 32, "songHash": "a" * 32,
                                    "trackName": "Song", "artistName": "Artist", "duration": 123})
    assert len(calls) == len(closed) == 1


@pytest.mark.parametrize("failure", [429, {"status": 429}])
def test_kugou_refusal_does_not_start_catalog_fallback(monkeypatch, failure):
    calls, closed = replies(monkeypatch, failure)
    with pytest.raises(chinese_lyrics.LyricsProviderError) as error:
        chinese_lyrics.search("KuGou", "Song")
    assert error.value.retry_after == 60
    assert len(calls) == len(closed) == 1


def test_kugou_cancel_after_lyric_search_does_not_start_catalog_fallback(monkeypatch):
    cancel = Event()
    calls = []
    def request(url, **kwargs):
        calls.append(url)
        cancel.set()
        return SimpleNamespace(status_code=200, headers={},
                               json=lambda: {"status": 200, "candidates": []}, close=lambda: None)
    monkeypatch.setattr(chinese_lyrics.requests, "get", request)
    assert chinese_lyrics.search("KuGou", "Song", cancel) == []
    assert len(calls) == 1


@pytest.mark.parametrize("data", [None, [], {}, {"info": None}, {"info": [None]}, {"info": [{"hash": "invalid"}]}])
def test_kugou_malformed_catalog_is_an_error(monkeypatch, data):
    replies(monkeypatch, {"status": 200, "candidates": []}, {"status": 1, "data": data})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="invalid search results"):
        chinese_lyrics.search("KuGou", "Song")


def test_kugou_catalog_api_failure_is_not_empty_results(monkeypatch):
    replies(monkeypatch, {"status": 200, "candidates": []}, {"status": 0, "errcode": 500})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="code 0"):
        chinese_lyrics.search("KuGou", "Song")


def test_kugou_malformed_word_timing_falls_back_to_line_lyrics(monkeypatch):
    calls, closed = replies(monkeypatch,
        {"status": 200, "content": encoded_krc("[1000,1000]<0,100,0>A<50,100,0>B<20,100,0>C")},
        {"status": 200, "content": base64.b64encode(b"[00:01.000]ABC").decode()},
    )
    result = chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "42", "accessKey": "key"})
    assert result["lyricsFormat"] == "lrc"
    assert result["syncedLyrics"] == "[00:01.000]ABC"
    assert calls[0][1]["params"]["fmt"] == "krc"
    assert calls[1][1]["params"] == {**calls[0][1]["params"], "fmt": "lrc"}
    assert len(calls) == len(closed) == 2


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
    replies(monkeypatch, payload, *([{"status": 1, "data": {"info": []}}] if source == "KuGou" else []))
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


def test_kuwo_301_uses_alternate_endpoint_with_code_envelope(monkeypatch):
    calls, closed = replies(monkeypatch,
        {"status": 301, "msg": "Unavailable"},
        {"code": 200, "data": {"lrclist": [{"time": "1.234", "lineLyric": "Song"}]}},
    )
    result = chinese_lyrics.fetch_lyrics({"source": "Kuwo", "id": "411629241"})
    assert result["syncedLyrics"] == "[00:01.234]Song"
    assert calls[0][0].startswith("https://m.kuwo.cn/")
    assert calls[1][0] == "https://www.kuwo.cn/openapi/v1/www/lyric/getlyric"
    assert all(options["params"] == {"musicId": "411629241"} for _, options in calls)
    assert len(closed) == 2


def test_kuwo_repeated_301_is_a_clear_error_without_more_requests(monkeypatch):
    calls, closed = replies(monkeypatch, {"status": 301}, {"code": 301})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="Try another result or provider"):
        chinese_lyrics.fetch_lyrics({"source": "Kuwo", "id": "42"})
    assert len(calls) == len(closed) == 2


def test_kuwo_rate_limit_does_not_attempt_alternate_endpoint(monkeypatch):
    calls, closed = replies(monkeypatch, 429)
    with pytest.raises(chinese_lyrics.LyricsProviderError) as error:
        chinese_lyrics.fetch_lyrics({"source": "Kuwo", "id": "42"})
    assert error.value.retry_after == 60
    assert len(calls) == 1
