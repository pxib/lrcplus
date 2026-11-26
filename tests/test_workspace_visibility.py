import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QPixmap
from PySide6.QtTest import QTest

from core.music_library import LibraryTrack
from ui.lrc_editor import LrcEditorDialog
from ui.main_window import Player


@pytest.fixture
def workspace(qapp, monkeypatch):
    loaded = []

    def thumbnail(path, size):
        loaded.append(path)
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.red)
        return pixmap

    monkeypatch.setattr("ui.player_library.load_embedded_cover_thumbnail", thumbnail)
    window = Player()
    window.library_tracks = [LibraryTrack("C:/music/song.mp3", "Song", album="Album")]
    window._populate_library_view()
    window.library_table.topLevelItem(0).setExpanded(True)
    try:
        yield window, loaded
    finally:
        qapp.processEvents()
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_library_covers_load_on_first_show(workspace, qapp):
    window, loaded = workspace
    qapp.processEvents()
    assert loaded == []
    window.show()
    qapp.processEvents()
    assert loaded == ["C:/music/song.mp3"]


def test_queued_covers_wait_until_library_is_visible_again(workspace, qapp):
    window, loaded = workspace
    window.show()
    qapp.processEvents()
    loaded.clear()
    window._library_cover_cache.clear()
    parent = window.library_table.topLevelItem(0)
    window._queue_visible_library_covers()
    assert window._library_cover_load_pending

    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    qapp.processEvents()
    assert loaded == []
    assert not window._library_cover_load_pending
    window._queue_visible_library_covers()
    assert not window._library_cover_load_pending
    window._load_visible_library_covers()
    assert loaded == []

    window.workspace_stack.setCurrentWidget(window.library_page)
    qapp.processEvents()
    assert loaded == ["C:/music/song.mp3"]
    assert window.library_table.topLevelItem(0) is parent
    assert parent.isExpanded()


def test_hidden_library_receives_no_paint_events(workspace, qapp):
    window, _loaded = workspace

    class PaintCounter(QObject):
        count = 0

        def eventFilter(self, watched, event):
            if event.type() == QEvent.Type.Paint:
                self.count += 1
            return False

    counter = PaintCounter(window)
    viewport = window.library_table.viewport()
    viewport.installEventFilter(counter)
    window.show()
    qapp.processEvents()
    assert counter.count > 0

    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    count = counter.count
    window.resize(window.width() + 50, window.height() + 50)
    for _ in range(3):
        viewport.update()
        qapp.processEvents()
    assert counter.count == count

    window.workspace_stack.setCurrentWidget(window.library_page)
    qapp.processEvents()
    assert counter.count > count


def test_workspace_shortcuts_switch_between_library_and_lyrics(workspace, qapp):
    window, _loaded = workspace
    window.show()
    qapp.processEvents()

    window.shortcut_lyrics.activated.emit()
    qapp.processEvents()
    assert window.workspace_stack.currentWidget() is window.lyrics_page

    window.shortcut_library.activated.emit()
    qapp.processEvents()
    assert window.workspace_stack.currentWidget() is window.library_page


def test_number_shortcuts_seek_without_intercepting_search(workspace, qapp, monkeypatch):
    window, _loaded = workspace
    positions = []
    monkeypatch.setattr(window.media_player, "duration", lambda: 120_000)
    monkeypatch.setattr(window.media_player, "setPosition", positions.append)
    window.seek_slider.setRange(0, 120_000)
    window.show()
    window.activateWindow()
    window.library_table.setFocus()
    qapp.processEvents()

    QTest.keyClick(window.library_table, Qt.Key.Key_5)
    QTest.keyClick(window.library_table, Qt.Key.Key_9, Qt.KeyboardModifier.KeypadModifier)
    QTest.keyClick(window.library_table, Qt.Key.Key_0)
    assert positions == [60_000, 108_000, 0]
    assert window.seek_slider.value() == 0

    window.library_search.setFocus()
    QTest.keyClick(window.library_search, Qt.Key.Key_5)
    QTest.keyClick(window.library_search, Qt.Key.Key_9, Qt.KeyboardModifier.KeypadModifier)
    assert window.library_search.text() == "59"
    assert positions == [60_000, 108_000, 0]

    monkeypatch.setattr(window, "_can_seek_source", lambda: False)
    window.library_table.setFocus()
    QTest.keyClick(window.library_table, Qt.Key.Key_5)
    assert positions == [60_000, 108_000, 0]


def test_double_click_lyrics_display_opens_editor(workspace, qapp, monkeypatch, tmp_path):
    window, _loaded = workspace
    opened = []

    def inspect_editor(dialog):
        dialog.show()
        qapp.processEvents()
        opened.append((dialog.lrc_path, dialog.line_text.toPlainText()))
        item = dialog.line_list.currentItem()
        assert dialog.line_list.visualItemRect(item).intersects(dialog.line_list.viewport().rect())
        assert not dialog.document_dirty
        dialog.reject()
        return dialog.result()

    monkeypatch.setattr(LrcEditorDialog, "exec", inspect_editor)
    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    monkeypatch.setattr(window, "_start_furigana_prefetch", lambda: None)
    lyrics_path = tmp_path / "song.lrc"
    lyrics_path.write_text(
        "".join(f"[00:{index:02d}.00]Line {index}\n" for index in range(50)),
        encoding="utf-8",
    )
    window.show()
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.load_lrc_file(lyrics_path)
    qapp.processEvents()
    window.update_lyrics(48_000)
    assert window.current_line == 48

    stack = window.lyric_display_stack
    scrolling = window.scrolling_lyrics_widget
    stack.setCurrentWidget(scrolling)
    scrolling.set_current_line(48, animate=False)
    qapp.processEvents()
    row = scrolling._mounted_rows[48]

    QTest.mouseClick(row, Qt.MouseButton.LeftButton)
    QTest.mouseDClick(row, Qt.MouseButton.RightButton)
    assert opened == []
    QTest.mouseDClick(row, Qt.MouseButton.LeftButton)

    scrolling.clear()
    QTest.mouseDClick(scrolling.container, Qt.MouseButton.LeftButton)

    stack.setCurrentWidget(window.lyric_widget)
    QTest.mouseDClick(window.lyric_widget, Qt.MouseButton.LeftButton)

    window.lyrics_button.click()

    assert opened == [(lyrics_path, "Line 48")] * 3 + [(lyrics_path, "Line 0")]
