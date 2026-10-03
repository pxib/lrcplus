"""
Built-in UI Tools plugin.

Owns the Settings dialog and the LRC editor dialog.
"""
from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QDialogButtonBox,
    QCheckBox,
    QComboBox,
    QHBoxLayout,
    QLabel,
    QListWidget,
    QListWidgetItem,
    QPushButton,
    QSlider,
    QTextEdit,
    QVBoxLayout,
    QWidget,
    QFileDialog,
    QTabWidget,
)


class SettingsDialog(QDialog):
    def __init__(
        self,
        folders,
        scrolling_lyrics_enabled=False,
        lyrics_direction="center",
        lyrics_padding=8,
        ruby_padding=2,
        show_ruby=True,
        parent=None,
    ):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.resize(500, 400)

        # Create tabs
        tabs = QTabWidget()

        # ===== GENERAL TAB =====
        general_tab = QWidget()
        general_layout = QVBoxLayout(general_tab)

        general_layout.addWidget(QLabel("LRC Search Folders:"))

        self.folder_list = QListWidget()
        for folder in folders:
            self.folder_list.addItem(QListWidgetItem(folder))

        add_button = QPushButton("Add Folder")
        add_button.clicked.connect(self.add_folder)

        remove_button = QPushButton("Remove")
        remove_button.clicked.connect(self.remove_folder)

        controls = QHBoxLayout()
        controls.addWidget(add_button)
        controls.addWidget(remove_button)

        general_layout.addWidget(self.folder_list)
        general_layout.addLayout(controls)
        general_layout.addStretch()

        # ===== LYRICS TAB =====
        lyrics_tab = QWidget()
        lyrics_layout = QVBoxLayout(lyrics_tab)

        lyrics_layout.addWidget(QLabel("Lyrics Display Options:"))

        self.scrolling_lyrics_checkbox = QCheckBox(
            "Show scrolling lyrics"
        )
        self.scrolling_lyrics_checkbox.setChecked(
            scrolling_lyrics_enabled
        )
        lyrics_layout.addWidget(self.scrolling_lyrics_checkbox)

        lyrics_layout.addWidget(
            QLabel("Horizontal direction:")
        )

        self.lyrics_direction_combo = QComboBox()
        self.lyrics_direction_combo.addItem("Left", "left")
        self.lyrics_direction_combo.addItem("Middle", "center")
        self.lyrics_direction_combo.addItem("Right", "right")

        direction_index = self.lyrics_direction_combo.findData(
            lyrics_direction
        )
        self.lyrics_direction_combo.setCurrentIndex(
            direction_index if direction_index >= 0 else 1
        )

        lyrics_layout.addWidget(self.lyrics_direction_combo)

        lyrics_layout.addWidget(
            QLabel(
                "Choose where lyric text is positioned horizontally."
            )
        )

        lyrics_layout.addWidget(QLabel("Padding:"))

        padding_layout = QHBoxLayout()
        self.lyrics_padding_slider = QSlider(Qt.Orientation.Horizontal)
        self.lyrics_padding_slider.setRange(0, 24)
        self.lyrics_padding_slider.setValue(
            max(0, min(24, int(lyrics_padding)))
        )

        self.lyrics_padding_label = QLabel(
            f"{self.lyrics_padding_slider.value()} px"
        )
        self.lyrics_padding_label.setFixedWidth(45)

        self.lyrics_padding_slider.valueChanged.connect(
            lambda value: self.lyrics_padding_label.setText(f"{value} px")
        )

        padding_layout.addWidget(self.lyrics_padding_slider)
        padding_layout.addWidget(self.lyrics_padding_label)
        lyrics_layout.addLayout(padding_layout)

        lyrics_layout.addWidget(
            QLabel(
                "Controls the vertical space between lyric lines."
            )
        )

        lyrics_layout.addWidget(QLabel("Ruby / Furigana:"))

        self.show_ruby_checkbox = QCheckBox("Show ruby text")
        self.show_ruby_checkbox.setChecked(bool(show_ruby))
        lyrics_layout.addWidget(self.show_ruby_checkbox)

        lyrics_layout.addWidget(QLabel("Ruby padding:"))

        ruby_padding_layout = QHBoxLayout()
        self.ruby_padding_slider = QSlider(Qt.Orientation.Horizontal)
        self.ruby_padding_slider.setRange(0, 18)
        self.ruby_padding_slider.setValue(
            max(0, min(18, int(ruby_padding)))
        )

        self.ruby_padding_label = QLabel(
            f"{self.ruby_padding_slider.value()} px"
        )
        self.ruby_padding_label.setFixedWidth(45)

        self.ruby_padding_slider.valueChanged.connect(
            lambda value: self.ruby_padding_label.setText(f"{value} px")
        )

        ruby_padding_layout.addWidget(self.ruby_padding_slider)
        ruby_padding_layout.addWidget(self.ruby_padding_label)
        lyrics_layout.addLayout(ruby_padding_layout)

        lyrics_layout.addWidget(
            QLabel(
                "Controls the gap between ruby text and the lyric text."
            )
        )

        lyrics_layout.addWidget(
            QLabel(
                "Scrolling lyrics reserve a dedicated furigana band above each line."
            )
        )

        lyrics_layout.addWidget(
            QLabel(
                "Scrolling lyrics shows multiple lines and automatically "
                "scrolls to the current line."
            )
        )
        lyrics_layout.addWidget(
            QLabel("• Click a lyric in single-line mode to jump to that time")
        )
        lyrics_layout.addStretch()

        tabs.addTab(general_tab, "General")
        tabs.addTab(lyrics_tab, "Lyrics")

        # Dialog buttons
        button_box = QDialogButtonBox(
            QDialogButtonBox.StandardButton.Ok
            | QDialogButtonBox.StandardButton.Cancel
        )
        button_box.accepted.connect(self.accept)
        button_box.rejected.connect(self.reject)

        layout = QVBoxLayout(self)
        layout.addWidget(tabs)
        layout.addWidget(button_box)

    def add_folder(self):
        folder = QFileDialog.getExistingDirectory(
            self,
            "Choose LRC Folder",
            "",
        )
        if not folder:
            return
        if not any(
            self.folder_list.item(row).text() == folder
            for row in range(self.folder_list.count())
        ):
            self.folder_list.addItem(QListWidgetItem(folder))

    def remove_folder(self):
        current_row = self.folder_list.currentRow()
        if current_row >= 0:
            self.folder_list.takeItem(current_row)

    def get_scrolling_lyrics_enabled(self):
        return self.scrolling_lyrics_checkbox.isChecked()

    def get_lyrics_direction(self):
        return self.lyrics_direction_combo.currentData()

    def get_lyrics_padding(self):
        return self.lyrics_padding_slider.value()

    def get_ruby_padding(self):
        return self.ruby_padding_slider.value()

    def get_show_ruby(self):
        return self.show_ruby_checkbox.isChecked()

    def get_folders(self):
        return [
            self.folder_list.item(row).text()
            for row in range(self.folder_list.count())
        ]


