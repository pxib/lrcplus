"""Check bounded retention and work without relying on timing thresholds."""

from collections import OrderedDict
from concurrent.futures import Future
from pathlib import Path
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QRect, QThread, QTimer, Qt
from PySide6.QtGui import QImage, QPainter, QPixmap
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QLabel, QLineEdit, QStyleOptionViewItem,
    QTreeWidget, QVBoxLayout, QWidget,
)

from core.audio_controller import AudioController
from core.music_library import LibraryTrack
from ui.lrc_editor import LrcEditorDialog
from ui.main_window import Player
from ui.player_dialogs import AlbumCoverDelegate
from ui.settings_dialog import PluginSettingsDialog, SettingsDialog


def test_album_shuffle_resolves_paths_linearly(tmp_path, monkeypatch):
    tracks = [LibraryTrack(str(tmp_path / f"song-{i}.mp3"), str(i),
                           artist="Artist", album=f"Album {i // 10}")
              for i in range(100)]
    controller = AudioController()
    controller.library_tracks = tracks
    controller.playback_settings = {"shuffle_mode": "albums"}
    paths = [Path(track.path) for track in tracks]
    original_resolve = Path.resolve
    calls = []

    def resolve(path, *args, **kwargs):
        calls.append(path)
        return original_resolve(path, *args, **kwargs)

    monkeypatch.setattr(Path, "resolve", resolve)
    monkeypatch.setattr("core.audio_controller.random.choice", lambda keys: keys[0])
    assert controller._choose_shuffle_index(paths, 0) == 10
    assert len(calls) <= 2 * len(tracks)
    assert controller._choose_shuffle_index(paths, 0, -1) == 19


def test_album_shuffle_keeps_folder_fallback(tmp_path, monkeypatch):
    controller = AudioController()
    controller.library_tracks = []
    controller.playback_settings = {"shuffle_mode": "albums"}
    paths = [tmp_path / "one" / "a.mp3", tmp_path / "one" / "b.mp3",
             tmp_path / "two" / "c.mp3"]
    monkeypatch.setattr("core.audio_controller.random.choice", lambda keys: keys[0])
    assert controller._choose_shuffle_index(paths, 0) == 2
    assert controller._choose_shuffle_index(paths, -1) == 0


def test_editor_is_deleted_after_modal_result(tmp_path, qapp, monkeypatch):
    parent = QWidget()
    parent.get_current_lyrics_path = lambda: tmp_path / "draft.lrcx"
    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    monkeypatch.setattr(LrcEditorDialog, "_refresh_ruby_preview", lambda *args: None)

    def reject(dialog):
        dialog.reject()
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr(LrcEditorDialog, "exec", reject)
    for _ in range(3):
        Player.edit_current_lyrics(parent)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert parent.findChildren(LrcEditorDialog) == []
    parent.deleteLater()


@pytest.mark.parametrize("outcome", ["accept", "reject", "exec_error", "apply_error"])
def test_settings_are_deleted_after_every_open(qapp, monkeypatch, outcome):
    window = Player()
    qapp.processEvents()

    def finish(dialog):
        if outcome == "exec_error":
            raise RuntimeError("settings execution failed")
        if outcome == "reject":
            dialog.reject()
        else:
            dialog.accept()
        return dialog.result()

    def apply_error(dialog):
        raise RuntimeError("settings apply failed")

    monkeypatch.setattr(SettingsDialog, "exec", finish)
    if outcome == "apply_error":
        monkeypatch.setattr(SettingsDialog, "get_startup_settings", apply_error)
    try:
        for _ in range(3):
            if outcome.endswith("error"):
                with pytest.raises(RuntimeError, match="settings .* failed"):
                    window.open_settings()
            else:
                window.open_settings()
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert window.findChildren(SettingsDialog) == []
    finally:
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


@pytest.mark.parametrize("outcome", ["accept", "reject", "error"])
def test_plugin_settings_release_widgets_after_every_open(qapp, monkeypatch, outcome):
    parent = QWidget()
    saved = []
    parent.plugin_manager = SimpleNamespace(get_settings_widgets=lambda: [{
        "owner": "example", "widget_factory": lambda body: QLabel("Settings", body),
        "apply": lambda widget: saved.append(widget.text()),
    }])

    def finish(dialog):
        if outcome == "error":
            raise RuntimeError("plugin settings failed")
        if outcome == "accept":
            dialog._save()
        else:
            dialog.reject()
        return dialog.result()

    monkeypatch.setattr(PluginSettingsDialog, "exec", finish)
    try:
        for _ in range(3):
            if outcome == "error":
                with pytest.raises(RuntimeError, match="plugin settings failed"):
                    SettingsDialog._open_plugin_settings(parent, "example", "Example")
            else:
                SettingsDialog._open_plugin_settings(parent, "example", "Example")
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            assert parent.findChildren(PluginSettingsDialog) == []
        assert saved == (["Settings"] * 3 if outcome == "accept" else [])
    finally:
        parent.deleteLater()


