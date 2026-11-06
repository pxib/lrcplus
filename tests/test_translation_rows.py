from collections import OrderedDict

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QFontMetrics, QImage

import lyrics.furigana as readings
from lyrics.furigana_widget import FuriganaWidget
from lyrics.line_breaks import reading_source
from lyrics.lrcx_parser import load_lrcx
from core.fonts import library_font
from test_karaoke_screen_timing import screen


@pytest.fixture
def translation_provider(monkeypatch):
    calls = []
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "get_reading_mode", lambda: "test_translation")
    monkeypatch.setattr(readings, "available_reading_providers", lambda: {"test_translation"})
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)

    def generate(provider, text):
        calls.append(text)
        return [{"text": text, "reading": "かな"}]

    monkeypatch.setattr(readings, "generate_reading_segments", generate)
    monkeypatch.setattr(readings, "generate_reading_segments_batch",
                        lambda provider, texts: [generate(provider, text) for text in texts])
    return calls


@pytest.fixture
def immediate_reading_workers(monkeypatch):
    from concurrent.futures import Future

    class Executor:
        def submit(self, function):
            future = Future()
            try:
                future.set_result(function())
            except Exception as error:
                future.set_exception(error)
            return future

    monkeypatch.setattr("lyrics.furigana_widget._FURIGANA_EXECUTOR", Executor())


@pytest.mark.parametrize("timed", [False, True])
def test_karaoke_screen_skips_translation_in_both_rows(
        qapp, screen, translation_provider, immediate_reading_workers, timed):
    from lyrics.models import LyricLine, LyricSegment

    for index, original in enumerate(("Original", "Upcoming")):
        start = index * 10
        screen.window.lyric_lines[index] = LyricLine(
            start, original + "\nTranslation", [
                LyricSegment(start, original), LyricSegment(start, "\nTranslation", instant=True),
            ], end=start + 10, has_karaoke=timed, translation_lines=[1],
        )
    screen.window.current_line = 0
    screen.screen.show()
    qapp.processEvents()
    screen.refresh_karaoke_screen()
    qapp.processEvents()
    assert set(translation_provider) == {"Original", "Upcoming"}
    for widget in (screen.screen.current_lyric, screen.screen.next_lyric):
        assert widget.translation_lines == [1]
        translated = [part for part in widget._prepare_segments() if part.get("translation")]
        assert "".join(part["text"] for part in translated) == "Translation"
        assert all(not part.get("reading") for part in translated)


def test_floating_lyrics_skips_translation_and_retains_role_on_same_text(
        qapp, translation_provider, immediate_reading_workers):
    from types import SimpleNamespace
    from lyrics.models import LyricLine
    from plugins.community.floating_lyrics.plugin import FloatingLyricsPlugin, FloatingLyricsWindow

    text = "Original\nTranslation"
    floating = FloatingLyricsWindow(dict(FloatingLyricsPlugin.DEFAULTS))
    plugin = FloatingLyricsPlugin.__new__(FloatingLyricsPlugin)
    plugin.window = SimpleNamespace(
        current_line=0, lyrics=[(0, text)], lyric_lines=[LyricLine(0, text, translation_lines=[1])])
    plugin.floating = floating
    try:
        floating._current_text = text  # Same visible text, newly supplied row metadata.
        floating.show()
        plugin.refresh()
        qapp.processEvents()
        assert translation_provider == ["Original"]
        assert floating.lyric_widget.translation_lines == [1]
        translated = [part for part in floating.lyric_widget._prepare_segments() if part.get("translation")]
        assert "".join(part["text"] for part in translated) == "Translation"
        assert all(not part.get("reading") for part in translated)
        plugin.refresh()
        assert translation_provider == ["Original"]
    finally:
        floating.close()
        floating.deleteLater()


@pytest.mark.parametrize("prefetch", [False, True])
def test_translation_is_never_sent_to_reading_provider(translation_provider, prefetch):
    text = "Original\n翻訳\nOther\n訳"
    key = reading_source(text, [1, 3])
    if prefetch:
        assert readings.prefetch_furigana_batches([key]) == (1, 1)
    tokens = readings.build_furigana_segments(key, force=True)
    assert translation_provider == ["Original", "Other"]
    assert "".join(token["text"] for token in tokens) == text
    assert [token["reading"] for token in tokens if token["text"] in {"翻訳", "訳"}] == [None, None]
    assert readings.get_cached_furigana_segments(text) is None
    assert readings.get_cached_furigana_segments(key) == tokens
    readings.build_furigana_segments(key)
    assert translation_provider == ["Original", "Other"]


def test_translation_does_not_change_language_detection():
    key = reading_source("Hello\nかな", [1])
    assert readings.detect_lyrics_language(key) == "latin"
    assert readings.detect_lyrics_collection_language([key]) == "latin"


