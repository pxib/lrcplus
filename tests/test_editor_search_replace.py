"""Search visibility, literal matching, annotation preservation and Undo."""

from copy import deepcopy

import pytest
from PySide6.QtWidgets import QDialog, QDialogButtonBox, QTableWidget

from lyrics.editor_search import find_text_matches, replace_lyric_matches, visible_text_ranges
from lyrics.formatting import style_at
from lyrics.loader import load_lyrics
from lyrics.karaoke_timing import segment_end
from lyrics.models import LyricLine, LyricSegment


def accept_replacements(dialog):
    dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Apply).click()
    return QDialog.DialogCode.Accepted


@pytest.mark.parametrize("text,query,sensitive,expected", [
    ("Hello HELLO", "hello", False, [(0, 5), (6, 11)]),
    ("Hello HELLO", "Hello", True, [(0, 5)]),
    ("Straße STRASSE", "strasse", False, [(0, 6), (7, 14)]),
    ("ß", "s", False, []),
    ("[a.*] a", "[a.*]", False, [(0, 5)]),
    ("aaa", "aa", False, [(0, 2)]),
    ("a a", " ", False, [(1, 2)]),
    ("Anything", "", False, []),
])
def test_literal_match_ranges(text, query, sensitive, expected):
    assert find_text_matches(text, query, case_sensitive=sensitive) == expected


def test_visible_ranges_map_styles_quotes_breaks_and_utf16():
    raw = r"> 😀**Hello**{\nt}世界"
    ranges = visible_text_ranges(raw)
    assert len(ranges) == len("😀Hello\n世界")
    encoded = raw.encode("utf-16-le")
    values = [encoded[a * 2:b * 2].decode("utf-16-le") for a, b in ranges]
    assert values == ["😀", "H", "e", "l", "l", "o", r"{\nt}", "世", "界"]


def test_replacements_preserve_timing_styles_and_unaffected_readings():
    line = LyricLine(2, "Hello 世界", [
        LyricSegment(2, "Hello ", end=3),
        LyricSegment(3, "世界", ruby="せかい", ruby_source="manual", end=4),
    ], end=4, has_karaoke=True, is_blockquote=True,
        style_spans=[(0, 5, True, False, False, False), (6, 8, False, True, False, False)],
        reading_groups=[(6, 8)])
    original = deepcopy(line)
    corrected = replace_lyric_matches(line, [(0, 5)], "Hi")
    assert line == original
    assert corrected.text == "Hi 世界"
    assert corrected.start == 2 and corrected.end == 4 and corrected.is_blockquote
    assert [(s.start, s.end) for s in corrected.segments] == [(2, 3), (3, 4)]
    assert corrected.segments[0].needs_timing_review
    assert corrected.segments[1].ruby == "せかい"
    assert corrected.reading_groups == [(3, 5)]
    assert style_at(corrected.style_spans, 0)[0]
    assert style_at(corrected.style_spans, 3)[1]


def test_replacements_clear_changed_readings_and_keep_translation_rows():
    line = LyricLine(1, "世界\nHello Hello", [
        LyricSegment(1, "世界", ruby="せかい", ruby_source="manual", end=2),
        LyricSegment(2, "\nHello Hello", end=3),
    ], has_karaoke=True, translation_lines=[1], reading_groups=[(0, 2)])
    changed = replace_lyric_matches(line, [(0, 2)], "地球")
    assert not changed.segments[0].ruby
    assert changed.reading_groups == []
    assert changed.translation_lines == [1]
    shortened = replace_lyric_matches(line, find_text_matches(line.text, "Hello"), "Hi")
    assert shortened.text == "世界\nHi Hi"
    assert shortened.segments[0].ruby == "せかい"
    assert shortened.translation_lines == [1]


def test_find_replace_controls_only_appear_with_a_search(editor, qapp):
    editor.show()
    qapp.processEvents()
    assert not editor.lyric_search_tools.isVisible()
    editor.focus_lyric_search()
    assert not editor.lyric_search_tools.isVisible()
    editor.lyric_search.setText("Hello")
    assert editor.lyric_search_tools.isVisible()
    assert not editor.lyric_replace_panel.isVisible()
    editor.lyric_replace_toggle.click()
    assert editor.lyric_replace_panel.isVisible()
    editor.lyric_replace.setText("Hi")
    editor.lyric_replace_toggle.click()
    assert not editor.lyric_replace_panel.isVisible()
    editor.lyric_replace_toggle.click()
    assert editor.lyric_replace_panel.isVisible()
    assert editor.lyric_replace.text() == "Hi"
    assert editor.replace_all_button.isEnabled()
    editor.lyric_search.setText("absent")
    assert editor.lyric_search_tools.isVisible()
    assert not editor.replace_all_button.isEnabled()
    editor.lyric_search.clear()
    assert not editor.lyric_search_tools.isVisible()
    assert not editor.lyric_search_status.isVisible()
    assert not editor.line_text.extraSelections()


def test_highlights_use_visible_text_across_markup_without_moving_cursor(editor):
    editor.line_text.setPlainText(r"> 😀He**llo**{\nt}Hello")
    cursor = editor.line_text.textCursor()
    editor.lyric_search.setText("hello")
    selections = editor.line_text.extraSelections()
    assert len(selections) == 2
    assert selections[0].cursor.selectedText() == "He**llo"
    assert selections[1].cursor.selectedText() == "Hello"
    assert editor.line_text.textCursor().position() == cursor.position()
    editor.lyric_search_case.setChecked(True)
    assert not editor.line_text.extraSelections()


