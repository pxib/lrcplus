"""Large results remain usable even when they cannot be retained in a cache."""

from core.idle_cache import IdleCache
from core.library_lyrics import LibraryLyricsIndex
import lyrics.furigana as readings
import pytest


def test_oversized_lyric_search_text_remains_searchable(tmp_path):
    path = tmp_path / "song.lrc"
    path.write_text("[00:01]" + "long lyric " * 100, encoding="utf-8")
    index = LibraryLyricsIndex()
    index._texts = IdleCache(5000, max_bytes=1000)
    first = index._text(path)
    assert "long lyric" in first[0] and "long lyric" in first[1]
    assert not index._texts
    assert index._text(path) == first


def test_uncached_readings_are_returned_and_translation_rows_keep_original_text(monkeypatch):
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", IdleCache(max_bytes=0))
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "build_pinyin_segments",
                        lambda text: [{"text": text, "reading": "generated"}])
    progress = []
    assert readings.build_furigana_segments("source", progress_callback=progress.append) == [
        {"text": "source", "reading": "generated"},
    ]
    assert progress == [[{"text": "source", "reading": "generated"}]]
    assert readings.build_furigana_segments(r"source{\nt}translation") == [
        {"text": "source", "reading": "generated"},
        {"text": "\n", "reading": None},
        {"text": "translation", "reading": None},
    ]
    assert not readings._SEGMENT_CACHE


@pytest.mark.parametrize("max_bytes", [0, 4096])
@pytest.mark.parametrize("iterator", [False, True])
def test_reading_results_are_independent_of_provider_data_and_cached_results(monkeypatch, max_bytes, iterator):
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", IdleCache(max_bytes=max_bytes))
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    parts = [{"text": "source", "reading": "generated"}]
    monkeypatch.setattr(readings, "build_pinyin_segments",
                        lambda text: iter(parts) if iterator else parts)
    result = readings.build_furigana_segments("source")
    assert result == parts
    result[0]["reading"] = "edited"
    assert parts == [{"text": "source", "reading": "generated"}]
    assert readings.build_furigana_segments("source") == parts


def test_uncached_multiline_results_preserve_dictionary_overrides(monkeypatch):
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "build_pinyin_segments",
                        lambda text: [{"text": text, "reading": "generated"}])
    monkeypatch.setattr(readings, "has_dictionary_entries", lambda: True)
    monkeypatch.setattr(readings, "dictionary_matches",
                        lambda text, mode, **kwargs: [(0, len(text), "custom")] if text == "source" else [])
    results = []
    for budget in (4096, 0):
        monkeypatch.setattr(readings, "_SEGMENT_CACHE", IdleCache(max_bytes=budget))
        results.append(readings.build_furigana_segments(r"source{\nt}translation"))
    assert results[0] == results[1]
    assert results[1][0]["reading"] == "custom"
    assert results[1][-1] == {"text": "translation", "reading": None}


def test_oversized_yomi_result_is_returned_without_retries_or_cache_retention(monkeypatch):
    from types import SimpleNamespace

    monkeypatch.setattr(readings, "_YOMI_CACHE", IdleCache(max_bytes=0))
    words = [{"surface": "source", "reading": "generated"}]
    calls = []

    def post(*args, **kwargs):
        calls.append(args)
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"words": words})

    monkeypatch.setattr(readings.requests, "post", post)
    assert readings.yomi_analyze("source") == words
    assert len(calls) == 1
    assert not readings._YOMI_CACHE
