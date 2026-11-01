"""Check consonant doubling independently of kana script and timing splits."""

from copy import deepcopy

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import kana_to_romaji, kana_to_romaji_parts
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts


GEMINATES = [
    ("ぱっが", "pagga"), ("パッガ", "pagga"), ("ﾊﾟｯｶﾞ", "pagga"),
    ("は\u309aっか\u3099", "pagga"),
    ("がっこう", "gakkou"), ("バッグ", "baggu"), ("ベッド", "beddo"),
    ("ブッブー", "bubbuu"),
    ("きって", "kitte"), ("ざっし", "zasshi"), ("いっしょ", "issho"),
    ("まっしろ", "masshiro"), ("カッパ", "kappa"), ("ジッポ", "jippo"),
    ("バッジ", "bajji"), ("ぽっちゅ", "potchu"), ("っちゃう", "tchau"),
    ("まっちゃ", "matcha"), ("マッチョ", "matcho"), ("バッファ", "baffa"),
    ("マッハ", "mahha"), ("ピッツァ", "pittsa"), ("ざっざ", "zazza"),
    ("ぎっぎゃ", "giggya"), ("ビッグ", "biggu"), ("ロボット", "robotto"),
    ("pagga", "pagga"),
]


def partitions(text):
    """Every possible sequence of nonempty, contiguous timed chunks."""
    for mask in range(1 << (len(text) - 1)):
        boundaries = [0] + [i for i in range(1, len(text)) if mask & (1 << (i - 1))] + [len(text)]
        yield [text[a:b] for a, b in zip(boundaries, boundaries[1:])]


@pytest.mark.parametrize("source, expected", GEMINATES)
def test_geminate_spellings_and_all_timing_partitions(source, expected):
    assert kana_to_romaji(source) == expected
    for parts in partitions(source):
        assert "".join(kana_to_romaji_parts(parts)) == expected, parts
        segments = [{"text": part, "_romaji_word_start": i == 0} for i, part in enumerate(parts)]
        assert "".join(all_romaji_texts(segments)) == expected, parts


@pytest.mark.parametrize("parts", [["ぱっ", "が"], ["パッ", "ガ"], ["ﾊﾟｯ", "ｶﾞ"]])
def test_sokuon_continues_across_token_boundaries_in_each_script(parts):
    segments = [{"text": part, "_romaji_word_start": True} for part in parts]
    assert "".join(all_romaji_texts(segments)) == "pagga"


def test_voicing_marks_retain_the_base_kanas_timing_owner():
    assert kana_to_romaji_parts(["ﾊ", "ﾟ", "ｯ", "ｶ", "ﾞ"]) == ["pa", "", "g", "ga", ""]
    assert kana_to_romaji_parts(["は", "\u309a", "っ", "か", "\u3099"]) == ["pa", "", "g", "ga", ""]


@pytest.mark.parametrize("source, expected", [
    ("ぱっが", "pagga"), ("ﾊﾟｯｶﾞ", "pagga"), ("バッグ", "baggu"),
    ("バッジ", "bajji"), ("ぽっちゅ", "potchu"), ("まっしろ", "masshiro"),
])
def test_doubled_consonants_render_with_their_original_timestamps(qapp, monkeypatch, source, expected):
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: [
        {"text": source, "reading": None, "_romaji_word_start": True},
    ])
    chunks = [LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)]
    original = deepcopy(chunks)
    row = renderer.FuriganaWidget()
    try:
        row.resize(800, 180)
        row.set_all_romaji_visible(True)
        row.set_segments(source, chunks, line_end=len(source))
        pieces = row._prepare_segments()
        assert "".join(piece["text"] for piece in pieces) == expected
        assert [(piece["start"], piece["end"]) for piece in pieces] == [(i, i + 1) for i in range(len(source))]
        assert chunks == original
    finally:
        row.deleteLater()
