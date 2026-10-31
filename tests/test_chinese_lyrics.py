import base64
from threading import Event
from types import SimpleNamespace

import pytest

from core import chinese_lyrics


@pytest.fixture(params=("NetEase Cloud Music", "QQ Music"))
def source(request):
    return request.param


def reply(monkeypatch, payload, *, status=200, headers=None, on_request=None):
    calls, closed = [], []
    def request(url, **kwargs):
        calls.append((url, kwargs))
        if on_request:
            on_request()
        return SimpleNamespace(status_code=status, headers=headers or {},
                               json=lambda: payload, close=lambda: closed.append(True))
    monkeypatch.setattr(chinese_lyrics.requests, "get", request)
    monkeypatch.setattr(chinese_lyrics.requests, "post", request)
    return calls, closed


def song_payload(source):
    if source == "NetEase Cloud Music":
        return {"code": 200, "result": {"songs": [{
            "id": 42, "name": "夜に駆ける", "artists": [{"name": "YOASOBI"}, {"name": "Guest"}],
            "album": {"name": "THE BOOK"},
        }]}}
    return {"code": 0, "data": {"song": {"itemlist": [{"mid": "song42", "name": "夜に駆ける", "singer": "YOASOBI"}]}}}


def lyric_payload(source, text):
    if source == "NetEase Cloud Music":
        return {"code": 200, "lrc": {"lyric": text}}
    return {"code": 0, "lyric": base64.b64encode(text.encode("utf-8")).decode("ascii")}


def record(source):
    return {"source": source, "id": 42 if source == "NetEase Cloud Music" else "song42"}


def test_search_normalizes_unicode_metadata_and_defers_lyrics(monkeypatch, source):
    calls, closed = reply(monkeypatch, song_payload(source))
    results = chinese_lyrics.search(source, "YOASOBI - 夜に駆ける")
    assert len(results) == 1
    assert results[0]["trackName"] == "夜に駆ける"
    assert results[0]["artistName"].startswith("YOASOBI")
    assert results[0]["source"] == source
    assert results[0]["hasSyncedLyrics"] is None
    assert "syncedLyrics" not in results[0]
    assert len(calls) == len(closed) == 1
    url, options = calls[0]
    assert url.startswith("https://")
    params = options["data"] if source == "NetEase Cloud Music" else options["params"]
    assert params["s" if source == "NetEase Cloud Music" else "key"] == "YOASOBI 夜に駆ける"
    assert "Referer" in options["headers"]
    assert "X-Real-IP" not in options["headers"]


@pytest.mark.parametrize("text, synced", [("[00:01.23]夜に駆ける\n[00:03.00]歌詞", True), ("夜に駆ける\n歌詞", False)])
def test_fetch_preserves_lyrics_and_detects_timestamps(monkeypatch, source, text, synced):
    calls, closed = reply(monkeypatch, lyric_payload(source, text))
    result = chinese_lyrics.fetch_lyrics(record(source))
    assert result["syncedLyrics" if synced else "plainLyrics"] == text
    assert result["hasSyncedLyrics"] is synced
    assert calls[0][1]["params"]["id" if source == "NetEase Cloud Music" else "songmid"] == record(source)["id"]
    assert len(closed) == 1


def test_qq_decodes_base64_utf8_and_html_entities(monkeypatch):
    reply(monkeypatch, lyric_payload("QQ Music", "[00:01.00]你好 &amp; 世界 &#39;歌词&#39;"))
    result = chinese_lyrics.fetch_lyrics(record("QQ Music"))
    assert result["syncedLyrics"] == "[00:01.00]你好 & 世界 '歌词'"


def test_empty_search_results(monkeypatch, source):
    payload = {"code": 200, "result": {"songCount": 0}} if source == "NetEase Cloud Music" else {"code": 0, "data": {}}
    reply(monkeypatch, payload)
    assert chinese_lyrics.search(source, "missing song") == []


@pytest.mark.parametrize("status", [403, 429, 503])
def test_http_refusals_are_errors_with_server_cooldown(monkeypatch, source, status):
    calls, closed = reply(monkeypatch, {}, status=status, headers={"Retry-After": "120"})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match=f"HTTP {status}") as error:
        chinese_lyrics.search(source, "Song")
    assert error.value.retry_after == 120
    assert len(closed) == 1


