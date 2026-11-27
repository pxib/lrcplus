"""Processed playback whose scheduling and output never wait for GUI events."""

from dataclasses import dataclass, replace
from threading import Lock
from time import monotonic

from PySide6.QtCore import QObject, QThread, QTimer, Qt, Signal, Slot
from PySide6.QtMultimedia import QAudioBuffer, QAudioSink

from core.audio_dsp import (
    PcmPitchProcessor, PcmRateProcessor, apply_pcm_gain_to_bytes,
)
from core.audio_sink import dispose_audio_sink
from core.pcm_buffers import dsp_plan, make_inbox, make_output


# Keep threads alive if their window is deleted during a native DSP call.
_LIVE_THREADS = set()

# Leave room for decoder jitter without filling the device with old controls.
_OUTPUT_BUFFER_US = 60_000
_STARTUP_RESERVE_US = 30_000
_WRITE_POLL_MS = 5
_POSITION_POLL_MS = 20


@dataclass(frozen=True)
class _Controls:
    device: object = None
    speed: float = 1.0
    shift: float = 0.0
    gain: float = 1.0
    enabled: bool = False
    playing: bool = False
    live_speed: bool = False
    pitch_linked: bool = False


class PcmPlayback(QObject):
    owns_output = True
    route_started = Signal()
    drained = Signal()
    failed = Signal(str)
    _wake = Signal()
    _route = Signal(int)
    _error = Signal(int, str)
    _drained = Signal(int)

    def __init__(self, parent=None, *, sink_factory=QAudioSink, equalizer=None, audio_tap=None):
        super().__init__(parent)
        self._audio_tap = audio_tap
        self._lock = Lock()
        self._controls = _Controls()
        self._generation = 0
        self._pending = make_inbox()
        self._output_progress_us = 0
        self._track_position_ms = None
        self._output_sample_rate = 0
        self._end_of_stream = False
        self._has_input = False
        self._input_expected = False
        self._drain_requested = False
        self._closed = False
        self._wake_pending = False
        self._buffer_output = None
        self._buffer_receiver = None
        self._buffer_receivers = set()
        self._thread = QThread()
        self._thread.setObjectName("processed-audio")
        self._engine = _PlaybackEngine(self, sink_factory, equalizer)
        self._engine.moveToThread(self._thread)
        self._thread.started.connect(self._engine.start)
        self._wake.connect(self._engine.pump, Qt.ConnectionType.QueuedConnection)
        self._route.connect(self._deliver_route, Qt.ConnectionType.QueuedConnection)
        self._error.connect(self._deliver_error, Qt.ConnectionType.QueuedConnection)
        self._drained.connect(self._deliver_drained, Qt.ConnectionType.QueuedConnection)
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
        self._output_progress_us = 0
        self._track_position_ms = None
        self._output_sample_rate = 0
        self._end_of_stream = False
        self._has_input = False
        self._input_expected = False
        self._drain_requested = False

    @property
    def _queued_bytes(self):
        return len(self._pending)

    def reset(self):
        with self._lock:
            if self._closed:
                return
            self._reset_locked()
        self._refresh_buffer_receiver()
        self._notify()

    def output_progress(self):
        """Read an output-thread snapshot without touching its Qt audio sink."""
        with self._lock:
            if self._closed or not self._controls.enabled:
                return None
            return self._generation, self._output_progress_us / 1000, self._controls.playing

    def output_sample_rate(self):
        with self._lock:
            return self._output_sample_rate

    def track_position_ms(self):
        """Read the track time at the sink's playhead, when source timing is known."""
        with self._lock:
            return self._track_position_ms if self._controls.enabled else None

    def configure(self, device, speed, shift, gain, *, enabled, playing,
                  live_speed=False, pitch_linked=False):
        with self._lock:
            if self._closed:
                return
            previous = self._controls
            self._controls = _Controls(device, speed, shift, gain, enabled, playing,
                                       live_speed, pitch_linked)
            if self._pending.configure(previous, self._controls):
                self._reset_locked()
            if enabled and playing:
                self._input_expected = True
        self._refresh_buffer_receiver()
        self._notify()

    def set_playing(self, playing):
        with self._lock:
            if self._closed:
                return
            old = self._controls
            self._controls = replace(old, playing=playing)
            if old.enabled and playing:
                self._input_expected = True
        self._notify()

    def finish(self, *, decoder_finished=False):
        """Keep processed output playing after the native decoder reaches EOF."""
        with self._lock:
            queued_input = self._buffer_receiver is not None
            if (self._closed or not self._controls.enabled
                    or not (self._has_input or (queued_input and decoder_finished
                                               and self._input_expected))
                    or not (decoder_finished or self._end_of_stream)):
                return False
            # EndOfMedia can reach the GUI before the audio thread receives its
            # final buffers. Only the queued EOF buffer may end that stream.
            if not queued_input:
                self._end_of_stream = True
            self._drain_requested = True
            old = self._controls
            self._controls = replace(old, playing=True)
        self._notify()
        return True

    def connect_buffer_output(self, output):
        # A queued native connection lets Qt stop its renderer without waiting
        # for a Python callback to acquire the GIL. The receiver lives on the
        # audio thread, so decoded audio still never waits for GUI events.
        with self._lock:
            if self._buffer_output is not None and self._buffer_output is not output:
                self._reset_locked()
            if self._controls.enabled and self._controls.playing:
                self._input_expected = True
        self._refresh_buffer_receiver(output)
        self._notify()

    def _refresh_buffer_receiver(self, output=None):
        previous_output = self._buffer_output
        if output is None:
            output = previous_output
        with self._lock:
            generation, closed = self._generation, self._closed
        receiver = self._buffer_receiver
        if (receiver is not None and receiver.generation == generation
                and output is previous_output and not closed):
            return
        if receiver is not None:
            try:
                previous_output.audioBufferReceived.disconnect(receiver.receive_buffer)
            except RuntimeError:
                pass  # The output may already have been deleted after a device change.
            receiver.deleteLater()
        self._buffer_receiver = None
        self._buffer_output = None if closed else output
        if closed or output is None:
            return
        receiver = _BufferReceiver(self, generation)
        receiver.moveToThread(self._thread)
        self._buffer_receiver = receiver
        self._buffer_receivers.add(receiver)
        self._thread.finished.connect(receiver.deleteLater)
        receiver.destroyed.connect(lambda: self._buffer_receivers.discard(receiver))
        output.audioBufferReceived.connect(
            receiver.receive_buffer, Qt.ConnectionType.QueuedConnection,
        )

    def receive_buffer(self, buffer, *, generation=None):
        """Copy decoded PCM into the bounded mailbox without touching GUI objects."""
        with self._lock:
            if generation is not None and generation != self._generation:
                return
            generation = self._generation
            controls = self._controls
            if self._closed or not controls.enabled:
                return
        if not buffer.isValid():
            with self._lock:
                if self._closed or generation != self._generation:
                    return
                self._end_of_stream = True
            self._notify()
            return
        data = bytes(buffer.data())
        if not data:
            return
        fmt = buffer.format()
        start_us = buffer.startTime()
        with self._lock:
            if self._closed or generation != self._generation:
                return
            # Decoder callbacks can arrive in a burst after a UI/GIL stall.
            # Bound delay without tearing down audible output and restarting
            # pitch warmup each time that happens.
            self._pending.push(data, fmt, start_us, fmt.bytesForDuration(120_000))
            self._end_of_stream = False
            self._has_input = True
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

    @Slot(int)
    def _deliver_drained(self, generation):
        with self._lock:
            current = not self._closed and generation == self._generation
        if current:
            self.drained.emit()

    def close(self):
        with self._lock:
            if self._closed:
                return
            self._closed = True
            self._reset_locked()
        self._refresh_buffer_receiver()
        self._notify()


