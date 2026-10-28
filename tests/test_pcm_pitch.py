from types import SimpleNamespace

import numpy as np
import pytest
from PySide6.QtMultimedia import QAudioFormat, QMediaPlayer

from core import audio_controller, audio_dsp
from core.audio_controller import AudioController
from core.audio_dsp import apply_pcm_pitch_to_bytes, reset_pcm_pitch_state


@pytest.fixture(autouse=True)
def clean_pitch_state():
    reset_pcm_pitch_state()
    yield
    reset_pcm_pitch_state()


@pytest.mark.parametrize("sample_rate", [44100, 48000])
@pytest.mark.parametrize("dtype,sample_format", [
    (np.float32, QAudioFormat.SampleFormat.Float),
    (np.int16, QAudioFormat.SampleFormat.Int16),
    (np.int32, QAudioFormat.SampleFormat.Int32),
])
def test_pitch_starts_within_200_ms_and_shifts_tone(sample_rate, dtype, sample_format):
    frames = np.arange(sample_rate * 2)
    tone = 0.25 * np.sin(2 * np.pi * 440 * frames / sample_rate)
    scale = 1.0 if dtype == np.float32 else float(-np.iinfo(dtype).min)
    source = (np.column_stack((tone, tone)) * scale).astype(dtype)
    output = []
    for start in range(0, len(source), 1024):
        block = source[start:start + 1024].tobytes()
        shifted = apply_pcm_pitch_to_bytes(block, sample_format, 2, sample_rate, 5)
        assert len(shifted) == len(block)
        output.append(np.frombuffer(shifted, dtype=dtype).reshape(-1, 2))
    result = np.concatenate(output)[:, 0].astype(np.float64) / scale
    assert np.flatnonzero(np.abs(result) > 0.01)[0] < sample_rate * 0.2
    # Check audible pitch after startup, rather than only inspecting buffers.
    steady = result[sample_rate // 2:]
    spectrum = abs(np.fft.rfft(steady * np.hanning(len(steady))))
    frequency = np.argmax(spectrum) * sample_rate / len(steady)
    expected = 440 * 2 ** (5 / 12)
    assert frequency == pytest.approx(expected, rel=0.002)
    # Repeated short-block resets can pass a coarse frequency check yet spread
    # most energy into sidebands. A continuous tone should stay spectrally pure.
    frequencies = np.fft.rfftfreq(len(steady), 1 / sample_rate)
    power = spectrum ** 2
    assert power[np.abs(frequencies - expected) < 3].sum() / power.sum() > 0.98
    assert 0.05 < np.sqrt(np.mean(steady ** 2)) < 0.4


def test_irregular_callbacks_do_not_add_silence_or_grow_pitch_queue(monkeypatch):
    class IdentityPitch:
        latency_frames = 8000

        def process(self, block):
            return block

        def close(self):
            pass

    monkeypatch.setattr(audio_dsp, "create_pitch_stream", lambda *args: IdentityPitch())
    sizes = [127, 1470, 4096, 63, 1024, 18000] * 8
    output = []
    for size in sizes:
        shifted = apply_pcm_pitch_to_bytes(
            np.full((size, 2), 0.25, dtype=np.float32).tobytes(),
            QAudioFormat.SampleFormat.Float, 2, 48000, 5,
        )
        output.append(np.frombuffer(shifted, dtype=np.float32).reshape(-1, 2))
        assert len(apply_pcm_pitch_to_bytes._pending) <= 8000
    result = np.concatenate(output)
    assert len(result) == sum(sizes)
    assert np.all(result[:8000] == 0)
    assert np.all(result[8000:] == 0.25)


@pytest.mark.parametrize("reset_mode", ["seek", "bypass", "pitch", "format"])
def test_pitch_discontinuities_cannot_replay_old_audio(reset_mode):
    sample_format = QAudioFormat.SampleFormat.Float
    data = np.full((12000, 2), 0.25, dtype=np.float32).tobytes()
    apply_pcm_pitch_to_bytes(data, sample_format, 2, 48000, 5)
    shift = 5
    dtype = np.float32
    if reset_mode == "seek":
        reset_pcm_pitch_state()
    elif reset_mode == "bypass":
        assert apply_pcm_pitch_to_bytes(data, sample_format, 2, 48000, 0) == data
    elif reset_mode == "pitch":
        shift = -5
    else:
        sample_format = QAudioFormat.SampleFormat.Int16
        dtype = np.int16
    silence = np.zeros((24000, 2), dtype=dtype).tobytes()
    result = apply_pcm_pitch_to_bytes(silence, sample_format, 2, 48000, shift)
    assert not np.any(np.frombuffer(result, dtype=dtype))


class Timer:
    active = False

    def start(self):
        self.active = True

    def stop(self):
        self.active = False

    def isActive(self):
        return self.active


class Sink:
    def __init__(self, device, audio_format, parent):
        self.audio_format = audio_format
        self.free = 0
        self.written = bytearray()
        self.suspended = False
        self.deleted = False

    def format(self):
        return self.audio_format

    def setVolume(self, volume):
        self.volume = volume

    def setBufferSize(self, size):
        self.buffer_size = size

    def bytesFree(self):
        return self.free

    def start(self):
        return self

    def write(self, data):
        self.written.extend(data)
        return len(data)

    def reset(self):
        self.written.clear()

    def stop(self):
        pass

    def deleteLater(self):
        self.deleted = True

    def suspend(self):
        self.suspended = True

    def resume(self):
        self.suspended = False


@pytest.fixture
def playback(monkeypatch):
    monkeypatch.setattr(audio_controller, "QAudioSink", Sink)
    monkeypatch.setattr(audio_controller, "save_playback_settings", lambda settings: None)

    class Playback(AudioController):
        def __init__(self):
            self.volume = 100
            self.volume_slider = SimpleNamespace(value=lambda: self.volume)
            self.audio_output = SimpleNamespace(
                setVolume=lambda value: setattr(self, "native_volume", value),
                device=lambda: None,
            )
            self.native_volume = 1.0
            self.state = QMediaPlayer.PlaybackState.PlayingState
            self.playback_speed = 1.0
            self.pitch_semitones = 5
            self.pitch_linked_to_speed = False
            self.playback_settings = {}
            self.pitch_status_label = SimpleNamespace(setText=lambda text: None)
            self._pcm_sink = None
            self._pcm_sink_io = None
            self._pcm_pending = bytearray()
            self._pcm_drain_timer = Timer()
            self._current_pcm_gain = 1.0
            self._pcm_buffer_output = object()
            self.media_player = SimpleNamespace(
                playbackState=lambda: self.state,
                audioBufferOutput=lambda: self._pcm_buffer_output,
                setPlaybackRate=lambda rate: None,
            )
            self.audio_format = QAudioFormat()
            self.audio_format.setSampleRate(48000)
            self.audio_format.setChannelCount(2)
            self.audio_format.setSampleFormat(QAudioFormat.SampleFormat.Float)

        def write(self, value=0.25, frames=1024):
            data = np.full((frames, 2), value, dtype=np.float32).tobytes()
            self._write_pcm_buffer(SimpleNamespace(
                isValid=lambda: True, data=lambda: data, format=lambda: self.audio_format,
            ))

    return Playback()


def test_native_audio_stays_audible_until_processed_audio_is_written(playback):
    playback.write()
    assert playback._pcm_sink is None
    assert playback.native_volume == 1.0
    for _ in range(10):
        playback.write()
    assert playback._pcm_sink is not None
    assert playback.native_volume == 1.0  # Sink has no free space yet.
    playback._pcm_sink.free = 100000
    playback._drain_pcm_pending()
    assert playback._pcm_sink.written
    assert playback.native_volume == 0.0
    playback.volume = 50
    playback._configure_pcm_volume(50)
    assert playback._pcm_sink.volume == 0.5
    assert playback.native_volume == 0.0


def test_stalled_sink_keeps_recent_audio_with_bounded_delay(playback):
    playback.pitch_semitones = 0
    playback.volume = 150
    playback._current_pcm_gain = 1.5
    for index in range(30):
        playback.write(value=index / 100)
    assert len(playback._pcm_pending) <= playback.audio_format.bytesForDuration(120000)
    assert len(playback._pcm_pending) % playback.audio_format.bytesPerFrame() == 0
    assert playback._pcm_sink.buffer_size == playback.audio_format.bytesForDuration(60000)
    pending = np.frombuffer(playback._pcm_pending, dtype=np.float32)
    assert pending[-1] == pytest.approx(0.29 * 1.5)
    assert pending[0] > 0.2  # Old queued audio was discarded.


@pytest.mark.parametrize("change", ["pitch", "speed", "link"])
def test_control_changes_flush_stale_audio_and_keep_decoder_connected(playback, change):
    playback.volume = 150
    playback._current_pcm_gain = 1.5
    for _ in range(10):
        playback.write()
    sink = playback._pcm_sink
    output = playback._pcm_buffer_output
    assert playback._pcm_pending
    if change == "pitch":
        playback.handle_pitch_change("Semitones", -5)
    elif change == "speed":
        playback.handle_speed_change(125)
    else:
        playback.handle_pitch_link_change(True)
    assert playback._pcm_buffer_output is output
    assert sink.deleted
    assert not playback._pcm_pending
    assert not playback._pcm_drain_timer.isActive()
    assert not apply_pcm_pitch_to_bytes._started
    assert playback.native_volume == 1.0


def test_pause_preserves_audio_and_resume_restarts_drain(playback):
    for _ in range(10):
        playback.write()
    playback.state = QMediaPlayer.PlaybackState.PausedState
    playback._sync_pcm_sink_state(playback.state)
    pending = bytes(playback._pcm_pending)
    assert not playback._pcm_drain_timer.isActive()
    assert playback._pcm_sink.suspended
    playback._pcm_sink.free = 100000
    playback.state = QMediaPlayer.PlaybackState.PlayingState
    playback._sync_pcm_sink_state(playback.state)
    assert not playback._pcm_sink.suspended
    assert playback._pcm_sink.written == pending
    assert not playback._pcm_pending


def test_stop_resets_processor_even_during_initial_warmup(playback):
    playback.write()
    assert playback._pcm_sink is None
    assert apply_pcm_pitch_to_bytes._state is not None
    assert not apply_pcm_pitch_to_bytes._started
    playback._sync_pcm_sink_state(QMediaPlayer.PlaybackState.StoppedState)
    assert apply_pcm_pitch_to_bytes._state is None
    assert not len(apply_pcm_pitch_to_bytes._input)


@pytest.mark.parametrize("dtype,sample_format", [
    (np.int16, QAudioFormat.SampleFormat.Int16),
    (np.int32, QAudioFormat.SampleFormat.Int32),
])
@pytest.mark.parametrize("sign", [-1, 1])
def test_pitch_saturates_integer_pcm_without_overflow(monkeypatch, dtype, sample_format, sign):
    class LoudPitch:
        latency_frames = 8000

        def process(self, block):
            return np.full_like(block, sign * 1.2)

        def close(self):
            pass

    monkeypatch.setattr(audio_dsp, "create_pitch_stream", lambda *args: LoudPitch())
    result = apply_pcm_pitch_to_bytes(
        np.zeros((16000, 2), dtype=dtype).tobytes(), sample_format, 2, 48000, 5,
    )
    samples = np.frombuffer(result, dtype=dtype).reshape(-1, 2)[8000:]
    limit = np.iinfo(dtype).max if sign > 0 else np.iinfo(dtype).min
    assert np.all(samples == limit)


@pytest.mark.parametrize("change", ["pitch", "speed", "link"])
def test_unchanged_controls_do_not_restart_pitch_warmup(playback, change):
    for _ in range(10):
        playback.write()
    sink = playback._pcm_sink
    pending = bytes(playback._pcm_pending)
    if change == "pitch":
        playback.handle_pitch_change("Semitones", 5)
    elif change == "speed":
        playback.handle_speed_change(100)
    else:
        playback.handle_pitch_link_change(False)
    assert playback._pcm_sink is sink
    assert not sink.deleted
    assert playback._pcm_pending == pending
    assert apply_pcm_pitch_to_bytes._started


def test_pitch_is_independent_of_decoder_callback_boundaries():
    rng = np.random.default_rng(42)
    source = rng.normal(0, 0.1, (96000, 2)).astype(np.float32)

    def render(sizes):
        reset_pcm_pitch_state()
        result = []
        position = 0
        index = 0
        while position < len(source):
            size = sizes[index % len(sizes)]
            data = source[position:position + size].tobytes()
            result.append(apply_pcm_pitch_to_bytes(
                data, QAudioFormat.SampleFormat.Float, 2, 48000, 5,
            ))
            position += size
            index += 1
        return np.frombuffer(b"".join(result), np.float32)

    regular = render([1024])
    irregular = render([1, 127, 2011, 63, 8192, 511])
    np.testing.assert_allclose(regular, irregular, rtol=0, atol=1e-6)


def test_pitch_preserves_stereo_relationship_and_stable_level():
    rate = 48000
    t = np.arange(rate * 3) / rate
    tone = (0.25 * np.sin(2 * np.pi * 440 * t)).astype(np.float32)
    source = np.column_stack((tone, -tone))
    result = []
    for start in range(0, len(source), 1024):
        result.append(np.frombuffer(apply_pcm_pitch_to_bytes(
            source[start:start + 1024].tobytes(),
            QAudioFormat.SampleFormat.Float, 2, rate, 5,
        ), np.float32).reshape(-1, 2))
    steady = np.concatenate(result)[rate:]
    np.testing.assert_allclose(steady[:, 0], -steady[:, 1], rtol=0, atol=1e-5)
    windows = steady[:len(steady) // 1024 * 1024, 0].reshape(-1, 1024)
    levels = np.sqrt(np.mean(windows ** 2, axis=1))
    assert np.std(levels) / np.mean(levels) < 0.02


def test_reset_closes_the_native_processor():
    data = np.zeros((4096, 2), np.float32).tobytes()
    apply_pcm_pitch_to_bytes(data, QAudioFormat.SampleFormat.Float, 2, 48000, 5)
    effect = apply_pcm_pitch_to_bytes._effect
    reset_pcm_pitch_state()
    assert effect._handle is None
    effect.close()  # Repeated cleanup is safe.


@pytest.mark.parametrize("sample_rate", [44100, 48000])
def test_plus_five_pitch_starts_within_110_ms(sample_rate):
    frames = np.arange(sample_rate // 4)
    tone = (0.25 * np.sin(2 * np.pi * 440 * frames / sample_rate)).astype(np.float32)
    result = []
    for start in range(0, len(tone), 1024):
        source = np.column_stack((tone[start:start + 1024], tone[start:start + 1024]))
        result.append(np.frombuffer(apply_pcm_pitch_to_bytes(
            source.tobytes(), QAudioFormat.SampleFormat.Float, 2, sample_rate, 5,
        ), np.float32).reshape(-1, 2))
    samples = np.concatenate(result)[:, 0]
    first = np.flatnonzero(np.abs(samples) > 0.01)[0]
    assert first < sample_rate * 0.11


def test_smaller_startup_reserve_preserves_identical_processed_audio(monkeypatch):
    rate = 48000
    rng = np.random.default_rng(18)
    source = rng.normal(0, 0.1, (rate * 2, 2)).astype(np.float32)
    source[::4096] += 0.5
    create_stream = audio_dsp.create_pitch_stream

    def render(extra_reserve):
        reset_pcm_pitch_state()

        def make_stream(*args):
            stream = create_stream(*args)
            stream.latency_frames += extra_reserve
            return stream

        monkeypatch.setattr(audio_dsp, "create_pitch_stream", make_stream)
        blocks = []
        for start in range(0, len(source), 777):
            blocks.append(apply_pcm_pitch_to_bytes(
                source[start:start + 777].tobytes(),
                QAudioFormat.SampleFormat.Float, 2, rate, 5,
            ))
        latency = apply_pcm_pitch_to_bytes._effect.latency_frames
        return np.frombuffer(b"".join(blocks), np.float32).reshape(-1, 2)[latency:]

    shorter = render(0)
    previous = render(2048)  # The context previously counted twice at +5 st.
    np.testing.assert_array_equal(shorter[:len(previous)], previous)
