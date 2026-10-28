from copy import deepcopy

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QLabel, QListWidget, QSpinBox

import ui.lrc_editor as editor_module
from lyrics.loader import load_lyrics

def test_consecutive_shifts_undo_and_redo_one_operation_at_a_time(editor):
    original = deepcopy(editor.lines)
    editor._apply_timing_shift(500, 0, 1)
    first_shift = deepcopy(editor.lines)
    editor._apply_timing_shift(250, 0, 0)
    second_shift = deepcopy(editor.lines)
    assert editor.undo_button.isEnabled()
    editor.undo()
    assert editor.lines == first_shift
    editor.undo()
    assert editor.lines == original
    assert not editor.undo_button.isEnabled()
    assert not editor.document_dirty
    editor.redo()
    assert editor.lines == first_shift
    editor.redo()
    assert editor.lines == second_shift
    assert editor.document_dirty and not editor.redo_button.isEnabled()


def test_new_edit_after_undo_discards_redo_branch(editor):
    editor._apply_timing_shift(500, 0, 0)
    editor.undo()
    editor._apply_timing_shift(100, 0, 0)
    editor.redo()
    assert editor.lines[0].start == 2.1
    assert not editor.redo_button.isEnabled()


def test_invalid_shift_changes_neither_document_nor_history(editor):
    before = deepcopy(editor.lines)
    with pytest.raises(ValueError):
        editor._apply_timing_shift(-2500, 0, 1)
    assert editor.lines == before
    assert not editor._undo_stack
    assert not editor.document_dirty


def test_manual_segment_edit_can_be_undone(editor):
    before = deepcopy(editor.lines)
    item = editor.segment_table.item(0, 0)
    assert item is not None
    item.setText("2.600")
    assert editor.lines[0].segments[0].start == 2.6
    editor.undo()
    assert editor.lines == before


def test_instant_fill_checkbox_can_be_undone(editor):
    checkbox = editor.segment_table.cellWidget(0, 4)
    checkbox.setChecked(True)
    assert editor.lines[0].segments[0].instant
    editor.undo()
    assert not editor.lines[0].segments[0].instant


def test_shift_undo_restores_selection_when_sidebar_is_sorted(editor):
    editor.sidebar_sort_mode = "timestamp_desc"
    editor._populate_lines(preserve_line_index=1)
    editor._apply_timing_shift(500, 1, 1)
    editor.undo()
    assert editor._current_line_index() == 1


def test_failed_save_keeps_editor_open_dirty_and_recoverable(editor, monkeypatch):
    editor.document_dirty = True
    editor._autosave_path.write_text("recoverable", encoding="utf-8")
    warnings = []

    def locked(*args, **kwargs):
        raise PermissionError("Locked destination")

    monkeypatch.setattr(editor_module, "atomic_write_text", locked)
    monkeypatch.setattr(editor_module.QMessageBox, "warning", lambda *args: warnings.append(args[2]))
    editor.save_and_close()
    assert editor.document_dirty
    assert editor.result_path is None
    assert editor._autosave_path.read_text(encoding="utf-8") == "recoverable"
    assert "Locked destination" in warnings[0]


def test_shifted_document_saves_and_reopens_with_backup(editor):
    original_file = "[00:00.000]Previous file\n"
    editor.lrc_path.write_text(original_file, encoding="utf-8")
    editor._apply_timing_shift(500, 0, 1)
    expected = deepcopy(editor.lines)
    editor.save_and_close()
    restored = load_lyrics(editor.result_path)
    assert [line.start for line in restored] == [line.start for line in expected]
    assert restored[0].segments[0].start == expected[0].segments[0].start
    assert restored[0].segments[0].end == expected[0].segments[0].end
    assert editor.lrc_path.with_name("song.lrcx.bak").read_text(encoding="utf-8") == original_file
    assert not editor.document_dirty


def test_timing_tools_parse_pending_source_edits_before_shifting(editor):
    editor.source_editor.setPlainText("[00:10.000]Raw source edit\n")
    assert editor._prepare_timing_tools()
    editor._apply_timing_shift(250, 0, 0)
    assert editor.lines[0].start == 10.25
    assert editor.lines[0].text == "Raw source edit"


def test_shift_dialog_previews_and_applies_range(editor, monkeypatch):
    def interact(dialog):
        first, last, delta = dialog.findChildren(QSpinBox)
        first.setValue(2)
        last.setValue(2)
        delta.setValue(250)
        apply_button = dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply)
        assert apply_button.isEnabled()
        assert any("5.250" in label.text() for label in dialog.findChildren(QLabel))
        apply_button.click()
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_timing_shift_dialog()
    assert [line.start for line in editor.lines] == [2, 5.25]


def test_validation_dialog_can_navigate_to_affected_line(editor, monkeypatch):
    editor.lines[1].end = 4

    def interact(dialog):
        results = dialog.findChild(QListWidget)
        assert results.count() >= 1
        assert results.item(0).text().startswith("Line 2:")
        results.itemDoubleClicked.emit(results.item(0))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(QDialog, "exec", interact)
    editor.show_timing_validation()
    assert editor._current_line_index() == 1
