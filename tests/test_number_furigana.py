"""Shared Japanese number readings in generation, prefetch and ruby display."""

from collections import OrderedDict
from copy import deepcopy

import pytest

import lyrics.furigana as readings
import lyrics.furigana_widget as renderer
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts


COUNTS = [
    ("3回", "さんかい", "sankai"), ("３回", "さんかい", "sankai"),
    ("1回", "いっかい", "ikkai"), ("1人", "ひとり", "hitori"),
    ("2人", "ふたり", "futari"), ("20歳", "はたち", "hatachi"),
    ("300円", "さんびゃくえん", "sambyakuen"),
]


@pytest.fixture
def japanese_generation(monkeypatch):
    monkeypatch.setattr(readings, "_READING_MODE", "furigana")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_AUTO_LANGUAGE_CONTEXT", "japanese")
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())


@pytest.mark.parametrize("source, kana, romaji", COUNTS)
@pytest.mark.parametrize("tokenization", ["word", "characters"])
def test_number_reading_covers_full_quantity_once(source, kana, romaji, tokenization):
    words = [{"surface": char, "reading": "ニン" if char == "人" else ""} for char in source]
    if tokenization == "word":
        words = [{"surface": source, "reading": ""}]
    tokens = readings._build_segments_from_words(source, words)
    assert [(token["text"], token["reading"]) for token in tokens] == [(source, kana)]
    assert "".join(all_romaji_texts(tokens)) == romaji


def test_other_annotations_and_source_whitespace_are_preserved():
    source = "歌と3回\n20歳の君"
    tokens = [
        {"text": "歌", "reading": "うた", "_romaji_word_start": True},
        {"text": "と3回\n20歳の", "reading": None},
        {"text": "君", "reading": "きみ", "_romaji_word_start": True},
    ]
    original = deepcopy(tokens)
    result = readings._with_number_readings(source, tokens)
    assert "".join(token["text"] for token in result) == source
    assert [(token["text"], token.get("reading")) for token in result if token.get("reading")] == [
        ("歌", "うた"), ("3回", "さんかい"), ("20歳", "はたち"), ("君", "きみ"),
    ]
    assert tokens == original


@pytest.mark.parametrize("source", ["12:30", "2026/11/01", "003", "I love you 3000", "ああ3.14", "ああ100%"])
def test_ambiguous_numbers_receive_no_generated_ruby(source):
    segments = [{"text": source, "reading": None}]
    assert readings._with_number_readings(source, segments) == segments


@pytest.mark.parametrize("unavailable", [False, True])
def test_generation_caches_number_readings_even_without_a_parser(japanese_generation, monkeypatch, unavailable):
    calls = []

    def analyze(text):
        calls.append(text)
        return None if unavailable else [{"surface": char, "reading": ""} for char in text]

    monkeypatch.setattr(readings, "_analyze", analyze)
    updates = []
    segments = readings.build_furigana_segments("1人", progress_callback=updates.append)
    assert [(segment["text"], segment["reading"]) for segment in segments] == [("1人", "ひとり")]
    assert readings.build_furigana_segments("1人") == segments
    assert calls == ["1人"]
    assert updates == [segments]


@pytest.mark.parametrize("parse_mode", ["batch", "line"])
@pytest.mark.parametrize("unavailable", [False, True])
def test_prefetch_preserves_multiline_number_ownership(japanese_generation, monkeypatch, parse_mode, unavailable):
    monkeypatch.setattr(readings, "_ACTIVE_PARSER", "mecab_local")
    monkeypatch.setattr(readings, "_MECAB_PARSE_MODE", parse_mode)
    monkeypatch.setattr(readings, "_analyze", lambda text: None if unavailable else [
        {"surface": char, "reading": ""} for char in text
    ])
    lines = ["3回", "あと1人\n20歳だよ", "123"]
    assert readings.prefetch_furigana_batches(lines) == (3, 3)
    for source in lines:
        assert "".join(token["text"] for token in readings.get_cached_furigana_segments(source)) == source
    assert [token["reading"] for token in readings.get_cached_furigana_segments(lines[1]) if token.get("reading")] == ["ひとり", "はたち"]
    assert not any(token.get("reading") for token in readings.get_cached_furigana_segments("123"))


def test_automatic_reading_opt_out_still_applies(japanese_generation, monkeypatch):
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", False)
    monkeypatch.setattr(readings, "_analyze", lambda text: pytest.fail("automatic generation is disabled"))
    assert readings.build_furigana_segments("3回") == [{"text": "3回", "reading": None}]
    assert readings.prefetch_furigana_batches(["3回"]) == (0, 0)


def test_pinyin_generation_does_not_receive_japanese_number_readings(japanese_generation, monkeypatch):
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    tokens = [{"text": "3", "reading": None}, {"text": "人", "reading": "rén"}]
    monkeypatch.setattr(readings, "build_pinyin_segments", lambda text: tokens)
    assert readings.build_furigana_segments("3人") == tokens


@pytest.mark.parametrize("language", ["japanese", "chinese"])
def test_auto_mode_respects_the_songs_language(japanese_generation, monkeypatch, language):
    monkeypatch.setattr(readings, "_READING_MODE", "auto")
    monkeypatch.setattr(readings, "_AUTO_LANGUAGE_CONTEXT", language)
    monkeypatch.setattr(readings, "find_reading_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(readings, "_analyze", lambda text: None)
    chinese_tokens = [{"text": "3", "reading": None}, {"text": "人", "reading": "rén"}]
    monkeypatch.setattr(readings, "build_pinyin_segments", lambda text: chinese_tokens)
    tokens = readings.build_furigana_segments("3人")
    if language == "japanese":
        assert [(token["text"], token["reading"]) for token in tokens] == [("3人", "さんにん")]
    else:
        assert tokens == chinese_tokens


@pytest.mark.parametrize("source, kana, romaji", COUNTS)
@pytest.mark.parametrize("timed", [False, True])
def test_number_ruby_renders_over_original_digits(qapp, monkeypatch, source, kana, romaji, timed):
    tokens = readings._build_segments_from_words(source, [{"surface": source, "reading": ""}])
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    chunks = [LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)]
    original = deepcopy(chunks)
    row = renderer.FuriganaWidget()
    try:
        row.resize(800, 180)
        if timed:
            row.set_segments(source, chunks, line_end=len(source))
        else:
            row.set_lyric(source)
        pieces = row._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == source
        assert "".join(piece.get("reading") or "" for piece in pieces) == kana
        assert row._has_visible_ruby()
        assert chunks == original
        if timed:
            assert [(piece["start"], piece["end"]) for piece in pieces] == [(i, i + 1) for i in range(len(source))]
        row.set_all_romaji_visible(True)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == romaji
    finally:
        row.deleteLater()


def test_manual_number_ruby_wins_when_reopening_karaoke(qapp, monkeypatch):
    source = "20歳"
    tokens = readings._build_segments_from_words(source, [{"surface": source, "reading": ""}])
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    chunk = LyricSegment(1, source, end=3)
    chunk.ruby = "にじゅっさい"
    chunk.ruby_source = "manual"
    row = renderer.FuriganaWidget()
    try:
        row.set_segments(source, [chunk], line_end=3)
        assert [(piece["text"], piece["reading"]) for piece in row._prepare_segments()] == [(source, "にじゅっさい")]
        row.set_all_romaji_visible(True)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == "nijussai"
    finally:
        row.deleteLater()
