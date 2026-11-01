"""Japanese number readings must preserve lyric text and timed chunk ownership."""

from copy import deepcopy

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import _build_segments_from_words
from lyrics.japanese_numbers import integer_reading
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts


COUNTS = [
    ("3回", "sankai"), ("３回", "sankai"), ("1回", "ikkai"),
    ("6回", "rokkai"), ("8回", "hakkai"), ("10回", "jukkai"),
    ("1人", "hitori"), ("2人", "futari"), ("3人", "sannin"),
    ("4人", "yonin"), ("14人", "juuyonin"), ("21人", "nijuuichinin"),
    ("20歳", "hatachi"), ("２０才", "hatachi"), ("1歳", "issai"),
    ("8歳", "hassai"), ("10歳", "jussai"), ("21歳", "nijuuissai"),
    ("1個", "ikko"), ("6個", "rokko"), ("8個", "hakko"),
    ("3枚", "sammai"), ("2台", "nidai"), ("300円", "sambyakuen"),
    ("600円", "roppyakuen"), ("800円", "happyakuen"), ("3000円", "sanzen'en"),
    ("8000円", "hassen'en"), ("10000円", "ichiman'en"),
    ("1本", "ippon"), ("3本", "sambon"), ("4本", "yonhon"),
    ("6本", "roppon"), ("100本", "hyappon"), ("1000本", "sembon"),
    ("1匹", "ippiki"), ("3匹", "sambiki"), ("6匹", "roppiki"),
    ("1冊", "issatsu"), ("8冊", "hassatsu"), ("10冊", "jussatsu"),
    ("2つ", "futatsu"), ("3つ", "mittsu"), ("10つ", "too"),
    ("1番", "ichiban"), ("1000000回", "hyakumankai"),
]


@pytest.mark.parametrize("source, expected", COUNTS)
@pytest.mark.parametrize("character_chunks", [False, True])
def test_whole_count_readings_across_timed_chunks(source, expected, character_chunks):
    chunks = list(source) if character_chunks else [source]
    segments = [{"text": part, "_romaji_word_start": True} for part in chunks]
    original = deepcopy(segments)
    assert "".join(all_romaji_texts(segments)) == expected
    assert segments == original


@pytest.mark.parametrize("source, expected", [
    ("ああ3", "aasan"), ("あと300", "atosambyaku"),
    ("123", "123"), ("12345", "12345"), ("I love you 3000", "I love you 3000"),
    ("ああ003", "aa003"), ("ああ12345", "aa12345"),
    ("ああ3.14", "aa3.14"), ("ああ1,000", "aa1,000"),
    ("ああ12:30", "aa12:30"), ("ああ2026/11/01", "aa2026/11/01"),
    ("ああ2026-11-01", "aa2026-11-01"), ("ああ3月", "aa3月"),
    ("ああR2D2", "aaR2D2"), ("ああ3D", "aa3D"),
    ("ああ-3", "aa-3"), ("ああ−3", "aa−3"),
    ("ああ100%", "aa100%"), ("ああ1_000", "aa1_000"),
    ("ああ3\n123", "aasan\n123"),
])
@pytest.mark.parametrize("character_chunks", [False, True])
def test_ambiguous_numbers_and_english_remain_numeric(source, expected, character_chunks):
    chunks = list(source) if character_chunks else [source]
    segments = [{"text": part} for part in chunks]
    assert "".join(all_romaji_texts(segments)) == expected


@pytest.mark.parametrize("source", ["3.14", "1,000", "12:30", "2026/11/01", "2026-11-01", "R2D2", "-3", "100%", "1_000"])
def test_tokenizer_boundaries_do_not_break_numeric_literals(source):
    segments = [{"text": char, "_romaji_word_start": True} for char in source]
    assert "".join(all_romaji_texts(segments)) == source


def test_number_reading_in_a_tokenized_japanese_line():
    source = "あと3回だけ"
    tokens = _build_segments_from_words(source, [
        {"surface": "あと", "reading": "アト"},
        {"surface": "3", "reading": ""},
        {"surface": "回", "reading": "カイ"},
        {"surface": "だけ", "reading": "ダケ"},
    ])
    assert "".join(all_romaji_texts(tokens)) == "ato sankai dake"


@pytest.mark.parametrize("source, expected", [("3回", "sankai"), ("1人", "hitori"), ("20歳", "hatachi")])
@pytest.mark.parametrize("timed", [False, True])
def test_number_rendering_preserves_lyrics_and_timestamps(qapp, monkeypatch, source, expected, timed):
    tokens = [{"text": char, "reading": None, "_romaji_word_start": True} for char in source]
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    chunks = [LyricSegment(index, char, end=index + 1) for index, char in enumerate(source)]
    original = deepcopy(chunks)
    row = renderer.FuriganaWidget()
    try:
        row.resize(800, 180)
        row.set_all_romaji_visible(True)
        if timed:
            row.set_segments(source, chunks, line_end=len(source))
        else:
            row.set_lyric(source)
        pieces = row._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == expected
        assert chunks == original
        assert row.lyric == source
        if timed:
            assert [(piece["start"], piece["end"]) for piece in pieces] == [(i, i + 1) for i in range(len(source))]
        row.set_all_romaji_visible(False)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == source
    finally:
        row.deleteLater()


def test_manual_number_readings_are_authoritative():
    segments = [{"text": "20歳", "reading": "にじゅっさい", "ruby_source": "manual"}]
    assert "".join(all_romaji_texts(segments)) == "nijussai"
    segments = [{"text": "123", "reading": "いちにさん", "ruby_source": "manual"}]
    assert "".join(all_romaji_texts(segments)) == "ichinisan"


def test_integer_reading_handles_four_digit_groups_and_limits():
    assert integer_reading(100010001) == "いちおくいちまんいち"
    assert integer_reading(10 ** 12) == "いちちょう"
    assert integer_reading(-1) is None
    assert integer_reading(10 ** 16) is None
