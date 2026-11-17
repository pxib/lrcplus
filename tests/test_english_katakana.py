"""English dictionary readings are explicit and preserve the lyric source."""
import sys
from pathlib import Path

import pytest

import lyrics.furigana as readings
from core.plugin_manager import PluginManager
from core.reading_dictionary import new_dictionary, save_dictionaries
from lyrics.reading_providers import find_reading_provider
from plugins.community.english_katakana.plugin import build_segments


@pytest.fixture
def english_provider(monkeypatch):
    import plugins.community.english_katakana.plugin as provider

    entries = {"hello": "ハロー", "lose": "ルーズ", "rose": "ローズ", "i'm": "アイム", "don't": "ドント"}
    # PluginManager loads its own module, so replace the process boundary
    # after loading, through the registered generator's module globals.
    monkeypatch.setattr(readings, "_READING_MODE", "auto")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_AUTO_LANGUAGE_CONTEXT", None)
    manager = PluginManager()
    plugin = Path(__file__).resolve().parents[1] / "plugins/community/english_katakana/plugin.py"
    manager.load_from_file("english_katakana", plugin)
    module = sys.modules[manager.plugins["english_katakana"].module_name]
    monkeypatch.setattr(module, "_isolated_convert", lambda texts: [
        provider.build_segments(text, entries.get) for text in texts
    ])
    try:
        yield manager
    finally:
        manager.unload_all()
        readings.clear_reading_segment_cache()


def test_explicit_mode_and_unload(english_provider):
    assert "english_katakana" in readings.available_reading_modes()
    assert find_reading_provider("hello lose", language="latin") is None
    assert readings.build_furigana_segments("hello lose") == [{"text": "hello lose", "reading": None}]
    readings.set_reading_mode("english_katakana")
    assert readings.build_furigana_segments("lose rose") == [
        {"text": "lose", "reading": "ルーズ"},
        {"text": " ", "reading": None},
        {"text": "rose", "reading": "ローズ"},
    ]
    english_provider.unload_all()
    assert "english_katakana" not in readings.available_reading_modes()
    assert readings.build_furigana_segments("lose rose") == [{"text": "lose rose", "reading": None}]


def test_contractions_unknown_words_and_source_preservation(english_provider):
    readings.set_reading_mode("english_katakana")
    text = "I’m, don't lose—rose!\n  feelings café 日本語 123 🎵"
    segments = readings.build_furigana_segments(text)
    assert "".join(part["text"] for part in segments) == text
    assert [(part["text"], part["reading"]) for part in segments if part["reading"]] == [
        ("I’m", "アイム"), ("don't", "ドント"), ("lose", "ルーズ"), ("rose", "ローズ"),
    ]


def test_no_partial_lookup_for_non_english_words():
    calls = []
    assert build_segments("café", lambda word: calls.append(word) or "wrong") == [
        {"text": "café", "reading": None},
    ]
    assert calls == []
    assert build_segments("", lambda word: "wrong") == []


def test_loading_plugin_does_not_import_alkana(monkeypatch):
    import builtins
    from types import SimpleNamespace
    from plugins.community.english_katakana.plugin import EnglishKatakanaPlugin

    original_import = builtins.__import__

    def guarded_import(name, *args, **kwargs):
        assert not name.startswith("alkana"), "Dictionary must stay outside the player"
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", guarded_import)
    registered = []
    api = SimpleNamespace(register_reading_provider=lambda *args, **kwargs: registered.append((args, kwargs)))
    EnglishKatakanaPlugin().on_load(SimpleNamespace(api=api))
    assert len(registered) == 1
    assert "batch_generator" in registered[0][1]