class LrcEditorDialog(QDialog):
    def __init__(self, lrc_path, parent=None):
        super().__init__(parent)
        self.lrc_path = Path(lrc_path)
        self.setWindowTitle(f"Edit {self.lrc_path.name}")
        self.resize(700, 500)

        self.editor = QTextEdit(self)
        if self.lrc_path.exists():
            self.editor.setPlainText(
                self.lrc_path.read_text(encoding="utf-8-sig")
            )
        else:
            self.editor.setPlainText("")

        save_button = QPushButton("Save")
        save_button.clicked.connect(self.save_and_close)

        cancel_button = QPushButton("Cancel")
        cancel_button.clicked.connect(self.reject)

        buttons = QHBoxLayout()
        buttons.addStretch()
        buttons.addWidget(save_button)
        buttons.addWidget(cancel_button)

        layout = QVBoxLayout(self)
        layout.addWidget(self.editor)
        layout.addLayout(buttons)

    def save_and_close(self):
        self.lrc_path.parent.mkdir(parents=True, exist_ok=True)
        self.lrc_path.write_text(
            self.editor.toPlainText(),
            encoding="utf-8",
        )
        self.accept()

class UiToolsPlugin:
    name = "UI Tools"
    version = "1.0.0"
    description = "Built-in settings and LRC editing dialogs."

    def on_load(self, context):
        context.register_service("ui.settings_dialog", SettingsDialog)
        context.register_service("ui.lrc_editor", LrcEditorDialog)
        context.log(f"Loaded {self.name} {self.version}")

    def on_unload(self, context):
        context.log(f"Unloaded {self.name}")


PLUGIN_CLASS = UiToolsPlugin
