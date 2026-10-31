"""Bounded background lyric checks with selection priority and cancellation."""
from concurrent.futures import ThreadPoolExecutor
from threading import Event
from time import monotonic

from PySide6.QtCore import QObject, QTimer, Signal


class LyricsPreviewQueue(QObject):
    finished = Signal(object, object)

    def __init__(self, parent):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=2, thread_name_prefix="lyrics-preview")
        self._timer = QTimer(self)
        self._timer.setInterval(25)
        self._timer.timeout.connect(self._poll)
        self._futures = {}
        self._pending = []
        self._generation = 0
        self._cancel = Event()
        self._function = None
        self._args = ()
        self._next_start = 0
        self._paused_until = 0

    def start(self, function, items, *args):
        self.cancel()
        self._cancel = Event()
        self._function, self._args = function, args
        self._pending = list(items)
        self._next_start = 0
        self._paused_until = 0
        if self._pending or self._futures:
            self._timer.start()

    def prioritize(self, item):
        for index, candidate in enumerate(self._pending):
            if candidate is item:
                self._pending.insert(0, self._pending.pop(index))
                break

    def pause(self, seconds):
        self._paused_until = max(self._paused_until, monotonic() + seconds)

    def cancel(self):
        self._generation += 1
        self._cancel.set()
        self._pending.clear()
        for future in self._futures:
            future.cancel()

    def close(self):
        self.cancel()
        self._timer.stop()
        self._executor.shutdown(wait=False, cancel_futures=True)

    def _poll(self):
        for future, (generation, item) in list(self._futures.items()):
            if not future.done():
                continue
            del self._futures[future]
            if generation != self._generation or future.cancelled():
                continue
            try:
                payload = future.result()
            except Exception as error:
                payload = error
            self.finished.emit(item, payload)
        now = monotonic()
        if self._pending and len(self._futures) < 2 and now >= max(self._next_start, self._paused_until):
            item = self._pending.pop(0)
            future = self._executor.submit(self._function, dict(item), *self._args, self._cancel)
            self._futures[future] = self._generation, item
            self._next_start = now + .25
        if not self._pending and not self._futures:
            self._timer.stop()
