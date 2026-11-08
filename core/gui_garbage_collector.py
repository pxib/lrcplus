"""Collect Python reference cycles on the GUI thread, between Qt events."""

import gc
from time import monotonic

from PySide6.QtCore import QObject, QThread, QTimer, Slot


class GuiGarbageCollector(QObject):
    """Prevent worker allocations from collecting GUI objects in cycles.

    Reference counting still runs normally. Only automatic cycle collection is
    replaced, since it can otherwise destroy Qt wrappers on any Python thread.
    """

    def __init__(self, app):
        if QThread.currentThread() != app.thread():
            raise RuntimeError("Install GUI garbage collection on the GUI thread.")
        super().__init__(app)
        gc.disable()
        self._last_full_collection = monotonic()
        self._timer = QTimer(self)
        self._timer.setInterval(1000)
        self._timer.timeout.connect(self._collect)
        self._timer.start()

    @Slot()
    def _collect(self):
        now = monotonic()
        if now - self._last_full_collection >= 30:
            # Periodically reclaim older cycles even on Python versions whose
            # young-generation collection no longer promotes through gen 1.
            gc.collect(2)
            self._last_full_collection = now
        elif gc.get_count()[0] >= gc.get_threshold()[0]:
            gc.collect(0)


def install_gui_garbage_collector(app):
    collector = getattr(app, "_gui_garbage_collector", None)
    if collector is None:
        collector = GuiGarbageCollector(app)
        app._gui_garbage_collector = collector
    return collector
