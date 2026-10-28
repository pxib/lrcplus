from copy import deepcopy
import json

import pytest
from PySide6.QtCore import QEvent, QSignalBlocker, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLineEdit, QMessageBox

from lyrics.models import LyricLine, LyricSegment
from lyrics.editor_source import parse_editor_source
import ui.lrc_editor as editor_module


def set_text_without_signals(widget, text):
    blocker = QSignalBlocker(widget)
    widget.setPlainText(text)
    del blocker


def test_text_editing_works_without_karaoke_plugin(make_lyrics_editor):
    editor = make_lyrics_editor(karaoke=False)
    set_text_without_signals(editor.line_text, "Edited without karaoke")
    editor.line_details_changed()
    assert editor.lines[0].text == "Edited without karaoke"
    assert editor.document_dirty
    editor.undo()
    assert editor.lines[0].text == "Hello"


def test_changing_timestamp_preserves_multiline_text(editor):
    editor.lines[0] = LyricLine(2, "First\nSecond", [LyricSegment(2, "First\nSecond")])
    editor.load_selected_line(0)
    editor.line_time.setValue(3)
    assert editor.lines[0].text == "First\nSecond"
    assert editor.lines[0].segments[0].start == 3
    assert "Multiline lyrics" in editor._lrcx_only_features()


@pytest.mark.parametrize("action", ["button", "escape", "title_bar"])
def test_all_close_paths_allow_cancelling_without_losing_recovery(editor, qapp, monkeypatch, action):
    editor.show()
    editor._apply_timing_shift(100, 0, 0)
    editor._autosave_if_needed()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    if action == "button":
        editor.reject()
    elif action == "escape":
        QTest.keyClick(editor, Qt.Key.Key_Escape)
    else:
        editor.close()
    qapp.processEvents()
    assert editor.isVisible()
    assert editor.document_dirty and editor._autosave_path.exists()


def test_discard_stops_autosave_and_removes_recovery(editor, monkeypatch):
    editor.document_dirty = True
    editor._autosave_if_needed()
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Discard)
    editor.reject()
    assert not editor._autosave_path.exists()
    assert not editor._autosave_timer.isActive()
    assert editor._closed


def test_save_keeps_editor_open_and_undo_marks_saved_document_dirty(editor):
    editor.show()
    original = deepcopy(editor.lines)
    editor._apply_timing_shift(250, 0, 0)
    assert editor.save_document()
    assert editor.isVisible() and not editor.document_dirty
    editor.undo()
    assert editor.lines == original
    assert editor.document_dirty and editor.save_button.isEnabled()
    editor.redo()
    assert not editor.document_dirty


def test_save_choice_during_close_keeps_window_open_if_writing_fails(editor, monkeypatch):
    editor.show()
    editor.document_dirty = True
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Save)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)

    def fail(*args, **kwargs):
        raise PermissionError("locked")

    monkeypatch.setattr(editor_module, "atomic_write_text", fail)
    editor.reject()
    assert editor.isVisible() and editor.document_dirty


def test_invalid_source_is_rejected_without_touching_model_or_history(editor, monkeypatch):
    original = deepcopy(editor.lines)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    editor.source_editor.setPlainText("[00:02.000]Valid\nUnfinished source line\n")
    history = len(editor._undo_stack)
    assert not editor.reload_model_from_source()
    assert editor.lines == original and editor._source_dirty
    assert len(editor._undo_stack) == history
    assert "Source line 2" in warnings[0]


def test_reload_applies_offset_and_generation_mode_and_supports_undo(editor):
    original_mode = editor._current_generation_mode()
    editor.source_editor.setPlainText("[format:LRCX]\n[offset:350]\n[g:pinyin]\n[00:10.000]Changed\n")
    assert editor.reload_model_from_source()
    assert editor.lines[0].start == 10
    assert editor.lyrics_offset_ms == 350 and editor.lyrics_offset.value() == 350
    assert editor._current_generation_mode() == "pinyin"
    editor.undo()
    assert editor.lyrics_offset_ms == 0
    assert editor._current_generation_mode() == original_mode


def test_leaving_source_tab_applies_pending_edits(editor):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.source_editor.setPlainText("[00:12.000]From Source\n")
    editor.tabs.setCurrentIndex(0)
    assert not editor._source_dirty
    assert editor.lines[0].text == "From Source"
    assert editor.line_time.value() == 12


