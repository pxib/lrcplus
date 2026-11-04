"""Bounded PCM processing, with Qt audio devices kept on the GUI thread."""

from collections import deque
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QObject, Qt, Signal

from core.audio_dsp import (
    PcmPitchProcessor, apply_pcm_rate_to_bytes, apply_pcm_gain_to_bytes,
)


class PcmWorker(QObject):
    ready = Signal(object)
    failed = Signal(str)
    _completed = Signal(object)

    def __init__(self, parent=None):
        super().__init__(parent)
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="pcm")
        self._processor = PcmPitchProcessor()
        self._pending = deque()
        self._future = None
        self._generation = 0
        self._needs_reset = True
        self._closed = False
        self._completed.connect(self._complete, Qt.ConnectionType.QueuedConnection)

    def reset(self):
        self._generation += 1
        self._pending.clear()
        self._needs_reset = True

    def submit(self, data, audio_format, speed, shift, gain):
        if self._closed:
            return
        # Drop a stalled backlog instead of replaying increasingly old audio.
        limit = max(audio_format.bytesForDuration(120_000), len(data))
        if sum(len(job[0]) for job in self._pending) + len(data) > limit:
            self.reset()
        self._pending.append((data, audio_format, speed, shift, gain))
        self._dispatch()

    def _dispatch(self):
        if self._future is not None or not self._pending:
            return
        job = self._pending.popleft()
        reset = self._needs_reset
        self._needs_reset = False
        generation = self._generation
        self._future = self._executor.submit(self._process, job, reset)
        self._future.add_done_callback(
            lambda future: self._notify_complete(future, generation)
        )

    def _notify_complete(self, future, generation):
        if self._closed:
            return
        try:
            self._completed.emit((future, generation))
        except RuntimeError:
            # The window may have been destroyed during a native call.
            pass

    def _process(self, job, reset):
        if reset:
            self._processor.reset()
        data, fmt, speed, shift, gain = job
        pcm = apply_pcm_rate_to_bytes(data, fmt.sampleFormat(), fmt.channelCount(), speed)
        pcm = self._processor.process(
            pcm, fmt.sampleFormat(), fmt.channelCount(), fmt.sampleRate(), shift,
        )
        if gain > 1:
            pcm = apply_pcm_gain_to_bytes(pcm, fmt.sampleFormat(), gain)
        started = abs(shift) < 0.001 or self._processor._started
        return pcm, fmt, started

    def _complete(self, completion):
        if self._closed:
            return
        future, generation = completion
        self._future = None
        try:
            result = future.result()
        except Exception as error:
            if generation == self._generation:
                self.reset()
                self.failed.emit(str(error))
        else:
            if generation == self._generation:
                self.ready.emit(result)
        self._dispatch()

    def close(self):
        if self._closed:
            return
        self._closed = True
        self.reset()
        # Cleanup follows any running native call on the same worker.
        self._executor.submit(self._processor.reset)
        self._executor.shutdown(wait=False)
