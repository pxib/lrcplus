"""User-visible selection, preview and Undo workflows for structural edits."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QCheckBox, QComboBox, QDialog, QDialogButtonBox, QListWidget, QSpinBox, QTextEdit

from lyrics.formatting import style_at
from lyrics.loader import load_lyrics
from lyrics.models import LyricLine, LyricSegment


@pytest.fixture
def batch_editor(editor):
    editor.lines = [LyricLine(1 + index * 2, f"Line {index + 1}", [
        LyricSegment(1 + index * 2, f"Line {index + 1}", end=2 + index * 2),
    ], has_karaoke=True) for index in range(5)]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    return editor


def apply_dialog(dialog):
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply).click()
    return QDialog.DialogCode.Accepted


def test_shift_and_control_click_select_multiple_lines(batch_editor, qapp):
    editor = batch_editor
    editor.show()
    editor.activateWindow()
    qapp.processEvents()
    QTest.mouseClick(editor.line_list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ShiftModifier,
                     editor.line_list.visualItemRect(editor.line_list.item(2)).center())
    assert editor._selected_line_indices() == [0, 1, 2]
    assert editor.bulk_line_controls.isVisible()
    assert "3 lines selected" in editor.line_selection_label.text()
    QTest.mouseClick(editor.line_list.viewport(), Qt.MouseButton.LeftButton, Qt.KeyboardModifier.ControlModifier,
                     editor.line_list.visualItemRect(editor.line_list.item(4)).center())
    assert editor._selected_line_indices() == [0, 1, 2, 4]


def test_selection_survives_sidebar_sort_and_bulk_shift_undo(batch_editor):
    editor = batch_editor
    editor._set_line_selection([1, 3], current=3)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    assert editor._selected_line_indices() == [1, 3]
    assert editor._current_line_index() == 3
    original = deepcopy(editor.lines)
    editor._apply_selected_timing_shift(500, [1, 3])
    assert [line.start for line in editor.lines] == [1, 3.5, 5, 7.5, 9]
    assert editor._selected_line_indices() == [1, 3]
    assert len(editor._undo_stack) == 1
    editor.undo()
    assert editor.lines == original and editor._selected_line_indices() == [1, 3]
    editor.redo()
    assert [line.start for line in editor.lines] == [1, 3.5, 5, 7.5, 9]
    assert editor._selected_line_indices() == [1, 3]


def test_invalid_bulk_shift_is_atomic(batch_editor):
    editor = batch_editor
    before = deepcopy(editor.lines)
    with pytest.raises(ValueError):
        editor._apply_selected_timing_shift(-4000, [0, 3])
    assert editor.lines == before and not editor._undo_stack
    assert not editor.document_dirty


def test_selected_shift_dialog_previews_exact_lines_in_source_order(batch_editor, monkeypatch):
    editor = batch_editor
    editor._set_line_selection([1, 3], 3)
    editor.sidebar_sort_combo.setCurrentIndex(2)

    def interact(dialog):
        preview = dialog.findChild(QListWidget)
        assert preview.count() == 2
        assert preview.item(0).text().startswith("2. [00:03.000]")
        assert preview.item(1).text().startswith("4. [00:07.000]")
        dialog.findChild(QSpinBox).setValue(250)
        return apply_dialog(dialog)

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_timing_shift_dialog()
    assert [line.start for line in editor.lines] == [1, 3.25, 5, 7.25, 9]


@pytest.mark.parametrize("direction,expected,selection", [
    (-1, ["Line 2", "Line 1", "Line 4", "Line 3", "Line 5"], [0, 2]),
    (1, ["Line 1", "Line 3", "Line 2", "Line 5", "Line 4"], [2, 4]),
])
def test_bulk_move_preserves_source_order_and_selection(batch_editor, direction, expected, selection):
    editor = batch_editor
    editor._set_line_selection([1, 3], 3)
    original = deepcopy(editor.lines)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor.move_line(direction)
    assert [line.text for line in editor.lines] == expected
    assert editor._selected_line_indices() == selection
    editor.undo()
    assert editor.lines == original and editor._selected_line_indices() == [1, 3]


def test_bulk_format_preview_apply_and_undo_keep_timing(batch_editor, monkeypatch):
    editor = batch_editor
    editor._set_line_selection([0, 2], 0)
    before = deepcopy(editor.lines)

    def interact(dialog):
        assert dialog.findChild(QListWidget).count() == 2
        for check in dialog.findChildren(QCheckBox):
            check.setChecked(check.text() in {"Bold", "Italic"})
        return apply_dialog(dialog)

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_bulk_format_dialog()
    assert style_at(editor.lines[0].style_spans, 0)[:2] == (True, True)
    assert style_at(editor.lines[2].style_spans, 0)[:2] == (True, True)
    assert not editor.lines[1].style_spans
    assert editor.lines[0].segments == before[0].segments
    assert editor._selected_line_indices() == [0, 2]
    assert editor.save_document()
    assert style_at(load_lyrics(editor.lrc_path)[2].style_spans, 0)[:2] == (True, True)
    editor.undo()
    assert editor.lines == before and editor._selected_line_indices() == [0, 2]


@pytest.mark.parametrize("accept", [False, True])
def test_bulk_delete_previews_and_can_cancel_or_undo(batch_editor, monkeypatch, accept):
    editor = batch_editor
    editor._set_line_selection([1, 3], 3)
    before = deepcopy(editor.lines)

    def interact(dialog):
        assert dialog.findChild(QListWidget).count() == 2
        return apply_dialog(dialog) if accept else QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.delete_line()
    if accept:
        assert [line.text for line in editor.lines] == ["Line 1", "Line 3", "Line 5"]
        editor.undo()
        assert editor._selected_line_indices() == [1, 3]
    else:
        assert not editor._undo_stack and editor._selected_line_indices() == [1, 3]
    assert editor.lines == before


def test_text_correction_keeps_karaoke_marks_only_affected_chunk_and_is_undoable(editor):
    editor.lines[0] = LyricLine(1, "Hello world", [
        LyricSegment(1.2, "Hello ", end=2, ruby_spans=[(0, 5, "first")]),
        LyricSegment(2.5, "world", end=3.5, ruby="second", ruby_source="manual"),
    ], end=4, has_karaoke=True)
    editor._populate_lines(preserve_line_index=0)
    before = deepcopy(editor.lines)
    editor.line_text.setPlainText("Hello worlds")
    line = editor.lines[0]
    assert line.has_karaoke
    assert [(s.start, s.end) for s in line.segments] == [(1.2, 2), (2.5, 3.5)]
    assert not line.segments[0].needs_timing_review and line.segments[1].needs_timing_review
    assert "2" in editor.text_timing_notice.text()
    assert editor.segment_table.item(1, 0).toolTip()
    editor.undo()
    assert editor.lines == before and not editor.lines[0].segments[1].needs_timing_review
    editor.redo()
    assert editor.lines[0].segments[1].needs_timing_review
    editor.segment_table.item(1, 0).setText("00:02.600")
    assert not editor.lines[0].segments[1].needs_timing_review
    assert not editor.segment_table.item(1, 0).toolTip()


def test_deleting_first_repeated_word_keeps_the_second_words_timestamp(editor):
    editor.lines[0] = LyricLine(1, "one one", [
        LyricSegment(1, "one ", end=2), LyricSegment(3, "one", end=4),
    ], end=4, has_karaoke=True)
    editor._populate_lines(preserve_line_index=0)
    cursor = editor.line_text.textCursor()
    cursor.setPosition(0)
    cursor.setPosition(4, QTextCursor.MoveMode.KeepAnchor)
    cursor.removeSelectedText()
    assert editor.lines[0].text == "one"
    assert len(editor.lines[0].segments) == 1
    assert editor.lines[0].segments[0].start == 3


@pytest.mark.parametrize("raw,prefix,left,right", [
    ("**AB**CD", "**AB", "AB", "CD"),
    ("A😀BC", "A😀", "A😀", "BC"),
    (r"AB{\n}CD", r"AB{\n}", "AB\n", "CD"),
])
def test_split_at_cursor_understands_styles_newline_markers_and_unicode(editor, monkeypatch, raw, prefix, left, right):
    editor.line_text.setPlainText(raw)
    before = deepcopy(editor.lines)
    cursor = editor.line_text.textCursor()
    cursor.setPosition(len(prefix.encode("utf-16-le")) // 2)
    editor.line_text.setTextCursor(cursor)

    def interact(dialog):
        preview = dialog.findChild(QTextEdit).toPlainText()
        assert left in preview and right in preview
        return apply_dialog(dialog)

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.split_line_button.click()
    assert [line.text for line in editor.lines[:2]] == [left, right]
    assert editor._current_line_index() == 1
    assert editor.save_document()
    assert [line.text for line in load_lyrics(editor.lrc_path)[:2]] == [left, right]
    editor.undo()
    assert editor.lines == before


def test_merge_selected_lines_uses_source_order_after_sidebar_sort(batch_editor, monkeypatch):
    editor = batch_editor
    editor._set_line_selection([1, 2], 2)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    original = deepcopy(editor.lines)

    def interact(dialog):
        assert dialog.findChild(QListWidget).item(0).text().startswith("2.")
        dialog.findChild(QComboBox).setCurrentIndex(1)
        return apply_dialog(dialog)

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.merge_lines_button.click()
    assert [line.text for line in editor.lines] == ["Line 1", "Line 2\nLine 3", "Line 4", "Line 5"]
    assert editor.lines[1].segments[0].start == 3
    assert editor.lines[1].segments[-1].start == 5
    assert editor._selected_line_indices() == [1]
    assert editor.save_document()
    assert load_lyrics(editor.lrc_path)[1].text == "Line 2\nLine 3"
    editor.undo()
    assert editor.lines == original and editor._selected_line_indices() == [1, 2]


def test_split_and_merge_preserve_timing_without_karaoke_plugin(make_lyrics_editor, monkeypatch):
    editor = make_lyrics_editor(karaoke=False)
    before = deepcopy(editor.lines)
    cursor = editor.line_text.textCursor()
    cursor.setPosition(2)
    editor.line_text.setTextCursor(cursor)
    monkeypatch.setattr(QDialog, "exec", apply_dialog)
    editor.split_current_line()
    assert [line.text for line in editor.lines] == ["He", "llo", "World"]
    assert editor.lines[0].has_karaoke and editor.lines[1].has_karaoke
    editor._set_line_selection([0, 1], 0)

    def merge(dialog):
        dialog.findChild(QComboBox).setCurrentIndex(2)
        return apply_dialog(dialog)

    monkeypatch.setattr(QDialog, "exec", merge)
    editor.merge_selected_lines()
    assert editor.lines[0].text == "Hello"
    assert editor.save_document()
    assert load_lyrics(editor.lrc_path)[0].text == "Hello"
    editor.undo()
    editor.undo()
    assert editor.lines == before


@pytest.mark.parametrize("action", ["split", "merge"])
def test_cancelling_line_surgery_does_not_change_document_or_history(editor, monkeypatch, action):
    before = deepcopy(editor.lines)
    cursor = editor.line_text.textCursor()
    cursor.setPosition(2)
    editor.line_text.setTextCursor(cursor)
    monkeypatch.setattr(QDialog, "exec", lambda self: QDialog.DialogCode.Rejected)
    if action == "split":
        editor.split_current_line()
    else:
        editor.merge_selected_lines()
    assert editor.lines == before and not editor._undo_stack
    assert not editor.document_dirty
