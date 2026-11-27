import threading
import time
import wave
from math import log2

import numpy as np
import pytest
from PySide6.QtMultimedia import QAudioBuffer, QAudioBufferOutput, QAudioFormat

from core.pcm_playback import PcmPlayback


def audio_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(2)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


def buffer(value=0.25, frames=4096, *, start_us=-1):
    return QAudioBuffer(np.full((frames, 2), value, np.float32).tobytes(), audio_format(), start_us)


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

    def processedUSecs(self):
        self.check_thread()
        return self.fmt.durationForBytes(self.total_written)

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


def test_equalizer_processes_audio_at_normal_speed_pitch_and_volume(qapp):
    from plugins.core.equalizer.dsp import EqualizerProcessor

    sinks = []
    equalizer = EqualizerProcessor(lambda: (-6, ()))
    worker = PcmPlayback(
        equalizer=equalizer, sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1],
    )
    try:
        worker.configure(None, 1, 0, 1, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: sinks and sinks[0].total_written == 4096 * 8)
        samples = np.frombuffer(bytes(sinks[0].written), dtype=np.float32)
        np.testing.assert_allclose(samples, 0.25 * 10 ** (-6 / 20))
    finally:
        close(worker)


def test_output_primes_real_audio_reserve_before_starting(qapp):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(frames=1024))
        wait_without_gui(lambda: len(worker._engine._pending) == 8192)
        assert sinks[0].total_written == 0
        worker.receive_buffer(buffer(frames=416))  # Exactly 30 ms of real audio.
        wait_without_gui(lambda: sinks[0].total_written == 1440 * 8)
        assert worker._engine._primed
    finally:
        close(worker)


def test_decoder_wakes_idle_output_and_only_applies_changed_sink_volume(qapp):
    class VolumeMeter(Sink):
        def __init__(self, *args):
            super().__init__(*args)
            self.volume_updates = []

        def setVolume(self, volume):
            super().setVolume(volume)
            self.volume_updates.append(volume)

    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(VolumeMeter(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 0.5, enabled=True, playing=False)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: sinks and sinks[0].suspended.is_set())
        sink = sinks[0]
        assert not sink.total_written
        worker.set_playing(True)
        wait_without_gui(lambda: sink.total_written == 32768)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: sink.total_written == 65536)
        worker.configure(None, 1, 0, 0.75, enabled=True, playing=True)
        wait_without_gui(lambda: sink.volume_updates == [0.5, 0.75])
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        wait_without_gui(lambda: sink.volume_updates == [0.5, 0.75, 1.0])
        worker.configure(None, 1, 0, 2.0, enabled=True, playing=True)
        worker.receive_buffer(buffer())
        wait_without_gui(lambda: sink.total_written == 98304)
        assert sink.volume_updates == [0.5, 0.75, 1.0]
        assert len(sinks) == 1
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


def test_pitch_tail_waits_for_device_playback_and_discards_stale_completion(qapp):
    from core.audio_dsp import PcmPitchProcessor

    class ClockedSink(Sink):
        played_us = 0

        def processedUSecs(self):
            self.check_thread()
            return self.played_us

    source = np.zeros((6007, 2), np.float32)
    tone = .5 * np.sin(2 * np.pi * 440 * np.arange(2400) / 48000)
    source[-2400:] = np.column_stack((tone, tone))
    fmt = audio_format()
    reference = PcmPitchProcessor()
    try:
        expected = reference.process(source.tobytes(), fmt.sampleFormat(), 2, 48000, 5)
        expected += reference.flush()
    finally:
        reference.reset()
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(ClockedSink(*args)) or sinks[-1])
    finished = []
    worker.drained.connect(lambda: finished.append(True))
    try:
        worker.configure(None, 1, 5, 1, enabled=True, playing=True)
        worker.receive_buffer(QAudioBuffer(source.tobytes(), fmt))
        worker.receive_buffer(QAudioBuffer())
        assert worker.finish()
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == len(expected))
        assert bytes(sinks[0].written) == expected
        qapp.processEvents()
        assert not finished  # A successful write does not mean it was heard.
        sinks[0].played_us = fmt.durationForBytes(len(expected))
        wait_without_gui(lambda: not worker._has_input)
        qapp.processEvents()
        assert finished == [True]
        assert not worker.finish(decoder_finished=True)
        # Further EOF polls cannot write or announce the same tail again.
        time.sleep(.03)
        qapp.processEvents()
        assert finished == [True]
        assert sinks[0].total_written == len(expected)
    finally:
        close(worker)


