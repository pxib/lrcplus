"""Progress and results for a background ReplayGain scan."""

from html import escape
from queue import Empty, SimpleQueue
from threading import Event

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtWidgets import (
    QDialog, QHeaderView, QLabel, QProgressBar, QPushButton, QTreeWidget, QTreeWidgetItem, QVBoxLayout,
)

from core.background_job import BackgroundJob
from core.replaygain_scan import scan_replaygain
from ui.settings_controls import _apply_settings_dialog_style, settings_theme_options


class ReplayGainDialog(QDialog):
    scanned = Signal(object)

    def __init__(self, parent, executable, groups, *, album):
        super().__init__(parent)
        self.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
        self.setWindowTitle("ReplayGain Scan")
        self.resize(640, 360)
        self._cancelled = Event()
        self._updates = SimpleQueue()

        layout = QVBoxLayout(self)
        self.status = QLabel("Preparing scan…", self)
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.progress = QProgressBar(self)
        self.progress.setRange(0, sum(map(len, groups)))
        self.progress.setValue(0)
        layout.addWidget(self.progress)
        self.results = QTreeWidget(self)
        self.results.setHeaderLabels(("Track", "Track gain", "Album gain", "Status"))
        self.results.setRootIsDecorated(False)
        self.results.setUniformRowHeights(True)
        self.results.header().setSectionResizeMode(0, QHeaderView.ResizeMode.Stretch)
        self.results.header().resizeSection(1, 85)
        self.results.header().resizeSection(2, 85)
        self.results.header().resizeSection(3, 175)
        layout.addWidget(self.results, 1)
        self.close_button = QPushButton("Cancel", self)
        self.close_button.clicked.connect(self.reject)
        layout.addWidget(self.close_button, alignment=Qt.AlignmentFlag.AlignRight)
        _apply_settings_dialog_style(self, *settings_theme_options(parent))

        self._timer = QTimer(self, interval=100)
        self._timer.timeout.connect(self._update_progress)
        self._timer.start()
        self._job = BackgroundJob(self, "replaygain-scan")
        self._job.finished.connect(self._finish_scan)
        self._job.failed.connect(self._scan_failed)
        self._job.start(scan_replaygain, executable, groups, album, self._cancelled, self._updates)

    def _update_progress(self):
        current = None
        while True:
            try:
                current = self._updates.get_nowait()
            except Empty:
                break
        if current is not None:
            completed, name = current
            self.progress.setValue(completed)
            self.status.setText(f"Scanning {name}")

    def _finish_scan(self, results):
        if results is None:
            return
        self._timer.stop()
        self.progress.setValue(self.progress.maximum())
        rows = []
        paths = []
        for result in results:
            track_gain = result.gain.track_gain if result.gain else None
            album_gain = result.gain.album_gain if result.gain else None
            item = QTreeWidgetItem((result.path.name, self._format_gain(track_gain),
                                    self._format_gain(album_gain), result.status or "Saved"))
            item.setToolTip(0, f"<qt>{escape(str(result.path))}</qt>")
            rows.append(item)
            if result.gain is not None:
                paths.append(result.path)
        self.results.addTopLevelItems(rows)
        tracks = "track" if len(results) == 1 else "tracks"
        self.status.setText(f"Saved ReplayGain for {len(paths)} of {len(results)} {tracks}.")
        self.close_button.setText("Close")
        self.scanned.emit(paths)

    @staticmethod
    def _format_gain(gain):
        return f"{gain:+.2f} dB" if gain is not None else "—"

    def _scan_failed(self, message):
        self._timer.stop()
        self.status.setText(f"ReplayGain scan failed. {message}")
        self.close_button.setText("Close")

    def done(self, result):
        self._cancelled.set()
        self._timer.stop()
        self._job.close()
        super().done(result)