def test_line_history_copies_only_changed_line_and_handles_bulk_edits(editor):
    untouched = editor.lines[1]
    editor.line_text.setPlainText("Changed")
    state = editor._undo_stack[-1]
    assert state["line_index"] == 0
    assert len(state["lines"]) == 1
    assert state["lines"][0].text == "Hello"
    assert editor.lines[1] is untouched

    editor._apply_timing_shift(250, 0, 1)
    editor.undo()
    assert editor.lines[0].text == "Changed"
    assert editor.lines[1].start == 5
    editor.undo()
    assert editor.lines[0].text == "Hello"
    assert editor.lines[1].text == "World"
    editor.redo()
    editor.redo()
    assert editor.lines[0].text == "Changed"
    assert editor.lines[1].start == 5.25


def test_closing_editor_stops_active_waveform_decoder(editor):
    stopped = threading.Event()
    started = threading.Event()

    class Decoder(QThread):
        def run(self):
            started.set()
            stopped.wait(3)

        def cancel(self):
            stopped.set()

    worker = Decoder(editor.lyrics_waveform)
    editor.lyrics_waveform._worker = worker
    worker.start()
    try:
        assert started.wait(2)
        editor.reject()
        assert stopped.is_set() and not worker.isRunning()
        assert editor.lyrics_waveform._worker is None
    finally:
        worker.cancel()
        worker.wait()


def test_reading_worker_finishing_after_close_does_not_emit(tmp_path, qapp, monkeypatch):
    pending = []
    emitted = []
    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    monkeypatch.setattr(LrcEditorDialog, "_refresh_ruby_preview", lambda *args: None)
    dialog = LrcEditorDialog(tmp_path / "draft.lrcx")
    dialog._reading_generation_finished.connect(lambda *args: emitted.append(args))
    monkeypatch.setattr("ui.lrc_editor.build_furigana_segments",
                        lambda text: [{"text": text, "reading": "reading"}])
    monkeypatch.setattr("ui.lrc_editor.threading.Thread", lambda target, **kwargs:
                        SimpleNamespace(start=lambda: pending.append(target)))
    dialog._start_background_reading_generation(["Hello"], 0)
    dialog.reject()
    dialog.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    pending[0]()
    assert emitted == []


@pytest.fixture
def library(qapp):
    window = QWidget()
    tree = QTreeWidget(window)
    tree.setColumnCount(12)
    QVBoxLayout(window).addWidget(tree)
    player = SimpleNamespace(
        library_table=tree, library_search=QLineEdit(window),
        _library_search_timer=QTimer(window), library_columns=list(range(12)),
        _library_expansion_before_search=None,
        library_folder_label=QLabel(window), library_folders=[],
        library_tracks=[LibraryTrack(f"C:/music/{i}.mp3", f"Song {i}",
                                     artist="Artist", album=f"Album {i // 10}")
                        for i in range(300)],
        _library_cover_cache=OrderedDict(), _library_cover_cache_limit=150,
        _library_cover_load_pending=False,
        _library_track_number=Player._library_track_number,
        _format_library_duration=Player._format_library_duration,
        _format_library_size=Player._format_library_size,
        _queue_visible_library_covers=lambda: None,
        resize=window.resize, show=window.show,
    )
    player._filter_library_view = lambda: Player._filter_library_view(player)
    player._library_cover_pixmap = lambda path: Player._library_cover_pixmap(player, path)
    player._load_visible_library_covers = lambda: Player._load_visible_library_covers(player)
    player._library_search_timer.setSingleShot(True)
    player.library_search.textChanged.connect(lambda text: player._library_search_timer.start(150))
    Player._populate_library_view(player)
    yield player
    player._library_search_timer.stop()
    player._library_cover_cache.clear()
    player._library_filter_groups.clear()
    window.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_library_filter_reuses_rows_and_hides_empty_albums(library):
    first = library.library_table.topLevelItem(0)
    second = library.library_table.topLevelItem(1)
    child = first.child(0)
    library.library_search.setText("SONG 0")
    assert library._library_search_timer.isActive()
    library._filter_library_view()
    assert library.library_table.topLevelItem(0) is first
    assert first.child(0) is child
    assert not first.isHidden() and not child.isHidden()
    assert first.child(1).isHidden() and second.isHidden()
    library.library_search.setText("")
    library._filter_library_view()
    assert not second.isHidden() and not first.child(1).isHidden()


