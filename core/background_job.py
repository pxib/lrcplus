"""Run blocking work without calling Qt objects from a worker thread."""

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, QTimer, Signal


class BackgroundJob(QObject):
    finished = Signal(object)
    failed = Signal(str)

    def __init__(self, parent, name):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix=name)
        self._future = None
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)

    def start(self, function, *args):
        self.cancel()
        self._future = self._executor.submit(function, *args)
        self._timer.start()

    def cancel(self):
        self._timer.stop()
        if self._future is not None:
            self._future.cancel()
            self._future = None

    def close(self):
        self.cancel()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _poll(self):
        future = self._future
        if future is None or not future.done():
            return
        self._future = None
        self._timer.stop()
        try:
            result = future.result()
        except Exception as error:
            self.failed.emit(str(error))
        else:
            self.finished.emit(result)
