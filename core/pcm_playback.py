"""Processed playback whose scheduling and output never wait for GUI events."""

from collections import deque
from dataclasses import dataclass
from threading import Lock

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioSink

from core.audio_dsp import (
    PcmPitchProcessor, PcmRateProcessor, apply_pcm_gain_to_bytes,
)
from core.audio_sink import dispose_audio_sink


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
        self._end_of_stream = False
        self._closed = False
        self._wake_pending = False
        self._thread = QThread()
        self._thread.setObjectName("processed-audio")
        self._engine = _PlaybackEngine(self, sink_factory)
        self._engine.moveToThread(self._thread)
        self._thread.started.connect(self._engine.start)
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
        self._end_of_stream = False

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
            pitch_restart = ((abs(previous.shift) < 0.001) != (abs(shift) < 0.001)
                             or abs(previous.shift - shift) >= 12.0)
            if (previous.device != device or previous.enabled != enabled
                    or abs(previous.speed - speed) >= 0.001 or pitch_restart):
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
            with self._lock:
                if self._closed or generation != self._generation:
                    return
                self._end_of_stream = True
            return
        data = bytes(buffer.data())
        if not data:
            return
        fmt = buffer.format()
        with self._lock:
            if self._closed or generation != self._generation:
                return
            limit = max(fmt.bytesForDuration(120_000), len(data))
            # Decoder callbacks can arrive in a burst after a UI/GIL stall.
            # Bound delay without tearing down audible output and restarting
            # pitch warmup each time that happens.
            while self._pending and self._queued_bytes + len(data) > limit:
                dropped, _, _ = self._pending.popleft()
                self._queued_bytes -= len(dropped)
            self._pending.append((data, fmt, controls))
            self._queued_bytes += len(data)
            self._end_of_stream = False
        # Do not emit Qt signals from the renderer's direct callback: stopping
        # or reconfiguring QMediaPlayer can hold its connection locks while
        # waiting for that callback to return. The audio thread polls this inbox.

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
        self._rate_processor = PcmRateProcessor()
        self._last_input = None
        self._generation = -1
        self._sink = None
        self._io = None
        self._pending = bytearray()
        self._active = False
        self._primed = False
        self._playing = False
        self._controls = _Controls()
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.setInterval(20)
        self._poll_timer.timeout.connect(self.pump)

    @Slot()
    def start(self):
        self._poll_timer.start()
        self.pump()

    def _stop_sink(self):
        self._pending.clear()
        self._active = False
        self._primed = False
        sink, self._sink = self._sink, None
        self._io = None
        if sink is not None:
            dispose_audio_sink(sink)

    def _reset(self):
        self._stop_sink()
        self._processor.reset()
        self._rate_processor.reset()
        self._last_input = None

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
            self._poll_timer.stop()
            self._reset()
            self.thread().quit()
            return
        try:
            interval = 5 if controls.enabled and controls.playing else 20
            if self._poll_timer.interval() != interval:
                self._poll_timer.setInterval(interval)
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
                self._last_input = (fmt, settings)
                pcm = self._rate_processor.process(
                    data, fmt.sampleFormat(), fmt.channelCount(), settings.speed,
                    sample_rate=fmt.sampleRate(),
                )
                self._process_resampled(pcm, fmt, settings)
                self._drain()
            with mailbox._lock:
                end_of_stream = mailbox._end_of_stream and not mailbox._pending
            if end_of_stream and self._last_input is not None and self._current():
                fmt, settings = self._last_input
                self._process_resampled(self._rate_processor.flush(), fmt, settings)
            self._drain(force=end_of_stream)
        except (ImportError, ValueError, RuntimeError, OSError) as error:
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

    def _process_resampled(self, pcm, fmt, settings):
        if not pcm:
            return
        pcm = self._processor.process(
            pcm, fmt.sampleFormat(), fmt.channelCount(), fmt.sampleRate(), settings.shift,
        )
        if settings.gain > 1:
            pcm = apply_pcm_gain_to_bytes(pcm, fmt.sampleFormat(), settings.gain)
        if not self._current():
            return
        if abs(settings.shift) >= 0.001 and not self._processor._started:
            return
        self._accept(pcm, fmt)

    def _accept(self, pcm, fmt):
        if self._sink is None or self._sink.format() != fmt:
            if self._sink is not None:
                self._stop_sink()
            self._sink = self._sink_factory(self._controls.device, fmt, self)
            self._sink.setVolume(min(1.0, self._controls.gain))
            # Capacity alone does not provide a reserve: wait for actual audio
            # below before starting playback, then retain room for decoder bursts.
            self._sink.setBufferSize(max(fmt.bytesForDuration(120_000), fmt.bytesPerFrame()))
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

    def _drain(self, force=False):
        if not self._playing or self._io is None or not self._pending or not self._current():
            return
        if not self._primed:
            # Python-backed decoder delivery and DSP still need the GIL. Dialog
            # construction can briefly hold it even with an independent Qt loop.
            reserve = self._sink.format().bytesForDuration(60_000)
            if len(self._pending) < reserve and not force:
                return
            self._primed = True
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
