"""Decorative lyric symbols must not interrupt Japanese word readings."""

import pytest

import lyrics.furigana as readings


@pytest.mark.parametrize("parser", ["yomi", "mecab_local"])
@pytest.mark.parametrize("symbol", ["～", "〜", "~", "☆", "♪", "♡"])
def test_symbols_are_ignored_by_tokenizer_and_preserved_in_furigana(monkeypatch, parser, symbol):
    source = f"日{symbol}々に"
    calls = []

    def analyze(text):
        calls.append(text)
        return [
            {"surface": "日々", "reading_raw": "ヒビ"},
            {"surface": "に", "reading": "ニ"},
        ]

    monkeypatch.setattr(readings, "get_active_parser", lambda: parser)
    monkeypatch.setattr(readings, "yomi_analyze", analyze)
    monkeypatch.setattr(readings, "mecab_analyze", analyze)
    segments = readings._build_segments_from_words(source, readings._analyze(source))

    assert calls == ["日々に"]
    assert [(s["text"], s["reading"]) for s in segments] == [
        ("日", "ひ"), (symbol, None), ("々", "び"), ("に", None),
    ]
    assert "".join(s["text"] for s in segments) == source
    assert segments[0]["_romaji_word_start"]
    assert all(not s["_romaji_word_start"] for s in segments[1:3])


def test_kana_iteration_marks_and_punctuation_reach_tokenizer_unchanged(monkeypatch):
    source = "きゃっ、コーヒー！日々\n時々"
    calls = []
    monkeypatch.setattr(readings, "get_active_parser", lambda: "yomi")
    monkeypatch.setattr(readings, "yomi_analyze", lambda text: calls.append(text) or [])
    readings._analyze(source)
    assert calls == [source]


def test_symbol_only_input_does_not_call_tokenizer(monkeypatch):
    def unexpected(text):
        pytest.fail("symbols need no analysis")

    monkeypatch.setattr(readings, "yomi_analyze", unexpected)
    monkeypatch.setattr(readings, "mecab_analyze", unexpected)
    source = "～☆♪"
    assert readings._analyze(source) == []
    assert readings.token_to_furigana_segments(source, "ホシ") == [
        {"text": source, "reading": None},
    ]


def test_symbols_and_whitespace_survive_batch_analysis_and_local_fallback(monkeypatch):
    source = "☆日～々に♪\n 日♡々！"
    calls = []

    def analyze(text):
        calls.append(text)
        return [
            {"surface": "日々", "reading": "ヒビ"},
            {"surface": "に", "reading": "ニ"},
            {"surface": "日々", "reading": "ヒビ"},
            {"surface": "！", "reading": ""},
        ]

    monkeypatch.setattr(readings, "get_active_parser", lambda: "mecab_local")
    monkeypatch.setattr(readings, "mecab_analyze", lambda text: None)
    monkeypatch.setattr(readings, "yomi_analyze", analyze)
    segments = readings._build_segments_from_words(source, readings._analyze(source))
    assert calls == ["日々に\n 日々！"]
    assert "".join(s["text"] for s in segments) == source
    assert [s["reading"] for s in segments if s.get("reading")] == ["ひ", "び", "ひ", "び"]
    rows = readings._split_segments_by_lines(segments, 2)
    assert ["".join(s["text"] for s in row) for row in rows] == source.split("\n")


@pytest.mark.parametrize("surface, reading", [
    ("きゃっ", "キャッ"), ("コーヒー", "コーヒー"), ("食べちゃった", "タベチャッタ"),
])
def test_small_kana_are_preserved(surface, reading):
    segments = readings.token_to_furigana_segments(surface, reading)
    assert "".join(s["text"] for s in segments) == surface
    assert all(not s.get("reading") for s in segments if not any(map(readings.is_kanji, s["text"])))
