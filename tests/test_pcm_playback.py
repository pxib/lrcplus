import threading
import time
import wave

import numpy as np
import pytest
from PySide6.QtCore import Qt
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioFormat

from core.pcm_playback import PcmPlayback


def audio_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def buffer(value=0.25, frames=4096):
    return QAudioBuffer(np.full((frames, 2), value, np.float32).tobytes(), audio_format())


class Sink:
    """Simulated device with a configurable delay between partial writes."""

    def __init__(self, device, fmt, parent, *, write_interval=0.005):
        self.owner = threading.get_ident()
        self.device = device
        self.fmt = fmt
        self.written = bytearray()
        self.total_written = 0
        self.last_write = 0
        self.write_interval = write_interval
        self.suspended = threading.Event()
        self.was_reset = threading.Event()
        self.progress = threading.Event()

    def check_thread(self):
        assert threading.get_ident() == self.owner

    def format(self):
        self.check_thread()
        return self.fmt

    def setVolume(self, volume):
        self.check_thread()
        self.volume = volume

    def setBufferSize(self, size):
        self.check_thread()
        self.buffer_size = size

    def start(self):
        self.check_thread()
        return self

    def bytesFree(self):
        self.check_thread()
        return 4096 if time.monotonic() - self.last_write >= self.write_interval else 0

    def write(self, data):
        self.check_thread()
        assert not self.suspended.is_set()
        self.written.extend(data)
        self.total_written += len(data)
        self.last_write = time.monotonic()
        self.progress.set()
        return len(data)

    def reset(self):
        self.check_thread()
        self.written.clear()
        self.was_reset.set()

    def stop(self):
        self.check_thread()

    def deleteLater(self):
        self.check_thread()

    def suspend(self):
        self.check_thread()
        self.suspended.set()

    def resume(self):
        self.check_thread()
        self.suspended.clear()


def wait_without_gui(predicate):
    deadline = time.monotonic() + 3
    while not predicate() and time.monotonic() < deadline:
        time.sleep(0.001)
    assert predicate()


def close(worker):
    # A join in the test verifies shutdown can finish with no GUI events.
    thread = worker._thread
    worker.close()
    assert thread.wait(3000)


def test_output_primes_real_audio_reserve_before_starting(qapp):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        for expected in (8192, 16384):
            worker.receive_buffer(buffer(frames=1024))
            wait_without_gui(lambda: len(worker._engine._pending) == expected)
            assert sinks[0].total_written == 0
        worker.receive_buffer(buffer(frames=1024))
        wait_without_gui(lambda: sinks[0].total_written == 24576)
        assert worker._engine._primed
    finally:
        close(worker)


def test_short_stream_drains_even_when_smaller_than_startup_reserve(qapp):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(frames=1024))
        wait_without_gui(lambda: len(worker._engine._pending) == 8192)
        assert sinks[0].total_written == 0
        worker.receive_buffer(QAudioBuffer())
        wait_without_gui(lambda: sinks[0].total_written == 8192)
    finally:
        close(worker)


def test_renderer_callback_never_emits_qt_wakeup_signals(qapp, monkeypatch):
    worker = PcmPlayback(sink_factory=Sink)
    notifications = []
    renderer = []
    notify = worker._notify

    def record_notification():
        notifications.append(threading.get_ident())
        notify()

    monkeypatch.setattr(worker, "_notify", record_notification)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)

        def receive():
            renderer.append(threading.get_ident())
            worker.receive_buffer(buffer())
            worker.receive_buffer(QAudioBuffer())

        producer = threading.Thread(target=receive)
        producer.start()
        producer.join(2)
        assert not producer.is_alive()
        assert renderer[0] not in notifications
        wait_without_gui(lambda: worker._engine._active)
    finally:
        close(worker)


def test_decoder_burst_keeps_pitch_history_and_active_sink(qapp, monkeypatch):
    sinks = []
    entered, release = threading.Event(), threading.Event()
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    process = worker._engine._processor.process

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return process(*args)

    monkeypatch.setattr(worker._engine._processor, "process", blocked)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        generation = worker._generation
        worker.receive_buffer(buffer(frames=1024))
        assert entered.wait(2)
        for _ in range(50):
            worker.receive_buffer(buffer(0.5, frames=1024))
        assert worker._queued_bytes <= audio_format().bytesForDuration(120000)
        assert worker._generation == generation
        expected = worker._queued_bytes + 8192
        release.set()
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == expected)
        assert len(sinks) == 1
        assert not sinks[0].was_reset.is_set()
        assert worker._generation == generation
    finally:
        release.set()
        close(worker)


