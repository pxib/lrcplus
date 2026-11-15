"""Stylized katakana grammar must retain source surfaces and kanji readings."""

import pytest

from lyrics import furigana


@pytest.mark.parametrize("source, normalized", [
    ("千本桜 夜ニ紛レ", "千本桜 夜に紛れ"),
    ("君ノ声モ 届カナイヨ", "君の声も 届かないよ"),
    ("此処ハ宴 鋼ノ檻", "此処は宴 鋼の檻"),
    ("ソノ断頭台デ見下ロシテ", "その断頭台で見下ろして"),
    ("嘆ク唄モ聞コエナイヨ", "嘆く唄も聞こえないよ"),
    ("コーヒーを飲む", "コーヒーを飲む"),
    ("東京ライブ 日本デビュー", "東京ライブ 日本デビュー"),
    ("君の声モ届カナイヨ", "君の声モ届カナイヨ"),
    ("カナイヨ", "カナイヨ"),
])
def test_analysis_kana_is_conservative(source, normalized):
    assert furigana._analysis_kana(source) == normalized


@pytest.mark.parametrize("parser", ["yomi", "mecab_local"])
@pytest.mark.parametrize("decoration", ["", "☆"])
def test_stylized_grammar_restores_source_after_analysis(monkeypatch, parser, decoration):
    source = f"千本桜 夜ニ紛{decoration}レ\nコーヒーを飲む"
    calls = []

    def analyze(text):
        calls.append(text)
        return [
            {"surface": "千本桜", "reading": "センボンザクラ"},
            {"surface": "夜", "reading": "ヨル"},
            {"surface": "に", "reading": "ニ", "pos": ["助詞"]},
            {"surface": "紛れ", "reading": "マギレ", "pos": ["動詞"]},
            {"surface": "コーヒー", "reading": "コーヒー"},
            {"surface": "を", "reading": "ヲ"},
            {"surface": "飲む", "reading": "ノム"},
        ]

    monkeypatch.setattr(furigana, "get_active_parser", lambda: parser)
    monkeypatch.setattr(furigana, "yomi_analyze", analyze)
    monkeypatch.setattr(furigana, "mecab_analyze", analyze)
    words = furigana._analyze(source)
    tokens = furigana._build_segments_from_words(source, words)
    assert calls == ["千本桜 夜に紛れ\nコーヒーを飲む"]
    assert "".join(token["text"] for token in tokens) == source
    assert next(token["reading"] for token in tokens if token["text"] == "紛") == "まぎ"
    assert [word["surface"] for word in words][2:4] == ["ニ", f"紛{decoration}レ"]
    assert next(word for word in words if word["surface"] == "ニ")["pos"] == ["助詞"]


def test_local_dictionary_reads_stylized_inflections(monkeypatch):
    pytest.importorskip("fugashi")
    pytest.importorskip("unidic_lite")
    monkeypatch.setattr(furigana, "get_active_parser", lambda: "mecab_local")
    source = "千本桜 夜ニ紛レ\nソノ断頭台デ見下ロシテ"
    tokens = furigana._build_segments_from_words(source, furigana._analyze(source))
    assert "".join(token["text"] for token in tokens) == source
    assert next(token["reading"] for token in tokens if token["text"] == "紛") == "まぎ"
    assert next(token["reading"] for token in tokens if token["text"] == "見下") == "みお"
