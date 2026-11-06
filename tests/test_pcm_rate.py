import math

import numpy as np
import pytest
from PySide6.QtMultimedia import QAudioFormat

from core.audio_dsp import PcmRateProcessor, apply_pcm_rate_to_bytes


FLOAT = QAudioFormat.SampleFormat.Float


def render(source, rate, sizes, sample_format=FLOAT):
    processor = PcmRateProcessor()
    output = []
    position = 0
    index = 0
    while position < len(source):
        size = sizes[index % len(sizes)]
        output.append(processor.process(
            source[position:position + size].tobytes(), sample_format,
            source.shape[1], rate, sample_rate=48000,
        ))
        position += size
        index += 1
    output.append(processor.flush())
    assert processor.flush() == b""
    return np.frombuffer(b"".join(output), source.dtype).reshape(-1, source.shape[1])


@pytest.mark.parametrize("rate", [0.25, 0.73, 1.25, 1.333333333, 2.0])
@pytest.mark.parametrize("dtype,sample_format", [
    (np.float32, FLOAT),
    (np.int16, QAudioFormat.SampleFormat.Int16),
    (np.int32, QAudioFormat.SampleFormat.Int32),
])
def test_rate_is_independent_of_callback_boundaries_and_preserves_duration(rate, dtype, sample_format):
    rng = np.random.default_rng(42)
    scale = 1 if dtype == np.float32 else np.iinfo(dtype).max
    source = (rng.normal(0, 0.1, (10003, 2)) * scale).astype(dtype)
    regular = render(source, rate, [1024], sample_format)
    irregular = render(source, rate, [1, 127, 2011, 63, 8192, 511], sample_format)
    np.testing.assert_array_equal(regular, irregular)
    assert len(regular) == math.ceil(len(source) / rate)
    complete = apply_pcm_rate_to_bytes(source.tobytes(), sample_format, 2, rate)
    assert regular.tobytes() == complete


@pytest.mark.parametrize("rate,frequency", [(1.25, 0.45), (1.5, 0.4), (2, 0.35)])
def test_downsampling_rejects_tones_above_output_nyquist(rate, frequency):
    tone = (0.25 * np.sin(2 * np.pi * frequency * np.arange(24000))).astype(np.float32)
    result = render(tone[:, None], rate, [127, 1024])[:, 0][200:-200]
    rejection_db = 20 * np.log10(np.sqrt(np.mean(result ** 2)) / (0.25 / np.sqrt(2)))
    assert rejection_db < -75


@pytest.mark.parametrize("rate", [0.25, 0.73, 1.25, 2.0])
def test_rate_preserves_passband_and_stereo_relationship(rate):
    tone = (0.25 * np.sin(2 * np.pi * 0.1 * np.arange(24000))).astype(np.float32)
    result = render(np.column_stack((tone, -tone)), rate, [1024])
    np.testing.assert_allclose(result[:, 0], -result[:, 1], rtol=0, atol=1e-7)
    positions = np.arange(200, len(result) - 200) * rate
    expected = 0.25 * np.sin(2 * np.pi * 0.1 * positions)
    np.testing.assert_allclose(result[200:-200, 0], expected, rtol=0, atol=1e-5)


@pytest.mark.parametrize("frames", [0, 1, 7, 34])
@pytest.mark.parametrize("rate", [0.25, 1.25, 2])
def test_short_stream_flushes_without_losing_frames(frames, rate):
    source = np.full((frames, 2), 0.25, np.float32)
    result = render(source, rate, [1])
    assert len(result) == math.ceil(frames / rate)
    np.testing.assert_allclose(result, 0.25, atol=1e-7)


@pytest.mark.parametrize("change", ["reset", "rate", "channels", "sample_rate", "format", "bypass"])
def test_discontinuities_discard_old_filter_history(change):
    processor = PcmRateProcessor()
    processor.process(np.full((1024, 2), 0.5, np.float32).tobytes(), FLOAT, 2, 1.25,
                      sample_rate=48000)
    channels, rate, sample_rate, sample_format, dtype = 2, 1.25, 48000, FLOAT, np.float32
    if change == "reset":
        processor.reset()
    elif change == "rate":
        rate = 0.73
    elif change == "channels":
        channels = 1
    elif change == "sample_rate":
        sample_rate = 44100
    elif change == "format":
        sample_format, dtype = QAudioFormat.SampleFormat.Int16, np.int16
    else:
        processor.process(bytes(1024 * 8), FLOAT, 2, 1)
    output = processor.process(np.zeros((1024, channels), dtype).tobytes(), sample_format,
                               channels, rate, sample_rate=sample_rate)
    output += processor.flush()
    assert not np.any(np.frombuffer(output, dtype))


def test_stream_history_stays_bounded():
    processor = PcmRateProcessor()
    for _ in range(100):
        processor.process(bytes(4096 * 8), FLOAT, 2, 1.37)
        assert len(processor._frames) <= 2 * processor._radius + 2


@pytest.mark.parametrize("rate", [1, 1.0001, 0, -1, float("nan"), float("inf")])
def test_unity_and_invalid_rates_preserve_bytes(rate):
    source = np.linspace(-1, 1, 100, dtype=np.float32).tobytes()
    assert apply_pcm_rate_to_bytes(source, FLOAT, 2, rate) == source


@pytest.mark.parametrize("dtype,sample_format", [
    (np.int16, QAudioFormat.SampleFormat.Int16),
    (np.int32, QAudioFormat.SampleFormat.Int32),
])
def test_filter_overshoot_saturates_integer_pcm(dtype, sample_format):
    limits = np.iinfo(dtype)
    source = np.repeat([limits.min, limits.max], 256).astype(dtype)[:, None]
    result = render(source, 0.73, [127], sample_format)
    assert result.min() == limits.min
    assert result.max() == limits.max

