"""Opening an editor should build only one immutable saved-state snapshot."""

from copy import deepcopy

import pytest

from ui.lrc_editor import LrcEditorDialog


def test_open_captures_saved_state_once(make_lyrics_editor, tmp_path, monkeypatch):
    path = tmp_path / "opening.lrc"
    path.write_text("[00:01.000]First\n[00:02.000]Second\n", encoding="utf-8")
    snapshots = []
    original = LrcEditorDialog._document_state

    def capture(dialog):
        state = original(dialog)
        snapshots.append(state)
        return state

    monkeypatch.setattr(LrcEditorDialog, "_document_state", capture)
    dialog = make_lyrics_editor(path=path, populate=False)

    assert len(snapshots) == 1
    assert dialog._saved_document_state is snapshots[0]
    assert not dialog.document_dirty
    assert dialog._undo_stack == []
    assert dialog._redo_stack == []


@pytest.mark.parametrize("target,field,value", [
    ("line", "start", 7),
    ("line", "text", "Changed"),
    ("line", "end", 8),
    ("line", "has_karaoke", False),
    ("line", "style_spans", [(0, 2, True, False, False, False)]),
    ("line", "is_blockquote", True),
    ("line", "translation_lines", [1]),
    ("segment", "start", 3),
    ("segment", "text", "Changed"),
    ("segment", "ruby", "Reading"),
    ("segment", "instant", True),
    ("segment", "end", 4),
    ("segment", "ruby_source", "manual"),
    ("segment", "ruby_spans", [(0, 2, "Reading")]),
    ("segment", "visual_line", 1),
])
def test_saved_snapshot_detects_model_changes(editor, target, field, value):
    before = editor._document_state()
    original_lines = deepcopy(editor.lines)
    model = editor.lines[0] if target == "line" else editor.lines[0].segments[0]
    if isinstance(value, list):
        getattr(model, field).extend(value)
    else:
        setattr(model, field, value)

    assert editor._document_state() != before
    editor.lines = original_lines
    assert editor._document_state() == before


def test_undo_to_opened_document_restores_clean_state(make_lyrics_editor, tmp_path):
    path = tmp_path / "undo.lrc"
    path.write_text("[00:01.000]Original\n", encoding="utf-8")
    dialog = make_lyrics_editor(path=path, populate=False)

    dialog.line_text.setPlainText("Edited")
    assert dialog.document_dirty
    dialog.undo()
    assert dialog.lines[0].text == "Original"
    assert not dialog.document_dirty
    dialog.redo()
    assert dialog.lines[0].text == "Edited"
    assert dialog.document_dirty
