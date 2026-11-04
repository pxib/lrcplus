"""Processed playback whose scheduling and output never wait for GUI events."""

from collections import deque
from dataclasses import dataclass
from threading import Lock

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioSink

from core.audio_dsp import (
    PcmPitchProcessor, apply_pcm_gain_to_bytes, apply_pcm_rate_to_bytes,
)


# Keep threads alive if their window is deleted during a native DSP call.
_LIVE_THREADS = set()


@dataclass(frozen=True)
class _Controls:
    device: object = None
    speed: float = 1.0
    shift: float = 0.0
    gain: float = 1.0
    enabled: bool = False
    playing: bool = False


class PcmPlayback(QObject):
    owns_output = True
    route_started = Signal()
    failed = Signal(str)
    _wake = Signal()
    _route = Signal(int)
    _error = Signal(int, str)

    def __init__(self, parent=None, *, sink_factory=QAudioSink):
        super().__init__(parent)
        self._lock = Lock()
        self._controls = _Controls()
        self._generation = 0
        self._pending = deque()
        self._queued_bytes = 0
        self._closed = False
        self._wake_pending = False
        self._thread = QThread()
        self._thread.setObjectName("processed-audio")
        self._engine = _PlaybackEngine(self, sink_factory)
        self._engine.moveToThread(self._thread)
        self._wake.connect(self._engine.pump, Qt.ConnectionType.QueuedConnection)
        self._route.connect(self._deliver_route, Qt.ConnectionType.QueuedConnection)
        self._error.connect(self._deliver_error, Qt.ConnectionType.QueuedConnection)
        self._thread.finished.connect(self._engine.deleteLater)
        self._thread.finished.connect(self._thread.deleteLater)
        thread = self._thread
        self._thread.finished.connect(lambda: _LIVE_THREADS.discard(thread))
        _LIVE_THREADS.add(thread)
        self._thread.start(QThread.Priority.HighPriority)

    def _notify(self):
        with self._lock:
            if self._wake_pending:
                return
            self._wake_pending = True
        self._wake.emit()

    def _reset_locked(self):
        self._generation += 1
        self._pending.clear()
        self._queued_bytes = 0

    def reset(self):
        with self._lock:
            if self._closed:
                return
            self._reset_locked()
        self._notify()

    def configure(self, device, speed, shift, gain, *, enabled, playing):
        with self._lock:
            if self._closed:
                return
            previous = self._controls
            self._controls = _Controls(device, speed, shift, gain, enabled, playing)
            if previous.device != device or previous.enabled != enabled:
                self._reset_locked()
        self._notify()

    def set_playing(self, playing):
        with self._lock:
            if self._closed:
                return
            old = self._controls
            self._controls = _Controls(old.device, old.speed, old.shift, old.gain,
                                       old.enabled, playing)
        self._notify()

    def receive_buffer(self, buffer):
        """Direct decoder callback: copy and enqueue, without touching GUI objects."""
        with self._lock:
            generation = self._generation
            controls = self._controls
            if self._closed or not controls.enabled:
                return
        if not buffer.isValid():
            return
        data = bytes(buffer.data())
        if not data:
            return
        fmt = buffer.format()
        with self._lock:
            if self._closed or generation != self._generation:
                return
            limit = max(fmt.bytesForDuration(120_000), len(data))
            if self._queued_bytes + len(data) > limit:
                self._reset_locked()
            self._pending.append((data, fmt, controls))
            self._queued_bytes += len(data)
        self._notify()

    @Slot(int)
    def _deliver_route(self, generation):
        with self._lock:
            current = not self._closed and generation == self._generation
        if current:
            self.route_started.emit()

    @Slot(int, str)
    def _deliver_error(self, generation, error):
        with self._lock:
            current = not self._closed and generation == self._generation
        if current:
            self.failed.emit(error)

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._reset_locked()
        self._notify()


