from pathlib import Path

from PySide6.QtCore import Qt, Signal
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QVBoxLayout,
)


class PlaylistDialog(QDialog):
    track_requested = Signal(int)
    remove_requested = Signal(int)
    clear_requested = Signal()

    def __init__(self, playlist, current_index, parent=None):
        super().__init__(parent)

        self.setWindowTitle("Playlist")
        self.resize(600, 400)

        self.playlist = []
        self.current_index = -1

        self.title_label = QLabel()
        self.list_widget = QListWidget()

        self.load_button = QPushButton("Load")
        self.remove_button = QPushButton("Remove")
        self.clear_button = QPushButton("Clear")
        self.close_button = QPushButton("Close")

        button_layout = QHBoxLayout()
        button_layout.addWidget(self.load_button)
        button_layout.addWidget(self.remove_button)
        button_layout.addWidget(self.clear_button)
        button_layout.addStretch()
        button_layout.addWidget(self.close_button)

        layout = QVBoxLayout()
        layout.addWidget(self.title_label)
        layout.addWidget(self.list_widget)
        layout.addLayout(button_layout)
        self.setLayout(layout)

        self.load_button.clicked.connect(self._request_selected_track)
        self.remove_button.clicked.connect(self._request_remove_selected)
        self.clear_button.clicked.connect(self.clear_requested.emit)
        self.close_button.clicked.connect(self.accept)
        self.list_widget.itemDoubleClicked.connect(
            lambda item: self._request_selected_track()
        )

        self.set_playlist(playlist, current_index)

    def set_playlist(self, playlist, current_index):
        self.playlist = list(playlist)
        self.current_index = current_index
        self.refresh()

    def refresh(self):
        self.list_widget.clear()
        self.title_label.setText(
            f"Playlist ({len(self.playlist)})"
        )

        for index, audio_path in enumerate(self.playlist):
            prefix = "> " if index == self.current_index else "  "
            display_text = (
                f"{prefix}{index + 1:02d}  {Path(audio_path).name}"
            )

            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, index)
            self.list_widget.addItem(item)

            if index == self.current_index:
                self.list_widget.setCurrentItem(item)

        has_selection = self.list_widget.currentItem() is not None
        self.load_button.setEnabled(has_selection)
        self.remove_button.setEnabled(has_selection)
        self.clear_button.setEnabled(bool(self.playlist))

    def _selected_index(self):
        item = self.list_widget.currentItem()
        if item is None:
            return None

        index = item.data(Qt.ItemDataRole.UserRole)
        if not isinstance(index, int):
            return None

        if not 0 <= index < len(self.playlist):
            return None

        return index

    def _request_selected_track(self):
        index = self._selected_index()
        if index is not None:
            self.track_requested.emit(index)

    def _request_remove_selected(self):
        index = self._selected_index()
        if index is not None:
            self.remove_requested.emit(index)
