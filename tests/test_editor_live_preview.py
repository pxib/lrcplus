"""The selected lyric preview updates without changing the document or playback."""

from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest

from lyrics.models import LyricLine, LyricSegment


def test_missing_display_font_uses_bundled_face_without_changing_preference(make_lyrics_editor):
    from core.fonts import library_font
    from core.lyrics_settings import save_global_lyrics_settings
    from core.settings import get_effective_lyrics_settings

    missing = "LRCPlus Missing Preview Font 8491"
    save_global_lyrics_settings({"font_family": missing})
    editor = make_lyrics_editor(karaoke=False)
    fallback = library_font(editor.font()).family()
    preview = editor.lyrics_renderer_preview
    assert preview.base_font_family == missing
    assert preview.base_font.family() == fallback
    assert preview.reading_font.family() == fallback
    assert get_effective_lyrics_settings()["font_family"] == missing


@pytest.mark.parametrize("karaoke", [False, True])
def test_preview_is_available_on_first_selection_without_changing_document(make_lyrics_editor, qapp, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    original = deepcopy(editor.lines)
    editor.show()
    qapp.processEvents()
    assert editor.lyrics_live_preview_panel.isVisible()
    assert editor.lyrics_renderer_preview.lyric == "Hello"
    assert editor.lyrics_preview_stack.currentWidget() is editor.lyrics_preview_scroll
    editor.line_list.setCurrentRow(1)
    assert editor.lyrics_renderer_preview.lyric == "World"
    assert editor.lines == original
    assert not editor.document_dirty and not editor._undo_stack


def test_live_text_formatting_translation_and_undo(make_lyrics_editor):
    editor = make_lyrics_editor(karaoke=False)
    editor.line_text.setPlainText(r"> **Hello**{\nt}Translation")
    preview = editor.lyrics_renderer_preview
    assert preview.lyric == "Hello\nTranslation"
    assert preview.is_blockquote
    assert preview.translation_lines == [1]
    assert preview.style_spans == editor.lines[0].style_spans
    assert preview.style_spans
    assert any(piece.get("display_layer") == "translation" for piece in preview._prepare_segments())
    editor.undo()
    assert preview.lyric == "Hello" and not preview.is_blockquote
    assert not preview.translation_lines


def test_reading_edits_and_background_completion_update_preview(editor, monkeypatch):
    line = LyricLine(1, "猫と犬", [LyricSegment(1, "猫と犬", ruby_spans=[(0, 1, "ねこ")])])
    editor.lines = [line]
    tokens = [{"text": "猫"}, {"text": "と"}, {"text": "犬"}]
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: deepcopy(tokens))
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: deepcopy(tokens))
    editor._populate_lines(preserve_line_index=0)
    assert any(piece.get("reading") == "ねこ" for piece in editor.lyrics_renderer_preview.segments)
    line.segments[0].ruby_spans = [(0, 1, "にゃん")]
    editor._populate_furigana_editor(line)
    assert any(piece.get("reading") == "にゃん" for piece in editor.lyrics_renderer_preview.segments)
    tokens[2]["reading"] = "いぬ"
    editor._on_reading_generation_finished(editor._reading_generation_epoch, 0, True)
    assert any(piece.get("reading") == "いぬ" for piece in editor.lyrics_renderer_preview.segments)


def test_playback_updates_highlight_with_offset_without_rebuilding_or_following_other_lines(editor, monkeypatch):
    preview = editor.lyrics_renderer_preview
    editor.lyrics_offset.setValue(500)
    rebuilt = []
    monkeypatch.setattr(preview, "set_segments", lambda *args, **kwargs: rebuilt.append(True))
    editor.preview_position_changed(3000)
    assert preview.karaoke_position == 2.5
    editor.preview_position_changed(6000)
    assert preview.karaoke_position == 5.5
    assert preview.lyric == "Hello" and editor._current_line_index() == 0
    assert rebuilt == []


def test_smooth_highlight_respects_playback_speed(editor, monkeypatch):
    editor.player = SimpleNamespace(media_player=SimpleNamespace(playbackRate=lambda: 2.0))
    editor._display_position_playing = True
    editor._display_position_anchor_ms = 2000
    editor._display_position_anchor_monotonic = 10
    monkeypatch.setattr("ui.lyrics_editor_playback.time.monotonic", lambda: 10.25)
    editor._update_precise_playback_display()
    assert editor.lyrics_renderer_preview.karaoke_position == 2.5
    editor.player = None


def test_empty_lyric_and_empty_document_clear_old_preview(make_lyrics_editor):
    editor = make_lyrics_editor(karaoke=False)
    editor.line_text.clear()
    assert editor.lyrics_renderer_preview.lyric == ""
    assert editor.lyrics_preview_stack.currentWidget() is editor.lyrics_preview_empty
    assert editor.lyrics_preview_empty.text() == "This lyric is empty."
    editor.lines = []
    editor._populate_lines()
    editor._clear_editor_views()
    assert editor.lyrics_renderer_preview.lyric == ""
    assert editor.lyrics_preview_empty.text() == "Select a lyric to preview."


def test_preview_cannot_edit_readings_and_expand_does_not_modify_document(editor, monkeypatch, qapp):
    original = deepcopy(editor.lines)
    calls = []
    preview = editor.lyrics_renderer_preview
    monkeypatch.setattr(preview, "_edit_reading_for_segment", lambda *args: calls.append(True))
    editor.show()
    qapp.processEvents()
    QTest.mouseDClick(preview, Qt.MouseButton.LeftButton, pos=QPoint(20, 20))
    assert calls == []
    compact_height = editor.line_text.height()
    editor.expand_lyric_text_button.click()
    assert editor.line_text.height() > compact_height
    editor.expand_lyric_text_button.click()
    assert editor.line_text.height() == compact_height
    assert editor.lines == original
    assert not editor.document_dirty


def test_closing_editor_stops_preview_reading_workers(editor):
    editor.close()
    assert not editor.lyrics_renderer_preview._reading_timer.isActive()
    assert editor.lyrics_renderer_preview._furigana_future is None


def test_preview_leaves_generation_to_the_editor_when_cache_is_cold(editor, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: None)
    monkeypatch.setattr("ui.lrc_editor.get_cached_furigana_segments", lambda text: None)
    editor.line_text.setPlainText("Uncached text")
    preview = editor.lyrics_renderer_preview
    assert preview.lyric == "Uncached text"
    assert preview._furigana_future is None
    assert not preview._reading_timer.isActive()
