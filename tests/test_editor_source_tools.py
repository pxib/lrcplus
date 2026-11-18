"""Live source diagnostics, syntax colors and navigation without model edits."""

from copy import deepcopy

import pytest
from PySide6.QtCore import QPoint, QPointF, Qt
from PySide6.QtGui import QColor, QPalette, QTextCursor, QWheelEvent
from PySide6.QtTest import QSignalSpy, QTest

from lyrics.editor_source import effective_source_suffix, parse_editor_source, validate_editor_source


def test_source_validation_collects_all_lines_and_matches_save_error():
    source = "[offset:nan]\n  [00:70.000]Bad seconds\nUnfinished line\n[00:03.000]Valid"
    issues = validate_editor_source(source, ".lrcx")
    assert [issue.line_number for issue in issues] == [1, 2, 3]
    assert issues[1].column == 2
    assert issues[1].length == len("[00:70.000]")
    with pytest.raises(ValueError) as error:
        parse_editor_source(source, ".lrcx")
    assert str(error.value) == f"Source line 1: {issues[0].message}"


@pytest.mark.parametrize("source,message", [
    ("[00:01.000]<00:bad>Word", "invalid karaoke timestamp"),
    ("[00:01.000]<00:01.500", "invalid karaoke timestamp"),
    ("[00:01.000]{漢字|かんじ", "unclosed reading"),
    ("[00:01.000][00:02.000]Word", "separate LRCX line"),
])
def test_invalid_extended_syntax_is_reported_and_rejected(source, message):
    issues = validate_editor_source(source, ".lrcx")
    assert message in issues[0].message
    with pytest.raises(ValueError, match=message):
        parse_editor_source(source, ".lrcx")


@pytest.mark.parametrize("source,suffix", [
    ("[ar:Artist]\n[offset:350]\n[00:01.000]<00:01.500>{世界|せかい}" + r"{\nt}Translation", ".lrcx"),
    ("[00:01.000][00:02.000]Repeated", ".lrc"),
    ("[00:01.000]1 < 3 and *unpaired text {braces}", ".lrcx"),
    ("Unfinished <00:bad> and any plain text", ".txt"),
])
def test_valid_syntax_and_plain_text_are_not_flagged(source, suffix):
    assert not validate_editor_source(source, suffix)


def test_lrc_diagnostics_use_same_automatic_extended_format_as_reload():
    source = "[format:LRCX]\n[00:01.000][00:02.000]Word"
    suffix = effective_source_suffix(source, ".lrc")
    assert suffix == ".lrcx"
    assert "separate LRCX line" in validate_editor_source(source, suffix)[0].message


def test_live_issues_clear_on_correction_and_do_not_apply_source(editor, qapp):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.show()
    before = deepcopy(editor.lines)
    field = editor.source_editor
    changed = QSignalSpy(field.diagnosticsChanged)
    field.setPlainText("[00:01.000]Valid\n  [00:99.000]Invalid")
    assert changed.wait(1000)
    assert len(field.diagnostics) == 1
    assert editor.source_issues_list.isVisible()
    assert "Line 2:" in editor.source_issues_list.item(0).text()
    assert len(field.extraSelections()) == 2  # Current row and error underline.
    QTest.mouseClick(editor.source_issues_list.viewport(), Qt.MouseButton.LeftButton,
                     pos=editor.source_issues_list.visualItemRect(editor.source_issues_list.item(0)).center())
    assert field.textCursor().blockNumber() == 1
    assert field.textCursor().selectedText() == "[00:99.000]"
    assert editor.source_position_label.text() == f"Ln 2, Col {len('  [00:99.000]') + 1}"
    assert editor.lines == before
    field.setPlainText("[00:01.000]Valid\n[00:02.000]Corrected")
    assert changed.wait(1000)
    assert not field.diagnostics
    assert not editor.source_issues_list.isVisible()
    assert len(field.extraSelections()) == 1
    assert editor.lines == before and editor._source_dirty
    assert editor.reload_model_from_source()
    assert [line.text for line in editor.lines] == ["Valid", "Corrected"]


