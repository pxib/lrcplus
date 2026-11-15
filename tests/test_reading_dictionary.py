from copy import deepcopy
import json

import pytest

from core.reading_dictionary import (
    dictionary_matches, export_dictionary_file, import_dictionary_file,
    load_dictionaries, new_dictionary, save_dictionaries, validate_dictionaries,
)
from core.settings import get_app_data_dir
import lyrics.furigana as readings


def dictionary_data(entries, name="Custom"):
    dictionary = new_dictionary(name)
    dictionary["entries"] = [dict(word=word, reading=reading, type=kind, comment="", match="word") for word, reading, kind in entries]
    return {"version": 1, "dictionaries": [dictionary]}


@pytest.fixture
def reading_mode(monkeypatch):
    monkeypatch.setattr(readings, "_READING_MODE", "furigana")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_AUTO_LANGUAGE_CONTEXT", "japanese")
    readings.clear_reading_segment_cache()
    yield
    readings.clear_reading_segment_cache()


def test_dictionary_longest_match_precedence_disable_and_atomic_backup(tmp_path):
    data = dictionary_data([("明日", "あす", "furigana"), ("明日香", "あすか", "furigana")])
    other = dictionary_data([("明日", "あした", "furigana")], "Other")["dictionaries"][0]
    data["dictionaries"].append(other)
    save_dictionaries(data)
    assert dictionary_matches("明日香と明日", "furigana", word_starts={0, 3, 4}, word_ends={3, 4, 6}) == [(0, 3, "あすか"), (4, 6, "あす")]
    assert dictionary_matches("明日", "pinyin") == []
    before = load_dictionaries()
    data["dictionaries"][0]["enabled"] = False
    save_dictionaries(data)
    assert dictionary_matches("明日", "furigana") == [(0, 2, "あした")]
    backup = get_app_data_dir() / "reading-dictionaries.json.bak"
    assert json.loads(backup.read_text(encoding="utf-8")) == before
    assert load_dictionaries(reload=True) == data


def test_dictionary_validation_does_not_replace_valid_file():
    data = dictionary_data([("明日", "あす", "furigana")])
    save_dictionaries(data)
    invalid = deepcopy(data)
    invalid["dictionaries"][0]["entries"].append(dict(invalid["dictionaries"][0]["entries"][0]))
    with pytest.raises(ValueError, match="Duplicate"):
        save_dictionaries(invalid)
    assert load_dictionaries(reload=True) == data


@pytest.mark.parametrize("suffix", [".json", ".tsv"])
def test_dictionary_export_import_round_trip(tmp_path, suffix):
    dictionary = dictionary_data([("明日", "あす", "furigana"), ("重慶", "chóng qìng", "pinyin")])["dictionaries"][0]
    dictionary["entries"][0]["comment"] = "Song reading"
    path = tmp_path / ("dictionary" + suffix)
    export_dictionary_file(path, dictionary)
    imported = import_dictionary_file(path)
    assert imported[0]["entries"] == dictionary["entries"]


def test_google_ime_import_accepts_pos_and_utf16(tmp_path):
    path = tmp_path / "User dictionary.txt"
    path.write_text("!Google Japanese Input\nあす\t明日\t名詞\tSong reading\n", encoding="utf-16")
    entries = import_dictionary_file(path)[0]["entries"]
    assert entries == [{"reading": "あす", "word": "明日", "type": "furigana", "comment": "Song reading", "match": "word"}]


def test_cached_provider_results_update_without_regeneration_and_restore_after_delete(reading_mode, monkeypatch):
    calls = []
    monkeypatch.setattr(readings, "_analyze", lambda text: calls.append(text) or [{"surface": text, "reading": "アシタ"}])
    initial = readings.build_furigana_segments("明日")
    save_dictionaries(dictionary_data([("明日", "あす", "furigana")]))
    assert readings.build_furigana_segments("明日")[0]["reading"] == "あす"
    save_dictionaries({"version": 1, "dictionaries": []})
    assert readings.build_furigana_segments("明日") == initial
    assert calls == ["明日"]


def test_dictionary_overlays_across_tokens_without_changing_source_or_other_readings(reading_mode):
    save_dictionaries(dictionary_data([("明日", "あす", "furigana")]))
    tokens = [
        {"text": "君", "reading": "きみ", "_dictionary_word_start": True, "_dictionary_word_end": True},
        {"text": "と", "reading": "と", "_dictionary_word_start": True, "_dictionary_word_end": True},
        {"text": "明", "reading": "あ", "_dictionary_word_start": True, "_dictionary_word_end": False},
        {"text": "日", "reading": "した", "_dictionary_word_start": False, "_dictionary_word_end": True},
    ]
    original = deepcopy(tokens)
    result = readings._with_dictionary_readings("君と明日", tokens)
    assert "".join(part["text"] for part in result) == "君と明日"
    assert result[0] == tokens[0]
    assert result[-1]["reading"] == "あす"
    assert tokens == original


def test_translation_rows_are_excluded_and_cache_never_bakes_in_dictionary(reading_mode, monkeypatch):
    save_dictionaries(dictionary_data([("明日", "あす", "furigana")]))
    monkeypatch.setattr(readings, "_analyze", lambda text: [{"surface": text, "reading": "アシタ"}])
    text = r"明日{\nt}明日"
    parts = readings.build_furigana_segments(text)
    assert parts[0]["reading"] == "あす"
    assert parts[-1]["reading"] is None
    save_dictionaries({"version": 1, "dictionaries": []})
    parts = readings.build_furigana_segments(text)
    assert parts[0]["reading"] != "あす"
    assert parts[-1]["reading"] is None


