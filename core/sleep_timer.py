"""One-shot sleep timer, independent of playback and wall-clock changes."""

import math
import time

from PySide6.QtCore import QObject, QTimer, Signal


def elapsed_seconds():
    # Linux's ordinary monotonic clock excludes suspend. Windows' performance
    # counter includes it; CLOCK_BOOTTIME provides that behavior on Linux too.
    if hasattr(time, "CLOCK_BOOTTIME"):
        return time.clock_gettime(time.CLOCK_BOOTTIME)
    return time.monotonic()


class SleepTimer(QObject):
    changed = Signal()
    pause_requested = Signal()

    def __init__(self, parent=None, *, is_playing=lambda: False, clock=elapsed_seconds):
        super().__init__(parent)
        self._clock = clock
        self._is_playing = is_playing
        self.state = "inactive"
        self.action = "pause"
        self._deadline = None
        self._last_poll = None
        self._display = None
        self._timer = QTimer(self)
        self._timer.setInterval(250)
        self._timer.timeout.connect(self.check)

    @property
    def active(self):
        return self.state != "inactive"

    @property
    def finishing(self):
        return self.state == "finishing"

    @property
    def remaining_seconds(self):
        if self._deadline is None:
            return 0
        return max(0, math.ceil(self._deadline - self._clock()))

    def _notify(self):
        display = (self.state, self.remaining_seconds, self.action)
        if display != self._display:
            self._display = display
            self.changed.emit()

    def start(self, minutes, action="pause"):
        if isinstance(minutes, bool) or not isinstance(minutes, int) or not 1 <= minutes <= 720:
            raise ValueError("Sleep duration must be 1–720 minutes.")
        if action not in ("pause", "finish_track"):
            raise ValueError("Unknown sleep timer action.")
        self.action = action
        self._last_poll = self._clock()
        self._deadline = self._last_poll + minutes * 60
        self.state = "countdown"
        self._timer.start()
        self._notify()

    def finish_current_track(self):
        self._timer.stop()
        self._deadline = None
        self.action = "finish_track"
        self.state = "finishing"
        self._notify()

    def cancel(self):
        self._timer.stop()
        self._deadline = None
        self.state = "inactive"
        self._notify()

    def complete(self):
        if self.active:
            self.cancel()
            self.pause_requested.emit()

    def add_five_minutes(self):
        self.check()
        if self.state == "countdown":
            self._deadline = min(self._deadline + 300, self._clock() + 720 * 60)
            self._notify()

    def check(self):
        if self.state != "countdown":
            return
        now = self._clock()
        delayed = now - self._last_poll >= 5
        self._last_poll = now
        if now >= self._deadline:
            # An overdue timer after suspend (or a blocked GUI) should silence
            # playback on return, rather than extend it for another whole song.
            if self.action == "finish_track" and self._is_playing() and not delayed:
                self.finish_current_track()
            else:
                self.complete()
        else:
            self._notify()
