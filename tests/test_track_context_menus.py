from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import (
    QAbstractItemView, QDialog, QInputDialog, QLabel, QMenu, QMessageBox,
    QTreeWidget, QTreeWidgetItem, QWidget,
)
from shiboken6 import isValid

from core.music_library import LibraryTrack
from core.playlists import load_saved_playlists, save_playlist
from ui.playlist_dialog import PlaylistDialog
from ui.queue_actions import QueueActionsMixin
from ui import track_actions


@pytest.fixture
def tracks(tmp_path):
    paths = [tmp_path / f"{name}.mp3" for name in ("日本語 & song", "second", "third", "fourth")]
    for path in paths:
        path.touch()
    return paths


def action(menu, label):
    # Retain wrappers while inspecting menus: dropping a temporary submenu
    # QAction wrapper can destroy its QMenu in this PySide build.
    menu._test_actions = menu.actions()
    return next(item for item in menu._test_actions if item.text() == label)


def intercept_menu(monkeypatch, module, callback):
    # Shiboken installs exec on each instance, shadowing class monkeypatches.
    def make_menu(*args, **kwargs):
        menu = QMenu(*args, **kwargs)
        menu.exec = lambda pos: callback(menu, pos)
        return menu

    monkeypatch.setattr(f"{module}.QMenu", make_menu)


def track_point(widget, index):
    widget.show()
    return widget.visualItemRect(widget.topLevelItem(0).child(index)).center()


class LibraryWindow(QueueActionsMixin, QWidget):
    def __init__(self, tracks):
        super().__init__()
        self.library_table = QTreeWidget(self)
        self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        album = QTreeWidgetItem(self.library_table, ["Album"])
        for path in tracks:
            item = QTreeWidgetItem(album, [path.name])
            item.setData(0, Qt.ItemDataRole.UserRole, str(path))
        album.setExpanded(True)
        self.calls = []

    def show_playlist(self):
        self.calls.append("show_playlist")

    def _enqueue_from_ui(self, paths, play_next=False):
        self.calls.append((list(paths), play_next))


def test_library_context_menu_preserves_album_selection_and_retargets_unselected_track(qapp, tracks, monkeypatch):
    window = LibraryWindow(tracks)
    album = window.library_table.topLevelItem(0)
    album.setSelected(True)
    album.child(1).setSelected(True)
    menus = []

    def use_menu(menu, pos):
        menus.append(menu)
        action(menu, "Add to queue").trigger()
        copy = action(menu, "Copy").menu()
        action(copy, "File paths").trigger()

    intercept_menu(monkeypatch, "ui.queue_actions", use_menu)
    try:
        window._show_library_queue_menu(track_point(window.library_table, 1))
        assert window.calls == [([str(path) for path in tracks], False)]
        assert qapp.clipboard().text().splitlines() == list(map(str, tracks))
        album.setSelected(False)
        album.child(1).setSelected(False)
        album.child(0).setSelected(True)
        window._show_library_queue_menu(track_point(window.library_table, 2))
        assert window.calls[-1] == ([str(tracks[2])], False)
        assert qapp.clipboard().text() == str(tracks[2])
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert all(not isValid(menu) for menu in menus)
    finally:
        window.deleteLater()


def test_playlist_selection_appends_fresh_contents_and_keeps_duplicates(qapp, tracks):
    save_playlist("R&B", [tracks[0]])
    menu = QMenu()
    saved = []
    submenu = track_actions.add_saved_playlist_menu(menu, [tracks[1], tracks[1]], menu, saved.append)
    save_playlist("R&B", [tracks[2]], overwrite=True)
    try:
        action(submenu, "R&&B").trigger()
        assert load_saved_playlists()["R&B"] == list(map(str, [tracks[2], tracks[1], tracks[1]]))
        assert saved == ["R&B"]
    finally:
        menu.deleteLater()


def test_new_playlist_does_not_overwrite_existing_and_cancel_does_nothing(qapp, tracks, monkeypatch):
    save_playlist("Keep", [tracks[0]])
    menu = QMenu()
    submenu = track_actions.add_saved_playlist_menu(menu, [tracks[1]], menu)
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("keep", True))
    try:
        action(submenu, "New playlist…").trigger()
        assert warnings
        assert load_saved_playlists() == {"Keep": [str(tracks[0])]}
        monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("New", False))
        action(submenu, "New playlist…").trigger()
        assert "New" not in load_saved_playlists()
        monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("New", True))
        action(submenu, "New playlist…").trigger()
        assert load_saved_playlists()["New"] == [str(tracks[1])]
    finally:
        menu.deleteLater()


