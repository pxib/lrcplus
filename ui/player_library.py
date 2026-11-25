"""Music library widgets, column preferences, cover loading and folder scanning."""

from pathlib import Path
from PySide6.QtCore import QByteArray, QItemSelectionModel, QSize, Qt, QTimer
from PySide6.QtGui import QMovie, QPixmap
from PySide6.QtWidgets import (
    QHBoxLayout,
    QLabel,
    QMenu,
    QInputDialog,
    QPushButton,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QAbstractItemView,
    QMessageBox,
)
from core.settings import save_music_library, save_music_library_columns
from core.playlists import path_key
from ui.file_drops import audio_drop_paths, LibraryTrackTree, DropIndicator
from core.library_search import close_metadata_match, search_words
from ui.search_history import SearchHistoryLineEdit
from ui.library_lyrics_search import LyricMatchDelegate
from ui.library_sorting import LIBRARY_SORT_ROLE, LibraryTreeItem
from ui.library_rows import LibraryGroupRows, LibraryRowData, LibraryTrackItem
from ui.artwork_background import extract_embedded_cover, load_embedded_cover_thumbnail
from ui.player_dialogs import AlbumCoverDelegate, CoverPreviewDialog, LibraryScanWorker
from core.fonts import library_font
from ui.content_list import ContentListDelegate


