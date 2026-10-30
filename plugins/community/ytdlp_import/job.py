"""Asynchronous subprocess transport and cancellation for the URL importer."""
import json
import os
from pathlib import Path
import shutil
import signal
import sys
import tempfile

from PySide6.QtCore import QObject, QProcess, QProcessEnvironment, Signal

from plugins.community.ytdlp_import.worker import PREFIX


class _WindowsJob:
    """Own only this worker's process tree; closing the handle stops it all."""

    def __init__(self, pid):
        import ctypes
        from ctypes import wintypes

        class BasicLimits(ctypes.Structure):
            _fields_ = [("process_time", ctypes.c_longlong), ("job_time", ctypes.c_longlong),
                        ("flags", wintypes.DWORD), ("min_working_set", ctypes.c_size_t),
                        ("max_working_set", ctypes.c_size_t), ("active_processes", wintypes.DWORD),
                        ("affinity", ctypes.c_size_t), ("priority", wintypes.DWORD),
                        ("scheduling", wintypes.DWORD)]

        class ExtendedLimits(ctypes.Structure):
            _fields_ = [("basic", BasicLimits), ("io_counters", ctypes.c_ulonglong * 6),
                        ("process_memory", ctypes.c_size_t), ("job_memory", ctypes.c_size_t),
                        ("peak_process_memory", ctypes.c_size_t), ("peak_job_memory", ctypes.c_size_t)]

        kernel = ctypes.WinDLL("kernel32", use_last_error=True)
        kernel.CreateJobObjectW.argtypes = [ctypes.c_void_p, wintypes.LPCWSTR]
        kernel.CreateJobObjectW.restype = wintypes.HANDLE
        kernel.SetInformationJobObject.argtypes = [wintypes.HANDLE, ctypes.c_int, ctypes.c_void_p, wintypes.DWORD]
        kernel.SetInformationJobObject.restype = wintypes.BOOL
        kernel.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
        kernel.OpenProcess.restype = wintypes.HANDLE
        kernel.AssignProcessToJobObject.argtypes = [wintypes.HANDLE, wintypes.HANDLE]
        kernel.AssignProcessToJobObject.restype = wintypes.BOOL
        kernel.CloseHandle.argtypes = [wintypes.HANDLE]
        kernel.CloseHandle.restype = wintypes.BOOL
        self.kernel = kernel
        self.handle = kernel.CreateJobObjectW(None, None)
        if not self.handle:
            raise ctypes.WinError(ctypes.get_last_error())
        try:
            limits = ExtendedLimits()
            limits.basic.flags = 0x2000  # JOB_OBJECT_LIMIT_KILL_ON_JOB_CLOSE
            if not kernel.SetInformationJobObject(self.handle, 9, ctypes.byref(limits), ctypes.sizeof(limits)):
                raise ctypes.WinError(ctypes.get_last_error())
            process = kernel.OpenProcess(0x0100 | 0x0001, False, pid)  # SET_QUOTA | TERMINATE
            if not process:
                raise ctypes.WinError(ctypes.get_last_error())
            try:
                if not kernel.AssignProcessToJobObject(self.handle, process):
                    raise ctypes.WinError(ctypes.get_last_error())
            finally:
                kernel.CloseHandle(process)
        except OSError:
            self.close()
            raise

    def close(self):
        if self.handle:
            self.kernel.CloseHandle(self.handle)
            self.handle = None


def worker_command():
    python = sys.executable
    if getattr(sys, "frozen", False):
        python = os.environ.get("LRCPLUS_DEPENDENCY_PYTHON")
        if not python or not Path(python).is_file():
            raise RuntimeError("Install the plugin dependencies and restart LRCPlus to prepare its private Python runtime.")
    return [python, "-I", "-u", str(Path(__file__).with_name("worker.py"))]