def test_reset_invalidates_queued_drain_notification(qapp):
    class ClockedSink(Sink):
        def processedUSecs(self):
            return 10_000_000

    worker = PcmPlayback(sink_factory=ClockedSink)
    finished = []
    worker.drained.connect(lambda: finished.append(True))
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(frames=1024))
        worker.receive_buffer(QAudioBuffer())
        assert worker.finish()
        wait_without_gui(lambda: not worker._has_input)
        worker.reset()
        qapp.processEvents()
        assert not finished
    finally:
        close(worker)


@pytest.mark.parametrize("stop_early", [False, True, "sleep_pause", "sleep_finish"])
def test_player_waits_for_audible_processed_tail_before_advancing(qapp, tmp_path, monkeypatch, stop_early):
    from PySide6.QtMultimedia import QMediaDevices
    from core.settings import save_playback_settings
    from ui import main_window

    if not QMediaDevices.audioOutputs():
        pytest.skip("No audio output for the muted decoder integration check")

    class ClockedSink(Sink):
        played_us = 0

        def processedUSecs(self):
            return self.played_us

    first, second = [tmp_path / name for name in ("first.wav", "second.wav")]
    for path in (first, second):
        with wave.open(str(path), "wb") as audio:
            audio.setnchannels(2)
            audio.setsampwidth(2)
            audio.setframerate(48000)
            samples = np.zeros((16000, 2), np.int16)
            tone = (10000 * np.sin(2 * np.pi * 440 * np.arange(2400) / 48000)).astype(np.int16)
            samples[-2400:] = tone[:, None]
            audio.writeframes(samples.tobytes())
    sinks = []
    monkeypatch.setattr(main_window, "PcmPlayback", lambda parent, **kwargs: PcmPlayback(
        parent, sink_factory=lambda *args: sinks.append(ClockedSink(*args)) or sinks[-1],
        **kwargs,
    ))
    monkeypatch.setattr(main_window.Player, "_automatic_update_check", lambda self: None)
    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "0")
    save_playback_settings({"play_mode": "playlist", "volume": 0})
    window = main_window.Player()
    for method in ("update_background_for_audio", "_start_lyrics_search", "_notify_queue_changed"):
        monkeypatch.setattr(window, method, lambda *args: None)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    errors = []
    window._pcm_worker.failed.connect(errors.append)
    try:
        window.handle_pitch_change("Semitones", 5)
        window.playlist = [first, second]
        window.current_index = 0
        window.load_audio(first)
        window.media_player.play()
        deadline = time.monotonic() + 5
        while not getattr(window, "_pcm_end_pending", False) and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.002)
        assert window._pcm_end_pending, errors
        assert window.current_audio_path == first
        wait_without_gui(lambda: bool(sinks) and window._pcm_worker._engine._stream_flushed
                         and not window._pcm_worker._engine._pending)
        fmt = sinks[0].fmt
        dtype = {QAudioFormat.SampleFormat.Float: np.float32,
                 QAudioFormat.SampleFormat.Int16: np.int16,
                 QAudioFormat.SampleFormat.Int32: np.int32}[fmt.sampleFormat()]
        samples = np.frombuffer(bytes(sinks[0].written), dtype).astype(np.float64)
        if dtype != np.float32:
            samples /= -np.iinfo(dtype).min
        assert np.max(np.abs(samples)) > 0.01
        if not stop_early:
            from PySide6.QtTest import QTest

            position_updates = []
            monkeypatch.setattr(window, "_update_lyrics_at_position", position_updates.append)
            cue_offset = np.flatnonzero(np.abs(samples) > 0.01)[0]
            sinks[0].played_us = round(cue_offset * 1_000_000 / (fmt.sampleRate() * fmt.channelCount()))
            QTest.qWait(100)
            assert window._pcm_route_active
            assert window.current_audio_path == first
            assert position_updates[-1] == pytest.approx(13600 / 48, abs=35)
        if stop_early is True:
            window.stop_playback()
        elif stop_early == "sleep_pause":
            now = [0.0]
            window.sleep_timer._clock = lambda: now[0]
            window.sleep_timer.start(1)
            now[0] = 60
            window.sleep_timer.check()
        elif stop_early == "sleep_finish":
            window.sleep_timer.finish_current_track()
        sinks[0].played_us = 10_000_000
        if stop_early:
            if stop_early == "sleep_finish":
                deadline = time.monotonic() + 3
                while window._pcm_end_pending and time.monotonic() < deadline:
                    qapp.processEvents()
                    time.sleep(.002)
            from PySide6.QtTest import QTest
            QTest.qWait(50)
            assert window.current_audio_path == first
            assert not window._pcm_end_pending
            assert not window.sleep_timer.active
        else:
            deadline = time.monotonic() + 3
            while window.current_audio_path != second and time.monotonic() < deadline:
                qapp.processEvents()
                time.sleep(.002)
            assert window.current_audio_path == second
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_output_progress_snapshot_uses_the_audio_thread_clock_and_resets(qapp):
    class ClockedSink(Sink):
        played_us = 0

        def processedUSecs(self):
            self.check_thread()
            return self.played_us

    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(ClockedSink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(frames=4800, start_us=4_000_000))
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 4800 * 8)
        wait_without_gui(lambda: worker.track_position_ms() == 4000)
        sinks[0].played_us = 50000
        wait_without_gui(lambda: worker.output_progress()[1] == 50)
        assert worker.track_position_ms() == pytest.approx(4050)
        worker.set_playing(False)
        wait_without_gui(sinks[0].suspended.is_set)
        assert worker.track_position_ms() == pytest.approx(4050)
        worker.set_playing(True)
        wait_without_gui(lambda: not sinks[0].suspended.is_set())
        sinks[0].played_us = 75000
        wait_without_gui(lambda: worker.track_position_ms() == pytest.approx(4075))
        before = worker.output_progress()[0]
        worker.reset()
        after = worker.output_progress()
        assert after[0] > before
        assert after[1] == 0
        assert worker.track_position_ms() is None
        worker.receive_buffer(buffer(frames=4800, start_us=9_000_000))
        wait_without_gui(lambda: worker.track_position_ms() == 9000)
    finally:
        close(worker)


