"""Connect the library and queue manager to core queue operations."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox

from core.music_library import read_track
from core.playlists import available_tracks
from core.settings import save_music_library
from ui.playlist_dialog import PlaylistDialog


class QueueActionsMixin:
    def _register_queue_paths(self, paths):
        known = {track.path for track in self.library_tracks}
        changed = False
        for path in paths:
            resolved = str(Path(path).resolve())
            if resolved not in known:
                try:
                    self.library_tracks.append(read_track(resolved))
                    known.add(resolved)
                    changed = True
                except (OSError, ValueError):
                    pass
        if changed:
            save_music_library(self.library_folders, [track.to_dict() for track in self.library_tracks], self.library_column_state)
            self._populate_library_view()

    def _enqueue_from_ui(self, paths, play_next=False):
        result = available_tracks(paths)
        try:
            self.enqueue_tracks(result.paths, play_next=play_next)
            self._register_queue_paths(result.paths)
        except OSError as error:
            QMessageBox.warning(self, "Queue", str(error))
        if result.unavailable:
            QMessageBox.warning(self, "Unavailable Tracks", f"Skipped {len(result.unavailable)} missing or unsupported file(s).")

    def _replace_queue_from_ui(self, paths):
        try:
            self.replace_queue(paths)
            self._register_queue_paths(paths)
        except OSError as error:
            QMessageBox.warning(self, "Queue", str(error))

    def add_files_to_queue(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add to Queue", "",
                                             "Audio Files (*.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wav *.wma *.aiff *.ape);;All Files (*)")
        if paths:
            self._enqueue_from_ui(paths)

    def _selected_library_paths(self):
        # Walk in library order and expand selected album headers. Selecting an
        # album and one of its tracks includes that track only once.
        paths = []
        for i in range(self.library_table.topLevelItemCount()):
            album = self.library_table.topLevelItem(i)
            if album.isHidden():
                continue
            for j in range(album.childCount()):
                item = album.child(j)
                if not item.isHidden() and (album.isSelected() or item.isSelected()):
                    path = item.data(0, Qt.ItemDataRole.UserRole)
                    if path:
                        paths.append(path)
        return paths

    def _show_library_queue_menu(self, pos):
        item = self.library_table.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.library_table.clearSelection()
            self.library_table.setCurrentItem(item)
            item.setSelected(True)
        paths = self._selected_library_paths()
        if not paths:
            return
        menu = QMenu(self)
        menu.addAction("Play next", lambda: self._enqueue_from_ui(paths, True))
        menu.addAction("Add to queue", lambda: self._enqueue_from_ui(paths))
        menu.addSeparator()
        menu.addAction("Queue and saved playlists…", self.show_playlist)
        menu.exec(self.library_table.viewport().mapToGlobal(pos))

    def show_playlist(self):
        dialog = PlaylistDialog(self.playlist, self.current_index, self)

        def refresh_dialog():
            dialog.set_playlist(self.playlist, self.current_index)

        def play(index):
            if not 0 <= index < len(self.playlist):
                return
            if not Path(self.playlist[index]).is_file():
                QMessageBox.warning(dialog, "Unavailable Track", "This file is missing. Remove it or choose another track.")
                return
            try:
                self.play_queue_index(index)
            except OSError as error:
                QMessageBox.warning(dialog, "Queue", str(error))

        dialog.track_requested.connect(play)
        dialog.remove_tracks_requested.connect(self.remove_queue_tracks)
        dialog.play_next_requested.connect(self.move_queue_tracks_next)
        dialog.clear_requested.connect(self.clear_queue)
        dialog.order_requested.connect(self.reorder_queue)
        dialog.add_requested.connect(self._enqueue_from_ui)
        dialog.replace_requested.connect(self._replace_queue_from_ui)
        self.queue_changed.connect(refresh_dialog)
        try:
            dialog.exec()
        finally:
            self.queue_changed.disconnect(refresh_dialog)
            dialog.deleteLater()