@pytest.mark.parametrize("write_interval", [0.005, 0.02])
def test_decoder_processing_and_device_writes_continue_while_gui_is_stalled(qapp, write_interval):
    sinks = []

    def factory(*args):
        sink = Sink(*args, write_interval=write_interval)
        sinks.append(sink)
        return sink

    worker = PcmPlayback(sink_factory=factory)
    source = QAudioBufferOutput()
    source.audioBufferReceived.connect(worker.receive_buffer, Qt.ConnectionType.DirectConnection)
    worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
    routes = []
    errors = []
    worker.route_started.connect(lambda: routes.append(True))
    worker.failed.connect(errors.append)

    next_buffer = threading.Semaphore(0)
    stopping = threading.Event()

    def decoder():
        for _ in range(6):
            next_buffer.acquire()
            if stopping.is_set():
                return
            source.audioBufferReceived.emit(buffer())

    producer = threading.Thread(target=decoder)
    try:
        producer.start()
        # Intentionally do not process a single GUI event. The old output
        # path stopped here waiting for its GUI completion and drain timer.
        # Pace the decoder by completed writes. Fixed sleeps can overrun the
        # bounded audio queue when the runner schedules device writes slowly.
        for index in range(6):
            next_buffer.release()
            expected = (index + 1) * 32768
            wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == expected)
        producer.join(3)
        assert not producer.is_alive()
        assert sinks[0].owner != threading.get_ident()
        assert sinks[0].buffer_size == audio_format().bytesForDuration(120000)
        assert not routes  # The UI notification is still queued.
        np.testing.assert_array_equal(np.frombuffer(sinks[0].written, np.float32), 0.375)
        qapp.processEvents()
        assert routes == [True]
        assert not errors
    finally:
        stopping.set()
        next_buffer.release()
        producer.join(3)
        close(worker)


def test_seek_discards_inflight_audio_without_waiting_on_gui(qapp, monkeypatch):
    sinks = []
    entered, release = threading.Event(), threading.Event()
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    process = worker._engine._processor.process

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return process(*args)

    monkeypatch.setattr(worker._engine._processor, "process", blocked)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(0.25))
        assert entered.wait(2)
        worker.reset()
        worker.receive_buffer(buffer(0.5))
        release.set()
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 32768)
        assert len(sinks) == 1
        np.testing.assert_array_equal(np.frombuffer(sinks[0].written, np.float32), 0.75)
    finally:
        release.set()
        close(worker)


def test_pause_and_resume_preserve_audio_on_output_thread(qapp):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 32768)
        sink = sinks[0]
        worker.set_playing(False)
        assert sink.suspended.wait(2)
        worker.receive_buffer(buffer(0.5))
        wait_without_gui(lambda: len(worker._engine._pending) == 32768)
        assert sink.total_written == 32768
        worker.set_playing(True)
        wait_without_gui(lambda: sink.total_written == 65536)
        assert not sink.suspended.is_set()
        assert len(sinks) == 1
    finally:
        close(worker)


def test_close_during_processing_returns_before_native_work_finishes(qapp, monkeypatch):
    entered, release = threading.Event(), threading.Event()
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])

    def blocked(data, *args):
        entered.set()
        assert release.wait(3)
        return data

    monkeypatch.setattr(worker._engine._processor, "process", blocked)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        assert entered.wait(2)
        worker.close()
        assert worker._thread.isRunning()
        release.set()
        assert worker._thread.wait(3000)
        assert not sinks
    finally:
        release.set()
        close(worker)


def test_output_device_change_recreates_sink_on_audio_thread(qapp):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        worker.configure("first", 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 32768)
        worker.configure("second", 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: len(sinks) == 2 and sinks[1].total_written == 32768)
        assert sinks[0].was_reset.is_set()
        assert sinks[1].device == "second"
    finally:
        close(worker)


def test_device_failure_is_delivered_on_gui_thread_and_stops_input(qapp):
    def unavailable(*args):
        raise OSError("Device disappeared")

    worker = PcmPlayback(sink_factory=unavailable)
    errors = []
    worker.failed.connect(lambda message: errors.append((message, threading.get_ident())))
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: not worker._controls.enabled)
        assert not errors
        qapp.processEvents()
        assert errors == [("Device disappeared", threading.get_ident())]
        worker.receive_buffer(buffer())
        assert not worker._pending
    finally:
        close(worker)


@pytest.mark.parametrize("speed", [0.25, 0.73, 1.25, 2.0])
def test_resampler_tail_reaches_device_once_at_end_of_stream(qapp, speed):
    from core.audio_dsp import apply_pcm_rate_to_bytes

    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    tone = (0.25 * np.sin(2 * np.pi * 440 * np.arange(1031) / 48000)).astype(np.float32)
    source = np.column_stack((tone, -tone))
    expected = apply_pcm_rate_to_bytes(source.tobytes(), audio_format().sampleFormat(), 2, speed)
    try:
        worker.configure(None, speed, 0, 1, enabled=True, playing=True)
        for start in range(0, len(source), 127):
            worker.receive_buffer(QAudioBuffer(source[start:start + 127].tobytes(), audio_format()))
        worker.receive_buffer(QAudioBuffer())
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == len(expected))
        assert bytes(sinks[0].written) == expected
        time.sleep(0.03)  # Further end-of-stream polls cannot duplicate the tail.
        assert sinks[0].total_written == len(expected)
    finally:
        close(worker)