def test_dictionary_applies_with_automatic_providers_disabled(reading_mode, monkeypatch):
    save_dictionaries(dictionary_data([("明日", "あす", "furigana")]))
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", False)
    monkeypatch.setattr(readings, "_analyze", lambda text: pytest.fail("No automatic provider request expected"))
    assert readings.build_furigana_segments("明日")[0]["reading"] == "あす"
    assert readings.get_cached_furigana_segments("明日")[0]["reading"] == "あす"
    parts = readings.build_furigana_segments(r"明日{\nt}明日")
    assert "".join(part["text"] for part in parts) == "明日\n明日"
    assert parts[0]["reading"] == "あす"
    assert parts[-1]["reading"] is None


def test_corrupt_dictionary_does_not_interrupt_readings_or_overwrite_file(reading_mode):
    path = get_app_data_dir() / "reading-dictionaries.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text("{ invalid dictionary", encoding="utf-8")
    assert dictionary_matches("明日", "furigana") == []
    assert path.read_text(encoding="utf-8") == "{ invalid dictionary"
    with pytest.raises(ValueError):
        load_dictionaries(reload=True)


def test_song_ruby_and_karaoke_timing_win_over_dictionary(qapp, reading_mode):
    from lyrics.furigana_widget import FuriganaWidget
    from lyrics.models import LyricSegment
    save_dictionaries(dictionary_data([("明日", "あす", "furigana")]))
    readings._cache_furigana_segments("明日", [{"text": "明日", "reading": "あした"}])
    widget = FuriganaWidget()
    try:
        segments = [LyricSegment(1, "明", end=2), LyricSegment(2, "日", end=3)]
        original = deepcopy(segments)
        widget.set_segments("明日", segments, 3)
        assert any(part.get("reading") == "あす" for part in widget.segments)
        assert segments == original
        authored = [LyricSegment(1, "明日", ruby="あした", ruby_source="manual", end=3)]
        widget.set_segments("明日", authored, 3)
        assert any(part.get("reading") == "あした" for part in widget.segments)
        assert all(part.get("reading") != "あす" for part in widget.segments)
    finally:
        widget.deleteLater()


def test_whole_word_dictionary_protects_compounds_in_single_and_batch_generation(reading_mode, monkeypatch):
    save_dictionaries(dictionary_data([("私", "わたし", "furigana")]))
    words = {
        "私": {"surface": "私", "reading": "ワタクシ"},
        "は": {"surface": "は", "reading": "ハ"},
        "私立": {"surface": "私立", "reading": "シリツ"},
        "大学": {"surface": "大学", "reading": "ダイガク"},
    }
    def analyze(text):
        parts = []
        for row in text.split("\n"):
            names = ["私", "は", "私立", "大学"] if row == "私は私立大学" else ["私立", "大学"]
            parts.extend(dict(words[name]) for name in names)
        return parts
    monkeypatch.setattr(readings, "_analyze", analyze)
    def verify(parts):
        assert next(part["reading"] for part in parts if part["text"] == "私") == "わたし"
        assert next(part["reading"] for part in parts if part["text"] == "私立") == "しりつ"
        assert "".join(part["text"] for part in parts) == "私は私立大学"
    verify(readings.build_furigana_segments("私は私立大学"))
    readings.clear_reading_segment_cache()
    readings.prefetch_furigana_batches(["私は私立大学", "私立大学"])
    verify(readings.get_cached_furigana_segments("私は私立大学"))
    assert readings.get_cached_furigana_segments("私立大学")[0]["reading"] == "しりつ"


def test_whole_word_does_not_treat_ruby_piece_as_lexical_word(reading_mode, monkeypatch):
    save_dictionaries(dictionary_data([("立", "たつ", "furigana")]))
    monkeypatch.setattr(readings, "_analyze", lambda text: [{"surface": "立つ", "reading": "タツ"}])
    parts = readings.build_furigana_segments("立つ")
    assert next(part["reading"] for part in parts if part["text"] == "立") == "た"
    assert not any(part.get("reading_source") == "dictionary" for part in parts)


def test_unknown_word_boundaries_are_conservative_and_inside_words_is_explicit(reading_mode):
    data = dictionary_data([("私", "わたし", "furigana")])
    save_dictionaries(data)
    assert dictionary_matches("私立", "furigana") == []
    assert dictionary_matches("私", "furigana") == [(0, 1, "わたし")]
    assert dictionary_matches("私、私立", "furigana") == [(0, 1, "わたし")]
    data["dictionaries"][0]["entries"][0]["match"] = "substring"
    save_dictionaries(data)
    assert dictionary_matches("私立", "furigana") == [(0, 1, "わたし")]
    data["dictionaries"][0]["entries"].append(
        {"word": "私立", "reading": "しりつ", "type": "furigana", "comment": "", "match": "word"})
    save_dictionaries(data)
    assert dictionary_matches("私立", "furigana") == [(0, 2, "しりつ")]


def test_legacy_entries_default_to_whole_word():
    data = dictionary_data([("私", "わたし", "furigana")])
    del data["dictionaries"][0]["entries"][0]["match"]
    normalized = validate_dictionaries(data)
    assert normalized["dictionaries"][0]["entries"][0]["match"] == "word"