def test_invalid_source_keeps_source_tab_active(editor, monkeypatch):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.source_editor.setPlainText("unfinished")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: None)
    editor.tabs.setCurrentIndex(0)
    assert editor.tabs.currentWidget() is editor.source_editor.parentWidget()
    assert editor._source_dirty


def test_refresh_source_does_not_overwrite_pending_text_without_confirmation(editor, monkeypatch):
    editor.source_editor.setPlainText("unfinished")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.No)
    editor.sync_source_from_model()
    assert editor.source_editor.toPlainText() == "unfinished"


def test_autosave_recovers_unfinished_source_and_last_structured_model(editor, make_lyrics_editor, monkeypatch):
    editor.source_editor.setPlainText("[00:02.000]Edited\nunfinished")
    editor._autosave_if_needed()
    payload = json.loads(editor._autosave_path.read_text(encoding="utf-8"))
    assert payload["source"].endswith("unfinished")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    recovered = make_lyrics_editor(path=editor.lrc_path, populate=False)
    assert recovered.source_editor.toPlainText() == payload["source"]
    assert recovered._source_dirty and recovered.document_dirty
    assert recovered.lines[0].text == "Hello"


def test_legacy_lrcx_autosave_still_recovers(editor, make_lyrics_editor, monkeypatch):
    editor._autosave_path.write_text("[format:LRCX]\n[00:07.000]Legacy\n", encoding="utf-8")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    recovered = make_lyrics_editor(path=editor.lrc_path, populate=False)
    assert recovered.lines[0].text == "Legacy"


def test_plain_txt_source_does_not_gain_synthetic_timestamp_text(tmp_path, make_lyrics_editor):
    path = tmp_path / "plain.txt"
    path.write_text("First\nSecond\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False, karaoke=False)
    assert editor.source_editor.toPlainText() == "First\nSecond\n"
    editor.source_editor.setPlainText("Changed\nSecond\n")
    assert editor.reload_model_from_source()
    assert [line.text for line in editor.lines] == ["Changed", "Second"]


def test_lyric_search_wraps_and_follows_sidebar_sorting(editor):
    editor.lines = [LyricLine(2, "Hello first"), LyricLine(5, "Elsewhere"), LyricLine(8, "HELLO last")]
    editor._populate_lines(preserve_line_index=0)
    editor.lyric_search.setText("hello")
    editor.find_next_lyric()
    assert editor._current_line_index() == 2
    assert editor.lyric_search_status.text() == "Match 2 of 2"
    editor.find_next_lyric()
    assert editor._current_line_index() == 0
    editor.find_previous_lyric()
    assert editor._current_line_index() == 2
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor.find_next_lyric()
    assert editor._current_line_index() == 0


def test_search_no_matches_does_not_change_selection(editor):
    editor.lyric_search.setText("absent")
    editor.find_next_lyric()
    assert editor._current_line_index() == 0
    assert editor.lyric_search_status.text() == "No matching lyrics."


def test_karaoke_shortcuts_do_not_intercept_text_cells_or_modified_keys(editor, monkeypatch):
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    inline = QLineEdit(editor.segment_table.viewport())
    calls = []
    monkeypatch.setattr(editor, "_handle_karaoke_shortcut", lambda key: calls.append(key))
    editor.segment_table.setCurrentCell(0, 2)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_E, Qt.KeyboardModifier.NoModifier, "e")
    assert not editor.eventFilter(inline, event)
    editor.segment_table.setCurrentCell(0, 0)
    modified = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_E, Qt.KeyboardModifier.ControlModifier)
    assert not editor.eventFilter(inline, modified)
    assert editor.eventFilter(inline, event)
    assert calls == [Qt.Key.Key_E]


def test_space_in_karaoke_timestamp_grid_toggles_playback(editor, monkeypatch):
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    calls = []
    monkeypatch.setattr(editor, "toggle_preview", lambda: calls.append(True))
    editor.segment_table.setCurrentCell(0, 0)
    event = QKeyEvent(QEvent.Type.KeyPress, Qt.Key.Key_Space, Qt.KeyboardModifier.NoModifier, " ")
    assert editor.eventFilter(editor.segment_table, event)
    assert calls == [True]


