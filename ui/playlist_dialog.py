from pathlib import Path

from PySide6.QtCore import Qt, Signal, QTimer, QItemSelectionModel
from PySide6.QtWidgets import (
    QAbstractItemView, QComboBox, QDialog, QFileDialog, QHBoxLayout,
    QInputDialog, QLabel, QListWidget, QListWidgetItem, QMenu, QMessageBox,
    QPushButton, QToolButton, QVBoxLayout,
)

from core.playlists import (
    available_tracks, delete_playlist, load_saved_playlists, read_m3u,
    rename_playlist, save_playlist, write_m3u,
)


class PlaylistDialog(QDialog):
    track_requested = Signal(int)
    remove_tracks_requested = Signal(object)
    play_next_requested = Signal(object)
    clear_requested = Signal()
    order_requested = Signal(object)
    add_requested = Signal(object, bool)
    replace_requested = Signal(object)

    def __init__(self, playlist, current_index, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Queue and Saved Playlists")
        self.resize(820, 540)
        self.playlist = []
        self.current_index = -1
        self._reorder_pending = False

        self.saved_combo = QComboBox()
        self.saved_combo.setMinimumWidth(180)
        self.saved_combo.setPlaceholderText("Select a saved playlist")
        self.saved_combo.currentIndexChanged.connect(self._update_buttons)
        self.load_saved_button = QPushButton("Load")
        self.load_saved_button.clicked.connect(lambda: self._use_saved(append=False))
        self.append_saved_button = QPushButton("Append")
        self.append_saved_button.clicked.connect(lambda: self._use_saved(append=True))
        manage_button = QToolButton()
        manage_button.setText("Save / manage")
        manage_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        manage_menu = QMenu(manage_button)
        self.save_action = manage_menu.addAction("Save queue as…", self._save_as)
        self.update_action = manage_menu.addAction("Update selected playlist…", self._update_saved)
        manage_menu.addSeparator()
        self.rename_action = manage_menu.addAction("Rename selected playlist…", self._rename_saved)
        self.delete_action = manage_menu.addAction("Delete selected playlist…", self._delete_saved)
        manage_button.setMenu(manage_menu)
        saved_row = QHBoxLayout()
        saved_row.addWidget(QLabel("Saved playlists:"))
        saved_row.addWidget(self.saved_combo, 1)
        saved_row.addWidget(self.load_saved_button)
        saved_row.addWidget(self.append_saved_button)
        saved_row.addWidget(manage_button)

        self.title_label = QLabel()
        hint = QLabel("Drag tracks to reorder. Right-click a track for Play next. Repeated tracks are kept.")
        hint.setWordWrap(True)
        self.list_widget = QListWidget()
        self.list_widget.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.list_widget.setDragDropMode(QAbstractItemView.DragDropMode.InternalMove)
        self.list_widget.setDefaultDropAction(Qt.DropAction.MoveAction)
        self.list_widget.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.list_widget.customContextMenuRequested.connect(self._show_track_menu)
        self.list_widget.model().rowsMoved.connect(self._rows_moved)
        self.list_widget.itemSelectionChanged.connect(self._update_buttons)
        self.list_widget.itemDoubleClicked.connect(lambda _item: self._request_selected_track())

        self.load_button = QPushButton("Play")
        self.load_button.clicked.connect(self._request_selected_track)
        self.up_button = QPushButton("Move up")
        self.up_button.clicked.connect(lambda: self._move_selected(-1))
        self.down_button = QPushButton("Move down")
        self.down_button.clicked.connect(lambda: self._move_selected(1))
        self.remove_button = QPushButton("Remove")
        self.remove_button.clicked.connect(self._request_remove_selected)
        self.clear_button = QPushButton("Clear")
        self.clear_button.clicked.connect(self.clear_requested.emit)
        controls = QHBoxLayout()
        for button in (self.load_button, self.up_button, self.down_button, self.remove_button):
            controls.addWidget(button)
        controls.addStretch()
        controls.addWidget(self.clear_button)

        add_button = QPushButton("Add files…")
        add_button.clicked.connect(self._add_files)
        import_button = QToolButton()
        import_button.setText("Import M3U…")
        import_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        import_menu = QMenu(import_button)
        import_menu.addAction("Append to queue…", lambda: self._import_m3u(append=True))
        import_menu.addAction("Replace queue…", lambda: self._import_m3u(append=False))
        import_button.setMenu(import_menu)
        self.export_button = QPushButton("Export M3U…")
        self.export_button.clicked.connect(self._export_m3u)
        close_button = QPushButton("Close")
        close_button.clicked.connect(self.accept)
        files_row = QHBoxLayout()
        for button in (add_button, import_button, self.export_button):
            files_row.addWidget(button)
        files_row.addStretch()
        files_row.addWidget(close_button)
        self.status_label = QLabel()
        self.status_label.setWordWrap(True)

        layout = QVBoxLayout(self)
        layout.addLayout(saved_row)
        layout.addWidget(self.title_label)
        layout.addWidget(hint)
        layout.addWidget(self.list_widget, 1)
        layout.addLayout(controls)
        layout.addWidget(self.status_label)
        layout.addLayout(files_row)
        self.set_playlist(playlist, current_index)
        self._reload_saved()

    def set_playlist(self, playlist, current_index):
        selected = self._selected_indices()
        self.playlist = list(playlist)
        self.current_index = current_index
        self.list_widget.clear()
        self.title_label.setText(f"Queue ({len(self.playlist)} tracks)")
        for index, audio_path in enumerate(self.playlist):
            prefix = "▶ " if index == current_index else ""
            path = Path(audio_path)
            missing = " [missing]" if not path.is_file() else ""
            item = QListWidgetItem(f"{prefix}{index + 1:02d}  {path.name}{missing}")
            item.setData(Qt.ItemDataRole.UserRole, index)
            item.setToolTip(str(path))
            self.list_widget.addItem(item)
        self._select_indices(selected or ([current_index] if current_index >= 0 else []))
        self._update_buttons()

    def _selected_indices(self):
        return sorted(item.data(Qt.ItemDataRole.UserRole) for item in self.list_widget.selectedItems())

    def _selected_index(self):
        item = self.list_widget.currentItem()
        if item is not None and item.isSelected():
            index = item.data(Qt.ItemDataRole.UserRole)
            if isinstance(index, int) and 0 <= index < len(self.playlist):
                return index
        selected = self._selected_indices()
        return selected[0] if selected else None

    def _select_indices(self, indices):
        self.list_widget.clearSelection()
        first = True
        for index in indices:
            item = self.list_widget.item(index)
            if item is not None:
                if first:
                    self.list_widget.setCurrentItem(item, QItemSelectionModel.SelectionFlag.NoUpdate)
                    first = False
                item.setSelected(True)

    def _update_buttons(self, *_args):
        if not hasattr(self, "export_button"):
            return
        selected = self._selected_indices()
        for button in (self.load_button, self.remove_button):
            button.setEnabled(bool(selected))
        self.up_button.setEnabled(bool(selected) and selected[0] > 0)
        self.down_button.setEnabled(bool(selected) and selected[-1] < len(self.playlist) - 1)
        for widget in (self.clear_button, self.export_button, self.save_action):
            widget.setEnabled(bool(self.playlist))
        has_saved = self.saved_combo.currentIndex() >= 0
        for widget in (self.load_saved_button, self.append_saved_button, self.rename_action, self.delete_action):
            widget.setEnabled(has_saved)
        self.update_action.setEnabled(has_saved and bool(self.playlist))

    def _request_selected_track(self):
        index = self._selected_index()
        if index is not None:
            self.track_requested.emit(index)

    def _request_remove_selected(self):
        selected = self._selected_indices()
        if selected:
            self.remove_tracks_requested.emit(selected)

    def _rows_moved(self, *_args):
        # Wait until Qt finishes every row move in a multi-selection drop.
        if not self._reorder_pending:
            self._reorder_pending = True
            QTimer.singleShot(0, self._commit_drag_order)

    def _commit_drag_order(self):
        self._reorder_pending = False
        order = [self.list_widget.item(i).data(Qt.ItemDataRole.UserRole)
                 for i in range(self.list_widget.count())]
        if order != list(range(len(self.playlist))):
            self._request_order(order)

    def _request_order(self, order):
        selected = self._selected_indices()
        self.order_requested.emit(order)
        self._select_indices([order.index(i) for i in selected if i in order])

    def _move_selected(self, direction):
        selected = set(self._selected_indices())
        order = list(range(len(self.playlist)))
        rows = range(len(order)) if direction < 0 else range(len(order) - 1, -1, -1)
        for row in rows:
            other = row + direction
            if order[row] in selected and 0 <= other < len(order) and order[other] not in selected:
                order[row], order[other] = order[other], order[row]
        self._request_order(order)

    def _show_track_menu(self, pos):
        item = self.list_widget.itemAt(pos)
        if item is None:
            return
        if not item.isSelected():
            self.list_widget.setCurrentItem(item)
        menu = QMenu(self)
        menu.addAction("Play", self._request_selected_track)
        menu.addAction("Play next", lambda: self.play_next_requested.emit(self._selected_indices()))
        menu.addAction("Remove from queue", self._request_remove_selected)
        menu.exec(self.list_widget.viewport().mapToGlobal(pos))

    def _reload_saved(self, selected=None):
        selected = selected or self.saved_combo.currentText()
        self.saved_playlists = load_saved_playlists()
        self.saved_combo.clear()
        self.saved_combo.addItems(sorted(self.saved_playlists, key=str.casefold))
        index = self.saved_combo.findText(selected)
        self.saved_combo.setCurrentIndex(index if index >= 0 else (0 if self.saved_playlists else -1))
        self._update_buttons()

    def _confirm_replace(self):
        return not self.playlist or QMessageBox.question(
            self, "Replace Queue", "Replace the current queue? Saved playlists will be kept.",
            QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
            QMessageBox.StandardButton.No,
        ) == QMessageBox.StandardButton.Yes

    def _use_import(self, result, *, append):
        if not result.paths:
            self.status_label.setText("No playable local audio files found. The queue was kept.")
            return
        if not append and not self._confirm_replace():
            return
        if append:
            self.add_requested.emit(result.paths, False)
        else:
            self.replace_requested.emit(result.paths)
        skipped = len(result.unavailable)
        self.status_label.setText(
            f"{'Added' if append else 'Loaded'} {len(result.paths)} track(s)."
            + (f" Skipped {skipped} missing, unsupported, or remote entry/entries." if skipped else "")
        )
        self.status_label.setToolTip("\n".join(result.unavailable[:20]))

    def _use_saved(self, *, append):
        paths = self.saved_playlists.get(self.saved_combo.currentText())
        if paths is not None:
            self._use_import(available_tracks(paths), append=append)

    def _save_as(self):
        name, ok = QInputDialog.getText(self, "Save Playlist", "Playlist name:")
        if not ok:
            return
        try:
            try:
                name = save_playlist(name, self.playlist)
            except FileExistsError:
                if QMessageBox.question(self, "Replace Saved Playlist", f'Replace "{name.strip()}"?',
                                        QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                        QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
                    return
                name = save_playlist(name, self.playlist, overwrite=True)
            self._reload_saved(name)
            self.status_label.setText(f'Saved "{name}".')
        except (OSError, ValueError) as error:
            self._show_error(error)

    def _update_saved(self):
        name = self.saved_combo.currentText()
        if QMessageBox.question(self, "Update Playlist", f'Replace the saved contents of "{name}" with this queue?',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        try:
            save_playlist(name, self.playlist, overwrite=True)
            self._reload_saved(name)
            self.status_label.setText(f'Updated "{name}".')
        except (OSError, ValueError) as error:
            self._show_error(error)

    def _rename_saved(self):
        old = self.saved_combo.currentText()
        name, ok = QInputDialog.getText(self, "Rename Playlist", "Playlist name:", text=old)
        if ok:
            try:
                self._reload_saved(rename_playlist(old, name))
            except (OSError, ValueError) as error:
                self._show_error(error)

    def _delete_saved(self):
        name = self.saved_combo.currentText()
        if QMessageBox.question(self, "Delete Playlist", f'Delete the saved playlist "{name}"? Audio files will be kept.',
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) == QMessageBox.StandardButton.Yes:
            try:
                delete_playlist(name)
                self._reload_saved()
            except OSError as error:
                self._show_error(error)

    def _add_files(self):
        paths, _ = QFileDialog.getOpenFileNames(self, "Add to Queue", "",
                                             "Audio Files (*.mp3 *.flac *.m4a *.aac *.ogg *.opus *.wav *.wma *.aiff *.ape);;All Files (*)")
        if paths:
            self._use_import(available_tracks(paths), append=True)

    def _import_m3u(self, *, append):
        filename, _ = QFileDialog.getOpenFileName(self, "Import Playlist", "", "M3U Playlists (*.m3u *.m3u8)")
        if filename:
            try:
                self._use_import(read_m3u(filename), append=append)
            except (OSError, ValueError, UnicodeError) as error:
                self._show_error(error)

    def _export_m3u(self):
        filename, _ = QFileDialog.getSaveFileName(self, "Export Queue", "playlist.m3u8",
                                                "UTF-8 M3U Playlist (*.m3u8);;M3U Playlist (*.m3u)")
        if filename:
            if Path(filename).suffix.lower() not in {".m3u", ".m3u8"}:
                filename += ".m3u8"
            try:
                write_m3u(filename, self.playlist)
                self.status_label.setText(f"Exported {Path(filename).name}.")
            except (OSError, ValueError) as error:
                self._show_error(error)

    def _show_error(self, error):
        QMessageBox.warning(self, "Playlists", str(error))
