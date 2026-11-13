"""Hepburn consonants with explicit vowel kana preserved for singing."""

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import kana_to_romaji, kana_to_romaji_parts
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts, romanized_readings


EXAMPLES = [
    ("きっと", "kitto"), ("こっち", "kotchi"), ("っちゃう", "tchau"),
    ("ぽっちゅ", "potchu"), ("ぱっが", "pagga"),
    ("はんこ", "hanko"), ("さんばし", "sambashi"),
    ("あんぱん", "ampan"), ("あんまん", "amman"),
    ("くうき", "kuuki"), ("おおきい", "ookii"), ("ほうき", "houki"),
    ("いのうえ", "inoue"), ("おうえん", "ouen"),
    ("かのう", "kanou"), ("のうか", "nouka"),
    ("そうだん", "soudan"), ("しんよう", "shin'you"),
    ("ヒーロー", "hiiroo"), ("クーラー", "kuuraa"),
]


@pytest.mark.parametrize("source, expected", EXAMPLES)
def test_requested_spellings_across_every_timing_partition(source, expected):
    assert kana_to_romaji(source) == expected
    for mask in range(1 << (len(source) - 1)):
        boundaries = [0] + [i for i in range(1, len(source)) if mask & (1 << (i - 1))] + [len(source)]
        parts = [source[a:b] for a, b in zip(boundaries, boundaries[1:])]
        assert "".join(kana_to_romaji_parts(parts)) == expected, parts
        assert "".join(all_romaji_texts([{"text": part} for part in parts])) == expected, parts


@pytest.mark.parametrize("source", ["souvenir", "cool", "vacuum", "inoue", "cchau", "pagga", "room uu oo ou"])
def test_authored_latin_text_is_not_rewritten(source):
    assert kana_to_romaji(source) == source


def test_word_spaces_and_punctuation_end_conversion_context():
    assert kana_to_romaji("く う、お う、さん ば") == "ku u、o u、san ba"
    assert "".join(all_romaji_texts([
        {"text": "お", "_romaji_word_start": True},
        {"text": "う", "_romaji_word_start": True},
        {"text": "さん", "_romaji_word_start": True},
        {"text": "ば", "_romaji_word_start": True},
    ])) == "o u san ba"


@pytest.mark.parametrize("source, reading, expected", [
    ("抹茶", "まっちゃ", "matcha"), ("桟橋", "さんばし", "sambashi"),
    ("空気", "くうき", "kuuki"), ("大きい", "おおきい", "ookii"),
    ("井上", "いのうえ", "inoue"), ("3本", "さんぼん", "sambon"),
    ("20人", "にじゅうにん", "nijuunin"),
])
@pytest.mark.parametrize("timed", [False, True])
def test_romaji_annotations_share_the_rules_across_karaoke_boundaries(qapp, monkeypatch, source, reading, expected, timed):
    tokens = [{"text": source, "reading": reading, "_romaji_word_start": True}]
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    row = renderer.FuriganaWidget()
    try:
        row.resize(1000, 180)
        row.set_romaji_visible(True)
        if timed:
            row.set_segments(source, [LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)], len(source))
        else:
            row.set_lyric(source)
        pieces = row._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == source
        assert "".join(piece["_display_reading"] for piece in pieces) == expected
        assert "".join(piece.get("reading") or "" for piece in pieces) == reading
        if timed:
            assert len(pieces) == 1  # Generated ruby ignores karaoke separation.
            assert [(span["start"], span["end"]) for span in pieces[0]["_timing_spans"]] == [
                (i, i + 1) for i in range(len(source))]
        row.set_ruby_visible(True)
        assert "".join(piece["_display_reading"] for piece in row._prepare_segments()) == reading
    finally:
        row.deleteLater()


def test_independent_ruby_words_do_not_share_conversion_context():
    segments = [
        {"reading": "こう", "_reading_group": (0, 1)},
        {"reading": "う", "_reading_group": (1, 2)},
        {"reading": "さん", "_reading_group": (2, 3)},
        {"reading": "ば", "_reading_group": (3, 4)},
    ]
    assert romanized_readings(segments) == ["kou", "u", "san", "ba"]


def test_each_sung_vowel_keeps_its_own_timing_piece(qapp, monkeypatch):
    source = "いのうえ"
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: [
        {"text": source, "reading": None, "_romaji_word_start": True},
    ])
    assert kana_to_romaji_parts(list(source)) == ["i", "no", "u", "e"]
    row = renderer.FuriganaWidget()
    try:
        row.set_all_romaji_visible(True)
        chunks = [LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)]
        row.set_segments(source, chunks, len(source))
        pieces = row._prepare_segments()
        assert [piece["text"] for piece in pieces] == ["i", "no", "u", "e"]
        assert [(piece["start"], piece["end"]) for piece in pieces] == [(0, 1), (1, 2), (2, 3), (3, 4)]
    finally:
        row.deleteLater()
