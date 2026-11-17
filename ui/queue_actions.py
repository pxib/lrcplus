"""Connect the library and queue manager to core queue operations."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QFileDialog, QMenu, QMessageBox

from core.music_library import read_track
from core.playlists import available_tracks, path_key
from ui.playlist_dialog import PlaylistDialog
from ui.track_actions import add_saved_playlist_menu, add_track_file_actions, add_favorite_action


class QueueActionsMixin:
    def _register_queue_paths(self, paths):
        known = {track.path for track in self.library_tracks}
        changed = False
        for path in paths:
            resolved = str(Path(path).resolve())
            if path_key(resolved) in self.library_excluded_paths:
                continue
            if resolved not in known:
                try:
                    self.library_tracks.append(read_track(resolved))
                    if resolved not in self.library_files:
                        self.library_files.append(resolved)
                    known.add(resolved)
                    changed = True
                except (OSError, ValueError):
                    pass
        if changed:
            self._save_library_state()
            self._populate_library_view()
            if self._library_scan_worker is not None:
                self.rescan_music_library()

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
        play = menu.addAction("Play now", lambda: self._play_library_selection(paths))
        play.setToolTip("Insert the selected tracks after the current song and start playing them.")
        menu.addAction("Play next", lambda: self._enqueue_from_ui(paths, True))
        menu.addAction("Add to queue", lambda: self._enqueue_from_ui(paths))
        add_saved_playlist_menu(menu, paths, self)
        if hasattr(self, "favorite_paths"):
            add_favorite_action(menu, paths, self.favorite_paths, self._set_tracks_favorite)
        menu.addSeparator()
        add_track_file_actions(menu, paths, self)
        remove = menu.addAction("Remove from library", lambda: self.remove_library_tracks(paths))
        remove.setToolTip("Keep files on disk and leave the queue and saved playlists unchanged. Exclude from future rescans.")
        menu.addSeparator()
        menu.addAction("Queue and saved playlists…", self.show_playlist)
        try:
            menu.exec(self.library_table.viewport().mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _play_library_selection(self, paths):
        result = available_tracks(paths)
        if result.paths:
            # Insert after the current occurrence, preserving the rest of the
            # queue and the selected library order, then start that selection.
            index = max(0, min(self.current_index + 1, len(self.playlist)))
            try:
                self.enqueue_tracks(result.paths, play_next=True)
                self._register_queue_paths(result.paths)
                self.play_queue_index(index)
            except OSError as error:
                QMessageBox.warning(self, "Queue", str(error))
        if result.unavailable:
            QMessageBox.warning(self, "Unavailable Tracks", f"Skipped {len(result.unavailable)} missing or unsupported file(s).")

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
        dialog.favorite_tracks_requested.connect(self._set_tracks_favorite)
        def refresh_favorites():
            dialog.favorite_paths = self.favorite_paths
        self.favorites_changed.connect(refresh_favorites)
        self.queue_changed.connect(refresh_dialog)
        try:
            dialog.exec()
        finally:
            self.queue_changed.disconnect(refresh_dialog)
            self.favorites_changed.disconnect(refresh_favorites)
            dialog.deleteLater()
