import base64
import json

import pytest

from core import chinese_lyrics
from core.lyrics_translations import converted_translations, timed_translations, translation_preview, with_translations
from core.native_lyrics import krc_translations
from lyrics.lrcx_parser import load_lrcx
from lyrics.writer import serialize_lrcx
from test_native_lyrics_providers import encoded_krc, replies


def load_variant(tmp_path, result):
    path = tmp_path / "translated.lrcx"
    path.write_text(result["translatedLyrics"], encoding="utf-8")
    return load_lrcx(path, infer_ends=False)


@pytest.mark.parametrize("terminal", [2500, 3000])
def test_netease_translation_keeps_word_starts_ends_and_line_duration(monkeypatch, tmp_path, terminal):
    calls, _ = replies(monkeypatch, {
        "code": 200,
        "yrc": {"lyric": f"[1000,{terminal - 1000}](1100,250,0)Hello (1600,900,0)world"},
        "ytlrc": {"lyric": "[00:01.000]*Translated*"},
        "tlyric": {"lyric": "[00:05.000]Wrong timeline"},
    })
    result = chinese_lyrics.fetch_lyrics({"source": "NetEase Cloud Music", "id": 42})
    assert len(calls) == 1
    assert result["lyricsFormat"] == "lrcx"
    assert r"{\nt}" not in result["syncedLyrics"]
    line = load_variant(tmp_path, result)[0]
    assert line.text == "Hello world\n*Translated*"
    assert line.translation_lines == [1]
    assert not line.style_spans
    assert line.start == 1
    assert line.end == terminal / 1000
    assert [(part.start, part.end) for part in line.segments[:2]] == [(1.1, 1.35), (1.6, 2.5)]
    translated = line.segments[-1]
    assert (translated.start, translated.end, translated.instant) == (1, terminal / 1000, True)
    saved = tmp_path / "saved-again.lrcx"
    saved.write_text(serialize_lrcx([line]), encoding="utf-8")
    reloaded = load_lrcx(saved, infer_ends=False)[0]
    assert reloaded.text == line.text
    assert reloaded.translation_lines == [1]
    assert reloaded.end == terminal / 1000
    assert [(part.start, part.end) for part in reloaded.segments[:2]] == [(1.1, 1.35), (1.6, 2.5)]


def test_netease_line_translations_are_optional_and_do_not_shift_unmatched_rows(monkeypatch, tmp_path):
    replies(monkeypatch, {"code": 200, "lrc": {"lyric": "[00:01]One\n[00:02]Two\n[00:03]Three"},
                         "tlyric": {"lyric": "[00:01]Uno\n[00:03]Tres\n[00:04]Orphan"}})
    result = chinese_lyrics.fetch_lyrics({"source": "NetEase Cloud Music", "id": 42})
    assert result["lyricsFormat"] == "lrc"
    assert result["syncedLyrics"] == "[00:01]One\n[00:02]Two\n[00:03]Three"
    assert result["translationCount"] == 2
    lines = load_variant(tmp_path, result)
    assert [line.text for line in lines] == ["One\nUno", "Two", "Three\nTres"]
    assert [line.translation_lines for line in lines] == [[1], [], [1]]
    assert not any(line.has_karaoke for line in lines)


def test_netease_legacy_fallback_also_retains_translations(monkeypatch, tmp_path):
    calls, _ = replies(monkeypatch, 404, {"code": 200, "lrc": {"lyric": "[00:01]Hello"},
                                        "tlyric": {"lyric": "[00:01]Translation"}})
    result = chinese_lyrics.fetch_lyrics({"source": "NetEase Cloud Music", "id": 42})
    assert len(calls) == 2
    assert load_variant(tmp_path, result)[0].text == "Hello\nTranslation"


@pytest.mark.parametrize("translated", ["[00:01]You &amp; I", "[00:01]//"])
def test_qq_translation_decodes_separate_base64_field(monkeypatch, tmp_path, translated):
    calls, _ = replies(monkeypatch, {"code": 0,
                                   "lyric": base64.b64encode(b"[00:01]Original").decode(),
                                   "trans": base64.b64encode(translated.encode()).decode()})
    result = chinese_lyrics.fetch_lyrics({"source": "QQ Music", "id": "song42"})
    assert len(calls) == 1
    assert result["syncedLyrics"] == "[00:01]Original"
    if translated.endswith("//"):
        assert "translatedLyrics" not in result
    else:
        assert load_variant(tmp_path, result)[0].text == "Original\nYou & I"


def language_tag(rows, kind=1):
    payload = {"content": [{"type": kind, "lyricContent": rows}]}
    return "[language:" + base64.b64encode(json.dumps(payload).encode()).decode() + "]\n"