def test_replacement_preview_cancel_does_not_change_document(editor, monkeypatch):
    original = deepcopy(editor.lines)
    editor.lyric_search.setText("hello")
    editor.lyric_replace.setText("Hi")

    def cancel(dialog):
        table = dialog.findChild(QTableWidget)
        assert table.rowCount() == 1
        assert table.item(0, 1).text() == "Hello"
        assert table.item(0, 2).text() == "Hi"
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(QDialog, "exec", cancel)
    editor.replace_all_button.click()
    assert editor.lines == original
    assert not editor._undo_stack
    assert not editor.document_dirty


def test_replace_all_is_one_undo_and_preserves_selection_and_share_checks(editor, monkeypatch):
    editor.lines = [LyricLine(1, "Hello Hello"), LyricLine(3, "HELLO"), LyricLine(5, "Other")]
    editor._populate_lines(preserve_line_index=0)
    editor._set_line_selection([0, 1], current=0)
    editor._share_checked_line_ids = {id(editor.lines[0])}
    editor._reset_history_baseline()
    original = deepcopy(editor.lines)
    editor.lyric_search.setText("hello")
    editor.lyric_replace.setText("Hi")
    monkeypatch.setattr(QDialog, "exec", accept_replacements)
    editor.replace_all_button.click()
    assert [line.text for line in editor.lines] == ["Hi Hi", "Hi", "Other"]
    assert editor._selected_line_indices() == [0, 1]
    assert editor._share_checked_line_ids == {id(editor.lines[0])}
    assert len(editor._undo_stack) == 1 and editor.document_dirty
    editor.undo()
    assert editor.lines == original and not editor.document_dirty
    editor.redo()
    assert [line.text for line in editor.lines] == ["Hi Hi", "Hi", "Other"]


def test_selected_scope_navigation_and_replace_follow_canonical_indices(editor, monkeypatch):
    editor.lines = [LyricLine(1, "Hello"), LyricLine(3, "Hello"), LyricLine(5, "Hello")]
    editor._populate_lines(preserve_line_index=0)
    editor._set_line_selection([0, 2], current=0)
    editor.sidebar_sort_combo.setCurrentIndex(2)
    editor.lyric_search.setText("Hello")
    editor.lyric_search_scope.setCurrentIndex(1)
    editor.find_next_lyric()
    assert editor._current_line_index() == 2
    assert editor._selected_line_indices() == [0, 2]
    editor.lyric_replace.setText("Hi")
    monkeypatch.setattr(QDialog, "exec", accept_replacements)
    editor.replace_all_button.click()
    assert [line.text for line in editor.lines] == ["Hi", "Hello", "Hi"]


def test_replace_current_line_and_empty_replacement(editor, monkeypatch):
    editor.lines = [LyricLine(1, "Hello Hello"), LyricLine(3, "Hello")]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    editor.lyric_search.setText("Hello")
    monkeypatch.setattr(QDialog, "exec", accept_replacements)
    editor.replace_line_button.click()
    assert [line.text for line in editor.lines] == [" ", "Hello"]
    editor.undo()
    assert [line.text for line in editor.lines] == ["Hello Hello", "Hello"]


def test_noop_and_invalid_source_do_not_create_replacement_history(editor, monkeypatch):
    editor.lyric_search.setText("Hello")
    editor.lyric_replace.setText("Hello")
    monkeypatch.setattr(QDialog, "exec", lambda dialog: pytest.fail("No replacement to preview"))
    editor.preview_lyric_replacements()
    assert not editor._undo_stack
    editor.source_editor.setPlainText("Invalid raw source")
    original = deepcopy(editor.lines)
    monkeypatch.setattr("ui.lyrics_editor_document.QMessageBox.warning", lambda *args: None)
    editor.lyric_replace.setText("Hi")
    editor.preview_lyric_replacements()
    assert editor.lines == original
    assert not editor._undo_stack


def test_selected_scope_updates_when_selection_changes(editor):
    editor.lyric_search.setText("Hello")
    editor.lyric_search_scope.setCurrentIndex(1)
    assert editor.replace_all_button.isEnabled()
    editor._set_line_selection([1], current=1)
    assert not editor.replace_all_button.isEnabled()
    assert editor.lyric_search_status.text() == "No matching lyrics."
    assert not editor.line_text.extraSelections()


def test_replaced_document_round_trips_literal_text_and_authored_data(editor, monkeypatch):
    editor.lines = [LyricLine(2, "Hello 世界\nTranslation", [
        LyricSegment(2, "Hello ", end=3),
        LyricSegment(3, "世界", ruby="せかい", ruby_source="manual", end=3.8),
        LyricSegment(4, "\nTranslation", end=5, visual_line=1),
    ], end=5, has_karaoke=True, is_blockquote=True, translation_lines=[1],
        style_spans=[(0, 5, True, False, False, False)], reading_groups=[(6, 8)])]
    editor._populate_lines(preserve_line_index=0)
    editor.lyric_search.setText("Hello")
    editor.lyric_replace.setText("**Hi**")
    monkeypatch.setattr(QDialog, "exec", accept_replacements)
    editor.replace_all_button.click()
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path, infer_ends=False)[0]
    assert restored.text == "**Hi** 世界\nTranslation"
    assert restored.start == 2 and restored.end == 5
    assert restored.is_blockquote and restored.translation_lines == [1]
    assert restored.reading_groups == [(7, 9)]
    assert any(segment.ruby == "せかい" for segment in restored.segments)
    assert style_at(restored.style_spans, 0)[0]
    assert [(segment.start, segment_end(restored, index)) for index, segment in enumerate(restored.segments)
            if segment.text.strip()] == [(2, 3), (3, 3.8), (4, 5)]
