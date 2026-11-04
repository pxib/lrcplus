"""Cancellable worker process; no native translation libraries enter the UI."""
import json
import os
from pathlib import Path
import sys

from PySide6.QtCore import QObject, QProcess, Signal

from plugins.community.lyrics_translator.worker import PREFIX


class TranslationJob(QObject):
    succeeded = Signal(object)
    failed = Signal(str)
    progress = Signal(int, int)
    finished = Signal()

    def __init__(self, parent=None):
        super().__init__(parent)
        self.process = QProcess(self)
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._process_error)
        self._done = True

    def start(self, request):
        if not self._done:
            raise RuntimeError("A translation operation is already running.")
        self._done, self._cancelled = False, False
        self._buffer, self._error, self._result = b"", "", None
        self.request = {**request, "python_paths": [path for path in sys.path if path]}
        python = sys.executable
        if getattr(sys, "frozen", False):
            python = os.environ.get("LRCPLUS_DEPENDENCY_PYTHON")
            if not python or not Path(python).is_file():
                self._error = "Install the plugin dependencies and restart LRCPlus first."
                self._finish(1)
                return
        self.process.start(python, ["-u", str(Path(__file__).with_name("worker.py"))])

    def _started(self):
        if self._cancelled:
            self.process.kill()
            return
        self.process.write(json.dumps(self.request).encode("utf-8"))
        self.process.closeWriteChannel()

    def _read(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace")
            if not text.startswith(PREFIX):
                continue
            try:
                event = json.loads(text[len(PREFIX):])
                if "result" in event:
                    self._result = event["result"]
                elif "error" in event:
                    self._error = str(event["error"])
                elif "progress" in event:
                    self.progress.emit(*event["progress"])
            except (ValueError, TypeError):
                self._error = "The translation worker returned an invalid response."

    def _read_error(self):
        # Consume diagnostics without retaining or logging lyric text.
        self.process.readAllStandardError()

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = "Could not start the local translation worker."
            self._finish(1)

    def _finish(self, exit_code=0, *_args):
        if self._done:
            return
        self._read()
        self._done = True
        if not self._cancelled:
            if exit_code != 0 or self._result is None or self._error:
                self.failed.emit(self._error or "Argos stopped unexpectedly. Check its dependencies and model files.")
            else:
                self.succeeded.emit(self._result)
        self.finished.emit()

    def cancel(self):
        if not self._done:
            self._cancelled = True
            self.process.kill()

    def shutdown(self):
        self.cancel()
        if self.process.state() != QProcess.ProcessState.NotRunning:
            self.process.waitForFinished(2000)
