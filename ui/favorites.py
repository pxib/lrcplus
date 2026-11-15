"""Favorite controls shared by the playing track, library and queue menus."""

from PySide6.QtCore import QSignalBlocker, Qt
from PySide6.QtWidgets import QMessageBox

from core.favorites import favorite_key, save_favorites


class FavoritesMixin:
    def _set_tracks_favorite(self, paths, favorite):
        keys = {favorite_key(path) for path in paths}
        current = self.favorite_paths
        updated = current | keys if favorite else current - keys
        if updated == current:
            return
        try:
            self.favorite_paths = save_favorites(updated)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Favorites", f"Could not save favorites:\n{error}")
            self._refresh_favorite_button()
            return
        self.favorites_changed.emit()

    def _toggle_current_favorite(self):
        if self.current_audio_path:
            self._set_tracks_favorite(
                [self.current_audio_path],
                favorite_key(self.current_audio_path) not in self.favorite_paths,
            )

    def _refresh_favorite_button(self):
        if not hasattr(self, "favorite_button"):
            return
        path = self.current_audio_path
        favorite = bool(path and favorite_key(path) in self.favorite_paths)
        blocker = QSignalBlocker(self.favorite_button)
        self.favorite_button.setChecked(favorite)
        self.favorite_button.setText("♥" if favorite else "♡")
        self.favorite_button.setEnabled(bool(path))
        label = "Remove current song from favorites" if favorite else "Add current song to favorites"
        self.favorite_button.setToolTip(label)
        self.favorite_button.setAccessibleName(label)
        del blocker

    def _refresh_favorites(self):
        self._refresh_favorite_button()
        if hasattr(self, "_library_row_data"):
            self._library_row_data.favorite_paths = self.favorite_paths
            self._filter_library_view()
            column = self.library_favorite_column
            model = self.library_table.model()
            for i in range(self.library_table.topLevelItemCount()):
                group = self.library_table.topLevelItem(i)
                if group.childCount():
                    parent = self.library_table.indexFromItem(group)
                    model.dataChanged.emit(model.index(0, column, parent),
                                           model.index(group.childCount() - 1, column, parent), [])
            if self.library_table.sortColumn() == column:
                self.library_table.sortItems(column, self.library_table.header().sortIndicatorOrder())

    def _toggle_library_favorite(self, item, column):
        if column != self.library_favorite_column:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if path:
            self._set_tracks_favorite([path], favorite_key(path) not in self.favorite_paths)