@pytest.mark.parametrize("speed", [1, 1.5])
def test_pitch_clock_matches_played_cue_at_different_speeds(qapp, monkeypatch, speed):
    from core import audio_dsp

    class IdentityPitch:
        latency_frames = 2000

        def process(self, frames):
            return frames

        def close(self):
            pass

    class ClockedSink(Sink):
        played_us = 0

        def processedUSecs(self):
            self.check_thread()
            return self.played_us

    monkeypatch.setattr(audio_dsp, "create_pitch_stream", lambda *args: IdentityPitch())
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(ClockedSink(*args)) or sinks[-1])
    try:
        worker.configure(None, speed, 5, 1, enabled=True, playing=True)
        source = np.zeros((4800, 2), np.float32)
        source[2400:] = 0.25
        worker.receive_buffer(QAudioBuffer(source.tobytes(), audio_format(), 4_000_000))
        worker.receive_buffer(QAudioBuffer())
        wait_without_gui(lambda: sinks and worker._engine._stream_flushed and not worker._engine._pending)
        sink = sinks[0]
        cue = np.flatnonzero(np.frombuffer(sink.written, np.float32)[::2] > 0.1)[0]
        sink.played_us = round(cue * 1_000_000 / 48000)
        wait_without_gui(lambda: worker.output_progress()[1] == sink.played_us / 1000)
        assert worker.track_position_ms() == pytest.approx(4050, abs=0.1)
        sink.played_us = audio_format().durationForBytes(len(sink.written))
        wait_without_gui(lambda: worker.track_position_ms() == pytest.approx(4100))
    finally:
        close(worker)


@pytest.mark.parametrize("linked", [True, False])
def test_live_speed_changes_keep_one_sink_and_follow_the_source_clock(qapp, monkeypatch, linked):
    from core import audio_dsp

    class IdentityPitch:
        latency_frames = 2048

        def __init__(self, shift):
            self.shifts = [shift]

        def set_semitones(self, shift):
            self.shifts.append(shift)

        def process(self, frames):
            return frames

        def close(self):
            pass

    effects, sinks = [], []

    def make_pitch(_channels, _sample_rate, shift):
        effect = IdentityPitch(shift)
        effects.append(effect)
        return effect

    monkeypatch.setattr(audio_dsp, "create_pitch_stream", make_pitch)
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    try:
        previous = 4000
        for index, speed in enumerate((1.25, 0.73, 1.0)):
            shift = 0 if linked else -12 * log2(speed)
            worker.configure(None, speed, shift, 1, enabled=True, playing=True,
                             live_speed=True, pitch_linked=linked)
            current = worker.output_progress()[0]
            if index == 0:
                generation = current
            assert current == generation
            worker.receive_buffer(buffer(frames=4800, start_us=4_000_000 + 100_000 * index))
            wait_without_gui(lambda: (worker.track_position_ms() or 0) > previous + 20)
            previous = worker.track_position_ms()
        worker.receive_buffer(QAudioBuffer())
        wait_without_gui(lambda: worker.track_position_ms() == pytest.approx(4300, abs=0.1))
        assert len(sinks) == 1
        assert not sinks[0].was_reset.is_set()
        assert len(effects) == (0 if linked else 1)
        samples = np.frombuffer(bytes(sinks[0].written), np.float32)
        np.testing.assert_allclose(samples[:-2 * IdentityPitch.latency_frames], 0.25, rtol=0, atol=1e-7)
        if effects:
            assert effects[0].shifts[0] < 0 < max(effects[0].shifts)
            assert effects[0].shifts[-1] == 0
    finally:
        close(worker)


