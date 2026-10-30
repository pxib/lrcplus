"""Optional URL audio imports, using only the public host API."""
from pathlib import Path
import shutil

from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QFileDialog, QFormLayout, QHBoxLayout,
    QLabel, QLineEdit, QPushButton, QVBoxLayout, QWidget,
)

from plugins.community.ytdlp_import.dialog import ImportDialog


DEFAULTS = {
    "download_folder": str(Path.home() / "Music" / "LRCPlus"),
    "codec": "m4a", "add_to_queue": True,
    "ffmpeg_location": "", "js_runtime": "auto", "js_path": "",
    "subtitle_language": "", "subtitle_kind": "manual", "subtitles_as_lyrics": True,
    "use_musicbrainz_cover": False,
}


class YtdlpImportPlugin:
    name = "yt-dlp Audio Import"
    version = "1.2.0"
    description = "Import URL audio and subtitles, with optional synced lyrics."

    def __init__(self):
        self.context = None
        self.window = None
        self.dialog = None

    def on_load(self, context):
        self.context = context
        context.api.on("app_ready", self.on_app_ready)
        context.api.add_menu_action("Plugins", "Import audio from URL…", self.open_import)
        context.api.register_settings_widget(
            "yt-dlp Audio Import", self.create_settings_widget,
            apply=self.apply_settings_widget,
            description="Download folder, audio format, and external tools.", order=30,
        )
        app = QApplication.instance()
        if app is not None:
            app.aboutToQuit.connect(self.shutdown)

    def on_app_ready(self, window):
        self.window = window

    def settings(self):
        return {key: self.context.api.get_setting(key, default) for key, default in DEFAULTS.items()}

    def open_import(self):
        if self.dialog is None:
            self.dialog = ImportDialog(self.context.api, self.settings(), self.window)
        self.dialog.show()
        self.dialog.raise_()
        self.dialog.activateWindow()

    def shutdown(self):
        if self.dialog is not None:
            self.dialog.shutdown()
            self.dialog.close()
            self.dialog.deleteLater()
            self.dialog = None

    def on_unload(self, context):
        self.shutdown()
        app = QApplication.instance()
        if app is not None:
            try:
                app.aboutToQuit.disconnect(self.shutdown)
            except (RuntimeError, TypeError):
                pass

    def create_settings_widget(self, parent=None):
        values = self.settings()
        widget = QWidget(parent)
        layout = QVBoxLayout(widget)
        form = QFormLayout()
        widget.folder = QLineEdit(str(values["download_folder"]))
        folder_row = QHBoxLayout()
        folder_row.addWidget(widget.folder)
        browse = QPushButton("Browse…")

        def choose_folder():
            selected = QFileDialog.getExistingDirectory(widget, "Download Folder", widget.folder.text())
            if selected:
                widget.folder.setText(selected)

        browse.clicked.connect(choose_folder)
        folder_row.addWidget(browse)
        form.addRow("Download folder:", folder_row)
        widget.codec = QComboBox()
        widget.codec.addItem("M4A", "m4a")
        widget.codec.addItem("MP3 (192 kbps)", "mp3")
        widget.codec.setCurrentIndex(max(0, widget.codec.findData(values["codec"])))
        form.addRow("Default format:", widget.codec)
        widget.use_musicbrainz_cover = QCheckBox("Use MusicBrainz for album cover")
        widget.use_musicbrainz_cover.setChecked(bool(values["use_musicbrainz_cover"]))
        form.addRow(widget.use_musicbrainz_cover)
        widget.ffmpeg = QLineEdit(str(values["ffmpeg_location"]))
        widget.ffmpeg.setPlaceholderText("Auto-detect from PATH, or enter the FFmpeg folder")
        form.addRow("FFmpeg folder or executable:", widget.ffmpeg)
        widget.runtime = QComboBox()
        for text, data in (("Auto-detect", "auto"), ("Deno", "deno"), ("Node", "node"), ("QuickJS", "quickjs")):
            widget.runtime.addItem(text, data)
        widget.runtime.setCurrentIndex(max(0, widget.runtime.findData(values["js_runtime"])))
        form.addRow("JavaScript runtime:", widget.runtime)
        widget.runtime_path = QLineEdit(str(values["js_path"]))
        widget.runtime_path.setPlaceholderText("Optional executable path; choose a runtime type above")
        form.addRow("Runtime executable:", widget.runtime_path)
        layout.addLayout(form)
        detected = QLabel(
            "Detected FFmpeg: " + (shutil.which("ffmpeg") or "not found")
            + "\nDetected ffprobe: " + (shutil.which("ffprobe") or "not found")
        )
        detected.setWordWrap(True)
        layout.addWidget(detected)
        help_text = QLabel(
            'Install or repair <b>yt-dlp</b> and <b>mutagen</b> in Settings &gt; Dependencies, then restart. '
            'Reinstalling yt-dlp updates its Python package and matching YouTube challenge scripts. '
            'FFmpeg/ffprobe and a supported JavaScript runtime are installed separately. '
            'Use <b>Check setup</b> in the import window to inspect them. '
            '<a href="https://github.com/yt-dlp/yt-dlp#dependencies">Tool setup</a> · '
            '<a href="https://github.com/yt-dlp/yt-dlp/wiki/EJS">YouTube runtime setup</a>'
        )
        help_text.setOpenExternalLinks(True)
        help_text.setWordWrap(True)
        layout.addWidget(help_text)
        return widget

    def apply_settings_widget(self, widget):
        values = {
            "download_folder": widget.folder.text().strip() or DEFAULTS["download_folder"],
            "codec": widget.codec.currentData(),
            "use_musicbrainz_cover": widget.use_musicbrainz_cover.isChecked(),
            "ffmpeg_location": widget.ffmpeg.text().strip(),
            "js_runtime": widget.runtime.currentData(), "js_path": widget.runtime_path.text().strip(),
        }
        for key, value in values.items():
            self.context.api.set_setting(key, value)
        if self.dialog is not None:
            self.dialog.apply_settings(values)


PLUGIN_CLASS = YtdlpImportPlugin