def test_issue_navigation_and_column_indicator_handle_emoji(editor):
    field = editor.source_editor
    field.setPlainText("[00:01.000]😀<00:bad>Word")
    field.refresh_diagnostics()
    issue = field.diagnostics[0]
    assert issue.column == len("[00:01.000]😀")
    field.go_to_issue(issue)
    assert field.textCursor().selectedText() == "<00:bad>"
    cursor = field.textCursor()
    cursor.clearSelection()
    cursor.setPosition(len("[00:01.000]😀".encode("utf-16-le")) // 2)
    field.setTextCursor(cursor)
    assert editor.source_position_label.text() == "Ln 1, Col 13"


def test_syntax_colors_preserve_content_and_native_undo(editor):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    field = editor.source_editor
    original = field.toPlainText()
    field._preview_fonts.rehighlight()
    field.refresh_diagnostics()
    assert not editor.document_dirty
    assert not editor._undo_stack
    cursor = field.textCursor()
    cursor.movePosition(QTextCursor.MoveOperation.End)
    cursor.insertText("\n[00:09.000]Additional text")
    edited = field.toPlainText()
    field._preview_fonts.rehighlight()
    field.refresh_diagnostics()
    assert field.toPlainText() == edited
    editor.undo()
    assert field.toPlainText() == original
    editor.redo()
    assert field.toPlainText() == edited


def test_timestamp_and_karaoke_tokens_have_distinct_syntax_colors(editor):
    field = editor.source_editor
    field.setPlainText("[00:01.000]😀<00:01.500>{世界|せかい}" + r"{\nt}Translation")
    field._preview_fonts.rehighlight()
    formats = field.document().begin().layout().formats()
    text = field.toPlainText()

    def color_at(token):
        position = len(text[:text.index(token)].encode("utf-16-le")) // 2
        return next(run.format.foreground().color().name() for run in formats
                    if run.start <= position < run.start + run.length)

    assert len({color_at("[00:"), color_at("<00:"), color_at("{世界"), color_at(r"{\nt}")}) == 4


def test_line_number_gutter_grows_and_scrolls_with_document(editor, qapp):
    field = editor.source_editor
    editor.tabs.setCurrentWidget(field.parentWidget())
    editor.show()
    qapp.processEvents()
    old_width = field.line_numbers.width()
    field.setPlainText("\n".join(f"[00:01.000]Line {i}" for i in range(120)))
    qapp.processEvents()
    assert field.blockCount() == 120
    assert field.line_numbers.width() > old_width
    assert field.line_numbers.width() == field._gutter_width()
    field.verticalScrollBar().setValue(field.verticalScrollBar().maximum())
    qapp.processEvents()
    assert field.firstVisibleBlock().blockNumber() > 0


def test_plain_text_has_no_syntax_issues_or_colors(editor):
    field = editor.source_editor
    field.set_source_format(".txt")
    field.setPlainText("Anything <00:bad>\n[00:70.000] is plain text")
    field.refresh_diagnostics()
    field._preview_fonts.rehighlight()
    assert not field.diagnostics
    block = field.document().begin()
    while block.isValid():
        assert all(run.format.foreground().style() == Qt.BrushStyle.NoBrush for run in block.layout().formats())
        block = block.next()


def test_closing_stops_pending_source_validation(editor):
    editor.source_editor.setPlainText("Unfinished")
    assert editor.source_editor._diagnostics_timer.isActive()
    editor.document_dirty = False
    editor.close()
    assert not editor.source_editor._diagnostics_timer.isActive()


def test_source_palette_updates_error_contrast_without_changing_content(editor):
    field = editor.source_editor
    field.setPlainText("[00:99.000]Invalid")
    field.refresh_diagnostics()
    before = (field.toPlainText(), editor.document_dirty, editor._source_dirty)
    palette = field.palette()
    palette.setColor(QPalette.ColorRole.Base, QColor("#101010"))
    field.setPalette(palette)
    assert "#ff7b72" in editor.source_diagnostics_label.styleSheet()
    assert (field.toPlainText(), editor.document_dirty, editor._source_dirty) == before


def test_ctrl_wheel_zooms_source_and_gutter_without_editing_or_changing_undo(editor, qapp):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())
    editor.show()
    field = editor.source_editor
    qapp.processEvents()
    before = (field.toPlainText(), editor.document_dirty, editor._source_dirty, len(editor._undo_stack))
    original_size = field.font().pointSizeF()
    original_width = field.line_numbers.width()

    def wheel(delta, modifiers=Qt.KeyboardModifier.ControlModifier):
        event = QWheelEvent(QPointF(30, 30), QPointF(30, 30), QPoint(), QPoint(0, delta),
                            Qt.MouseButton.NoButton, modifiers, Qt.ScrollPhase.NoScrollPhase, False)
        qapp.sendEvent(field.viewport(), event)

    wheel(60)
    assert field.font().pointSizeF() == original_size
    wheel(60)
    assert field.font().pointSizeF() == original_size + 1
    assert field.line_numbers.width() >= original_width
    assert field._preview_fonts._resolver.font.pointSizeF() == original_size + 1
    wheel(-120)
    assert field.font().pointSizeF() == original_size
    wheel(120, Qt.KeyboardModifier.NoModifier)
    assert field.font().pointSizeF() == original_size
    wheel(12000)
    assert field.font().pointSizeF() == 40
    wheel(-12000)
    assert field.font().pointSizeF() == 6
    assert (field.toPlainText(), editor.document_dirty, editor._source_dirty, len(editor._undo_stack)) == before
