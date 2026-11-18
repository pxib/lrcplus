"""The lyrics editor offers a start page when no song or lyrics are loaded."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QFileDialog, QInputDialog, QLabel, QMessageBox

from lyrics.loader import load_lyrics
from ui.lrc_editor import LrcEditorDialog
from ui.main_window import Player


@pytest.fixture
def start_editor(qapp, monkeypatch):
    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    dialog = LrcEditorDialog()
    dialog.show()
    qapp.processEvents()
    yield dialog
    dialog.document_dirty = False
    dialog.close()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_empty_editor_displays_actions_without_building_workspace(start_editor):
    dialog = start_editor
    assert dialog._choosing_document
    assert any(label.text() == "No songs found" for label in dialog.findChildren(QLabel))
    assert dialog.create_lrcx_button.isVisible()
    assert dialog.open_lrcx_button.isVisible()
    assert not dialog.document_start_page.recover_button.isVisible()
    assert not hasattr(dialog, "tabs")
    assert not hasattr(dialog, "_autosave_timer")
    assert dialog.lrc_path is None and dialog.result_path is None


@pytest.mark.parametrize("action", ["create_lrcx_button", "open_lrcx_button"])
def test_cancel_picker_keeps_start_page(start_editor, monkeypatch, action):
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: ("", ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: ("", ""))
    getattr(start_editor, action).click()
    assert start_editor._choosing_document
    assert start_editor.document_start_page.isVisible()
    assert start_editor.isVisible()


@pytest.mark.parametrize("action", ["escape", "title_bar"])
def test_start_page_can_close_without_document_cleanup(start_editor, action):
    if action == "escape":
        QTest.keyClick(start_editor, Qt.Key.Key_Escape)
    else:
        start_editor.close()
    assert not start_editor.isVisible()
    assert start_editor._closed


def test_create_switches_same_dialog_to_blank_draft_and_saves(start_editor, tmp_path, monkeypatch, qapp):
    target = tmp_path / "new-lyrics"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    dialog = start_editor
    dialog.create_lrcx_button.click()
    qapp.processEvents()
    target = target.with_suffix(".lrcx")
    assert not dialog._choosing_document
    assert not dialog.document_start_page.isVisible()
    assert dialog.tabs.isVisible()
    assert dialog.is_new_document and not dialog.document_dirty
    assert dialog.lrc_path == target
    assert dialog.lines == [] and not target.exists()
    assert dialog._current_song_path() is None
    assert not dialog.force_song_button.isEnabled()
    dialog.add_line()
    dialog.line_text.setPlainText("Created without audio")
    assert dialog.save_document()
    assert load_lyrics(target)[0].text == "Created without audio"
    assert not dialog.is_new_document and not dialog.document_dirty


def test_open_switches_same_dialog_to_existing_lyrics_without_audio(start_editor, tmp_path, monkeypatch, qapp):
    target = tmp_path / "existing.lrcx"
    target.write_text("[offset:500]\n[00:01.000]Original\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(target), ""))
    dialog = start_editor
    dialog.open_lrcx_button.click()
    qapp.processEvents()
    assert not dialog._choosing_document
    assert dialog.tabs.isVisible()
    assert not dialog.is_new_document and not dialog.document_dirty
    assert dialog.lines[0].text == "Original"
    assert dialog.lyrics_offset_ms == 500
    assert dialog.lrc_path == target
    dialog.line_text.setPlainText("Edited without audio")
    assert dialog.save_and_close()
    assert dialog.result() == QDialog.DialogCode.Accepted
    assert load_lyrics(target)[0].text == "Edited without audio"


def test_new_document_preserves_existing_file_until_save(start_editor, tmp_path, monkeypatch):
    target = tmp_path / "replace.lrcx"
    original = "[offset:500]\n[00:01.000]Original\n"
    target.write_text(original, encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    start_editor.create_lrcx_button.click()
    assert start_editor.lines == [] and start_editor.lyrics_offset_ms == 0
    assert start_editor.is_new_document
    assert target.read_text(encoding="utf-8") == original
    assert start_editor.save_document()
    assert "Original" not in target.read_text(encoding="utf-8")


@pytest.mark.parametrize("unfinished_source", [False, True])
def test_recover_new_draft_from_start_page(start_editor, tmp_path, monkeypatch, unfinished_source):
    target = tmp_path / "draft.lrcx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    start_editor.create_lrcx_button.click()
    start_editor.add_line()
    start_editor.line_text.setPlainText("Structured edit")
    if unfinished_source:
        start_editor.tabs.setCurrentWidget(start_editor.source_editor.parentWidget())
        start_editor.source_editor.setPlainText("[00:02.000]Source edit\nunfinished")
    start_editor._autosave_if_needed()
    recovery_path = start_editor._autosave_path
    assert recovery_path.is_file()
    assert not target.exists()

    def choose_draft(parent, title, label, items, **kwargs):
        assert items == [str(target)]
        return str(target), True

    monkeypatch.setattr(QInputDialog, "getItem", choose_draft)
    recovered = LrcEditorDialog()
    recovered.show()
    try:
        assert recovered.document_start_page.recover_button.isVisible()
        recovered.document_start_page.recover_button.click()
        assert recovered.document_dirty and recovered.is_new_document
        assert recovered.save_button.isEnabled()
        assert recovered.lines[0].text == "Structured edit"
        assert not target.exists()
        if unfinished_source:
            assert recovered.tabs.currentWidget() is recovered.source_editor.parentWidget()
            assert recovered.source_editor.toPlainText() == "[00:02.000]Source edit\nunfinished"
            assert recovered._source_dirty
            recovered.source_editor.setPlainText("[00:02.000]Source edit")
        assert recovered.save_document()
        assert load_lyrics(target)[0].text == ("Source edit" if unfinished_source else "Structured edit")
        assert not recovery_path.exists()
    finally:
        recovered.done(QDialog.DialogCode.Rejected)
        recovered.deleteLater()


def test_open_drafts_for_same_target_keep_separate_recovery_copies(start_editor, tmp_path, monkeypatch):
    target = tmp_path / "draft.lrcx"
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    start_editor.create_lrcx_button.click()
    another = LrcEditorDialog(target, new_document=True)
    try:
        for dialog, text in ((start_editor, "First edit"), (another, "Second edit")):
            dialog.add_line()
            dialog.line_text.setPlainText(text)
            dialog._autosave_if_needed()
        assert start_editor._autosave_path != another._autosave_path
        assert another._autosave_path.is_file()
    finally:
        another.done(QDialog.DialogCode.Rejected)
        another.deleteLater()
    assert start_editor._autosave_path.is_file()
    assert not target.exists()


def test_create_can_recover_legacy_draft_sidecar(start_editor, tmp_path, monkeypatch):
    target = tmp_path / "draft.lrcx"
    legacy = tmp_path / ".draft.lrcx.autosave"
    legacy.write_text("[format:LRCX]\n[00:01.000]Legacy draft\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    start_editor.create_lrcx_button.click()
    assert start_editor.lines[0].text == "Legacy draft"
    assert start_editor.document_dirty and start_editor.is_new_document
    assert not target.exists()
    assert not legacy.exists() and start_editor._autosave_path.is_file()


@pytest.mark.parametrize("action", ["cancel", "create", "open"])
def test_player_opens_editor_start_page_without_song(qapp, tmp_path, monkeypatch, action):
    monkeypatch.setattr(Player, "_restore_startup_state", lambda self: None)
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    qapp.processEvents()
    opened = []
    target = tmp_path / "standalone.lrcx"
    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    monkeypatch.setattr(QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(target), ""))
    if action == "open":
        target.write_text("[00:01.000]Existing\n", encoding="utf-8")

    def inspect(dialog):
        opened.append(dialog)
        assert dialog._choosing_document
        assert dialog.parentWidget() is window
        if action == "cancel":
            dialog.reject()
        else:
            if action == "create":
                dialog.create_lrcx_button.click()
                dialog.add_line()
                dialog.line_text.setPlainText("New lyrics")
            else:
                dialog.open_lrcx_button.click()
            assert dialog.save_and_close()
        return dialog.result()

    monkeypatch.setattr(LrcEditorDialog, "exec", inspect)
    try:
        window.lyrics_button.click()
        assert len(opened) == 1
        assert window.current_audio_path is None
        assert window.current_lyrics_path == (None if action == "cancel" else target)
        if action != "cancel":
            assert load_lyrics(target)[0].text == ("New lyrics" if action == "create" else "Existing")
        assert not window._statistics_editor_active
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
