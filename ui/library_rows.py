"""Library item data derived on demand from the existing track model."""

from datetime import datetime
from pathlib import Path

from PySide6.QtCore import QSize, Qt

from ui.library_sorting import LIBRARY_SORT_ROLE, LibraryTreeItem


class LibraryRowData:
    def __init__(self, duration, size, number, lyric_column):
        self.duration = duration
        self.size = size
        self.number = number
        self.lyric_column = lyric_column
        self.matches = None


class LibraryTrackItem(LibraryTreeItem):
    """Keep one track reference, without native copies of thirteen text cells."""

    def __init__(self, track, artist, context):
        super().__init__()
        self.track = track
        self.artist = artist
        self.context = context

    def search_text(self):
        track = self.track
        return " ".join((track.display_title, track.artist, track.album,
                         track.album_artist, track.date, track.path)).casefold()

    def data(self, column, role):
        track = self.track
        if column == self.context.lyric_column:
            match = (self.context.matches or {}).get(track.path)
            if role in (Qt.ItemDataRole.DisplayRole, Qt.ItemDataRole.ToolTipRole):
                return match.text if match else ""
            if role == Qt.ItemDataRole.UserRole:
                return (match.start, match.end) if match else None
        if column == 0:
            if role == Qt.ItemDataRole.UserRole:
                return track.path
            if role == Qt.ItemDataRole.SizeHintRole:
                return QSize(0, 58)
        if role == LIBRARY_SORT_ROLE:
            if column == 1:
                return self.context.number(track.track_number)
            return {5: track.duration, 7: track.bitrate, 8: track.file_size,
                    11: track.modified}.get(column)
        if role == Qt.ItemDataRole.DisplayRole:
            if column == 0:
                return ""
            if column == 1:
                return track.track_number or ""
            if column == 2:
                return track.display_title
            if column == 3:
                return track.artist or self.artist
            if column == 4:
                return track.date
            if column == 5:
                return self.context.duration(track.duration)
            if column == 6:
                return track.codec
            if column == 7:
                return f"{track.bitrate} kbps" if track.bitrate else ""
            if column == 8:
                return self.context.size(track.file_size)
            if column == 9:
                return Path(track.path).name
            if column == 10:
                return track.path
            if column == 11:
                return datetime.fromtimestamp(track.modified).strftime("%Y-%m-%d %H:%M:%S") if track.modified else ""
        return super().data(column, role)


class LibraryGroupRows:
    """Iterate existing children without retaining a second per-track index."""

    def __init__(self, parent):
        self.parent = parent

    def __iter__(self):
        for row in range(self.parent.childCount()):
            yield self.parent.child(row), None
