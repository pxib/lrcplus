from types import SimpleNamespace
from copy import deepcopy

import pytest

import lyrics.furigana_widget as renderer
from lyrics.furigana import _build_segments_from_words, _split_segments_by_lines, kana_to_romaji, kana_to_romaji_parts
from lyrics.models import LyricSegment
from lyrics.romaji import all_romaji_texts
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


@pytest.mark.parametrize("source, expected", [
    ("がむしゃら", "gamushara"), ("がっこう", "gakkou"),
    ("まっちゃ", "matcha"), ("しんよう", "shin'you"),
    ("っちゃう", "tchau"), ("ぽっちゅ", "potchu"),
    ("ファッション", "fasshon"), ("チェリー", "cherii"),
    ("ヒーロー", "hiiroo"), ("フィルム", "firumu"),
    ("ｼｬｯﾀｰ", "shattaa"), ("ほしくて", "hoshikute"),
])
def test_hepburn_kana(source, expected):
    assert kana_to_romaji(source) == expected


@pytest.mark.parametrize("parts, expected", [
    (["がむし", "ゃ", "ら"], ["gamush", "a", "ra"]),
    (["ふりむい", "て"], ["furimui", "te"]),
    (["な", "っ", "て"], ["na", "t", "te"]),
    (["ま", "っ", "ち", "ゃ"], ["ma", "t", "ch", "a"]),
    (["ぽ", "っ", "ち", "ゅ"], ["po", "t", "ch", "u"]),
    (["ヒ", "ー", "ロ", "ー"], ["hi", "i", "ro", "o"]),
    (["しん", "よう"], ["shin'", "you"]),
])
def test_kana_context_crosses_timing_boundaries(parts, expected):
    assert kana_to_romaji_parts(parts) == expected


ROMAJI_LINES = [
    ([("ぽっちゅ", "ポッチュ", "副詞")], "potchu"),
    (
        [("だ", "ダ", "助動詞", "助動詞-ダ"), ("って", "ッテ", "助詞"),
         ("わたくし", "ワタクシ", "代名詞"), ("の", "ノ", "助詞"),
         ("「", "", "補助記号"), ("ヒーロー", "ヒーロー", "名詞"), ("」", "", "補助記号")],
        'da tte watakushi no "hiiroo"',
    ),
    (
        [("「", "", "補助記号"), ("また", "マタ", "副詞"), ("ね", "ネ", "助詞"),
         ("」", "", "補助記号"), ("って", "ッテ", "助詞"), ("ひとりごと", "ヒトリゴト", "名詞")],
        '"mata ne" tte hitorigoto',
    ),
    (
        [("がむしゃら", "ガムシャラ", "形状詞"), ("に", "ニ", "助動詞", "助動詞-ダ"),
         ("差し伸べ", "サシノベ", "動詞"), ("た", "タ", "助動詞", "助動詞-タ")],
        "gamushara ni sashinobeta",
    ),
    (
        [("振り向い", "フリムイ", "動詞"), ("て", "テ", "助詞"),
         ("ほしく", "ホシク", "形容詞"), ("て", "テ", "助詞")],
        "furimuite hoshikute",
    ),
    (
        [("君", "キミ", "代名詞"), ("の", "ノ", "助詞"), ("もの", "モノ", "名詞"),
         ("に", "ニ", "助詞"), ("なり", "ナリ", "動詞"),
         ("たく", "タク", "助動詞", "助動詞-タイ"), ("て", "テ", "助詞")],
        "kimi no mono ni naritakute",
    ),
    (
        [("いつ", "イツ", "代名詞"), ("も", "モ", "助詞"), ("「", "", "補助記号"),
         ("眠い", "ネムイ", "形容詞"), ("」", "", "補助記号"),
         ("って", "ッテ", "助詞"), ("いう", "イウ", "動詞"),
         ("くせ", "クセ", "名詞"), ("に", "ニ", "助詞")],
        'itsu mo "nemui" tte iu kuse ni',
    ),
]


def line_tokens(spec, grammar):
    words = []
    for surface, reading, *pos in spec:
        word = {"surface": surface, "reading": reading}
        if grammar:
            word.update(pos=[pos[0]], conjugation_type=pos[1] if len(pos) > 1 else "")
        words.append(word)
    source = "".join(word["surface"] for word in words)
    return source, _build_segments_from_words(source, words)


@pytest.mark.parametrize("spec, expected", ROMAJI_LINES)
@pytest.mark.parametrize("grammar", [False, True])
def test_word_division_and_quotes(spec, expected, grammar):
    source, tokens = line_tokens(spec, grammar)
    before = deepcopy(tokens)
    assert "".join(all_romaji_texts(tokens)) == expected
    assert tokens == before
    assert "".join(token["text"] for token in tokens) == source


@pytest.mark.parametrize("spec, expected", [
    ([("読ん", "ヨン", "動詞"), ("だ", "ダ", "助動詞", "助動詞-タ")], "yonda"),
    ([("泳い", "オヨイ", "動詞"), ("で", "デ", "助詞")], "oyoide"),
    ([("立っ", "タッ", "動詞"), ("て", "テ", "助詞")], "tatte"),
    ([("きれい", "キレイ", "形状詞"), ("だ", "ダ", "助動詞", "助動詞-ダ")], "kirei da"),
    ([("本", "ホン", "名詞"), ("で", "デ", "助詞")], "hon de"),
])
def test_inflection_and_copula_are_distinguished(spec, expected):
    _, tokens = line_tokens(spec, grammar=True)
    assert "".join(all_romaji_texts(tokens)) == expected


@pytest.mark.parametrize("spec, expected", ROMAJI_LINES)
@pytest.mark.parametrize("timed", [False, True])
def test_rendered_romaji_survives_character_timing_boundaries(qapp, monkeypatch, spec, expected, timed):
    source, tokens = line_tokens(spec, grammar=True)
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: tokens)
    chunks = ([LyricSegment(i, char, end=i + 1) for i, char in enumerate(source)]
              if timed else [LyricSegment(0, source, end=len(source))])
    original = deepcopy(chunks)
    row = FuriganaWidget()
    try:
        row.resize(1400, 180)
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
    finally:
        row.deleteLater()


@pytest.mark.parametrize("opening, closing", [("「", "」"), ("『", "』"), ('"', '"'), ("“", "”"), ("＂", "＂")])
def test_quote_roles_are_preserved_even_inside_a_single_chunk(opening, closing):
    parts = all_romaji_texts([{"text": f"の{opening}ヒーロー{closing}って"}])
    assert "".join(parts) == 'no "hiiroo" tte'


def test_word_boundaries_block_kana_context_and_particle_rewrites_stay_on_words():
    parts = [
        {"text": "しん", "_romaji_word_start": True},
        {"text": "よう", "_romaji_word_start": True},
        {"text": "へ", "_romaji_word_start": True},
        {"text": "や"},
        {"text": "へ", "_romaji_word_start": True, "_romaji_particle": True},
    ]
    assert "".join(all_romaji_texts(parts)) == "shin you heya e"


def test_styles_do_not_insert_word_boundaries(qapp):
    row = FuriganaWidget()
    try:
        row.lyric = "がむしゃら"
        row.segments = [{"text": row.lyric, "_romaji_word_start": True}]
        row.style_spans = [(0, 3, True, False, False, False)]
        row.set_all_romaji_visible(True)
        assert "".join(piece["text"] for piece in row._prepare_segments()) == "gamushara"
    finally:
        row.deleteLater()