@pytest.mark.parametrize("timed", [False, True])
@pytest.mark.parametrize("vertical", [False, True])
@pytest.mark.parametrize("romaji", [False, True])
def test_translation_font_and_text_in_renderer(qapp, tmp_path, translation_provider, timed, vertical, romaji):
    path = tmp_path / "translation.lrcx"
    boundary = "<00:02.000>" if timed else ""
    path.write_text(r"[00:01.000]君{\nt}" + boundary + r"かな{\n}次" + "\n", encoding="utf-8")
    line = load_lrcx(path)[0]
    readings.build_furigana_segments(reading_source(line.text, line.translation_lines))
    widget = FuriganaWidget()
    try:
        widget.resize(600, 400)
        widget.set_font(library_font(qapp.font()).family(), size=32, ruby_size=16)
        widget.setStyleSheet("background-color: #202020;")
        widget.set_direction("vertical" if vertical else "horizontal")
        widget.set_all_romaji_visible(romaji)
        if timed:
            widget.set_segments(line.text, line.segments, line.end, translation_lines=line.translation_lines)
        else:
            widget.set_lyric(line.text, translation_lines=line.translation_lines)
        pieces = widget._prepare_segments()
        translated = [piece for piece in pieces if piece.get("translation")]
        assert "".join(piece["text"] for piece in translated) == "かな"
        assert all(not piece.get("reading") for piece in translated)
        small_font = widget._paint_font(widget.base_font, translated[0])
        assert small_font.pointSizeF() == 24
        assert QFontMetrics(small_font).height() < QFontMetrics(widget.base_font).height()
        image = QImage(widget.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        widget.render(image)
        assert image.save(str(tmp_path / "translation.png"))
    finally:
        widget.deleteLater()


def test_editor_generation_and_preview_skip_translation(editor, translation_provider, monkeypatch):
    from PySide6.QtWidgets import QMessageBox

    monkeypatch.setattr("ui.lrc_editor.set_reading_mode", lambda mode: None)
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    editor.line_text.setPlainText(r"君{\nt}翻訳")
    editor.generate_readings_for_current_timestamp()
    assert translation_provider == ["君"]
    tokens = editor._furigana_editor_tokens(editor.lines[0])
    assert next(token for token in tokens if token["text"] == "翻訳")["reading"] is None


def test_scrolling_refresh_uses_translation_cache(qapp, translation_provider):
    from lyrics.models import LyricLine
    from lyrics.scrolling_widget import ScrollingLyricsWidget

    line = LyricLine(1, "Original\nTranslation", translation_lines=[1])
    widget = ScrollingLyricsWidget()
    try:
        widget.resize(600, 400)
        widget.set_lyrics([line])
        row = widget.rows[0]
        assert row.translation_lines == [1]
        readings.build_furigana_segments(reading_source(line.text, line.translation_lines))
        widget.refresh_furigana_for_texts()
        assert next(part for part in row.segments if part["text"] == "Original")["reading"]
        assert next(part for part in row.segments if part["text"] == "Translation")["reading"] is None
    finally:
        widget.clear()
        widget.deleteLater()


@pytest.mark.parametrize("reading_position", ["above", "below"])
@pytest.mark.parametrize("romaji", [False, True])
def test_translation_row_has_no_empty_reading_band(qapp, translation_provider, reading_position, romaji):
    text = "君\n翻訳\n次"
    readings.build_furigana_segments(reading_source(text, [1]))
    widget = FuriganaWidget()
    try:
        widget.resize(600, 400)
        widget.set_font(library_font(qapp.font()).family(), size=32, ruby_size=16)
        widget.set_ruby_position(reading_position)
        widget.set_romaji_visible(romaji)
        widget.set_lyric(text, translation_lines=[1])
        pieces = widget._prepare_segments()
        original, translated, following = pieces
        small_metrics = QFontMetrics(widget._styled_font(widget.base_font, translated))
        small_height = widget.TOP_MARGIN + small_metrics.height() + widget.BOTTOM_MARGIN
        normal_height = widget._content_metrics()[3]
        expected_top = normal_height + widget.TOP_MARGIN
        assert translated["base_y"] - small_metrics.ascent() == expected_top
        assert translated["reading_y"] is None
        assert translated["romaji_y"] is None
        assert following["base_y"] - original["base_y"] == normal_height + small_height
        assert widget.minimumSizeHint().height() == normal_height * 2 + small_height
        assert widget.minimumHeight() == widget.minimumSizeHint().height()
    finally:
        widget.deleteLater()


def test_wrapped_translation_rows_use_compact_heights(qapp, translation_provider):
    text = "Original\n" + "Translation " * 20 + "\nFollowing"
    readings.build_furigana_segments(reading_source(text, [1]))
    widget = FuriganaWidget()
    try:
        widget.resize(240, 800)
        widget.set_font(library_font(qapp.font()).family(), size=32, ruby_size=16)
        widget.set_text_wrapping(True, -1)
        widget.set_lyric(text, translation_lines=[1])
        pieces = widget._prepare_segments()
        translated = [part for part in pieces if part.get("translation")]
        baselines = sorted({part["base_y"] for part in translated})
        assert len(baselines) > 1
        small_metrics = QFontMetrics(widget._styled_font(widget.base_font, translated[0]))
        small_height = widget.TOP_MARGIN + small_metrics.height() + widget.BOTTOM_MARGIN
        assert all(b - a == small_height for a, b in zip(baselines, baselines[1:]))
        assert widget.minimumSizeHint().height() == 2 * widget._content_metrics()[3] + len(baselines) * small_height
    finally:
        widget.deleteLater()