def test_kugou_translation_indexes_original_rows_before_credit_filtering(monkeypatch, tmp_path):
    text = language_tag([["Credit translation"], ["Translated lyric"]])
    text += "[0,1000]<0,1000,0>Lyrics: Author\n[1000,2000]<100,500,0>Original"
    calls, _ = replies(monkeypatch, {"status": 200, "content": encoded_krc(text)})
    result = chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "42", "accessKey": "key"})
    line = load_variant(tmp_path, result)[0]
    assert len(calls) == 1
    assert line.text == "Original\nTranslated lyric"
    assert (line.segments[0].start, line.segments[0].end, line.end) == (1.1, 1.6, 3)
    assert "Credit" not in result["translatedLyrics"]


def test_kugou_line_fallback_retains_translation(monkeypatch, tmp_path):
    # A backwards positive offset forces the fallback while keeping the line index.
    text = language_tag([["Translation"]]) + "[1000,2000]<500,100,0>A<100,100,0>B"
    replies(monkeypatch, {"status": 200, "content": encoded_krc(text)},
            {"status": 200, "content": base64.b64encode(b"[00:01]Original").decode()})
    result = chinese_lyrics.fetch_lyrics({"source": "KuGou", "id": "42", "accessKey": "key"})
    assert result["lyricsFormat"] == "lrc"
    assert load_variant(tmp_path, result)[0].text == "Original\nTranslation"


@pytest.mark.parametrize("tag", ["[language:bad]", language_tag([["Only one"]]),
                                 language_tag([["roma"], ["roma"]], kind=0),
                                 language_tag([["one"], [None]])])
def test_malformed_or_unaligned_krc_metadata_is_optional(tag):
    assert krc_translations(tag + "\n[0,500]<0,500,0>A\n[1000,500]<0,500,0>B") == {}


@pytest.mark.parametrize("translation", [None, [], {}, {"lyric": None}, {"lyric": "Plain translation"},
                                         {"lyric": "[" + "9" * 100 + ":00]Invalid"},
                                         {"lyric": "[00:02]Unmatched"}])
def test_missing_or_invalid_translation_keeps_original_import_working(monkeypatch, translation):
    replies(monkeypatch, {"code": 200, "lrc": {"lyric": "[00:01]Original"}, "tlyric": translation})
    result = chinese_lyrics.fetch_lyrics({"source": "NetEase Cloud Music", "id": 42})
    assert result["syncedLyrics"] == "[00:01]Original"
    assert "translatedLyrics" not in result


def test_duplicate_timestamps_do_not_guess_translation_ownership():
    assert timed_translations("[00:01]One\n[00:01]Other") == {}
    original = {"syncedLyrics": "[00:01]One\n[00:01]Other", "lyricsFormat": "lrc"}
    assert with_translations(original, {1000: "Translated"}) == original


def test_repeated_lrc_timestamps_and_literal_formatting_survive(tmp_path):
    result = with_translations({"syncedLyrics": "[00:01][00:03]*Literal*", "lyricsFormat": "lrc"},
                               timed_translations("[00:01][00:03]Translated"))
    assert result["translationCount"] == 2
    lines = load_variant(tmp_path, result)
    assert [line.start for line in lines] == [1, 3]
    assert all(line.text == "*Literal*\nTranslated" and not line.style_spans for line in lines)


def test_multiline_krc_translation_stays_inside_the_lyric(tmp_path):
    result = with_translations({"syncedLyrics": "[00:01]Original", "lyricsFormat": "lrc"},
                               {1000: "First row\r\nSecond row"})
    line = load_variant(tmp_path, result)[0]
    assert line.text == "Original\nFirst row\nSecond row"
    assert line.translation_lines == [1, 2]


def test_invalid_qq_translation_does_not_break_original_import(monkeypatch):
    replies(monkeypatch, {"code": 0, "lyric": base64.b64encode(b"[00:01]Original").decode(),
                         "trans": "not base64"})
    result = chinese_lyrics.fetch_lyrics({"source": "QQ Music", "id": "song42"})
    assert result["syncedLyrics"] == "[00:01]Original"
    assert "translatedLyrics" not in result


@pytest.mark.parametrize("script,expected", [("simplified", "音乐与梦想"), ("traditional", "音樂與夢想")])
def test_chinese_translation_conversion_preserves_original_and_timing(tmp_path, script, expected):
    result = with_translations({"syncedLyrics": "[00:01.000]<00:01.100>僕の夢<00:02.500><00:03.000>",
                                "lyricsFormat": "lrcx"}, {1000: "音樂与梦想"})
    variant = converted_translations(result, script)
    assert result["translationRows"] == {1000: "音樂与梦想"}
    assert variant["syncedLyrics"] == result["syncedLyrics"]
    assert translation_preview(variant) == "[00:01.000]" + expected
    line = load_variant(tmp_path, variant)[0]
    assert line.text == "僕の夢\n" + expected
    assert (line.start, line.end, line.segments[0].start, line.segments[0].end) == (1, 3, 1.1, 2.5)
    assert converted_translations(result, "provided") is result