class ImportJob(QObject):
    event_received = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, request, parent=None):
        super().__init__(parent)
        self.request = dict(request)
        self.process = QProcess(self)
        self.process.setProcessEnvironment(QProcessEnvironment.systemEnvironment())
        self.process.started.connect(self._started)
        self.process.readyReadStandardOutput.connect(self._read_output)
        self.process.readyReadStandardError.connect(self._read_error)
        self.process.finished.connect(self._finish)
        self.process.errorOccurred.connect(self._process_error)
        self._buffer = b""
        self._stderr = b""
        self._result = None
        self._error = ""
        self._cancelled = False
        self._done = False
        self.stage = None
        self._process_job = None

    def start(self):
        try:
            command = worker_command()
            if self.request["action"] in {"download", "subtitles"}:
                folder = Path(self.request["folder"]).expanduser().resolve()
                folder.mkdir(parents=True, exist_ok=True)
                self.stage = Path(tempfile.mkdtemp(prefix=".lrcplus-import-", dir=folder))
                self.request.update(folder=str(folder), stage=str(self.stage))
            self.process.start(command[0], command[1:])
        except (OSError, RuntimeError) as error:
            self._error = str(error)
            self._finish(1)

    def _started(self):
        if self._cancelled:
            self.cancel()
            return
        if os.name == "nt":
            try:
                # The worker waits for this request before importing yt-dlp or
                # starting children, so they all inherit this job membership.
                self._process_job = _WindowsJob(self.process.processId())
            except OSError as error:
                self._error = "Could not prepare cancellable downloader processes: " + str(error)
                self.process.kill()
                return
        self.process.write(json.dumps(self.request).encode("utf-8"))
        self.process.closeWriteChannel()

    def _read_output(self):
        self._buffer += bytes(self.process.readAllStandardOutput())
        while b"\n" in self._buffer:
            line, self._buffer = self._buffer.split(b"\n", 1)
            text = line.decode("utf-8", "replace")
            if not text.startswith(PREFIX):
                continue
            try:
                event = json.loads(text[len(PREFIX):])
                if not isinstance(event, dict):
                    continue
            except ValueError:
                continue
            if event.get("event") == "result":
                self._result = event.get("result")
            elif event.get("event") == "error":
                self._error = str(event.get("message", "Download failed."))
            elif not self._cancelled:
                self.event_received.emit(event)
        if len(self._buffer) > 4_000_000:
            self._buffer = b""
            self._error = "The downloader returned an oversized response."
            self.process.kill()

    def _read_error(self):
        self._stderr = (self._stderr + bytes(self.process.readAllStandardError()))[-8000:]

    def _process_error(self, error):
        if error == QProcess.ProcessError.FailedToStart:
            self._error = "Could not start the downloader: " + self.process.errorString()
            self._finish(1)

    def cancel(self):
        self._cancelled = True
        pid = self.process.processId()
        if pid:
            if os.name == "nt":
                if self._process_job is not None:
                    self._process_job.close()
            else:
                try:
                    os.killpg(pid, signal.SIGKILL)
                except ProcessLookupError:
                    self.process.kill()
        self.process.kill()

    def shutdown(self):
        if not self._done:
            self.cancel()
            self.process.waitForFinished(3000)
            if not self._done:
                self._finish(1)

    def _cleanup(self):
        if self._process_job is not None:
            self._process_job.close()
            self._process_job = None
        if self.stage is not None:
            stage = self.stage.resolve()
            folder = Path(self.request["folder"]).resolve()
            if stage.parent == folder and stage.name.startswith(".lrcplus-import-"):
                shutil.rmtree(stage, ignore_errors=True)
            self.stage = None

    def _finish(self, exit_code, *_args):
        if self._done:
            return
        self._done = True
        self._read_output()
        self._read_error()
        self._cleanup()
        if self._cancelled:
            self.cancelled.emit()
        elif exit_code == 0 and isinstance(self._result, dict):
            self.succeeded.emit(self._result)
        else:
            detail = self._error or self._stderr.decode("utf-8", "replace").strip()[-2000:]
            self.failed.emit(detail or f"The downloader exited without a result (code {exit_code}).")
        self.finished.emit()
