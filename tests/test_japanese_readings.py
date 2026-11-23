import pytest

from lyrics import furigana
from lyrics.romaji import all_romaji_texts


@pytest.mark.parametrize("source, words, expected", [
    ("何を", [("何", "ナン"), ("を", "ヲ")], "なに"),
    ("何が", [("何", "ナン"), ("が", "ガ")], "なに"),
    ("何に", [("何", "ナン"), ("に", "ニ")], "なに"),
    ("何モ", [("何", "ナン"), ("モ", "モ")], "なに"),
    ("何も", [("何も", "ナンモ")], "なに"),
    ("何か", [("何か", "ナンカ")], "なに"),
    ("何ですか", [("何", "ナン"), ("ですか", "デスカ")], "なん"),
    ("何か月", [("何か", "ナンカ"), ("月", "ゲツ")], "なん"),
    ("何がし", [("何がし", "ナンガシ")], "なん"),
])
def test_generated_nani_depends_on_context(source, words, expected):
    tokens = furigana._build_segments_from_words(
        source, [{"surface": surface, "reading_raw": reading} for surface, reading in words]
    )
    assert tokens[0]["text"] == "何"
    assert tokens[0]["reading"] == expected
    assert "".join(token["text"] for token in tokens) == source


@pytest.mark.parametrize("words", [
    [("磊々落々", "ライライラクラク")],
    [("磊々", "ライ"), ("落々", "ラクラク")],
    [("磊", "ライ"), ("々", ""), ("落々", "ラクラク")],
    [("磊", "ライ"), ("落々", "ラクラク")],
    [],
])
def test_repeated_kanji_keep_ruby_when_analyzed_or_prefetched(monkeypatch, words):
    source = "磊々落々"
    expected = list(zip(source, ["らい", "らい", "らく", "らく"]))
    monkeypatch.setattr(furigana, "get_reading_mode", lambda: "furigana")
    monkeypatch.setattr(furigana, "_analyze", lambda _: [
        {"surface": surface, "reading": reading} for surface, reading in words
    ])
    furigana.clear_reading_segment_cache()
    try:
        generated = furigana.build_furigana_segments(source, force=True)
        assert [(token["text"], token["reading"]) for token in generated] == expected

        furigana.clear_reading_segment_cache()
        assert furigana.prefetch_furigana_batches([source], force=True) == (1, 1)
        cached = furigana.get_cached_furigana_segments(source)
        assert [(token["text"], token["reading"]) for token in cached] == expected
    finally:
        furigana.clear_reading_segment_cache()


@pytest.mark.parametrize("surface, reading, expected", [
    ("磊々落々", "ライライラクラク", ["らい", "らい", "らく", "らく"]),
    ("人々", "ヒトビト", ["ひと", "びと"]),
    ("図々しく", "ズウズウシク", ["ずう", "ずう", None]),
])
def test_iteration_marks_stay_in_kanji_run(surface, reading, expected):
    tokens = furigana.token_to_furigana_segments(surface, reading)
    assert [token["reading"] for token in tokens] == expected
    assert "".join(token["text"] for token in tokens) == surface


@pytest.mark.parametrize("surface, reading, expected_romaji", [
    ("云々", "ウンヌン", "unnun"),
    ("刻々", "コッコク", "kokkoku"),
    ("津々浦々", "ツツウラウラ", "tsutsuuraura"),
    ("悠々自適", "ユウユウジテキ", "yuuyuujiteki"),
])
def test_unclear_iteration_boundaries_keep_the_whole_reading(surface, reading, expected_romaji):
    tokens = furigana.token_to_furigana_segments(surface, reading)
    assert tokens == [{"text": surface, "reading": furigana.katakana_to_hiragana(reading)}]
    assert "".join(all_romaji_texts(tokens)) == expected_romaji


def test_separately_read_iteration_mark_keeps_its_ruby(monkeypatch):
    def unexpected_lookup(text):
        pytest.fail(f"An existing reading needs no lookup: {text}")

    monkeypatch.setattr(furigana, "_analyze", unexpected_lookup)
    tokens = furigana._build_segments_from_words("日々", [
        {"surface": "日", "reading": "ヒ"},
        {"surface": "々", "reading_raw": "ビ"},
    ])
    assert [(part["text"], part["reading"]) for part in tokens] == [("日", "ひ"), ("々", "び")]
    assert "".join(all_romaji_texts(tokens)) == "hibi"


@pytest.mark.parametrize("prefetch", [False, True])
def test_fallback_batches_unread_iteration_marks_and_checks_the_base_reading(monkeypatch, prefetch):
    source = "日々 人々 日々 色々 時々"
    calls = []

    def analyze(text):
        calls.append(text)
        if text == source:
            return [
                {"surface": "日", "reading": "ヒ"},
                {"surface": "々", "reading": ""},
                {"surface": "人", "reading": "ヒト"},
                {"surface": "日", "reading": "ヒ"},
                {"surface": "色", "reading": "イロ"},
                {"surface": "時", "reading": "トキ"},
            ]
        return [
            {"surface": "日日", "reading": "ヒビ"},
            {"surface": "人人", "reading": "ヒトビト"},
            {"surface": "色色", "reading": "シキシキ"},
            {"surface": "時", "reading": "トキ"},
            {"surface": "時", "reading": "トキ"},
        ]

    monkeypatch.setattr(furigana, "get_reading_mode", lambda: "furigana")
    monkeypatch.setattr(furigana, "_analyze", analyze)
    furigana.clear_reading_segment_cache()
    try:
        if prefetch:
            assert furigana.prefetch_furigana_batches([source], force=True) == (1, 1)
            tokens = furigana.get_cached_furigana_segments(source)
        else:
            tokens = furigana.build_furigana_segments(source, force=True)
    finally:
        furigana.clear_reading_segment_cache()
    assert "".join(part["text"] for part in tokens) == source
    assert calls == [source, "日日\n人人\n色色\n時時"]
    assert [(part["text"], part["reading"]) for part in tokens if part["reading"]] == [
        ("日", "ひ"), ("々", "び"), ("人", "ひと"), ("々", "びと"),
        ("日", "ひ"), ("々", "び"), ("色", "いろ"), ("時", "とき"),
    ]
