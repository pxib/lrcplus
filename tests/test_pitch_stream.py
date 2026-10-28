import numpy as np
import pytest

from core import pitch_stream
from core.pitch_stream import BLOCK_FRAMES, RubberBandPitchStream


@pytest.mark.parametrize("sample_rate", [44100, 48000, 96000])
@pytest.mark.parametrize("semitones", [-36, -24, -5, -0.1, 0.1, 5, 24, 48])
def test_native_pitch_output_fits_its_latency_reserve(sample_rate, semitones):
    effect = RubberBandPitchStream(2, sample_rate, semitones)
    try:
        assert effect.latency_frames < sample_rate * 0.2
        received = 0
        for start in range(0, sample_rate * 2, BLOCK_FRAMES):
            tone = (0.1 * np.sin(
                2 * np.pi * 440 * np.arange(start, start + BLOCK_FRAMES) / sample_rate
            )).astype(np.float32)
            received += len(effect.process(np.column_stack((tone, tone))))
            supplied = start + BLOCK_FRAMES
            # Include the worst-case remainder buffered by the fixed-block
            # adapter when a decoder callback ends between internal blocks.
            assert supplied - received + BLOCK_FRAMES - 1 <= effect.latency_frames
    finally:
        effect.close()


def test_missing_native_library_uses_continuous_fallback(monkeypatch):
    def unavailable():
        raise OSError("Library unavailable in this installation")

    monkeypatch.setattr(pitch_stream, "_load_rubberband", unavailable)
    effect = pitch_stream.create_pitch_stream(2, 48000, 5)
    assert isinstance(effect, pitch_stream.PedalboardPitchStream)
    received = 0
    for _ in range(150):
        received += len(effect.process(np.zeros((BLOCK_FRAMES, 2), np.float32)))
    assert received > 0
    assert 150 * BLOCK_FRAMES - received <= effect.latency_frames
    effect.close()
