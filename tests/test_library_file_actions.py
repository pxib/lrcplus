"""Library removal and file drops preserve disk files and transport state."""

from pathlib import Path
import time

import pytest
from PySide6.QtCore import QMimeData, QPoint, QPointF, Qt, QUrl
from PySide6.QtGui import QDragEnterEvent, QDragLeaveEvent, QDropEvent
from PySide6.QtWidgets import QApplication

from core.music_library import LibraryTrack, read_track, scan_folders
from core.playlists import path_key, save_playlist, load_saved_playlists
from core.settings import load_music_library, save_music_library, save_all_settings
from ui.file_drops import audio_drop_paths
from ui.main_window import Player
from ui.playlist_dialog import PlaylistDialog


def wait_for_scan(qapp, window):
    deadline = time.monotonic() + 5
    while window._library_scan_worker is not None and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.001)
    assert window._library_scan_worker is None


def mime_for(paths):
    mime = QMimeData()
    mime.setUrls([QUrl.fromLocalFile(str(path)) for path in paths])
    return mime


def drop_on(widget, paths):
    mime = mime_for(paths)
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, enter)
    drop = QDropEvent(QPointF(10, 10), Qt.DropAction.CopyAction, mime,
                      Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(widget, drop)
    return enter, drop


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    window.show()
    qapp.processEvents()
    yield window
    wait_for_scan(qapp, window)
    window.close()
    window.deleteLater()
    qapp.processEvents()


def test_drop_files_and_folders_deduplicates_and_survives_rescan(window, qapp, tmp_path):
    folder = tmp_path / "album"
    folder.mkdir()
    first = folder / "first.MP3"
    first.touch()
    loose = tmp_path / "loose.flac"
    loose.touch()
    text = folder / "notes.txt"
    text.touch()
    window.library_search.setText("hidden by an old search")
    window.library_favorites_filter.setChecked(True)
    enter, drop = drop_on(window, [folder, first, loose, loose, text])
    assert enter.isAccepted() and drop.isAccepted()
    wait_for_scan(qapp, window)
    assert {track.path for track in window.library_tracks} == {str(first.resolve()), str(loose.resolve())}
    assert window.current_audio_path is None
    assert window.playlist == []
    assert len(window._selected_library_paths()) == 2
    assert window.workspace_stack.currentWidget() is window.library_page
    assert window.library_search.text() == ""
    assert not window.library_favorites_filter.isChecked()
    assert window.library_folders == [str(folder)]
    window.rescan_music_library()
    wait_for_scan(qapp, window)
    assert len(window.library_tracks) == 2
    saved = load_music_library()
    assert set(saved["files"]) == {str(first), str(loose)}
    other = Player()
    try:
        assert len(other.library_tracks) == 2
        assert set(other.library_files) == set(saved["files"])
    finally:
        other.close()
        other.deleteLater()
        qapp.processEvents()


def test_remove_survives_rescan_and_restart_but_drop_restores(window, qapp, tmp_path):
    song = tmp_path / "song.mp3"
    song.touch()
    drop_on(window, [tmp_path])
    wait_for_scan(qapp, window)
    window.playlist = [song, song]
    window.favorite_paths = {path_key(song)}
    save_playlist("Keep", [song])
    window.remove_library_tracks([song])
    assert window.library_tracks == []
    assert song.is_file()
    assert window.playlist == [song, song]
    assert path_key(song) in window.favorite_paths
    assert load_saved_playlists()["Keep"] == [str(song)]
    window._register_queue_paths([song])
    assert window.library_tracks == []
    window.rescan_music_library()
    wait_for_scan(qapp, window)
    assert window.library_tracks == []
    other = Player()
    try:
        assert path_key(song) in other.library_excluded_paths
        assert other.library_tracks == []
    finally:
        other.close()
        other.deleteLater()
        qapp.processEvents()
    drop_on(window, [song])
    wait_for_scan(qapp, window)
    assert [track.path for track in window.library_tracks] == [str(song)]
    assert path_key(song) not in load_music_library()["excluded_paths"]


def test_unsupported_remote_and_missing_drops_are_rejected(tmp_path):
    text = tmp_path / "notes.txt"
    text.touch()
    mime = mime_for([text, tmp_path / "missing.mp3"])
    mime.setUrls(mime.urls() + [QUrl("https://example.com/song.mp3")])
    assert audio_drop_paths(mime, folders=True) == []


def test_queue_accepts_external_files_and_keeps_internal_reordering(qapp, tmp_path):
    song = tmp_path / "song.mp3"
    song.touch()
    dialog = PlaylistDialog([song, song], 0)
    received, orders = [], []
    dialog.add_requested.connect(lambda paths, next_: received.append((paths, next_)))
    dialog.add_requested.connect(lambda paths, next_: dialog.set_playlist(dialog.playlist + paths, dialog.current_index))
    dialog.order_requested.connect(orders.append)
    try:
        dialog.show()
        qapp.processEvents()
        enter, drop = drop_on(dialog.list_widget.viewport(), [song])
        assert enter.isAccepted() and drop.isAccepted()
        assert received == [([song], False)]
        assert dialog.current_index == 0
        assert dialog._selected_indices() == [2]
        model = dialog.list_widget.model()
        assert model.moveRow(model.index(-1, -1), 0, model.index(-1, -1), 2)
        qapp.processEvents()
        assert orders == [[1, 0, 2]]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_library_scan_supports_individual_files_without_duplicates(tmp_path):
    song = tmp_path / "song.mp3"
    song.touch()
    assert [track.path for track in scan_folders([tmp_path, song])] == [str(song)]


def test_drop_over_library_table_reaches_import_handler(window, qapp, tmp_path):
    song = tmp_path / "table-drop.mp3"
    song.touch()
    enter, drop = drop_on(window.library_table.viewport(), [song])
    assert enter.isAccepted() and drop.isAccepted()
    wait_for_scan(qapp, window)
    assert [track.path for track in window.library_tracks] == [str(song)]


def test_existing_library_writes_keep_exclusions_and_loose_sources(tmp_path):
    song = tmp_path / "song.mp3"
    song.touch()
    save_music_library([], [read_track(song).to_dict()], files=[str(song)], excluded_paths={"excluded"})
    save_music_library([], [])
    saved = load_music_library()
    assert saved["files"] == [str(song)]
    assert saved["excluded_paths"] == ["excluded"]


def test_drop_previews_name_the_destination_and_hide_on_leave(window, qapp, tmp_path):
    song = tmp_path / "preview.mp3"
    song.touch()
    mime = mime_for([song])
    enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                            Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
    QApplication.sendEvent(window, enter)
    assert window._library_drop_indicator.isVisible()
    assert "to Library" in window._library_drop_indicator.text()
    QApplication.sendEvent(window, QDragLeaveEvent())
    assert window._library_drop_indicator.isHidden()
    dialog = PlaylistDialog([], -1)
    try:
        dialog.show()
        qapp.processEvents()
        enter = QDragEnterEvent(QPoint(10, 10), Qt.DropAction.CopyAction, mime,
                                Qt.MouseButton.LeftButton, Qt.KeyboardModifier.NoModifier)
        QApplication.sendEvent(dialog.list_widget.viewport(), enter)
        assert dialog.list_widget._drop_indicator.isVisible()
        assert "end of the queue" in dialog.list_widget._drop_indicator.text()
        QApplication.sendEvent(dialog.list_widget.viewport(), QDragLeaveEvent())
        assert dialog.list_widget._drop_indicator.isHidden()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_legacy_standalone_imports_are_not_lost_by_the_first_rescan(qapp, tmp_path):
    song = tmp_path / "legacy.mp3"
    song.touch()
    save_all_settings({"music_library": {"folders": [], "tracks": [read_track(song).to_dict()]}})
    window = Player()
    try:
        window.rescan_music_library()
        wait_for_scan(qapp, window)
        assert [track.path for track in window.library_tracks] == [str(song)]
        assert load_music_library()["files"] == [str(song)]
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("query", ["", "song"])
def test_removal_keeps_live_library_layout_and_surviving_items(window, qapp, tmp_path, query):
    tracks = []
    for album in range(3):
        for index in range(20):
            path = tmp_path / f"{album}-{index}.mp3"
            path.touch()
            tracks.append(LibraryTrack(str(path), f"song {index:02}", "Artist", f"Album {album}"))
    window.library_tracks = tracks
    window._populate_library_view()
    tree = window.library_table
    header = tree.header()
    tree.setColumnWidth(2, 480)
    tree.setColumnHidden(4, True)
    header.moveSection(header.visualIndex(3), 1)
    tree.sortItems(2, Qt.SortOrder.DescendingOrder)
    window.library_search.setText(query)
    window._filter_library_view()
    groups = [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())]
    groups[0].setExpanded(False)
    kept = groups[1].child(10)
    removed = groups[1].child(0)
    path = removed.data(0, Qt.ItemDataRole.UserRole)
    tree.clearSelection()
    tree.setCurrentItem(kept)
    kept.setSelected(True)
    tree.scrollToItem(kept)
    qapp.processEvents()
    vertical = tree.verticalScrollBar().value()
    horizontal = tree.horizontalScrollBar().value()
    layout = bytes(header.saveState())
    expansion = [group.isExpanded() for group in groups]

    window.remove_library_tracks([path])
    qapp.processEvents()

    assert bytes(header.saveState()) == layout
    assert [tree.topLevelItem(i) for i in range(tree.topLevelItemCount())] == groups
    assert [group.isExpanded() for group in groups] == expansion
    assert tree.currentItem() is kept
    assert tree.selectedItems() == [kept]
    assert tree.verticalScrollBar().value() == vertical
    assert tree.horizontalScrollBar().value() == horizontal
    assert window.library_search.text() == query
    assert Path(path).is_file()
    assert not any(item.data(0, Qt.ItemDataRole.UserRole) == path
                   for parent, rows in window._library_filter_groups for item, _ in rows)
