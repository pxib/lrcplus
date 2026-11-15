"""Keep raw source editing and committed document history consistent."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QTextCursor
from PySide6.QtTest import QTest


def focus_source(editor, qapp):
    editor.show()
    editor.activateWindow()
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.source_editor.setFocus()
    qapp.processEvents()
    editor.source_editor.moveCursor(QTextCursor.MoveOperation.End)


@pytest.mark.parametrize("previous_edit", [False, True])
@pytest.mark.parametrize("control", ["toolbar", "keyboard"])
def test_source_undo_redo_preserves_structured_edits(editor, qapp, previous_edit, control):
    if previous_edit:
        editor.line_text.setPlainText("Structured edit")
    expected_lyric = editor.lines[0].text
    focus_source(editor, qapp)
    original = editor.source_editor.toPlainText()
    QTest.keyClicks(editor.source_editor, "[00:08.000]Draft")
    edited = editor.source_editor.toPlainText()
    assert edited != original and editor.undo_button.isEnabled()

    if control == "toolbar":
        QTest.mouseClick(editor.undo_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(editor.source_editor, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert editor.source_editor.toPlainText() == original
    assert editor.lines[0].text == expected_lyric
    assert editor.redo_button.isEnabled()
    assert not editor.undo_button.isEnabled()

    if control == "toolbar":
        QTest.mouseClick(editor.redo_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(editor.source_editor, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert editor.source_editor.toPlainText() == edited
    assert editor.lines[0].text == expected_lyric
    assert editor.undo_button.isEnabled() and not editor.redo_button.isEnabled()


@pytest.mark.parametrize("commit", ["reload", "leave_tab", "save"])
@pytest.mark.parametrize("control", ["toolbar", "keyboard"])
def test_committed_source_edits_use_document_history(editor, qapp, commit, control):
    focus_source(editor, qapp)
    QTest.keyClicks(editor.source_editor, "[00:08.000]Draft")
    if commit == "reload":
        assert editor.reload_model_from_source()
    elif commit == "save":
        assert editor.save_document()
    else:
        editor.tabs.setCurrentIndex(0)
    assert not editor._source_dirty
    assert editor.lines[-1].text == "Draft"

    target = (editor.source_editor if editor.tabs.currentWidget() is editor.source_editor.parentWidget()
              else editor.line_list)
    if control == "toolbar":
        QTest.mouseClick(editor.undo_button, Qt.MouseButton.LeftButton)
    else:
        target.setFocus()
        qapp.processEvents()
        QTest.keyClick(target, Qt.Key.Key_Z, Qt.KeyboardModifier.ControlModifier)
    assert [line.text for line in editor.lines] == ["Hello", "World"]
    assert editor.redo_button.isEnabled()
    if control == "toolbar":
        QTest.mouseClick(editor.redo_button, Qt.MouseButton.LeftButton)
    else:
        QTest.keyClick(target, Qt.Key.Key_Y, Qt.KeyboardModifier.ControlModifier)
    assert editor.lines[-1].text == "Draft"
    assert not editor._source_dirty


def test_new_source_typing_discards_document_redo(editor, qapp):
    editor.line_text.setPlainText("Structured edit")
    editor.undo()
    focus_source(editor, qapp)
    QTest.keyClicks(editor.source_editor, "[00:08.000]New branch")
    assert not editor._redo_stack
