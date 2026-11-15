"""File and saved-playlist actions shared by track context menus."""

from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt, QUrl
from PySide6.QtGui import QDesktopServices
from PySide6.QtWidgets import (
    QApplication, QDialog, QDialogButtonBox, QFormLayout, QInputDialog,
    QLabel, QMessageBox, QVBoxLayout,
)

from core.music_library import read_track
from core.playlists import load_saved_playlists, save_playlist
from core.favorites import favorite_key


def add_favorite_action(menu, paths, favorites, callback):
    paths = list(paths)
    favorite = bool(paths) and all(favorite_key(path) in favorites for path in paths)
    label = "Remove from favorites" if favorite else "Add to favorites"
    action = menu.addAction(label, lambda: callback(paths, not favorite))
    action.setEnabled(bool(paths))
    return action


def add_saved_playlist_menu(menu, paths, parent, on_saved=None):
    """Save the captured selection without changing the playback queue."""
    paths = list(paths)
    submenu = menu.addMenu("Add to saved playlist")

    def save_selection(name=None):
        if name is None:
            name, accepted = QInputDialog.getText(parent, "Save Selected Tracks", "New playlist name:")
            if not accepted:
                return
            existing = None
        else:
            # Read again when clicked: another window may have edited it.
            existing = load_saved_playlists().get(name)
            if existing is None:
                QMessageBox.warning(parent, "Playlists", "That saved playlist no longer exists.")
                return
        try:
            saved = save_playlist(name, paths if existing is None else [*existing, *paths],
                                  overwrite=existing is not None)
        except (OSError, ValueError) as error:
            QMessageBox.warning(parent, "Playlists", str(error))
            return
        if on_saved is not None:
            on_saved(saved)

    submenu.addAction("New playlist…", lambda: save_selection())
    names = sorted(load_saved_playlists(), key=str.casefold)
    if names:
        submenu.addSeparator()
        for name in names:
            action = submenu.addAction(name.replace("&", "&&"),
                                       lambda checked=False, name=name: save_selection(name))
            action.setToolTip(f'Append {len(paths)} selected track(s) to "{name}".')
    submenu.setEnabled(bool(paths))
    return submenu


def open_file_location(path, parent):
    path = Path(path).resolve()
    if path.is_file():
        try:
            if sys.platform == "win32":
                subprocess.Popen(["explorer.exe", "/select,", str(path)])
                return
            if sys.platform == "darwin":
                subprocess.Popen(["open", "-R", str(path)])
                return
        except OSError:
            pass
    if not path.parent.is_dir() or not QDesktopServices.openUrl(QUrl.fromLocalFile(str(path.parent))):
        QMessageBox.warning(parent, "Open File Location", "Could not open the folder for this track.")


def show_track_properties(path, parent):
    try:
        track = read_track(path)
    except (OSError, ValueError) as error:
        QMessageBox.warning(parent, "Track Properties", f"Could not read this track:\n{error}")
        return
    seconds = max(0, int(track.duration))
    minutes, seconds = divmod(seconds, 60)
    hours, minutes = divmod(minutes, 60)
    duration = f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"
    fields = [
        ("Title", track.display_title), ("Artist", track.artist),
        ("Album", track.album), ("Album artist", track.album_artist),
        ("Date", track.date), ("Track", track.track_number), ("Disc", track.disc_number),
        ("Duration", duration), ("Codec", track.codec),
        ("Bitrate", f"{track.bitrate} kbps" if track.bitrate else ""),
        ("File size", f"{track.file_size / (1024 * 1024):.2f} MiB"), ("File path", track.path),
    ]
    dialog = QDialog(parent)
    dialog.setWindowTitle("Track Properties")
    dialog.resize(520, 360)
    layout = QVBoxLayout(dialog)
    form = QFormLayout()
    form.setFieldGrowthPolicy(QFormLayout.FieldGrowthPolicy.AllNonFixedFieldsGrow)
    for title, value in fields:
        label = QLabel(str(value) if value else "—")
        label.setTextFormat(Qt.TextFormat.PlainText)
        label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        label.setWordWrap(True)
        form.addRow(f"{title}:", label)
    layout.addLayout(form)
    buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
    buttons.rejected.connect(dialog.reject)
    layout.addWidget(buttons)
    try:
        dialog.exec()
    finally:
        dialog.deleteLater()


def add_track_file_actions(menu, paths, parent):
    paths = [Path(path) for path in paths]
    single = len(paths) == 1
    location = menu.addAction("Open file location", lambda: open_file_location(paths[0], parent))
    location.setEnabled(single and paths[0].parent.is_dir())
    copy_menu = menu.addMenu("Copy")
    copy_menu.setEnabled(bool(paths))
    copy_menu.addAction("File paths", lambda: QApplication.clipboard().setText("\n".join(map(str, paths))))
    copy_menu.addAction("File names", lambda: QApplication.clipboard().setText("\n".join(path.name for path in paths)))
    properties = menu.addAction("Track properties…", lambda: show_track_properties(paths[0], parent))
    properties.setEnabled(single and paths[0].is_file())
