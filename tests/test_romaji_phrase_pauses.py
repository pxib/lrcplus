"""Authored Japanese spaces are pauses; generated word spaces are not."""

from copy import deepcopy

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import _build_segments_from_words
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts


def phrase_tokens(space):
    source = f"ああ{space}何も無い"
    words = [
        {"surface": "ああ", "reading": "アア"},
        {"surface": "何", "reading": "ナン"},
        {"surface": "も", "reading": "モ"},
        {"surface": "無い", "reading": "ナイ"},
    ]
    return source, _build_segments_from_words(source, words)


@pytest.mark.parametrize("space", [" ", "\u3000", "   ", " \u3000  "])
def test_authored_phrase_gap_becomes_one_comma(space):
    source, tokens = phrase_tokens(space)
    original = deepcopy(tokens)
    displayed = all_romaji_texts(tokens)
    assert "".join(displayed) == "aa, nani mo nai"
    assert displayed[1] == ","
    assert tokens == original
    assert "".join(token["text"] for token in tokens) == source


@pytest.mark.parametrize("source, expected", [
    ("ああ ああ", "aa, aa"),
    ("ああ\u3000ああ", "aa, aa"),
    ("ああ、 ああ", "aa, aa"),
    ("ああ 、ああ", "aa, aa"),
    ("ああ, ああ", "aa, aa"),
    ("ああ！ ああ", "aa! aa"),
    ("ああ… ああ", "aa... aa"),
    (" ああ ", "aa"),
    ("\u3000ああ\u3000", "aa"),
    ("ああ \n ああ", "aa\naa"),
    ("I love you", "I love you"),
    ("I\u3000love\u3000you", "I love you"),
    ("ああ hello", "aa, hello"),
    ("hello ああ", "hello, aa"),
    ("「 ああ 」って", '"aa" tte'),
    ("（ ああ ）", "(aa)"),
    ('" ああ "って', '"aa" tte'),
])
@pytest.mark.parametrize("character_chunks", [False, True])
def test_pause_spacing_respects_punctuation_and_source_context(source, expected, character_chunks):
    chunks = list(source) if character_chunks else [source]
    assert "".join(all_romaji_texts([{"text": text} for text in chunks])) == expected


def test_grammatical_spaces_are_not_phrase_pauses():
    _, tokens = phrase_tokens("")
    assert "".join(all_romaji_texts(tokens)) == "aa nani mo nai"


@pytest.mark.parametrize("source_key", ["ruby_source", "reading_source"])
def test_explicit_nani_reading_edits_are_preserved(source_key):
    _, tokens = phrase_tokens(" ")
    tokens[2][source_key] = "manual"
    assert "".join(all_romaji_texts(tokens)) == "aa, nan mo nai"


def test_live_reading_edits_remain_authoritative(qapp, monkeypatch):
    source, tokens = phrase_tokens(" ")
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    row = renderer.FuriganaWidget(manual_overrides={source: {2: "なん"}})
    try:
        row.set_all_romaji_visible(True)
        row.set_lyric(source)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == "aa, nan mo nai"
    finally:
        row.deleteLater()


@pytest.mark.parametrize("space", [" ", "\u3000", "  \u3000 "])
@pytest.mark.parametrize("timed", [False, True])
def test_pause_rendering_preserves_lyrics_and_timestamps(qapp, monkeypatch, space, timed):
    source, tokens = phrase_tokens(space)
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    chunks = [LyricSegment(index, char, end=index + 1) for index, char in enumerate(source)]
    original = deepcopy(chunks)
    row = renderer.FuriganaWidget()
    try:
        row.resize(1000, 180)
        row.set_all_romaji_visible(True)
        if timed:
            row.set_segments(source, chunks, line_end=len(source))
        else:
            row.set_lyric(source)
        pieces = row._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == "aa, nani mo nai"
        assert row.lyric == source
        assert chunks == original
        if timed:
            assert [(piece["start"], piece["end"]) for piece in pieces] == [(index, index + 1) for index in range(len(source))]
            assert next(piece for piece in pieces if "," in piece["text"])["start"] == 2
        row.set_all_romaji_visible(False)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == source
    finally:
        row.deleteLater()
