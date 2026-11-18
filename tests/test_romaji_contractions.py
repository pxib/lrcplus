"""Contracted predicates stay together across parser and karaoke splits."""

from copy import deepcopy

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import _build_segments_from_words
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts


EXAMPLES = [
    ([("泣い", "ナイ", "動詞"), ("て", "テ", "助詞"),
      ("ん", "ン", "助詞"), ("の", "ノ", "助詞")], "naiten no"),
    ([("泣い", "ナイ", "動詞"), ("てん", "テン", "助動詞"),
      ("の", "ノ", "助詞")], "naiten no"),
    ([("笑っ", "ワラッ", "動詞"), ("て", "テ", "助動詞"),
      ("ん", "ン", "助詞"), ("だ", "ダ", "助動詞")], "warattenda"),
    ([("読ん", "ヨン", "動詞"), ("で", "デ", "助詞"),
      ("ん", "ン", "助詞"), ("の", "ノ", "助詞")], "yonden no"),
    ([("読ん", "ヨン", "動詞"), ("でん", "デン", "助動詞", "助動詞-デス"),
      ("の", "ノ", "助詞")], "yonden no"),
    ([("泣い", "ナイ", "動詞"), ("てる", "テル", "助動詞"),
      ("の", "ノ", "助詞")], "naiteru no"),
    ([("読ん", "ヨン", "動詞"), ("でる", "デル", "助動詞")], "yonderu"),
    ([("食べ", "タベ", "動詞"), ("ちゃっ", "チャッ", "助動詞"),
      ("た", "タ", "助動詞", "助動詞-タ")], "tabechatta"),
    ([("遊ん", "アソン", "動詞"), ("じゃう", "ジャウ", "助動詞")], "asonjau"),
    ([("狂っ", "クルッ", "動詞"), ("て", "テ", "助詞"),
      ("しまっ", "シマッ", "動詞"), ("た", "タ", "助動詞", "助動詞-タ"),
      ("ん", "ン", "助詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "kurutte shimattanda"),
    ([("狂って", "クルッテ", "動詞"), ("しまった", "シマッタ", "動詞"),
      ("ん", "ン", "助詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "kurutte shimattanda"),
    ([("狂って", "クルッテ", "動詞"), ("しまった", "シマッタ", "動詞"),
      ("んだ", "ンダ", "助詞")], "kurutte shimattanda"),
    ([("食べ", "タベ", "動詞"), ("た", "タ", "助動詞", "助動詞-タ"),
      ("ん", "ン", "助詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "tabetanda"),
    ([("嬉しい", "ウレシイ", "形容詞"), ("ん", "ン", "助詞"),
      ("だ", "ダ", "助動詞", "助動詞-ダ")], "ureshiinda"),
    ([("行く", "イク", "動詞"), ("ん", "ン", "助詞"),
      ("です", "デス", "助動詞", "助動詞-デス")], "ikundesu"),
    ([("好き", "スキ", "形状詞"), ("な", "ナ", "助動詞", "助動詞-ダ"),
      ("ん", "ン", "助詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "suki nanda"),
    ([("泣い", "ナイ", "動詞"), ("てん", "テン", "助動詞"),
      ("だ", "ダ", "助動詞", "助動詞-ダ")], "naitenda"),
    ([("見", "ミ", "動詞"), ("たって", "タッテ", "助詞")], "mitatte"),
    ([("見", "ミ", "動詞"), ("た", "タ", "助動詞", "助動詞-タ"),
      ("って", "ッテ", "助詞")], "mitatte"),
    ([("読ん", "ヨン", "動詞"), ("だって", "ダッテ", "助詞")], "yondatte"),
    ([("高く", "タカク", "形容詞"), ("たって", "タッテ", "助詞")], "takakutatte"),
    ([("欲しい", "ホシイ", "形容詞"), ("ん", "ン", "助詞"),
      ("でしょ", "デショ", "助動詞", "助動詞-デス")], "hoshiindesho"),
]


def tokens_for(spec, grammar):
    words = []
    for surface, reading, pos, *conjugation in spec:
        word = {"surface": surface, "reading": reading}
        if grammar:
            value = f"{pos},一般"
            word["pos"] = {"fields": [pos, "一般"], "string": value, "serialized": [value]}[grammar]
            word["conjugation_type"] = conjugation[0] if conjugation else ""
        words.append(word)
    original = deepcopy(words)
    source = "".join(word["surface"] for word in words)
    tokens = _build_segments_from_words(source, words)
    assert words == original
    assert "".join(token["text"] for token in tokens) == source
    return source, tokens


@pytest.mark.parametrize("spec, expected", EXAMPLES)
@pytest.mark.parametrize("grammar", [None, "fields", "string", "serialized"])
def test_contractions_ignore_parser_token_and_grammar_formats(spec, expected, grammar):
    _, tokens = tokens_for(spec, grammar)
    original = deepcopy(tokens)
    assert "".join(all_romaji_texts(tokens)) == expected
    assert tokens == original


@pytest.mark.parametrize("source, spec, expected", [
    ("本でんの", [("本", "ホン", "名詞"), ("でん", "デン", "助動詞", "助動詞-デス"),
                 ("の", "ノ", "助詞")], "hon den no"),
    ("本で", [("本", "ホン", "名詞"), ("で", "デ", "助詞")], "hon de"),
    ("泣い てんの", EXAMPLES[1][0], "nai, ten no"),
    ("泣いて んの", EXAMPLES[0][0], "naite, n no"),
    ("泣いて\nんの", EXAMPLES[0][0], "naite\nn no"),
    ("本だ", [("本", "ホン", "名詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "hon da"),
    ("みかんだ", [("みかん", "ミカン", "名詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "mikan da"),
    ("きれいだ", [("きれい", "キレイ", "形状詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "kirei da"),
    ("狂ってしまった んだ", EXAMPLES[9][0], "kurutte shimatta, nda"),
    ("狂ってしまったん だ", EXAMPLES[9][0], "kurutte shimattan, da"),
    ("見 たって", EXAMPLES[17][0], "mi, tatte"),
    ("君だって", [("君", "キミ", "代名詞"), ("だって", "ダッテ", "助詞")], "kimi datte"),
    ("わかるって", [("わかる", "ワカル", "動詞"), ("って", "ッテ", "助詞")], "wakaru tte"),
    ("読んだって", [("読ん", "ヨン", "動詞"), ("だ", "ダ", "助動詞", "助動詞-タ"),
                   ("って", "ッテ", "助詞")], "yondatte"),
])
def test_contraction_grouping_respects_copulas_and_authored_breaks(source, spec, expected):
    words = [{"surface": surface, "reading": reading, "pos": [pos],
              "conjugation_type": conjugation[0] if conjugation else ""}
             for surface, reading, pos, *conjugation in spec]
    assert "".join(all_romaji_texts(_build_segments_from_words(source, words))) == expected


@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("layered", [False, True])
@pytest.mark.parametrize("spec, expected", [
    ([("なん", "ナン", "代名詞"), ("で", "デ", "助詞"),
      ("まだ", "マダ", "副詞"), *EXAMPLES[0][0]], "nan de mada naiten no"),
    EXAMPLES[9],
    ([("前", "マエ", "名詞"), *EXAMPLES[17][0], ("その", "ソノ", "連体詞"),
      ("感じ", "カンジ", "名詞"), ("（", "", "補助記号"),
      ("笑", "ワライ", "名詞"), ("）", "", "補助記号")], "mae mitatte sono kanji (warai)"),
])
def test_reported_line_preserves_lyrics_and_timing_in_each_romaji_display(qapp, monkeypatch, timed, layered, spec, expected):
    source, tokens = tokens_for(spec, "serialized")
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda _: tokens)
    chunks = [LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)]
    original = deepcopy(chunks)
    row = renderer.FuriganaWidget()
    try:
        row.resize(1400, 180)
        if layered:
            row.set_lyric_layers(["romaji", "lyrics", "translation", "furigana"],
                                 True, True, False, False)
        else:
            row.set_all_romaji_visible(True)
        if timed:
            row.set_segments(source, chunks, line_end=len(source))
        else:
            row.set_lyric(source)
        pieces = row._prepare_segments()
        romaji = [piece for piece in pieces if not layered or piece.get("display_layer") == "romaji"]
        assert "".join(piece["text"] for piece in romaji) == expected
        assert row.lyric == source
        assert chunks == original
        if timed:
            assert [(piece["start"], piece["end"]) for piece in romaji] == [
                (i, i + 1) for i in range(len(source))]
    finally:
        row.deleteLater()