def test_clock_jumps_to_surviving_audio_after_queue_overruns(qapp):
    class GatedSink(Sink):
        writable = False
        played_us = 0

        def bytesFree(self):
            return super().bytesFree() if self.writable else 0

        def processedUSecs(self):
            self.check_thread()
            return self.played_us

    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(GatedSink(*args)) or sinks[-1])
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.receive_buffer(buffer(frames=4800, start_us=2_000_000))
        wait_without_gui(lambda: len(worker._engine._pending) == 4800 * 8)
        worker.receive_buffer(buffer(frames=4800, start_us=2_300_000))
        wait_without_gui(lambda: len(worker._engine._pending) == 5760 * 8)
        sinks[0].writable = True
        wait_without_gui(lambda: worker.track_position_ms() == pytest.approx(2080))
        sinks[0].played_us = 30000
        wait_without_gui(lambda: worker.track_position_ms() == pytest.approx(2310))
    finally:
        close(worker)


def test_renderer_delivery_returns_while_audio_receiver_is_busy(qapp, monkeypatch):
    worker = PcmPlayback(sink_factory=Sink)
    source = QAudioBufferOutput()
    entered, release, emitted = threading.Event(), threading.Event(), threading.Event()
    receiver_threads = []
    receive = worker.receive_buffer

    def blocked(buffer, **kwargs):
        receiver_threads.append(threading.get_ident())
        entered.set()
        assert release.wait(3)
        receive(buffer, **kwargs)

    monkeypatch.setattr(worker, "receive_buffer", blocked)
    producer = threading.Thread(target=lambda: (
        source.audioBufferReceived.emit(buffer()), emitted.set(),
    ))
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.connect_buffer_output(source)
        producer.start()
        assert entered.wait(2)
        # The renderer must return even when Python on the audio thread is
        # blocked. Qt's stop/setSource cannot wait for that Python callback.
        assert emitted.wait(1)
        producer.join(2)
        assert not producer.is_alive()
        assert len(receiver_threads) == 1
        assert receiver_threads[0] not in (threading.get_ident(), producer.ident)
        release.set()
        wait_without_gui(lambda: worker._engine._active)
    finally:
        release.set()
        producer.join(3)
        close(worker)


@pytest.mark.parametrize("change", ["reset", "device", "output"])
def test_queued_decoder_buffers_are_discarded_when_route_changes(qapp, monkeypatch, change):
    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])
    source = QAudioBufferOutput()
    entered, release = threading.Event(), threading.Event()
    process = worker._engine._processor.process

    def blocked(*args):
        entered.set()
        assert release.wait(3)
        return process(*args)

    monkeypatch.setattr(worker._engine._processor, "process", blocked)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.connect_buffer_output(source)
        worker.receive_buffer(buffer())
        assert entered.wait(2)
        # These Qt events are queued behind the blocked DSP call. Disconnecting
        # the old receiver alone does not cancel their eventual delivery.
        source.audioBufferReceived.emit(buffer(0.75))
        source.audioBufferReceived.emit(QAudioBuffer())
        generation = worker._generation
        if change == "reset":
            worker.reset()
        elif change == "device":
            worker.configure("new-device", 1, 0, 1.5, enabled=True, playing=True)
        else:
            source = QAudioBufferOutput()
            worker.connect_buffer_output(source)
        assert worker._generation != generation
        # Reconnecting to the same output must not duplicate audio delivery.
        worker.connect_buffer_output(source)
        source.audioBufferReceived.emit(buffer(0))
        source.audioBufferReceived.emit(QAudioBuffer())
        release.set()
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 32768)
        np.testing.assert_array_equal(np.frombuffer(sinks[0].written, np.float32), 0)
        assert len(sinks) == 1
        qapp.processEvents()
        assert len(worker._buffer_receivers) == 1
    finally:
        release.set()
        close(worker)