class _PlaybackEngine(QObject):
    def __init__(self, mailbox, sink_factory):
        super().__init__()
        self._mailbox = mailbox
        self._sink_factory = sink_factory
        self._processor = PcmPitchProcessor()
        self._generation = -1
        self._sink = None
        self._io = None
        self._pending = bytearray()
        self._active = False
        self._playing = False
        self._controls = _Controls()
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(5)
        self._timer.timeout.connect(self.pump)

    def _stop_sink(self):
        self._timer.stop()
        self._pending.clear()
        self._active = False
        sink, self._sink = self._sink, None
        self._io = None
        if sink is not None:
            sink.reset()
            sink.stop()
            sink.deleteLater()

    def _reset(self):
        self._stop_sink()
        self._processor.reset()

    def _current(self):
        with self._mailbox._lock:
            return (not self._mailbox._closed
                    and self._generation == self._mailbox._generation)

    @Slot()
    def pump(self):
        mailbox = self._mailbox
        with mailbox._lock:
            mailbox._wake_pending = False
            closed = mailbox._closed
            generation = mailbox._generation
            controls = mailbox._controls
        if closed:
            self._reset()
            self.thread().quit()
            return
        try:
            if generation != self._generation:
                self._reset()
                self._generation = generation
            if not controls.enabled:
                return
            self._controls = controls
            if self._sink is not None:
                self._sink.setVolume(min(1.0, controls.gain))
                if controls.playing != self._playing:
                    if controls.playing:
                        self._sink.resume()
                    else:
                        self._sink.suspend()
            self._playing = controls.playing
            # Bound each turn so device writes and control changes get time.
            for _ in range(4):
                with mailbox._lock:
                    if (mailbox._closed or mailbox._generation != generation
                            or not mailbox._pending):
                        break
                    data, fmt, settings = mailbox._pending.popleft()
                    mailbox._queued_bytes -= len(data)
                pcm = apply_pcm_rate_to_bytes(
                    data, fmt.sampleFormat(), fmt.channelCount(), settings.speed,
                )
                pcm = self._processor.process(
                    pcm, fmt.sampleFormat(), fmt.channelCount(), fmt.sampleRate(), settings.shift,
                )
                if settings.gain > 1:
                    pcm = apply_pcm_gain_to_bytes(pcm, fmt.sampleFormat(), settings.gain)
                if not self._current():
                    break
                if abs(settings.shift) >= 0.001 and not self._processor._started:
                    continue
                self._accept(pcm, fmt)
                self._drain()
            self._drain()
        except (ValueError, RuntimeError, OSError) as error:
            self._reset()
            with mailbox._lock:
                report = not mailbox._closed and generation == mailbox._generation
                if report:
                    # Disable input until the GUI handles recovery. Check and
                    # update together so an old failure cannot disable a seek.
                    old = mailbox._controls
                    mailbox._controls = _Controls(old.device, old.speed, old.shift,
                                                  old.gain, False, old.playing)
                    mailbox._pending.clear()
                    mailbox._queued_bytes = 0
            if report:
                mailbox._error.emit(generation, str(error))
        with mailbox._lock:
            more = bool(mailbox._pending) or generation != mailbox._generation or mailbox._closed
        if more:
            mailbox._notify()

    def _accept(self, pcm, fmt):
        if self._sink is None or self._sink.format() != fmt:
            if self._sink is not None:
                self._stop_sink()
            self._sink = self._sink_factory(self._controls.device, fmt, self)
            self._sink.setVolume(min(1.0, self._controls.gain))
            # Enough scheduling margin for window changes without a large queue.
            self._sink.setBufferSize(max(fmt.bytesForDuration(60_000), fmt.bytesPerFrame()))
            self._io = self._sink.start()
            if self._io is None:
                raise RuntimeError("Qt could not start the PCM audio sink.")
            if not self._playing:
                self._sink.suspend()
        self._pending.extend(pcm)
        frame_size = fmt.bytesPerFrame()
        limit = max(fmt.bytesForDuration(120_000), len(pcm))
        limit -= limit % frame_size
        excess = len(self._pending) - limit
        if excess > 0:
            del self._pending[:excess]

    def _drain(self):
        if not self._playing or self._io is None or not self._pending or not self._current():
            self._timer.stop()
            return
        frame_size = self._sink.format().bytesPerFrame()
        count = min(max(0, self._sink.bytesFree()), len(self._pending))
        count -= count % frame_size
        if count:
            written = self._io.write(bytes(self._pending[:count]))
            if written < 0:
                raise RuntimeError("The PCM audio sink rejected audio data.")
            del self._pending[:written]
            if written and not self._active:
                self._active = True
                self._mailbox._route.emit(self._generation)
        if self._pending:
            if not self._timer.isActive():
                self._timer.start()
        else:
            self._timer.stop()
