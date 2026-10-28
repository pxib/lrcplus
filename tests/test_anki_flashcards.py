from lyrics import furigana
from plugins.community.anki_flashcards.plugin import format_lyric_with_readings


def test_analyze_lyric_words_maps_words_to_source_lines(monkeypatch):
    def fake_analyze(text):
        assert text == "私 は好き\n私は好き"
        return [
            {"surface": "私", "reading": "わたし"},
            {"surface": "は", "reading": "は"},
            {"surface": "好き", "reading": "すき"},
            {"surface": "私", "reading": "わたし"},
            {"surface": "は", "reading": "は"},
            {"surface": "好き", "reading": "すき"},
        ]

    monkeypatch.setattr(furigana, "_analyze", fake_analyze)

    words = furigana.analyze_lyric_words([
        {"text": "私 は好き", "start_ms": 1200},
        {"text": "私は好き", "start_ms": 3400},
    ])

    assert [word["text"] for word in words] == [
        "私", "は", "好き", "私", "は", "好き",
    ]
    assert [word["line_index"] for word in words] == [0, 0, 0, 1, 1, 1]
    assert [word["start_ms"] for word in words] == [
        1200, 1200, 1200, 3400, 3400, 3400,
    ]
    assert words[3]["context"] == "私は好き"


def test_format_lyric_with_readings_appends_furigana_inline():
    formatted = format_lyric_with_readings(
        "猫が好き！",
        [
            {"text": "猫", "reading": "ねこ"},
            {"text": "好き", "reading": "すき"},
        ],
    )

    assert formatted == (
        '猫<span style="font-size: 15px; color: #68716e;">（ねこ）</span>'
        'が好き<span style="font-size: 15px; color: #68716e;">（すき）</span>！'
    )