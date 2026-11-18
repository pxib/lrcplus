"""Editor layout persistence across hidden panels and document sessions."""

import pytest
from PySide6.QtCore import QRect
from PySide6.QtGui import QScreen
from PySide6.QtWidgets import QMessageBox

from core.settings import (
    get_editor_layout_settings, load_all_settings, save_all_settings,
    save_editor_layout_settings,
)
from ui.lrc_editor import LrcEditorDialog


@pytest.fixture
def large_screen(monkeypatch):
    monkeypatch.setattr(QScreen, "availableGeometry", lambda self: QRect(0, 0, 1920, 1080))


def test_reopening_remembers_dimensions_only(make_lyrics_editor, qapp, large_screen):
    first = make_lyrics_editor(karaoke=False)
    first.resize(1280, 840)
    first.show()
    qapp.processEvents()
    available = first.workspace_splitter.width() - first.workspace_splitter.handleWidth()
    first.workspace_splitter.setSizes([315, available - 315])
    sidebar_width = first.workspace_splitter.sizes()[0]
    first._select_line_index(1)
    first.readings_toggle.setChecked(False)
    first.lyric_search.setText("World")
    first.lyric_replace_toggle.setChecked(True)
    first.close()

    saved = load_all_settings()["lyrics_editor_layout"]
    assert set(saved) == {"window_size", "panel_widths"}
    assert saved["window_size"] == [1280, 840]
    assert saved["panel_widths"]["sidebar"] == sidebar_width
    second = make_lyrics_editor(karaoke=False)
    second.show()
    qapp.processEvents()
    assert (second.width(), second.height()) == (1280, 840)
    assert second.workspace_splitter.sizes()[0] == sidebar_width
    assert second._current_line_index() == 0
    assert second.readings_toggle.isChecked()
    assert not second.lyric_search.text()
    assert not second.lyric_replace_toggle.isChecked()
    assert not second.document_dirty


def test_hidden_panels_keep_widths_across_tabs_and_plugin_availability(
    make_lyrics_editor, qapp, large_screen,
):
    first = make_lyrics_editor()
    first.resize(1500, 880)
    first.show()
    first.tabs.setCurrentWidget(first.karaoke_scroll)
    qapp.processEvents()
    available = first.workspace_splitter.width() - 2 * first.workspace_splitter.handleWidth()
    first.workspace_splitter.setSizes([310, available - 310 - 400, 400])
    widths = first.workspace_splitter.sizes()
    first.tabs.setCurrentWidget(first.lyrics_page)
    qapp.processEvents()
    first.tabs.setCurrentWidget(first.karaoke_scroll)
    qapp.processEvents()
    assert first.workspace_splitter.sizes() == widths
    first.tabs.setCurrentWidget(first.source_editor.parentWidget())
    qapp.processEvents()
    assert first.workspace_splitter.sizes()[0] == 0
    assert first.workspace_splitter.sizes()[2] == 0
    first.close()

    without_karaoke = make_lyrics_editor(karaoke=False)
    without_karaoke.show()
    qapp.processEvents()
    assert without_karaoke.workspace_splitter.sizes()[0] == widths[0]
    without_karaoke.close()
    second = make_lyrics_editor()
    second.show()
    second.tabs.setCurrentWidget(second.karaoke_scroll)
    qapp.processEvents()
    assert second.workspace_splitter.sizes()[0] == widths[0]
    assert second.workspace_splitter.sizes()[2] == widths[2]


def test_cancelled_close_does_not_persist_new_dimensions(
    editor, qapp, monkeypatch, large_screen,
):
    save_editor_layout_settings([1200, 800], {"sidebar": 300, "karaoke_preview": 380})
    before = get_editor_layout_settings()
    editor.show()
    editor.resize(1300, 860)
    qapp.processEvents()
    editor.line_text.setPlainText("Unsaved lyric edit")
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Cancel)
    assert not editor.close()
    assert editor.isVisible()
    assert get_editor_layout_settings() == before


def test_start_page_keeps_resized_window_when_opening_document(
    make_lyrics_editor, tmp_path, qapp, large_screen,
):
    start = LrcEditorDialog()
    try:
        start.resize(1300, 850)
        start.show()
        qapp.processEvents()
        start._initialize_document(tmp_path / "new.lrcx", new_document=True)
        qapp.processEvents()
        assert (start.width(), start.height()) == (1300, 850)
    finally:
        start.close()
        start.deleteLater()
    reopened = make_lyrics_editor(karaoke=False)
    assert (reopened.width(), reopened.height()) == (1300, 850)


@pytest.mark.parametrize("stored", [
    None, [], {"window_size": ["wide", 800]},
    {"window_size": [True, 800], "panel_widths": {"sidebar": -1, "karaoke_preview": "wide"}},
    {"window_size": [0, 800], "panel_widths": None},
])
def test_malformed_layout_settings_use_defaults(stored):
    assert get_editor_layout_settings({"lyrics_editor_layout": stored}) == {
        "window_size": None, "panel_widths": {"sidebar": 250, "karaoke_preview": 360},
    }


def test_restored_window_fits_available_screen(make_lyrics_editor, monkeypatch, qapp):
    monkeypatch.setattr(QScreen, "availableGeometry", lambda self: QRect(0, 0, 1000, 900))
    save_all_settings({"playback": {"volume": 42}})
    save_editor_layout_settings([3000, 2000], {"sidebar": 300, "karaoke_preview": 380})
    editor = make_lyrics_editor(karaoke=False)
    editor.show()
    qapp.processEvents()
    assert (editor.width(), editor.height()) == (1000, 900)
    editor.close()
    assert load_all_settings()["playback"] == {"volume": 42}
