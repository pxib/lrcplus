from types import SimpleNamespace

from lyrics.furigana import _split_segments_by_lines
from lyrics.widgets import FuriganaWidget


def test_batch_line_splitting_preserves_romaji_word_starts():
    segments = [
        {"text": "本当", "reading": "ほんとう", "_romaji_word_start": True},
        {"text": "は", "reading": "は", "_romaji_word_start": True},
        {"text": "\n", "reading": None},
        {"text": "僕も", "reading": "ぼくも", "_romaji_word_start": True},
    ]

    lines = _split_segments_by_lines(segments, 2)

    assert [
        [segment.get("_romaji_word_start", False) for segment in line]
        for line in lines
    ] == [[True, True], [True]]


def test_all_romaji_uses_reading_for_repetition_mark():
    text, _ = FuriganaWidget._display_text(
        SimpleNamespace(all_romaji=True),
        {"text": "々", "reading": "びと"},
    )

    assert text == "bito"