def test_api_refusal_does_not_masquerade_as_no_results(monkeypatch, source):
    reply(monkeypatch, {"code": 403})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="code 403"):
        chinese_lyrics.search(source, "Song")


def test_missing_lyrics_cannot_be_saved(monkeypatch, source):
    reply(monkeypatch, lyric_payload(source, ""))
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="no lyrics available"):
        chinese_lyrics.fetch_lyrics(record(source))


def test_netease_instrumental_flag_does_not_load_placeholder(monkeypatch):
    reply(monkeypatch, {"code": 200, "nolyric": True, "lrc": {"lyric": "[00:00.00]纯音乐"}})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="no lyrics available"):
        chinese_lyrics.fetch_lyrics(record("NetEase Cloud Music"))


def test_qq_invalid_base64_is_an_error(monkeypatch):
    reply(monkeypatch, {"code": 0, "lyric": "!not base64!"})
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="invalid lyrics"):
        chinese_lyrics.fetch_lyrics(record("QQ Music"))


def test_malformed_search_is_an_error(monkeypatch, source):
    payload = {"code": 200, "result": []} if source == "NetEase Cloud Music" else {"code": 0, "data": []}
    reply(monkeypatch, payload)
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="invalid search results"):
        chinese_lyrics.search(source, "Song")


def test_cancel_before_request_does_not_connect(monkeypatch, source):
    calls, closed = reply(monkeypatch, {})
    cancel = Event()
    cancel.set()
    assert chinese_lyrics.search(source, "Song", cancel) == []
    assert chinese_lyrics.fetch_lyrics(record(source), cancel) == record(source)
    assert not calls


def test_cancel_during_request_discards_lyrics(monkeypatch, source):
    cancel = Event()
    calls, closed = reply(monkeypatch, lyric_payload(source, "[00:01.00]Hello"), on_request=cancel.set)
    assert chinese_lyrics.fetch_lyrics(record(source), cancel) == record(source)
    assert len(closed) == 1


def test_qq_example_removes_headers_intro_and_timed_credits(monkeypatch):
    text = """[ti:愛して 愛して 愛して (爱我爱我爱我)]
[ar:初音未来 (初音ミク)]
[al:MIKU-Pack 01 Song Collection "SCHOOL DAYS"]
[by:]
[offset:0]
[00:00.00]愛して 愛して 愛して - 初音未来 (初音ミク)
[00:15.71]词：きくお
[00:18.73]曲：きくお
[00:22.61]はるか遠い遠い昔 巻かれた首輪"""
    reply(monkeypatch, lyric_payload("QQ Music", text))
    result = chinese_lyrics.fetch_lyrics(record("QQ Music"))
    assert result["syncedLyrics"] == "[00:22.61]はるか遠い遠い昔 巻かれた首輪"


def test_netease_example_keeps_sung_line_at_zero(monkeypatch):
    sung = ("[00:00.000]Welcome to the Internet! Have a look around\n"
            "[00:03.827]Anything that brain of yours can think of can be found")
    text = "[00:00.000] 作词 : Bo Burnham\n[00:00.000] 作曲 : Bo Burnham\n" + sung
    reply(monkeypatch, lyric_payload("NetEase Cloud Music", text))
    result = chinese_lyrics.fetch_lyrics(record("NetEase Cloud Music"))
    assert result["syncedLyrics"] == sung


def test_intro_matches_search_metadata_without_header_tags():
    text = "[00:00.00]Song (歌曲) - Artist (别名)\n[00:01.23]First line"
    result = chinese_lyrics.clean_lyrics(text, {"trackName": "Song", "artistName": "Artist"})
    assert result == "[00:01.23]First line"


def test_cleanup_keeps_title_as_lyric_section_markers_and_later_repetitions():
    text = ("[ti:Song]\n[ar:Artist]\n[00:00.000]Song\n[00:01.01][Chorus]\n"
            "[00:02.02]Love: it is all around\n[00:03.03]Song - Artist")
    expected = text.split("[ar:Artist]\n", 1)[1]
    assert chinese_lyrics.clean_lyrics(text, {}) == expected


def test_credit_only_result_is_not_treated_as_lyrics(monkeypatch, source):
    reply(monkeypatch, lyric_payload(source, "[ti:Song]\n[ar:Artist]\n[00:00.000]作曲：Composer"))
    with pytest.raises(chinese_lyrics.LyricsProviderError, match="no lyrics available"):
        chinese_lyrics.fetch_lyrics(record(source))