@pytest.mark.parametrize("empty", [False, True])
def test_decoder_completion_waits_for_queued_buffers_and_eof(qapp, monkeypatch, empty):
    class ClockedSink(Sink):
        def processedUSecs(self):
            return 1_000_000

    sinks = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(ClockedSink(*args)) or sinks[-1])
    source = QAudioBufferOutput()
    entered, release = threading.Event(), threading.Event()
    receive = worker.receive_buffer
    finished = []
    worker.drained.connect(lambda: finished.append(True))

    def blocked(buffer, **kwargs):
        entered.set()
        assert release.wait(3)
        receive(buffer, **kwargs)

    monkeypatch.setattr(worker, "receive_buffer", blocked)
    try:
        worker.configure(None, 1, 0, 1.5, enabled=True, playing=True)
        worker.connect_buffer_output(source)
        if not empty:
            source.audioBufferReceived.emit(buffer())
        source.audioBufferReceived.emit(QAudioBuffer())
        assert entered.wait(2)
        assert not worker._has_input
        assert worker.finish(decoder_finished=True)
        assert not worker._end_of_stream
        release.set()
        wait_without_gui(lambda: worker._engine._stream_flushed and not worker._drain_requested)
        if empty:
            assert not sinks
        else:
            assert sinks[0].total_written == 32768
            np.testing.assert_array_equal(np.frombuffer(sinks[0].written, np.float32), 0.375)
        qapp.processEvents()
        assert finished == [True]
    finally:
        release.set()
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
    worker.connect_buffer_output(source)
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
        assert sinks[0].buffer_size == audio_format().bytesForDuration(60000)
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


def test_queued_audio_uses_latest_pitch_and_gain_without_restarting(qapp, monkeypatch):
    sinks = []
    entered, release = threading.Event(), threading.Event()
    shifts = []
    worker = PcmPlayback(sink_factory=lambda *args: sinks.append(Sink(*args)) or sinks[-1])

    def blocked(data, sample_format, channels, rate, shift):
        shifts.append(shift)
        if len(shifts) == 1:
            entered.set()
            assert release.wait(3)
        worker._engine._processor._started = True
        return data

    monkeypatch.setattr(worker._engine._processor, "process", blocked)
    try:
        worker.configure(None, 1, 5, 1, enabled=True, playing=True)
        generation = worker._generation
        worker.receive_buffer(buffer(.25, frames=1024))
        assert entered.wait(2)
        for _ in range(2):
            worker.receive_buffer(buffer(.5, frames=1024))
        worker.configure(None, 1, -5, 1.5, enabled=True, playing=True)
        release.set()
        wait_without_gui(lambda: bool(sinks) and sinks[0].total_written == 3072 * 8)
        assert shifts == [5, -5, -5]
        samples = np.frombuffer(sinks[0].written, np.float32).reshape(-1, 2).copy()
        np.testing.assert_array_equal(samples[:1024], .25)
        np.testing.assert_array_equal(samples[1024:], .75)
        assert worker._generation == generation
        assert len(sinks) == 1
        assert not sinks[0].was_reset.is_set()
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
    monkeypatch.setattr(main_window, "PcmPlayback", lambda parent, **kwargs: PcmPlayback(
        parent, sink_factory=lambda *args: sinks.append(DeviceSink(*args)) or sinks[-1],
        **kwargs,
    ))
    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "0")
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
        # Wait for steady device playback before testing a GUI stall. Decoder
        # startup on a busy runner can outlast a fixed 300 ms wait.
        deadline = time.monotonic() + 3
        while (sinks[-1].total_written < sinks[-1].native.format().bytesForDuration(200_000)
               or sinks[-1].writes[-1][1] != "ActiveState") and time.monotonic() < deadline:
            QTest.qWait(10)
        assert sinks[-1].writes[-1][1] == "ActiveState"
        written = sinks[-1].total_written
        before = len(sinks[-1].writes)
        # The actual Qt decoder queues buffers on the audio thread. Neither
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
                monkeypatch.setattr("ui.lrc_editor.LrcEditorDialog._confirm_close", lambda self: True)
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