@pytest.mark.parametrize("invalid", [False, True])
def test_worker_batches_validates_and_cleans_up(monkeypatch, invalid):
    import json
    from types import SimpleNamespace
    import plugins.community.english_katakana.plugin as provider

    calls = []

    def run(command, **kwargs):
        request = Path(command[command.index("--input") + 1])
        response = Path(command[command.index("--output") + 1])
        values = json.loads(request.read_text(encoding="utf-8"))
        calls.append(request.parent)
        result = [[{"text": text, "reading": "ルーズ"}] for text in values]
        if invalid:
            result[0][0]["text"] = "corrupted"
        response.write_text(json.dumps(result), encoding="utf-8")
        assert kwargs["timeout"] == 60
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(provider.subprocess, "run", run)
    if invalid:
        with pytest.raises(RuntimeError, match="preserve lyric text"):
            provider._isolated_convert(["lose", "rose"])
    else:
        assert len(provider._isolated_convert(["lose", "rose"])) == 2
    assert len(calls) == 1
    assert not calls[0].exists()


def test_custom_dictionary_fills_gaps_and_overrides_cached_readings(english_provider):
    readings.set_reading_mode("english_katakana")
    readings.build_furigana_segments("hello feelings")
    dictionary = new_dictionary("English corrections")
    dictionary["entries"] = [
        {"word": word, "reading": reading, "type": "english_katakana", "match": "word", "comment": ""}
        for word, reading in [("hello", "ハロウ"), ("feelings", "フィーリングス")]
    ]
    save_dictionaries({"version": 1, "dictionaries": [dictionary]})
    assert [(part["text"], part["reading"]) for part in readings.build_furigana_segments("hello feelings") if part["reading"]] == [
        ("hello", "ハロウ"), ("feelings", "フィーリングス"),
    ]


def test_automatic_readings_gate_still_applies(english_provider):
    readings.set_reading_mode("english_katakana")
    readings.set_automatic_readings_enabled(False)
    assert readings.build_furigana_segments("lose") == [{"text": "lose", "reading": None}]
    assert readings.build_furigana_segments("lose", force=True) == [{"text": "lose", "reading": "ルーズ"}]


def test_missing_words_ignore_readings_translations_and_non_english():
    from lyrics.reading_warnings import missing_english_readings, unconverted_words_message

    tokens = [
        {"text": "hello", "reading": "ハロー"},
        {"text": " feelings Feelings! café 日本語 123\ntranslation", "reading": None},
    ]
    assert missing_english_readings(tokens, [1]) == ["feelings"]
    # Manual and dictionary annotations can span only part of a provider token.
    assert missing_english_readings([
        {"text": "feel", "reading": "フィール"},
        {"text": "ings", "reading": "イングス"},
    ]) == []
    message = unconverted_words_message(["feelings", "Feelings", "stars"])
    assert "2 English word(s)" in message
    assert "feelings, stars" in message
    assert "Reading Dictionaries" in message
    assert unconverted_words_message([]) == ""


@pytest.mark.parametrize("all_lyrics", [False, True])
@pytest.mark.parametrize("text,expect_warning", [("hello feelings", True), ("hello", False), ("feelings", True)])
def test_generation_warns_about_missing_words(
    english_provider, make_lyrics_editor, monkeypatch, all_lyrics, text, expect_warning,
):
    from PySide6.QtWidgets import QMessageBox
    from lyrics.models import LyricLine, LyricSegment

    editor = make_lyrics_editor()
    editor.lines = [LyricLine(0, text, [LyricSegment(0, text)])]
    editor._populate_lines(preserve_line_index=0)
    editor.reading_mode_combo.setCurrentIndex(editor.reading_mode_combo.findData("english_katakana"))
    messages = []
    monkeypatch.setattr(QMessageBox, "warning", lambda parent, title, message: messages.append(("warning", message)))
    monkeypatch.setattr(QMessageBox, "information", lambda parent, title, message: messages.append(("information", message)))
    if all_lyrics:
        editor.generate_readings_for_all_lyrics()
    else:
        editor.generate_readings_for_current_timestamp()
    assert messages[-1][0] == ("warning" if expect_warning else "information")
    assert ("could not be converted" in messages[-1][1]) == expect_warning
    if expect_warning:
        assert "feelings" in messages[-1][1]
        assert "0 error(s)" not in messages[-1][1]
