from copy import deepcopy

import pytest
from PySide6.QtWidgets import QMessageBox, QPushButton

from lyrics.models import LyricLine, LyricSegment


@pytest.mark.parametrize("karaoke", [True, False])
def test_danger_actions_available_in_advanced(make_lyrics_editor, karaoke):
    editor = make_lyrics_editor(karaoke=karaoke)
    advanced = editor.tabs.widget(editor.tabs.count() - 1)
    assert advanced.isAncestorOf(editor.danger_frame)
    assert {button.text() for button in editor.danger_frame.findChildren(QPushButton)} == {
        "Clear Karaoke Tags", "Clear Furigana", "Clear Translations", "Clear Formatting",
    }


@pytest.mark.parametrize("action", ["reset_all_readings_advanced", "clear_all_formatting", "clear_all_karaoke"])
@pytest.mark.parametrize("confirm", [True, False])
def test_cleanup_preserves_other_layers_and_supports_undo(make_lyrics_editor, monkeypatch, action, confirm):
    editor = make_lyrics_editor(karaoke=False)
    segment = LyricSegment(2.2, "Hello", "reading", True, 3.5, "manual",
                           [(0, 5, "reading")], visual_line=1)
    editor.lines = [LyricLine(2, "Hello", [segment], end=4, has_karaoke=True,
                             style_spans=[(0, 5, True, False, False, False)],
                             is_blockquote=True, translation_lines=[1])]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    before = deepcopy(editor.lines)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: (
        QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.Cancel
    ))
    monkeypatch.setattr(QMessageBox, "exec", lambda box: 0)
    monkeypatch.setattr(QMessageBox, "clickedButton", lambda box: next(
        button for button in box.buttons()
        if button.text() == ("Clear Karaoke" if confirm else "Cancel")
    ))
    getattr(editor, action)()
    if not confirm:
        assert editor.lines == before
        assert not editor._undo_stack
        return
    line = editor.lines[0]
    expected = deepcopy(before[0])
    if action == "reset_all_readings_advanced":
        expected.segments[0].ruby = None
        expected.segments[0].ruby_source = None
        expected.segments[0].ruby_spans = []
    elif action == "clear_all_formatting":
        expected.style_spans = []
        expected.is_blockquote = False
    else:
        expected.has_karaoke = False
        expected.segments = [LyricSegment(2, "Hello", "reading", end=4,
                                          ruby_source="manual", ruby_spans=[(0, 5, "reading")])]
    assert line == expected
    assert editor.save_button.isEnabled()
    editor.undo()
    assert editor.lines == before
    editor.redo()
    assert editor.lines == [expected]


@pytest.mark.parametrize("karaoke", [True, False])
@pytest.mark.parametrize("confirm", [True, False])
def test_clear_translations_preserves_original_rows(make_lyrics_editor, monkeypatch, karaoke, confirm):
    from lyrics.loader import load_lyrics

    editor = make_lyrics_editor(karaoke=karaoke)
    editor.lines = [LyricLine(
        2, "Hello\nTranslation\nWorld\nAnother translation",
        [LyricSegment(2.2, "Hello", "reading", end=3, ruby_source="manual",
                      ruby_spans=[(0, 5, "reading")]),
         LyricSegment(2, "\nTranslation\n", instant=True),
         LyricSegment(3, "World", "second", end=4, ruby_source="manual",
                      ruby_spans=[(0, 5, "second")], visual_line=2),
         LyricSegment(2, "\nAnother translation", instant=True)],
        end=4, has_karaoke=karaoke,
        style_spans=[(0, 5, True, False, False, False),
                     (6, 17, False, True, False, False),
                     (18, 23, False, False, False, True)],
        is_blockquote=True, translation_lines=[1, 3],
    ), LyricLine(5, "Untranslated", [LyricSegment(5, "Untranslated")])]
    editor._populate_lines(preserve_line_index=0)
    editor._reset_history_baseline()
    before = deepcopy(editor.lines)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: (
        QMessageBox.StandardButton.Yes if confirm else QMessageBox.StandardButton.Cancel
    ))
    editor.clear_all_translations()
    if not confirm:
        assert editor.lines == before
        assert not editor._undo_stack
        return
    result = deepcopy(editor.lines)
    line = result[0]
    assert line.text == "Hello\nWorld"
    assert line.translation_lines == []
    assert line.start == 2 and line.end == 4
    assert line.has_karaoke == karaoke and line.is_blockquote
    assert line.style_spans == [(0, 5, True, False, False, False),
                                (6, 11, False, False, False, True)]
    assert "".join(segment.text for segment in line.segments) == line.text
    assert line.segments[0] == before[0].segments[0]
    expected_world = deepcopy(before[0].segments[2])
    expected_world.visual_line = 1
    assert line.segments[-1] == expected_world
    assert result[1] == before[1]
    assert editor.save_button.isEnabled()
    editor.undo()
    assert editor.lines == before
    editor.redo()
    assert editor.lines == result
    assert editor.save_document()
    restored = load_lyrics(editor.lrc_path, infer_ends=False)
    assert restored[0].text == "Hello\nWorld"
    assert restored[0].translation_lines == []


def test_clear_translations_with_rows_inside_one_segment(editor, monkeypatch):
    editor.lines = [LyricLine(2, "A\nTranslation\nB", [
        LyricSegment(2, "A\nTranslation\nB", ruby_spans=[(0, 1, "a"), (14, 15, "b")])
    ], translation_lines=[1])]
    editor._populate_lines(preserve_line_index=0)
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    editor.clear_all_translations()
    assert editor.lines[0].text == "A\nB"
    assert editor.lines[0].segments[0].ruby_spans == [(0, 1, "a"), (2, 3, "b")]