class PlayerLibraryMixin:
    def _build_library_page(self):
        self.setAcceptDrops(True)
        page = QWidget()
        root = QVBoxLayout(page)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(6)

        toolbar = QHBoxLayout()
        self.library_search = SearchHistoryLineEdit("library", remember_idle=True)
        self.library_search.setObjectName("librarySearch")
        self.library_search.setPlaceholderText("Search library...")
        self._library_search_timer = QTimer(self)
        self._library_expansion_before_search = None
        self._library_search_timer.setSingleShot(True)
        self._library_search_timer.setInterval(150)
        self._library_search_timer.timeout.connect(self._filter_library_view)
        self.library_search.textChanged.connect(lambda _text: self._library_search_timer.start())
        add_folder = QPushButton("Add Music Folder")
        add_folder.setObjectName("headerAction")
        add_folder.clicked.connect(self.add_music_folder)
        remove_folder = QPushButton("Remove Folder")
        remove_folder.setObjectName("headerAction")
        remove_folder.clicked.connect(self.remove_music_folder)
        rescan = QPushButton("Rescan")
        rescan.setObjectName("headerAction")
        rescan.clicked.connect(self.rescan_music_library)
        toolbar.addWidget(self.library_search, 1)
        self._build_library_search_filter(toolbar)
        self.library_favorites_filter = QPushButton("♡ Favorites")
        self.library_favorites_filter.setObjectName("headerAction")
        self.library_favorites_filter.setCheckable(True)
        self.library_favorites_filter.setToolTip("Show only favorite songs, combined with your search")
        self.library_favorites_filter.toggled.connect(
            lambda checked: self.library_favorites_filter.setText("♥ Favorites" if checked else "♡ Favorites")
        )
        self.library_favorites_filter.toggled.connect(self._filter_library_view)
        toolbar.addWidget(self.library_favorites_filter)
        toolbar.addWidget(add_folder)
        toolbar.addWidget(remove_folder)
        toolbar.addWidget(rescan)
        root.addLayout(toolbar)

        self.library_close_matches = QLabel("Close matches")
        self.library_close_matches.setObjectName("librarySearchStatus")
        self.library_close_matches.hide()
        root.addWidget(self.library_close_matches)
        root.addWidget(self.library_lyrics_status_widget)

        scan_status = QHBoxLayout()
        scan_status.setContentsMargins(0, 0, 0, 0)
        scan_status.setSpacing(6)
        self.library_scan_indicator = QLabel()
        self.library_scan_indicator.setObjectName("libraryScanIndicator")
        self.library_scan_indicator.setFixedSize(20, 20)
        self.library_scan_indicator.setAccessibleName("Library scan status")
        self.library_scan_movie = QMovie(
            str(Path(__file__).resolve().parents[1] / "assets" / "media" / "loading.gif"),
            parent=self.library_scan_indicator,
        )
        self.library_scan_movie.setScaledSize(QSize(20, 20))
        self.library_scan_indicator.setMovie(self.library_scan_movie)
        self.library_scan_indicator.hide()
        self.library_folder_label = QLabel()
        self.library_folder_label.setObjectName("libraryFolderLabel")
        self.library_folder_label.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        scan_status.addWidget(self.library_scan_indicator)
        scan_status.addWidget(self.library_folder_label, 1)
        root.addLayout(scan_status)

        self.library_columns = [
            ("cover", "Album cover"), ("track", "#"),
            ("title", "Title / track artist"),
            ("artist", "Artist / album"), ("date", "Date"),
            ("duration", "Duration"), ("codec", "Codec"),
            ("bitrate", "Bitrate"), ("file_size", "File size"),
            ("file_name", "File name"), ("file_path", "File path"),
            ("modified", "File last modified"),
            ("lyric_match", "Matching lyrics"),
            ("favorite", "Favorite"),
        ]
        # Append the logical column to preserve existing saved indexes.
        self.library_lyrics_column = next(i for i, (key, _) in enumerate(self.library_columns) if key == "lyric_match")
        self.library_favorite_column = len(self.library_columns) - 1
        self.library_table = LibraryTrackTree()
        self._apply_library_scroll_mode()
        self.library_table.setDragEnabled(True)
        self.library_table.setDragDropMode(QAbstractItemView.DragDropMode.DragOnly)
        self.library_table.setToolTip("Drop audio files or folders to add to the library. Right-click tracks to remove them without deleting files.")
        self.library_table.setObjectName("libraryTable")
        self.library_table.setFont(library_font(self.library_table.font()))
        # Resolve script runs explicitly for song metadata. Qt's automatic
        # fallback otherwise loads Gulim for Hangul even when Malgun is present.
        self.library_table.setItemDelegate(ContentListDelegate(self.library_table))
        self.library_table.setColumnCount(len(self.library_columns))
        self.library_table.setHeaderLabels([label for _, label in self.library_columns])
        self.library_table.setRootIsDecorated(True)
        self.library_table.setAlternatingRowColors(True)
        self.library_table.setSelectionMode(QAbstractItemView.SelectionMode.ExtendedSelection)
        self.library_table.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        self.library_table.customContextMenuRequested.connect(self._show_library_queue_menu)
        self.library_table.setEditTriggers(QAbstractItemView.EditTrigger.NoEditTriggers)
        self.library_table.itemDoubleClicked.connect(self._play_library_item)
        self.library_table.itemClicked.connect(self._open_library_cover_preview)
        self.library_table.itemClicked.connect(self._toggle_library_favorite)
        header = self.library_table.header()
        header.setStretchLastSection(False)
        header.setSectionsMovable(True)
        header.setContextMenuPolicy(Qt.ContextMenuPolicy.CustomContextMenu)
        header.customContextMenuRequested.connect(self._show_library_column_menu)
        self.library_table.setTreePosition(2)
        self.library_table.setIconSize(QSize(52, 52))
        self.library_table.setItemDelegateForColumn(0, AlbumCoverDelegate(
            self.library_table,
            cover_cache=self._library_cover_cache,
        ))
        self.library_table.setItemDelegateForColumn(
            self.library_lyrics_column, LyricMatchDelegate(self.library_table),
        )
        # Covers are loaded lazily for visible rows only. This prevents a
        # large library from decoding every embedded image at once.
        self.library_table.verticalScrollBar().valueChanged.connect(self._queue_visible_library_covers)
        self.library_table.itemExpanded.connect(lambda _item: self._queue_visible_library_covers())
        self.library_table.setColumnWidth(0, 62)
        self.library_table.setColumnWidth(1, 42)
        self.library_table.setColumnWidth(2, 330)
        self.library_table.setColumnWidth(3, 260)
        self.library_table.setColumnWidth(4, 85)
        self.library_table.setColumnWidth(5, 75)
        self.library_table.setColumnWidth(9, 180)
        self.library_table.setColumnWidth(10, 360)
        self.library_table.setColumnWidth(11, 150)
        self.library_table.setColumnWidth(self.library_lyrics_column, 340)
        self.library_table.setColumnWidth(self.library_favorite_column, 70)
        self._apply_library_column_state()
        self.library_table.setSortingEnabled(True)
        self._library_column_save_timer = QTimer(self)
        self._library_column_save_timer.setSingleShot(True)
        self._library_column_save_timer.setInterval(300)
        self._library_column_save_timer.timeout.connect(self._save_library_column_state)
        header.sortIndicatorChanged.connect(lambda *_: self._save_library_column_state())
        header.sectionResized.connect(lambda *_: self._library_column_save_timer.start())
        header.sectionMoved.connect(lambda *_: self._save_library_column_state())
        root.addWidget(self.library_table, 1)
        return page

    def _apply_library_scroll_mode(self):
        smooth = self.playback_settings.get("library_smooth_scrolling", False)
        self.library_table.set_smooth_scrolling(smooth)

    def _show_library_column_menu(self, pos):
        header = self.library_table.header()
        menu = QMenu(self)
        logical = header.logicalIndexAt(pos)
        if logical >= 0:
            for label, order in (("Sort ascending", Qt.SortOrder.AscendingOrder),
                                 ("Sort descending", Qt.SortOrder.DescendingOrder)):
                action = menu.addAction(label)
                action.setCheckable(True)
                action.setChecked(header.sortIndicatorSection() == logical and header.sortIndicatorOrder() == order)
                action.triggered.connect(lambda _checked=False, column=logical, direction=order: self.library_table.sortItems(column, direction))
            menu.addSeparator()
        columns_menu = menu.addMenu("Columns")
        for logical, (key, label) in enumerate(self.library_columns):
            action = columns_menu.addAction(label)
            action.setCheckable(True)
            action.setChecked(not header.isSectionHidden(logical))
            action.toggled.connect(lambda checked, index=logical: self._set_library_column_visible(index, checked))
        menu.addSeparator()
        reset = menu.addAction("Reset columns")
        reset.triggered.connect(self._reset_library_columns)
        try:
            menu.exec(header.mapToGlobal(pos))
        finally:
            menu.deleteLater()

    def _set_library_column_visible(self, logical, visible):
        if visible:
            self._restore_library_column_width(logical)
        self.library_table.setColumnHidden(logical, not visible)
        self._save_library_column_state()

    def _restore_library_column_width(self, logical):
        if self.library_table.columnWidth(logical) > 0:
            return
        default_widths = {
            0: 62, 1: 42, 2: 330, 3: 260, 4: 85, 5: 75,
            6: 90, 7: 85, 8: 90, 9: 180, 10: 360, 11: 150,
            self.library_lyrics_column: 340,
            self.library_favorite_column: 70,
        }
        self.library_table.setColumnWidth(logical, default_widths[logical])

    def _apply_library_column_state(self):
        state = self.library_column_state or {}
        header = self.library_table.header()
        hidden = set(state.get("hidden", [])) if "hidden" in state else set(
            i for i in range(6, len(self.library_columns)) if i != self.library_favorite_column
        )
        widths = state.get("widths", {})
        order = state.get("order", [])
        for logical in range(len(self.library_columns)):
            header.setSectionHidden(logical, logical in hidden)
            if str(logical) in widths:
                try: self.library_table.setColumnWidth(logical, int(widths[str(logical)]))
                except (TypeError, ValueError): pass

        if isinstance(order, list):
            for visual, logical in enumerate(order):
                try:
                    logical = int(logical)
                    if 0 <= logical < len(self.library_columns):
                        current = header.visualIndex(logical)
                        if current != visual: header.moveSection(current, visual)
                except (TypeError, ValueError): pass

        lyric_column = self.library_lyrics_column
        if not isinstance(order, list) or lyric_column not in order:
            header.moveSection(header.visualIndex(lyric_column), header.visualIndex(2) + 1)
        if not isinstance(order, list) or self.library_favorite_column not in order:
            header.moveSection(header.visualIndex(self.library_favorite_column), header.visualIndex(2))
        header.setSectionHidden(lyric_column, self.library_search_filter.currentData() != "lyrics")
        for logical in range(len(self.library_columns)):
            if not header.isSectionHidden(logical):
                self._restore_library_column_width(logical)
        sort_column = state.get("sort_column", -1)
        if not isinstance(sort_column, int) or not -1 <= sort_column < len(self.library_columns):
            sort_column = -1
        direction = Qt.SortOrder.DescendingOrder if state.get("sort_descending", False) else Qt.SortOrder.AscendingOrder
        header.setSortIndicator(sort_column, direction)

    def _save_library_column_state(self):
        if not hasattr(self, "library_table") or not hasattr(self, "library_columns"):
            return
        self._library_column_save_timer.stop()
        header = self.library_table.header()
        state = {
            "hidden": [i for i in range(len(self.library_columns)) if header.isSectionHidden(i)],
            "widths": {str(i): self.library_table.columnWidth(i) for i in range(len(self.library_columns))},
            "order": [header.logicalIndex(v) for v in range(len(self.library_columns))],
            "sort_column": header.sortIndicatorSection(),
            "sort_descending": header.sortIndicatorOrder() == Qt.SortOrder.DescendingOrder,
        }
        self.library_column_state = state
        save_music_library_columns(state)

    def _reset_library_columns(self):
        header = self.library_table.header()
        header.setSortIndicator(-1, Qt.SortOrder.AscendingOrder)
        for logical in range(len(self.library_columns)):
            header.setSectionHidden(logical, logical >= 6 and logical != self.library_favorite_column)
            current = header.visualIndex(logical)
            if current != logical: header.moveSection(current, logical)
        header.moveSection(header.visualIndex(self.library_lyrics_column), header.visualIndex(2) + 1)
        header.moveSection(header.visualIndex(self.library_favorite_column), header.visualIndex(2))
        header.setSectionHidden(self.library_lyrics_column, self.library_search_filter.currentData() != "lyrics")
        self.library_column_state = {}
        self._save_library_column_state()

    @staticmethod
    def _format_library_duration(seconds):
        try:
            total = max(0, int(round(float(seconds))))
        except (TypeError, ValueError):
            return ""
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        if hours:
            return f"{hours}:{minutes:02d}:{seconds:02d}"
        return f"{minutes}:{seconds:02d}"

    @staticmethod
    def _format_library_size(size):
        try:
            value = float(size)
        except (TypeError, ValueError):
            return ""
        for unit in ("B", "KB", "MB", "GB", "TB"):
            if value < 1024 or unit == "TB":
                return f"{value:.2f} {unit}" if unit != "B" else f"{int(value)} B"
            value /= 1024

    def _populate_library_view(self):
        if not hasattr(self, "library_table"):
            return
        if hasattr(self, "_library_lyrics_job"):
            self._reset_library_lyrics_search()
            self._library_lyrics_index.invalidate()
        self._library_filter_groups = []
        sorting_enabled = self.library_table.isSortingEnabled()
        self.library_table.setSortingEnabled(False)
        self.library_table.clear()
        self._library_row_data = LibraryRowData(
            self._format_library_duration, self._format_library_size,
            self._library_track_number, getattr(self, "library_lyrics_column", 12),
            getattr(self, "library_favorite_column", None), getattr(self, "favorite_paths", set()),
        )
        grouped = {}
        for track in self.library_tracks:
            group = (track.album_artist or track.artist or "Unknown artist", track.album or "Unknown album", track.date or "")
            grouped.setdefault(group, []).append(track)

        for (artist, album, date), tracks in grouped.items():
            heading = f"{artist} — {album}"
            if date:
                heading += f" [{date}]"
            # A group header is a real multi-column row, not a piece of
            # text painted across whichever column happens to be visible.
            # Keep the cover cell genuinely empty and put the heading in
            # the Title column. This is important when the user hides or
            # resizes columns: album/artist text must never appear inside
            # the Album cover column.
            parent_values = ["" for _ in self.library_columns]
            parent_values[2] = heading  # Title / track artist
            parent = LibraryTreeItem(parent_values)
            parent.setData(3, LIBRARY_SORT_ROLE, (artist + " / " + album).casefold())
            parent.setData(4, LIBRARY_SORT_ROLE, date.casefold())
            parent.setFirstColumnSpanned(False)
            parent.setExpanded(True)
            parent.setData(0, Qt.ItemDataRole.UserRole, None)
            parent.setData(0, Qt.ItemDataRole.UserRole + 1, (artist, album, date))
            self.library_table.addTopLevelItem(parent)
            tracks.sort(key=lambda t: (self._library_track_number(t.track_number), t.display_title.casefold()))
            filter_rows = LibraryGroupRows(parent)
            self._library_filter_groups.append((parent, filter_rows))
            for track in tracks:
                item = LibraryTrackItem(track, artist, self._library_row_data)
                parent.addChild(item)

        folder_text = "; ".join(self.library_folders) if self.library_folders else "Drop audio files or folders here, or use Add Music Folder."
        self.library_folder_label.setText(f"Library folders: {folder_text}")
        self.library_table.setSortingEnabled(sorting_enabled)
        # Do not auto-resize the cover column here: the user controls its width.
        self._filter_library_view()

    def _filter_library_view(self):
        self._library_search_timer.stop()
        query = self.library_search.text().casefold().strip()
        favorites_only = self.library_favorites_filter.isChecked()
        filtering = bool(query or favorites_only)
        lyrics_matches = None
        lyric_column = getattr(self, "library_lyrics_column", None)
        search_filter = getattr(self, "library_search_filter", None)
        if search_filter is not None:
            if query and search_filter.currentData() == "lyrics":
                lyrics_matches = self._search_library_lyrics(query)
            else:
                self._reset_library_lyrics_search()
        expansion = self._library_expansion_before_search
        if filtering and expansion is None:
            # Keep one snapshot for the whole search, including query
            # changes. Album keys survive scans that replace tree items.
            expansion = {}
            self._library_expansion_before_search = expansion
        self.library_table.setUpdatesEnabled(False)
        try:
            found = False
            close_matches = False
            matches_changed = self._library_row_data.matches is not lyrics_matches
            self._library_row_data.matches = lyrics_matches
            for parent, rows in self._library_filter_groups:
                group = tuple(parent.data(0, Qt.ItemDataRole.UserRole + 1))
                if filtering:
                    expansion.setdefault(group, parent.isExpanded())
                any_visible = False
                for item, haystack in rows:
                    if query and lyrics_matches is None:
                        haystack = item.search_text()
                    visible = (item.data(0, Qt.ItemDataRole.UserRole) in lyrics_matches
                               if lyrics_matches is not None else not query or query in haystack)
                    if favorites_only:
                        visible = visible and item.favorite_key in self.favorite_paths
                    if item.isHidden() == visible:
                        item.setHidden(not visible)
                    any_visible |= visible
                if parent.isHidden() == any_visible:
                    parent.setHidden(not any_visible)
                found |= any_visible
                if filtering and any_visible:
                    parent.setExpanded(True)
                elif not filtering and expansion is not None:
                    parent.setExpanded(expansion.get(group, parent.isExpanded()))
                if matches_changed and lyric_column is not None and parent.childCount():
                    model = self.library_table.model()
                    group_index = self.library_table.indexFromItem(parent)
                    model.dataChanged.emit(
                        model.index(0, lyric_column, group_index),
                        model.index(parent.childCount() - 1, lyric_column, group_index),
                        [Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole, Qt.ItemDataRole.UserRole],
                    )
            if query and lyrics_matches is None and not found:
                words = search_words(query)
                for parent, rows in self._library_filter_groups:
                    any_visible = False
                    for item, _ in rows:
                        visible = ((not favorites_only or item.favorite_key in self.favorite_paths)
                                   and close_metadata_match(words, item.track))
                        if item.isHidden() == visible:
                            item.setHidden(not visible)
                        any_visible |= visible
                    if parent.isHidden() == any_visible:
                        parent.setHidden(not any_visible)
                    if any_visible:
                        parent.setExpanded(True)
                    close_matches |= any_visible
            status = getattr(self, "library_close_matches", None)
            if status is not None:
                status.setVisible(close_matches)
            if matches_changed and self.library_table.sortColumn() == lyric_column:
                header = self.library_table.header()
                self.library_table.sortItems(lyric_column, header.sortIndicatorOrder())
            if not filtering:
                self._library_expansion_before_search = None
        finally:
            self.library_table.setUpdatesEnabled(True)
        self._queue_visible_library_covers()

    def _queue_visible_library_covers(self, *_args):
        if not self.library_table.isVisible() or self._library_cover_load_pending:
            return
        self._library_cover_load_pending = True
        QTimer.singleShot(0, self._load_visible_library_covers)

    def _load_visible_library_covers(self):
        self._library_cover_load_pending = False
        # A queued callback can outlive a switch to the Lyrics page.
        if not self.library_table.isVisible():
            return
        viewport = self.library_table.viewport().rect()
        item = self.library_table.itemAt(viewport.topLeft())
        if item is None:
            item = self.library_table.topLevelItem(0)
        # Walk from the first visible item through the viewport plus a
        # small prefetch margin, not through the entire library.
        y_limit = viewport.bottom() + 240
        current = item
        while current is not None:
            rect = self.library_table.visualItemRect(current)
            if rect.top() > y_limit:
                break
            path = current.data(0, Qt.ItemDataRole.UserRole)
            if path:
                self._library_cover_pixmap(path)
            current = self.library_table.itemBelow(current)
        self.library_table.viewport().update()

    def _library_cover_pixmap(self, path):
        """Return a thumbnail made directly from this track's embedded cover."""
        path = str(path)
        cached = self._library_cover_cache.get(path)
        if cached is not None:
            self._library_cover_cache.move_to_end(path)
            return cached

        # Decode directly to thumbnail dimensions instead of expanding a
        # potentially 4000x4000 cover in memory first.
        pixmap = load_embedded_cover_thumbnail(path, QSize(52, 52))
        self._library_cover_cache[path] = pixmap
        self._library_cover_cache.move_to_end(path)
        while len(self._library_cover_cache) > self._library_cover_cache_limit:
            self._library_cover_cache.popitem(last=False)
        return pixmap

    @staticmethod
    def _library_track_number(value):
        try:
            return int(str(value).split("/", 1)[0])
        except (TypeError, ValueError):
            return 10**9

    def _play_library_item(self, item, column=0):
        if item is None or column == self.library_favorite_column:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        self._clear_shuffle_history()
        if self.load_audio(path) is not False:
            self.media_player.play()

    def _open_library_cover_preview(self, item, column):
        if column != 0:
            return
        path = item.data(0, Qt.ItemDataRole.UserRole)
        if not path:
            return
        cover_data = QByteArray(extract_embedded_cover(path) or b"")
        viewer = CoverPreviewDialog.from_data(cover_data, self)
        if viewer is None:
            return
        try:
            viewer.exec()
        finally:
            viewer.deleteLater()

    def dragEnterEvent(self, event):
        paths = audio_drop_paths(event.mimeData(), folders=True)
        if paths and not self._close_after_library_scan:
            if not hasattr(self, "_library_drop_indicator"):
                self._library_drop_indicator = DropIndicator(self.centralWidget())
            count = len(paths)
            self._library_drop_indicator.preview(
                f"Add {count} {'item' if count == 1 else 'items'} to Library\n\n"
                "Tracks appear under their artist and album.\n"
                "Imported tracks will be selected and brought into view."
            )
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._hide_library_drop_indicator()
            event.ignore()

    def dragMoveEvent(self, event):
        self.dragEnterEvent(event)

    def _hide_library_drop_indicator(self):
        if hasattr(self, "_library_drop_indicator"):
            self._library_drop_indicator.hide()

    def dragLeaveEvent(self, event):
        self._hide_library_drop_indicator()
        event.accept()

    def dropEvent(self, event):
        self._hide_library_drop_indicator()
        paths = audio_drop_paths(event.mimeData(), folders=True)
        if not paths or self._close_after_library_scan:
            event.ignore()
            return
        folders = {path_key(path) for path in self.library_folders}
        files = {path_key(path) for path in self.library_files}
        previous = (list(self.library_folders), list(self.library_files), set(self.library_excluded_paths))
        for path in paths:
            key = path_key(path)
            if path.is_dir():
                if key not in folders:
                    self.library_folders.append(str(path))
                    folders.add(key)
            else:
                self.library_excluded_paths.discard(key)
                if key not in files:
                    self.library_files.append(str(path))
                    files.add(key)
        try:
            self._save_library_state()
        except (OSError, ValueError) as error:
            self.library_folders, self.library_files, self.library_excluded_paths = previous
            event.ignore()
            QMessageBox.warning(self, "Library import", f"Could not save the library: {error}")
            return
        pending = getattr(self, "_library_drop_reveal_paths", [])
        self._library_drop_reveal_paths = pending + paths
        self.statusBar().showMessage("Importing dropped tracks into Library...", 8000)
        self.rescan_music_library()
        event.setDropAction(Qt.DropAction.CopyAction)
        event.accept()

    def _save_library_state(self):
        save_music_library(
            self.library_folders, [track.to_dict() for track in self.library_tracks],
            self.library_column_state, files=self.library_files,
            excluded_paths=self.library_excluded_paths,
        )

    def _reveal_library_drop(self):
        paths = getattr(self, "_library_drop_reveal_paths", [])
        self._library_drop_reveal_paths = []
        if not paths:
            return
        exact = {path_key(path) for path in paths if not path.is_dir()}
        folders = [path for path in paths if path.is_dir()]
        targets = []
        for i in range(self.library_table.topLevelItemCount()):
            parent = self.library_table.topLevelItem(i)
            for j in range(parent.childCount()):
                item = parent.child(j)
                path = Path(item.data(0, Qt.ItemDataRole.UserRole))
                if path_key(path) in exact or any(path.is_relative_to(folder) for folder in folders):
                    targets.append(item)
        if not targets:
            self.statusBar().showMessage("No supported audio tracks found in the dropped items.", 8000)
            return
        # A previous search or Favorites filter must not hide the import.
        filters_cleared = bool(self.library_search.text() or self.library_favorites_filter.isChecked())
        if filters_cleared:
            self.library_search.clear()
            self.library_favorites_filter.setChecked(False)
            self._filter_library_view()
        self.workspace_stack.setCurrentWidget(self.library_page)
        self.library_table.clearSelection()
        for item in targets:
            item.parent().setExpanded(True)
            item.setSelected(True)
        first = targets[0]
        self.library_table.setCurrentItem(first, 2, QItemSelectionModel.SelectionFlag.NoUpdate)
        self.library_table.scrollToItem(first, QAbstractItemView.ScrollHint.PositionAtCenter)
        message = f"Showing {len(targets)} dropped {'track' if len(targets) == 1 else 'tracks'} in Library."
        if filters_cleared:
            message += " Search and Favorites filters cleared to show the import."
        self.statusBar().showMessage(message, 10000)

    def remove_library_tracks(self, paths):
        keys = {path_key(path) for path in paths}
        tracks = [track for track in self.library_tracks if path_key(track.path) not in keys]
        if len(tracks) == len(self.library_tracks):
            return
        excluded = self.library_excluded_paths | keys
        files = [path for path in self.library_files if path_key(path) not in keys]
        # Persist before updating the view; no music files are changed.
        try:
            save_music_library(self.library_folders, [track.to_dict() for track in tracks],
                               self.library_column_state, files=files, excluded_paths=excluded)
        except (OSError, ValueError) as error:
            QMessageBox.warning(self, "Remove from library", f"Could not save the library: {error}")
            return
        self.library_tracks = tracks
        self.library_files = files
        self.library_excluded_paths = excluded
        self._clear_shuffle_history()
        self._invalidate_gapless_preload()
        self._remove_library_rows(keys)
        self.update_transport_buttons()
        self.statusBar().showMessage("Removed from library. Files remain on disk; drop a file again to restore it.", 8000)
        if self._library_scan_worker is not None:
            self.rescan_music_library()

    def _remove_library_rows(self, keys):
        """Prune existing items without resetting headers, expansion or selection."""
        tree = self.library_table
        vertical = tree.verticalScrollBar().value()
        horizontal = tree.horizontalScrollBar().value()
        retained = []
        tree.setUpdatesEnabled(False)
        try:
            for parent, rows in self._library_filter_groups:
                expanded = parent.isExpanded()
                for index in range(parent.childCount() - 1, -1, -1):
                    item = parent.child(index)
                    if path_key(item.data(0, Qt.ItemDataRole.UserRole)) in keys:
                        parent.takeChild(index)
                if parent.childCount():
                    retained.append((parent, rows, expanded))
                else:
                    tree.takeTopLevelItem(tree.indexOfTopLevelItem(parent))
                    expansion = self._library_expansion_before_search
                    if expansion is not None:
                        expansion.pop(tuple(parent.data(0, Qt.ItemDataRole.UserRole + 1)), None)
            self._library_filter_groups = [(parent, rows) for parent, rows, _ in retained]
            matches = self._library_lyrics_matches
            for path in list(matches):
                if path_key(path) in keys:
                    del matches[path]
            if self._library_lyrics_query and self.library_lyrics_loading.isHidden():
                self.library_lyrics_status.setText(f"{len(matches)} song(s) match the lyrics.")
            self._filter_library_view()
            for parent, _, expanded in retained:
                parent.setExpanded(expanded)
            tree.verticalScrollBar().setValue(vertical)
            tree.horizontalScrollBar().setValue(horizontal)
        finally:
            tree.setUpdatesEnabled(True)

    def add_music_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Add Music Folder")
        if not folder:
            return
        folder = str(Path(folder).resolve())
        if folder not in self.library_folders:
            self.library_folders.append(folder)
        self.rescan_music_library()

    def remove_music_folder(self):
        if not self.library_folders:
            return
        folder, ok = QInputDialog.getItem(self, "Remove Music Folder", "Folder:", self.library_folders, 0, False)
        if not ok or not folder:
            return
        self.library_folders.remove(folder)
        self.rescan_music_library()

    def rescan_music_library(self):
        if self._close_after_library_scan:
            return
        if self._library_scan_worker is not None:
            # A finished worker may still have results queued on the GUI thread.
            # Coalesce all new requests into one scan of the latest folders.
            self._library_scan_pending = True
            self._library_scan_worker.requestInterruption()
            return
        self._set_library_scan_indicator("loading")
        self.library_folder_label.setText("Scanning music library...")
        self.library_table.setEnabled(False)
        worker = LibraryScanWorker(self.library_folders, self, files=self.library_files)
        self._library_scan_worker = worker
        worker.completed.connect(self._library_scan_finished)
        worker.failed.connect(self._library_scan_failed)
        worker.finished.connect(self._library_scan_stopped)
        worker.finished.connect(worker.deleteLater)
        worker.start()

    def _library_scan_stopped(self):
        if self.sender() is not self._library_scan_worker:
            return
        self._library_scan_worker = None
        if self._close_after_library_scan:
            QTimer.singleShot(0, self.close)
        elif self._library_scan_pending:
            self._library_scan_pending = False
            self.rescan_music_library()
        else:
            self.library_table.setEnabled(True)

    def _discard_library_scan_result(self):
        worker = self.sender()
        return (self._close_after_library_scan or self._library_scan_pending
                or (worker is not None and (worker is not self._library_scan_worker
                    or worker.folders != self.library_folders
                    or worker.files != self.library_files)))

    def _set_library_scan_indicator(self, state):
        self.library_scan_movie.stop()
        self.library_scan_indicator.hide()
        if state == "loading":
            self.library_scan_indicator.show()
            self.library_scan_movie.start()
            return
        if state in {"success", "error"}:
            icon, accessible_name = (
                ("success.png", "Library scan complete")
                if state == "success"
                else ("error.png", "Library scan error")
            )
            self.library_scan_indicator.setPixmap(QPixmap(
                str(Path(__file__).resolve().parents[1] / "assets" / "media" / icon)
            ).scaled(
                QSize(20, 20), Qt.AspectRatioMode.KeepAspectRatio,
                Qt.TransformationMode.SmoothTransformation,
            ))
            self.library_scan_indicator.setAccessibleName(accessible_name)
            self.library_scan_indicator.show()

    def _library_scan_finished(self, tracks):
        if self._discard_library_scan_result():
            return
        self._library_cover_cache.clear()
        self.library_tracks = [track for track in tracks if path_key(track.path) not in self.library_excluded_paths]
        self._save_library_state()
        self._populate_library_view()
        self._set_library_scan_indicator("success")
        self.update_transport_buttons()
        self._reveal_library_drop()

    def _library_scan_failed(self, error):
        if self._discard_library_scan_result():
            return
        self._set_library_scan_indicator("error")
        self._library_drop_reveal_paths = []
        self.library_folder_label.setText(f"Could not scan music library: {error}")