@pytest.mark.parametrize("top,expected,selected", [
    (True, [0, 3, 1, 2], [0, 1]),
    (False, [1, 2, 0, 3], [2, 3]),
])
def test_queue_context_move_preserves_duplicate_occurrences_and_clicked_play_target(qapp, tracks, monkeypatch, top, expected, selected):
    paths = [tracks[0], tracks[1], tracks[2], tracks[0]]
    dialog = PlaylistDialog(paths, 3)
    dialog.show()
    dialog._select_indices([0, 3])
    orders, played = [], []

    def reorder(order):
        orders.append(order)
        dialog.set_playlist([paths[index] for index in order], order.index(3))

    dialog.order_requested.connect(reorder)
    dialog.track_requested.connect(played.append)

    def use_menu(menu, pos):
        assert dialog._selected_indices() == [0, 3]
        assert not action(menu, "Open file location").isEnabled()
        action(menu, "Play").trigger()
        move = action(menu, "Move").menu()
        edge = action(move, "To top" if top else "To bottom")
        assert edge.isEnabled()
        edge.trigger()

    intercept_menu(monkeypatch, "ui.playlist_dialog", use_menu)
    try:
        dialog._show_track_menu(dialog.list_widget.visualItemRect(dialog.list_widget.item(3)).center())
        assert played == [3]
        assert orders == [expected]
        assert dialog._selected_indices() == selected
        assert dialog.playlist[dialog.current_index] == tracks[0]
        assert dialog.current_index == expected.index(3)
    finally:
        dialog.deleteLater()


def test_queue_context_saved_playlist_refreshes_picker_without_changing_queue(qapp, tracks, monkeypatch):
    dialog = PlaylistDialog(tracks, 2)
    dialog.show()
    dialog._select_indices([1, 3])
    monkeypatch.setattr(QInputDialog, "getText", lambda *args: ("Selected", True))

    def use_menu(menu, pos):
        action(action(menu, "Add to saved playlist").menu(), "New playlist…").trigger()

    intercept_menu(monkeypatch, "ui.playlist_dialog", use_menu)
    try:
        dialog._show_track_menu(dialog.list_widget.visualItemRect(dialog.list_widget.item(1)).center())
        assert load_saved_playlists()["Selected"] == list(map(str, [tracks[1], tracks[3]]))
        assert dialog.saved_combo.currentText() == "Selected"
        assert dialog.playlist == tracks
        assert dialog.current_index == 2
    finally:
        dialog.deleteLater()


def test_file_location_falls_back_to_folder_after_native_failure(qapp, tracks, monkeypatch):
    calls = []

    def fail(command):
        calls.append(command)
        raise OSError("Explorer unavailable")

    monkeypatch.setattr(track_actions.sys, "platform", "win32")
    monkeypatch.setattr(track_actions.subprocess, "Popen", fail)
    monkeypatch.setattr(track_actions.QDesktopServices, "openUrl", lambda url: calls.append(url.toLocalFile()) or True)
    track_actions.open_file_location(tracks[0], None)
    assert calls[0] == ["explorer.exe", "/select,", str(tracks[0])]
    assert Path(calls[1]) == tracks[0].parent


def test_properties_display_metadata_as_plain_text_and_release_dialog(qapp, tracks, monkeypatch):
    parent = QWidget()
    monkeypatch.setattr(track_actions, "read_track", lambda path: LibraryTrack(
        str(path), "<b>Literal title</b>", artist="Artist", duration=3661,
    ))

    def inspect(dialog):
        labels = dialog.findChildren(QLabel)
        title = next(label for label in labels if label.text() == "<b>Literal title</b>")
        assert title.textFormat() == Qt.TextFormat.PlainText
        assert any(label.text() == "1:01:01" for label in labels)
        dialog.reject()

    def make_dialog(*args):
        dialog = QDialog(*args)
        dialog.exec = lambda: inspect(dialog)
        return dialog

    monkeypatch.setattr(track_actions, "QDialog", make_dialog)
    try:
        track_actions.show_track_properties(tracks[0], parent)
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert not parent.findChildren(QDialog)
    finally:
        parent.deleteLater()