@pytest.mark.parametrize("text", ["[00:70.000]Invalid", "[offset:nan]\n[00:01.000]Text", "[00:01.000][00:03.000]Repeated"])
def test_source_parser_reports_invalid_timed_input(text):
    with pytest.raises(ValueError, match="Source line"):
        parse_editor_source(text, ".lrcx")


def test_metadata_survives_structured_edits_source_reload_and_save(tmp_path, make_lyrics_editor):
    path = tmp_path / "tagged.lrcx"
    path.write_text("[format:LRCX]\n[ar:Artist]\n[ti:Title]\n[custom:Keep me]\n[00:02.000]Text\n", encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False, karaoke=False)
    editor._apply_timing_shift(200, 0, 0)
    editor.sync_source_from_model()
    assert "[ar:Artist]" in editor.source_editor.toPlainText()
    assert editor.reload_model_from_source()
    assert editor.save_document()
    contents = path.read_text(encoding="utf-8")
    for tag in ("[ar:Artist]", "[ti:Title]", "[custom:Keep me]"):
        assert contents.count(tag) == 1


@pytest.mark.parametrize("column,text", [(0, "not a time"), (1, "00:01.000")])
def test_invalid_segment_timestamp_restores_display_without_creating_history(editor, monkeypatch, column, text):
    before = deepcopy(editor.lines)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    item = editor.segment_table.item(0, column)
    original_text = item.text()
    item.setText(text)
    assert editor.lines == before
    assert item.text() == original_text
    assert not editor._undo_stack and warnings


def test_changing_reading_mode_is_undoable(editor):
    original_mode = editor._current_generation_mode()
    next_index = next(index for index in range(editor.reading_mode_combo.count())
                      if editor.reading_mode_combo.itemData(index) != original_mode)
    editor.reading_mode_combo.setCurrentIndex(next_index)
    changed_mode = editor._current_generation_mode()
    assert changed_mode != original_mode
    editor.undo()
    assert editor._current_generation_mode() == original_mode
    assert editor.reading_mode_combo.currentData() == original_mode


def test_mark_playback_time_advances_to_correct_source_line_in_sorted_sidebar(editor):
    from types import SimpleNamespace

    editor.player = SimpleNamespace(media_player=SimpleNamespace(position=lambda: 3000))
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor._select_line_index(0)
    editor.mark_current_line_playback_time()
    assert editor.lines[0].start == 3
    assert editor._current_line_index() == 1


def test_refreshing_rich_lrc_document_uses_lossless_lrcx_source(editor):
    editor.lrc_path = editor.lrc_path.with_suffix(".lrc")
    editor.sync_source_from_model()
    assert editor._source_suffix == ".lrcx"
    assert "[format:LRCX]" in editor.source_editor.toPlainText()
    assert editor.reload_model_from_source()
    assert editor.lines[0].has_karaoke
    assert editor.lines[0].segments[0].start == 2.2


def test_clean_txt_save_and_close_does_not_rewrite_plain_text(tmp_path, make_lyrics_editor):
    path = tmp_path / "original.txt"
    original = "First line\nSecond line\n"
    path.write_text(original, encoding="utf-8")
    editor = make_lyrics_editor(path=path, populate=False, karaoke=False)
    editor.save_and_close()
    assert path.read_text(encoding="utf-8") == original


def test_typing_spaces_in_lyrics_does_not_toggle_playback(editor, qapp, monkeypatch):
    editor.show()
    editor.activateWindow()
    editor.line_text.setFocus()
    qapp.processEvents()
    calls = []
    monkeypatch.setattr(editor, "toggle_preview", lambda: calls.append(True))
    editor.line_text.selectAll()
    QTest.keyClicks(editor.line_text, "New lyric with spaces")
    assert editor.line_text.toPlainText() == "New lyric with spaces"
    assert calls == []


def test_ctrl_s_saves_and_keeps_editor_open(editor, qapp):
    editor.show()
    editor.activateWindow()
    editor._apply_timing_shift(100, 0, 0)
    qapp.processEvents()
    QTest.keyClick(editor, Qt.Key.Key_S, Qt.KeyboardModifier.ControlModifier)
    assert editor.lrc_path.exists()
    assert editor.isVisible() and not editor.document_dirty