def test_library_search_expands_results_and_restores_previous_state(library):
    first = library.library_table.topLevelItem(0)
    second = library.library_table.topLevelItem(1)
    third = library.library_table.topLevelItem(2)
    first.setExpanded(False)
    second.setExpanded(True)
    third.setExpanded(False)

    library.library_search.setText("song")
    library._filter_library_view()
    assert all(parent.isExpanded() for parent, _rows in library._library_filter_groups)

    # Changing the query or expanding/collapsing results must not replace
    # the state captured before the search began.
    first.setExpanded(False)
    library.library_search.setText("song 0")
    library._filter_library_view()
    assert first.isExpanded()
    library.library_search.setText("no matching tracks")
    library._filter_library_view()
    assert all(parent.isHidden() for parent, _rows in library._library_filter_groups)

    library.library_search.setText("   ")
    library._filter_library_view()
    assert not first.isExpanded()
    assert second.isExpanded()
    assert not third.isExpanded()
    assert all(not parent.isHidden() for parent, _rows in library._library_filter_groups)

    # A later search takes a fresh snapshot.
    second.setExpanded(False)
    library.library_search.setText("song 1")
    library._filter_library_view()
    assert second.isExpanded()
    library.library_search.clear()
    library._filter_library_view()
    assert not second.isExpanded()


def test_library_search_restores_expansion_after_rebuilding_rows(library):
    library.library_table.topLevelItem(0).setExpanded(False)
    library.library_table.topLevelItem(1).setExpanded(True)
    library.library_search.setText("song")
    library._filter_library_view()
    Player._populate_library_view(library)
    assert library.library_table.topLevelItem(0).isExpanded()
    library.library_search.clear()
    library._filter_library_view()
    assert not library.library_table.topLevelItem(0).isExpanded()
    assert library.library_table.topLevelItem(1).isExpanded()


def test_scrolling_covers_stays_bounded_without_row_references(library, qapp, monkeypatch):
    def thumbnail(*args):
        pixmap = QPixmap(52, 52)
        pixmap.fill(Qt.GlobalColor.red)
        return pixmap

    monkeypatch.setattr("ui.main_window.load_embedded_cover_thumbnail", thumbnail)
    library.resize(500, 400)
    library.show()
    qapp.processEvents()
    rows = [item for _parent, group in library._library_filter_groups for item, _ in group]
    for item in rows[::5]:
        library.library_table.scrollToItem(item, QAbstractItemView.ScrollHint.PositionAtTop)
        qapp.processEvents()
        library._load_visible_library_covers()
    assert len(library._library_cover_cache) == 150
    assert all(item.data(0, Qt.ItemDataRole.DecorationRole) is None for item in rows)
    assert library._library_cover_cache[rows[-1].data(0, Qt.ItemDataRole.UserRole)].width() == 52


def test_cover_delegate_paints_from_cache_without_row_decoration(library):
    item = library.library_table.topLevelItem(0).child(0)
    path = item.data(0, Qt.ItemDataRole.UserRole)
    pixmap = QPixmap(52, 52)
    pixmap.fill(Qt.GlobalColor.red)
    library._library_cover_cache[path] = pixmap
    delegate = AlbumCoverDelegate(library.library_table, library._library_cover_cache)
    option = QStyleOptionViewItem()
    option.rect = QRect(0, 0, 62, 58)
    image = QImage(62, 58, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.black)
    painter = QPainter(image)
    try:
        delegate.paint(painter, option, library.library_table.indexFromItem(item, 0))
    finally:
        painter.end()
    assert image.pixelColor(31, 29) == Qt.GlobalColor.red
    assert image.pixelColor(0, 0) == Qt.GlobalColor.black
    assert item.data(0, Qt.ItemDataRole.DecorationRole) is None


def test_superseded_artwork_is_cancelled_and_stops_polling(monkeypatch):
    callbacks = []
    futures = []

    def submit(*args):
        future = Future()
        futures.append(future)
        return future

    images = []
    player = SimpleNamespace(
        _booru_request_id=0, _booru_future=None,
        _booru_executor=SimpleNamespace(submit=submit), appearance_settings={},
        lyrics_background=SimpleNamespace(set_artwork_bytes=lambda data: images.append(data) or True),
    )
    player._invalidate_booru_request = lambda: Player._invalidate_booru_request(player)
    monkeypatch.setattr("ui.main_window.QTimer", SimpleNamespace(
        singleShot=lambda delay, callback: callbacks.append(callback),
    ))
    Player._start_booru_fetch(player, "old.mp3", fallback_data=b"old cover")
    futures[0].set_running_or_notify_cancel()  # In-flight downloads cannot be cancelled.
    Player._start_booru_fetch(player, "queued.mp3")
    Player._start_booru_fetch(player, "latest.mp3")
    assert futures[1].cancelled()
    assert not futures[0].cancelled()
    stale, cancelled, latest = callbacks
    callbacks.clear()
    stale()
    cancelled()
    assert callbacks == []  # No stale polling or artwork updates.
    futures[2].set_result((b"latest cover", {}))
    latest()
    assert images == [b"latest cover"]
    assert player._booru_future is None
