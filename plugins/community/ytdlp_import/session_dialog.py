"""Choose and test a local YouTube session for one queued download."""
from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QComboBox, QDialog, QDialogButtonBox, QFileDialog, QHBoxLayout, QLabel,
    QLineEdit, QPushButton, QVBoxLayout,
)

from .job import ImportJob, YOUTUBE_AGE_SESSION_HINT, is_youtube_age_restriction
from .worker import youtube_session_path


class YouTubeSessionDialog(QDialog):
    def __init__(self, parent=None, *, url, config, source=None):
        super().__init__(parent)
        self.setWindowTitle("Use a YouTube session")
        self.setMinimumWidth(480)
        self._job = None
        self._url = url
        self._config = dict(config)
        self.source = None
        layout = QVBoxLayout(self)

        notice = QLabel(
            "YouTube session support is a prototype. Compatibility varies by browser profile and video. "
            "Use Test session to check access before downloading."
        )
        notice.setWordWrap(True)
        layout.addWidget(notice)

        source_label = QLabel("Session source")
        self.kind = QComboBox()
        self.kind.addItem("Firefox or LibreWolf profile", "firefox")
        self.kind.addItem("Exported cookies.txt", "file")
        source_label.setBuddy(self.kind)
        layout.addWidget(source_label)
        layout.addWidget(self.kind)

        self.hint = QLabel()
        self.hint.setWordWrap(True)
        layout.addWidget(self.hint)
        self.path = QLineEdit()
        self.path.setAccessibleName("Session source path")
        self.browse = QPushButton("Browse…")
        self.browse.clicked.connect(self._browse)
        path_row = QHBoxLayout()
        path_row.addWidget(self.path, 1)
        path_row.addWidget(self.browse)
        layout.addLayout(path_row)

        self.status = QLabel()
        self.status.setTextFormat(Qt.TextFormat.PlainText)
        self.status.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Cancel)
        self.test = buttons.addButton("Test session", QDialogButtonBox.ButtonRole.ActionRole)
        self.test.setToolTip("Check whether yt-dlp can find audio for this video with the selected session.")
        self.use = buttons.addButton("Use for this download", QDialogButtonBox.ButtonRole.AcceptRole)
        self.test.clicked.connect(self._test_session)
        buttons.accepted.connect(self._use_session)
        buttons.rejected.connect(self.reject)
        layout.addWidget(buttons)

        self.kind.currentIndexChanged.connect(self._source_changed)
        self.path.textChanged.connect(self.status.clear)
        if source is not None:
            self.kind.setCurrentIndex(self.kind.findData(source["kind"]))
        self._source_changed()
        if source is not None:
            self.path.setText(source["path"])

    def _source_changed(self):
        self.path.clear()
        self.status.clear()
        if self.kind.currentData() == "firefox":
            self.hint.setText("In your signed-in browser, open about:support and find Profile Folder. Choose that folder here.")
        else:
            self.hint.setText("Choose a Netscape cookies.txt export from your signed-in YouTube session.")

    def _browse(self):
        if self.kind.currentData() == "firefox":
            path = QFileDialog.getExistingDirectory(self, "Choose browser profile folder")
        else:
            path, _ = QFileDialog.getOpenFileName(self, "Choose YouTube cookies.txt", filter="Text files (*.txt);;All files (*)")
        if path:
            self.path.setText(path)

    def _selected_source(self):
        source = {"kind": self.kind.currentData(), "path": self.path.text().strip()}
        try:
            source["path"] = str(youtube_session_path(source))
        except ValueError as error:
            self.status.setText(str(error))
            self.path.setFocus()
            return None
        return source

    def _test_session(self):
        source = self._selected_source()
        if source is None or self._job is not None:
            return
        self._set_testing(True)
        self.status.setText("Checking for audio formats on YouTube…")
        request = {"action": "check_youtube_session", "youtube_session": source,
                   "url": self._url, "config": self._config}
        self._job = ImportJob(request, self)
        self._job.succeeded.connect(lambda result: self.status.setText(result["message"]))
        self._job.failed.connect(self._test_failed)
        self._job.finished.connect(self._test_finished)
        self._job.start()

    def _test_failed(self, message):
        if is_youtube_age_restriction(message):
            message += "\n\n" + YOUTUBE_AGE_SESSION_HINT
        self.status.setText(message)

    def _set_testing(self, testing):
        for widget in (self.kind, self.path, self.browse, self.test, self.use):
            widget.setEnabled(not testing)

    def _test_finished(self):
        self._job.deleteLater()
        self._job = None
        self._set_testing(False)

    def _use_session(self):
        if self._job is None and (source := self._selected_source()) is not None:
            self.source = source
            self.accept()

    def reject(self):
        if self._job is not None:
            self._job.shutdown()
        super().reject()
