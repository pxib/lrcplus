"""Hidden editor tabs should retain drafts, not regenerable view buffers."""

from copy import deepcopy

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QPixmap


def test_source_is_created_on_first_use_and_released_after_leaving(editor):
    source = editor.source_editor
    original = deepcopy(editor.lines)
    assert source.document().isEmpty()
    editor.tabs.setCurrentWidget(source.parentWidget())
    text = source.toPlainText()
    assert "Hello" in text and "World" in text
    editor.tabs.setCurrentWidget(editor.lyrics_page)
    assert source.document().isEmpty()
    assert editor.lines == original
    assert not editor.document_dirty and not editor._undo_stack
    editor.tabs.setCurrentWidget(source.parentWidget())
    assert source.toPlainText() == text


def test_clean_undo_checkpoints_do_not_copy_hidden_source(editor, monkeypatch):
    editor.tabs.setCurrentWidget(editor.source_editor.parentWidget())

    def unexpected_read():
        pytest.fail("Clean source is reconstructible from the model snapshot")

    monkeypatch.setattr(editor.source_editor, "toPlainText", unexpected_read)
    state = editor._capture_state(line_index=0)
    assert state["source"] is None
    assert state["lines"][0] == editor.lines[0]


def test_invalid_source_draft_is_retained_when_leaving_tab_is_rejected(editor, monkeypatch):
    source = editor.source_editor
    editor.tabs.setCurrentWidget(source.parentWidget())
    source.setPlainText("Unfinished draft")
    monkeypatch.setattr("ui.lyrics_editor_document.QMessageBox.warning", lambda *args: None)
    editor.tabs.setCurrentWidget(editor.lyrics_page)
    assert editor.tabs.currentWidget() is source.parentWidget()
    assert source.toPlainText() == "Unfinished draft"
    assert editor._capture_state()["source"] == "Unfinished draft"


def test_hidden_lyric_preview_is_cleared_and_restores_latest_edits(editor, monkeypatch):
    preview = editor.lyrics_renderer_preview
    assert preview.lyric == "Hello"
    editor.tabs.setCurrentWidget(editor.share_page)
    assert preview.lyric == ""
    updates = []
    original = preview.set_segments
    monkeypatch.setattr(preview, "set_segments", lambda *args, **kwargs: updates.append(True))
    editor.line_list.setCurrentRow(1)
    editor.preview_position_changed(5000)
    assert not updates
    monkeypatch.setattr(preview, "set_segments", original)
    editor.tabs.setCurrentWidget(editor.lyrics_page)
    assert preview.lyric == "World"


def test_karaoke_preview_releases_hidden_content_and_reuses_editor_readings(editor, monkeypatch):
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda text: None)
    preview = editor.karaoke_renderer_preview
    assert not preview.lyric
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    assert preview.lyric == "Hello"
    assert preview._furigana_future is None
    assert not preview._reading_timer.isActive()
    editor.tabs.setCurrentWidget(editor.share_page)
    assert not preview.lyric
    updates = []
    original = preview.set_segments
    monkeypatch.setattr(preview, "set_segments", lambda *args, **kwargs: updates.append(True))
    editor.line_list.setCurrentRow(1)
    editor.preview_position_changed(5000)
    assert not updates
    monkeypatch.setattr(preview, "set_segments", original)
    editor.tabs.setCurrentWidget(editor.karaoke_scroll)
    assert preview.lyric == "World"


def test_hiding_waveform_releases_image_and_preserves_decoded_peaks(editor, qapp):
    waveform = editor.lyrics_waveform
    editor.lyrics_transport_stack.setCurrentWidget(waveform)
    editor.show()
    qapp.processEvents()
    assert waveform.isVisible()
    waveform._peaks = [0.25] * 1400
    peaks = waveform._peaks
    waveform._waveform_cache_gray = QPixmap(800, 150)
    waveform._waveform_cache_gray.fill(Qt.GlobalColor.gray)
    waveform._waveform_bars = [(1, 2, 3)]
    editor.tabs.setCurrentWidget(editor.share_page)
    assert waveform._waveform_cache_gray.isNull()
    assert not waveform._waveform_bars
    assert waveform._peaks is peaks


def test_reloading_unopened_source_preserves_existing_document(editor):
    original = deepcopy(editor.lines)
    assert editor.source_editor.document().isEmpty()
    assert editor.reload_model_from_source()
    assert editor.lines == original
