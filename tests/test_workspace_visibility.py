import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Qt
from PySide6.QtGui import QPixmap

from core.music_library import LibraryTrack
from ui.main_window import Player


@pytest.fixture
def workspace(qapp, monkeypatch):
    loaded = []

    def thumbnail(path, size):
        loaded.append(path)
        pixmap = QPixmap(size)
        pixmap.fill(Qt.GlobalColor.red)
        return pixmap

    monkeypatch.setattr("ui.main_window.load_embedded_cover_thumbnail", thumbnail)
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