class _BufferReceiver(QObject):
    """Give queued Qt buffers an immutable playback generation.

    Disconnecting a signal does not remove events it has already posted.
    Retired receivers reject those events after a track, seek, or route reset.
    """

    def __init__(self, mailbox, generation):
        super().__init__()
        self._mailbox = mailbox
        self.generation = generation

    @Slot(QAudioBuffer)
    def receive_buffer(self, buffer):
        self._mailbox.receive_buffer(buffer, generation=self.generation)


class _PlaybackEngine(QObject):
    def __init__(self, mailbox, sink_factory, equalizer):
        super().__init__()
        self._mailbox = mailbox
        self._sink_factory = sink_factory
        self._processor = PcmPitchProcessor()
        self._rate_processor = PcmRateProcessor(retain_live_history=True)
        self._equalizer = equalizer
        self._last_input = None
        self._stream_flushed = False
        self._generation = -1
        self._sink = None
        self._sink_volume = None
        self._sink_format = None
        self._next_position_poll = 0.0
        self._io = None
        self._pending = ()
        self._active = False
        self._primed = False
        self._playing = False
        self._controls = _Controls()
        self._poll_timer = QTimer(self)
        self._poll_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._poll_timer.timeout.connect(self.pump)

    @Slot()
    def start(self):
        self.pump()

    def _stop_sink(self):
        self._pending = ()
        self._active = False
        self._primed = False
        sink, self._sink = self._sink, None
        self._sink_volume = None
        self._sink_format = None
        self._next_position_poll = 0.0
        self._io = None
        if sink is not None:
            dispose_audio_sink(sink)

    def _reset(self):
        self._stop_sink()
        self._processor.reset()
        self._rate_processor.reset()
        if self._equalizer is not None:
            self._equalizer.reset()
        self._last_input = None
        self._stream_flushed = False

    @property
    def _written_bytes(self):
        return getattr(self._pending, "written_bytes", 0)

    def _current(self):
        with self._mailbox._lock:
            return (not self._mailbox._closed
                    and self._generation == self._mailbox._generation)

    def _set_sink_volume(self, gain):
        volume = min(1.0, gain)
        if volume != self._sink_volume:
            self._sink.setVolume(volume)
            self._sink_volume = volume

    def _schedule_output_poll(self, drain_requested):
        if not self._playing or self._sink is None:
            self._poll_timer.stop()
            return
        # Decoder callbacks wake the worker. Poll quickly only while waiting
        # for device room or for the last written samples to become audible.
        pending_writes = self._primed and bool(self._pending)
        interval = _WRITE_POLL_MS if pending_writes or drain_requested else _POSITION_POLL_MS
        if not self._poll_timer.isActive() or self._poll_timer.interval() != interval:
            self._poll_timer.start(interval)

    def _publish_position(self, *, force):
        progress = 0
        track_position = None
        played = True
        if self._sink is not None:
            now = monotonic()
            if not force and now < self._next_position_poll:
                return False
            self._next_position_poll = now + _POSITION_POLL_MS / 1000
            written_us = self._sink_format.durationForBytes(self._written_bytes)
            processed_us = self._sink.processedUSecs()
            played = processed_us >= written_us
            progress = min(processed_us, written_us)
            track_position = self._pending.position_ms(progress)
        mailbox = self._mailbox
        with mailbox._lock:
            if self._generation == mailbox._generation:
                mailbox._output_progress_us = progress
                mailbox._track_position_ms = track_position
        return played

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
            if generation != self._generation:
                self._reset()
                self._generation = generation
            if not controls.enabled:
                self._poll_timer.stop()
                return
            self._controls = controls
            playing_changed = controls.playing != self._playing
            if self._sink is not None:
                self._set_sink_volume(controls.gain)
                if playing_changed:
                    if controls.playing:
                        self._sink.resume()
                    else:
                        self._sink.suspend()
            self._playing = controls.playing
            # Bound each turn so device writes and control changes get time.
            for _ in range(4):
                with mailbox._lock:
                    if mailbox._closed or mailbox._generation != generation:
                        break
                    block = mailbox._pending.pop()
                    if block is None:
                        break
                    data, fmt, start_us = block
                    # Pitch and gain are live controls. Apply the latest values
                    # to queued input, rather than replaying old slider targets.
                    # Changes that require new audio history bump generation.
                    settings = mailbox._controls
                if self._last_input is None or self._last_input[0] != fmt:
                    if self._last_input is not None:
                        self._reset()
                    self._pending = make_output(fmt)
                self._pending.add_source(len(data) // fmt.bytesPerFrame(), start_us)
                self._last_input = (fmt, settings)
                self._stream_flushed = False
                pcm = self._rate_processor.process(
                    data, fmt.sampleFormat(), fmt.channelCount(), settings.speed,
                    sample_rate=fmt.sampleRate(), smooth=settings.live_speed,
                )
                self._process_resampled(pcm, fmt, settings)
                self._drain()
            with mailbox._lock:
                end_of_stream = mailbox._end_of_stream and not mailbox._pending
                drain_requested = mailbox._drain_requested
            if end_of_stream and self._last_input is None:
                self._stream_flushed = True
            if (end_of_stream and not self._stream_flushed
                    and self._last_input is not None and self._current()):
                fmt, settings = self._last_input
                self._process_resampled(self._rate_processor.flush(), fmt, settings, tail=True)
                self._accept_processed(self._processor.flush(), fmt, dsp_plan(settings, settings.speed))
                self._stream_flushed = True
            self._drain(force=end_of_stream)
            awaiting_tail = drain_requested and end_of_stream and self._stream_flushed and not self._pending
            played = self._publish_position(force=awaiting_tail or playing_changed)
            if awaiting_tail:
                # Written data still belongs to the device. Advancing on write
                # completion would cut off the tail already in its buffer.
                with mailbox._lock:
                    drained = (played and mailbox._drain_requested and not mailbox._closed
                               and generation == mailbox._generation and not mailbox._pending)
                    if drained:
                        mailbox._drain_requested = mailbox._has_input = False
                        mailbox._input_expected = False
                if drained:
                    mailbox._drained.emit(generation)
                    drain_requested = False
            self._schedule_output_poll(drain_requested)
        except (ImportError, ValueError, RuntimeError, OSError) as error:
            self._poll_timer.stop()
            self._reset()
            with mailbox._lock:
                report = not mailbox._closed and generation == mailbox._generation
                if report:
                    # Disable input until the GUI handles recovery. Check and
                    # update together so an old failure cannot disable a seek.
                    old = mailbox._controls
                    mailbox._controls = replace(old, enabled=False)
                    mailbox._pending.configure(old, mailbox._controls)
                    mailbox._pending.clear()
            if report:
                mailbox._error.emit(generation, str(error))
        with mailbox._lock:
            more = bool(mailbox._pending) or generation != mailbox._generation or mailbox._closed
        if more:
            mailbox._notify()

    def _process_resampled(self, pcm, fmt, settings, *, tail=False):
        if not pcm:
            return
        frame_size = fmt.bytesPerFrame()
        spans = self._rate_processor.spans or ((len(pcm) // frame_size, 1.0),)
        pending_before = len(self._pending)
        start = 0
        for frames, rate in spans:
            self._pending.add_resampled(frames, rate)
            end = start + frames * frame_size
            plan = dsp_plan(settings, rate)
            pitch_options = {"continuous": True} if plan.continuous else {}
            shifted = self._processor.process(
                pcm[start:end], fmt.sampleFormat(), fmt.channelCount(), fmt.sampleRate(), plan.shift,
                **pitch_options,
            )
            self._accept_processed(shifted, fmt, plan)
            start = end
        if not tail:
            # A ramp splits one decoder block into several spans. Keep the
            # whole new block when capping older pending audio.
            self._pending.trim(pending_before, fmt.bytesForDuration(120_000))

    def _accept_processed(self, pcm, fmt, plan):
        if not pcm:
            return
        frame_size = fmt.bytesPerFrame()
        frames = len(pcm) // frame_size
        padding, content = 0, frames
        if plan.pitched:
            if not self._processor._started:
                return
            padding, content = self._processor.content_frames
            if plan.trim_pitch_padding and (padding or content < frames):
                # Native output covers startup. During a ramp, wait for actual
                # pitch output instead of writing temporary silence.
                pcm = pcm[padding * frame_size:(padding + content) * frame_size]
                if not pcm:
                    return
                padding = 0
        if plan.boost > 1:
            pcm = apply_pcm_gain_to_bytes(pcm, fmt.sampleFormat(), plan.boost)
        if self._equalizer is not None:
            pcm = self._equalizer.process(pcm, fmt)
        if self._current():
            self._accept(pcm, fmt, padding, content)

    def _accept(self, pcm, fmt, padding, content):
        if self._sink is None:
            self._sink = self._sink_factory(self._controls.device, fmt, self)
            self._set_sink_volume(self._controls.gain)
            # Capacity alone does not provide a reserve: wait for actual audio
            # below before starting playback, then retain room for decoder bursts.
            self._sink.setBufferSize(max(fmt.bytesForDuration(_OUTPUT_BUFFER_US), fmt.bytesPerFrame()))
            self._io = self._sink.start()
            if self._io is None:
                raise RuntimeError("Qt could not start the PCM audio sink.")
            self._sink_format = self._sink.format()
            if not self._playing:
                self._sink.suspend()
        self._pending.queue(pcm, padding, content)

    def _drain(self, force=False):
        if not self._playing or self._io is None or not self._pending or not self._current():
            return
        audio_format = self._sink_format
        if not self._primed:
            # Python-backed decoder delivery and DSP still need the GIL. Dialog
            # construction can briefly hold it even with an independent Qt loop.
            reserve = audio_format.bytesForDuration(_STARTUP_RESERVE_US)
            if len(self._pending) < reserve and not force:
                return
            self._primed = True
        chunk = self._pending.peek(max(0, self._sink.bytesFree()))
        if chunk:
            written = self._io.write(chunk)
            if not 0 <= written <= len(chunk):
                raise RuntimeError("The PCM audio sink rejected audio data.")
            tap = self._mailbox._audio_tap
            if written and tap is not None and tap.collecting:
                tap.capture(memoryview(chunk)[:written], audio_format, "processed")
            self._pending.commit(written)
            if written and not self._active:
                self._active = True
                mailbox = self._mailbox
                track_position = self._pending.position_ms(self._sink.processedUSecs())
                with mailbox._lock:
                    if self._generation == mailbox._generation:
                        mailbox._output_sample_rate = audio_format.sampleRate()
                        mailbox._track_position_ms = track_position
                mailbox._route.emit(self._generation)