@pytest.mark.parametrize("change", ["speed", "pitch_bypass", "octave", "disabled", "track"])
def test_control_restart_discards_inflight_audio_and_queued_route_notification(qapp, monkeypatch, change):
    sinks = []
    entered, release = threading.Event(), threading.Event()
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    process = worker._engine._processor.process
    routes = []
    worker.route_started.connect(lambda: routes.append(True))

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return process(*args)

    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 32768)
        assert not routes  # The first route notification is queued on the GUI.
        monkeypatch.setattr(worker._engine._processor, "process", blocked)
        worker.receive_buffer(buffer(0.5))
        assert entered.wait(2)
        generation = worker._generation
        if change == "track":
            worker.reset()
        else:
            worker.configure(None, 1.25 if change == "speed" else 1,
                             5 if change == "pitch_bypass" else 12 if change == "octave" else 0,
                             1.5, enabled=change != "disabled", playing=True)
        assert worker._generation != generation
        qapp.processEvents()
        assert not routes  # A stale start must never mute native output.
        release.set()
        wait_without_gui(lambda: sinks[0].was_reset.is_set())
        assert sinks[0].total_written == 32768
        # Rapid switches while DSP was running must leave the final route usable.
        worker.configure(None, 0.73, -5, 1.5, enabled=True, playing=True)
        worker.configure(None, 2, 12, 1.5, enabled=True, playing=True)
        worker.configure(None, 1.25, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(0, frames=4096))
        worker.receive_buffer(QAudioBuffer())
        wait_without_gui(lambda: len(sinks) == 2 and sinks[1].total_written == 3277 * 8)
        assert not np.any(np.frombuffer(sinks[1].written, np.float32))
        qapp.processEvents()
        assert routes == [True]
    finally:
        release.set()
        close(worker)


@pytest.mark.parametrize("operation", ["gui_stall", "recents", "editor"])
def test_player_decoder_keeps_feeding_audio_during_gui_stall(qapp, tmp_path, monkeypatch, operation):
    from PySide6.QtMultimedia import QAudioSink, QMediaDevices
    from ui import main_window
    import pytest

    if not QMediaDevices.audioOutputs():
        pytest.skip("No audio output for the muted media-player integration check")

    class DeviceSink:
        def __init__(self, *args):
            self.native = QAudioSink(*args)
            self.owner = threading.get_ident()
            self.total_written = 0
            self.writes = []

        def __getattr__(self, name):
            return getattr(self.native, name)

        def start(self):
            self.io = self.native.start()
            return self if self.io is not None else None

        def write(self, data):
            assert self.owner == threading.get_ident()
            self.writes.append((time.monotonic(), self.native.state().name))
            count = self.io.write(data)
            self.total_written += max(0, count)
            return count

    path = tmp_path / "silence.wav"
    with wave.open(str(path), "wb") as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(bytes(48000 * 4 * 8))
    sinks = []
    monkeypatch.setattr(main_window, "PcmPlayback", lambda parent: PcmPlayback(
        parent, sink_factory=lambda *args: sinks.append(DeviceSink(*args)) or sinks[-1],
    ))
    window = main_window.Player()
    errors = []
    monkeypatch.setattr(window, "_pcm_output_failed", errors.append)
    worker = window._pcm_worker
    worker.failed.disconnect()
    worker.failed.connect(errors.append)
    for method in ("update_background_for_audio", "_start_lyrics_search", "_notify_queue_changed"):
        monkeypatch.setattr(window, method, lambda *args: None)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    try:
        window.volume_slider.setValue(0)
        window.handle_pitch_change("Semitones", 5)
        window.load_audio(path)
        window.media_player.play()
        deadline = time.monotonic() + 5
        while not sinks and not errors and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert sinks, f"Audio did not start: {errors}"
        from PySide6.QtTest import QTest
        from PySide6.QtCore import QTimer
        from PySide6.QtWidgets import QDialog
        QTest.qWait(300)
        written = sinks[-1].total_written
        before = len(sinks[-1].writes)
        # The actual Qt decoder emits directly into the audio mailbox. Neither
        # decoded-buffer delivery nor device writes may need GUI event handling.
        if operation == "gui_stall":
            time.sleep(0.3)
        else:
            original_exec = QDialog.exec
            def modal(dialog):
                QTimer.singleShot(150, dialog.reject)
                return original_exec(dialog)
            monkeypatch.setattr(QDialog, "exec", modal)
            if operation == "recents":
                window.show_recent_songs()
            else:
                lyrics_path = tmp_path / "silence.lrc"
                lyrics_path.write_text("\n".join(f"[00:{i:02d}.00]Hello world" for i in range(20)), encoding="utf-8")
                window.current_lyrics_path = lyrics_path
                monkeypatch.setattr(main_window.LrcEditorDialog, "_confirm_close", lambda self: True)
                window.edit_current_lyrics()
            QTest.qWait(200)
        assert sinks[-1].total_written > written + 8192
        writes = sinks[-1].writes[max(0, before - 1):]
        assert all(state != "IdleState" for _, state in writes), writes
        assert not errors
        assert sinks[-1].owner != threading.get_ident()
    finally:
        thread = worker._thread
        window.close()
        assert thread.wait(3000)
        window.deleteLater()
        qapp.processEvents()
