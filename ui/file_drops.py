"""Local audio drops shared by the player and queue, always copying references."""

from pathlib import Path

from PySide6.QtCore import Qt, Signal, QUrl, QEvent, QItemSelectionModel
from PySide6.QtGui import QPalette
from PySide6.QtWidgets import QLabel, QListWidget, QTreeWidget

from core.music_library import SUPPORTED_EXTENSIONS
from core.playlists import path_key


def audio_drop_paths(mime, *, folders=False):
    paths, seen = [], set()
    for url in mime.urls() if mime.hasUrls() else ():
        if not url.isLocalFile():
            continue
        try:
            path = Path(url.toLocalFile()).resolve()
            valid = (folders and path.is_dir()) or (
                path.is_file() and path.suffix.lower() in SUPPORTED_EXTENSIONS
            )
            key = path_key(path)
        except (OSError, ValueError):
            continue
        if valid and key not in seen:
            seen.add(key)
            paths.append(path)
    return paths


class DropIndicator(QLabel):
    """A mouse-transparent destination preview that follows its target's size."""

    def __init__(self, target):
        super().__init__(target)
        self.setObjectName("fileDropIndicator")
        self.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.setWordWrap(True)
        self.setTextFormat(Qt.TextFormat.PlainText)
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.hide()
        target.installEventFilter(self)

    def preview(self, message):
        palette = self.parentWidget().palette()
        background = palette.color(QPalette.ColorRole.Window)
        accent = palette.color(QPalette.ColorRole.Highlight).name()
        text = palette.color(QPalette.ColorRole.WindowText).name()
        self.setStyleSheet(
            f"QLabel#fileDropIndicator {{ background: rgba({background.red()}, {background.green()}, "
            f"{background.blue()}, 242); color: {text}; border: 3px dashed {accent}; "
            "border-radius: 12px; padding: 24px; font-size: 18px; font-weight: 600; }"
        )
        self.setText(message)
        self.setGeometry(self.parentWidget().rect().adjusted(8, 8, -8, -8))
        self.show()
        self.raise_()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self.setGeometry(watched.rect().adjusted(8, 8, -8, -8))
        return super().eventFilter(watched, event)


class LibraryTrackTree(QTreeWidget):
    def mimeData(self, items):
        mime = super().mimeData(items)
        paths, seen = [], set()
        for item in items:
            children = [item.child(i) for i in range(item.childCount())] if item.childCount() else [item]
            for child in children:
                if child.isHidden():
                    continue
                path = child.data(0, Qt.ItemDataRole.UserRole)
                if path and path_key(path) not in seen:
                    seen.add(path_key(path))
                    paths.append(QUrl.fromLocalFile(str(path)))
        mime.setUrls(paths)
        return mime

    def supportedDragActions(self):
        return Qt.DropAction.CopyAction


class QueueTrackList(QListWidget):
    files_dropped = Signal(object)

    def _preview_drop(self, paths):
        if not hasattr(self, "_drop_indicator"):
            self._drop_indicator = DropIndicator(self.viewport())
        count = len(paths)
        self._drop_indicator.preview(
            f"Append {count} {'track' if count == 1 else 'tracks'} to the end of the queue\n\n"
            "Playback continues. Added tracks will be selected."
        )

    def _hide_drop_indicator(self):
        if hasattr(self, "_drop_indicator"):
            self._drop_indicator.hide()

    def dragEnterEvent(self, event):
        paths = audio_drop_paths(event.mimeData())
        if paths:
            self._preview_drop(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._hide_drop_indicator()
            super().dragEnterEvent(event)

    def dragMoveEvent(self, event):
        paths = audio_drop_paths(event.mimeData())
        if paths:
            self._preview_drop(paths)
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            self._hide_drop_indicator()
            super().dragMoveEvent(event)

    def dragLeaveEvent(self, event):
        self._hide_drop_indicator()
        super().dragLeaveEvent(event)

    def dropEvent(self, event):
        self._hide_drop_indicator()
        paths = audio_drop_paths(event.mimeData())
        if paths:
            start = self.count()
            self.files_dropped.emit(paths)
            if self.count() > start:
                self.clearSelection()
                for index in range(start, self.count()):
                    self.item(index).setSelected(True)
                self.setCurrentRow(start, QItemSelectionModel.SelectionFlag.NoUpdate)
                self.scrollToItem(self.item(start))
            event.setDropAction(Qt.DropAction.CopyAction)
            event.accept()
        else:
            super().dropEvent(event)
