import numpy as np
import pytest

from core import pitch_stream
from core.pitch_stream import BLOCK_FRAMES, RubberBandPitchStream


@pytest.mark.parametrize("sample_rate", [44100, 48000, 96000])
@pytest.mark.parametrize("semitones", [-36, -24, -5, -0.1, 0.1, 5, 24, 48])
def test_native_pitch_output_fits_its_latency_reserve(sample_rate, semitones):
    effect = RubberBandPitchStream(2, sample_rate, semitones)
    try:
        # The live-consistency resampler needs extra context below two octaves.
        bound = 0.32 if semitones < -24 else 0.2
        assert effect.latency_frames < sample_rate * bound
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
    from pedalboard import PitchShift
    if not pitch_stream._pedalboard_streaming_supported(PitchShift):
        with pytest.raises(OSError, match="cannot stream"):
            pitch_stream.create_pitch_stream(2, 48000, 5)
        return
    effect = pitch_stream.create_pitch_stream(2, 48000, 5)
    assert isinstance(effect, pitch_stream.PedalboardPitchStream)
    received = 0
    for _ in range(150):
        received += len(effect.process(np.zeros((BLOCK_FRAMES, 2), np.float32)))
    assert received > 0
    assert 150 * BLOCK_FRAMES - received <= effect.latency_frames
    effect.close()


def test_silent_fallback_build_is_rejected(monkeypatch):
    import pedalboard

    class SilentPitch:
        def __init__(self, **kwargs):
            pass

        def __call__(self, samples, *args, **kwargs):
            return np.zeros_like(samples)

    monkeypatch.setattr(pedalboard, "PitchShift", SilentPitch)
    with pytest.raises(OSError, match="cannot stream"):
        pitch_stream.PedalboardPitchStream(2, 48000, 5)


def test_functioning_fallback_is_still_available(monkeypatch):
    import pedalboard

    class StreamingPitch:
        def __init__(self, semitones):
            self.semitones = semitones

        def __call__(self, samples, *args, **kwargs):
            return samples.copy()

    monkeypatch.setattr(pedalboard, "PitchShift", StreamingPitch)
    effect = pitch_stream.PedalboardPitchStream(2, 48000, 5)
    source = np.full((512, 2), .25, np.float32)
    try:
        assert np.array_equal(effect.process(source), source)
        effect.set_semitones(-5)
        assert np.array_equal(effect.process(source), source)
    finally:
        effect.close()
