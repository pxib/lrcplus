import math

import numpy as np
import pytest

from core.pitch_detector import PitchDetector, PitchEstimator


SAMPLE_RATE = 44100
FRAME_SIZE = PitchDetector.WINDOW_SIZE


def cents_error(estimated_hz, expected_hz):
    return 1200.0 * math.log2(estimated_hz / expected_hz)


def tone(frequency, *, amplitude=0.5, harmonics=None, noise=0.0, phase=0.0):
    time = np.arange(FRAME_SIZE, dtype=np.float64) / SAMPLE_RATE
    signal = amplitude * np.sin(2.0 * np.pi * frequency * time + phase)
    for harmonic, level in (harmonics or {}).items():
        signal += amplitude * level * np.sin(
            2.0 * np.pi * frequency * harmonic * time + phase * harmonic
        )
    if noise:
        signal += np.random.default_rng(7301).normal(0.0, noise, FRAME_SIZE)
    return signal


@pytest.mark.parametrize("frequency", [55.0, 73.42, 82.41, 110.0, 220.0, 440.0, 880.0])
def test_sine_estimates_known_fundamental(frequency):
    estimated, confidence = PitchEstimator(SAMPLE_RATE).estimate(tone(frequency))

    assert estimated > 0
    assert abs(cents_error(estimated, frequency)) < 12
    assert confidence >= 0.70


@pytest.mark.parametrize("fundamental", [82.41, 110.0, 220.0, 440.0])
def test_harmonic_rich_signal_keeps_fundamental(fundamental):
    signal = tone(fundamental, harmonics={2: 1.0, 3: 0.55, 4: 0.3})
    estimated, _confidence = PitchEstimator(SAMPLE_RATE).estimate(signal)

    assert estimated > 0
    assert abs(cents_error(estimated, fundamental)) < 25


def test_voiced_signal_cases_have_no_octave_errors():
    cases = [
        (110.0, tone(110.0)),
        (146.83, tone(146.83, harmonics={2: 1.0, 3: 0.7})),
        (220.0, tone(220.0, noise=0.08)),
        (440.0, tone(440.0, harmonics={2: 1.0, 3: 0.5})),
    ]
    octave_errors = 0
    for expected, signal in cases:
        estimated, _confidence = PitchEstimator(SAMPLE_RATE).estimate(signal)
        error = cents_error(estimated, expected) if estimated > 0 else float("inf")
        octave_errors += int(abs(abs(error) - 1200.0) < 200.0)
        assert estimated > 0
        assert abs(error) < 40

    assert octave_errors / len(cases) == 0.0


def test_noise_and_amplitude_envelope_do_not_create_octave_error():
    time = np.arange(FRAME_SIZE, dtype=np.float64) / SAMPLE_RATE
    envelope = np.linspace(0.15, 1.0, FRAME_SIZE)
    signal = envelope * np.sin(2.0 * np.pi * 146.83 * time)
    signal += np.random.default_rng(91).normal(0.0, 0.035, FRAME_SIZE)
    estimated, _confidence = PitchEstimator(SAMPLE_RATE).estimate(signal)

    assert estimated > 0
    assert abs(cents_error(estimated, 146.83)) < 30


def test_vibrato_tracks_near_the_center_pitch():
    time = np.arange(FRAME_SIZE, dtype=np.float64) / SAMPLE_RATE
    instantaneous_frequency = 220.0 * 2.0 ** (
        (35.0 * np.sin(2.0 * np.pi * 5.0 * time)) / 1200.0
    )
    phase = 2.0 * np.pi * np.cumsum(instantaneous_frequency) / SAMPLE_RATE
    estimated, _confidence = PitchEstimator(SAMPLE_RATE).estimate(0.5 * np.sin(phase))

    assert estimated > 0
    assert abs(cents_error(estimated, 220.0)) < 40


@pytest.mark.parametrize("sample_rate", [8000, 16000, 44100, 48000, 96000])
@pytest.mark.parametrize("frequency", [55.0, 110.0, 440.0, 1300.0])
def test_range_boundaries_and_microphone_sample_rates(sample_rate, frequency):
    time = np.arange(round(sample_rate * 0.08)) / sample_rate
    signal = 0.5 * np.sin(2 * np.pi * frequency * time)
    estimated, confidence = PitchEstimator(sample_rate).estimate(signal)

    assert estimated > 0
    assert abs(cents_error(estimated, frequency)) < 12
    assert confidence > 0.9


@pytest.mark.parametrize("sample_rate", [8000, 16000, 44100, 48000])
@pytest.mark.parametrize("frequency", [55.0, 110.0, 220.0, 440.0, 1300.0])
def test_quiet_fundamental_under_loud_second_harmonic(sample_rate, frequency):
    time = np.arange(round(sample_rate * 0.08)) / sample_rate
    signal = (0.15 * np.sin(2 * np.pi * frequency * time)
              + np.sin(4 * np.pi * frequency * time)
              + 0.15 * np.sin(6 * np.pi * frequency * time))
    estimator = PitchEstimator(sample_rate)
    estimated, confidence = estimator.estimate(signal)

    assert estimated > 0
    assert abs(cents_error(estimated, frequency)) < 25
    assert confidence > 0.9
    # Candidate limits must preserve the chosen fundamental.
    limited = estimator.estimate_candidates(signal, max_candidates=1)
    assert len(limited) == 1
    assert limited[0] == (estimated, confidence)


@pytest.mark.parametrize("frequency", [55.0, 110.0, 220.0, 440.0])
def test_missing_fundamental_from_second_and_third_harmonics(frequency):
    time = np.arange(FRAME_SIZE) / SAMPLE_RATE
    signal = np.sin(4 * np.pi * frequency * time) + 0.6 * np.sin(
        6 * np.pi * frequency * time
    )
    estimated, _ = PitchEstimator(SAMPLE_RATE).estimate(signal)
    assert estimated > 0
    assert abs(cents_error(estimated, frequency)) < 15


@pytest.mark.parametrize("signal", [
    [], np.zeros(FRAME_SIZE), np.ones(FRAME_SIZE), [0.1] * 7,
    [0.0, float("nan")] * 1000, [float("inf")] * FRAME_SIZE,
    np.ones((FRAME_SIZE, 2)), np.zeros((2, 2, 2)),
])
def test_invalid_or_unvoiced_frames_return_no_pitch(signal):
    assert PitchEstimator(SAMPLE_RATE).estimate(signal) == (0.0, 0.0)


def test_white_noise_and_single_impulse_are_unvoiced():
    estimator = PitchEstimator(SAMPLE_RATE)
    rng = np.random.default_rng(921)
    for _ in range(20):
        assert estimator.estimate(rng.normal(0, 0.2, FRAME_SIZE)) == (0.0, 0.0)
    impulse = np.zeros(FRAME_SIZE)
    impulse[FRAME_SIZE // 2] = 1
    assert estimator.estimate(impulse) == (0.0, 0.0)


def test_dc_offset_input_ownership_and_amplitude_gate():
    signal = tone(220.0) + 4.0
    original = signal.copy()
    estimated, _ = PitchEstimator(SAMPLE_RATE).estimate(signal)
    assert abs(cents_error(estimated, 220.0)) < 12
    np.testing.assert_array_equal(signal, original)
    assert PitchEstimator(SAMPLE_RATE).estimate(tone(220, amplitude=0.0001)) == (0, 0)
    estimated, _ = PitchEstimator(SAMPLE_RATE).estimate(tone(220, amplitude=1e300))
    assert abs(cents_error(estimated, 220.0)) < 12


@pytest.mark.parametrize("kwargs", [
    {"sample_rate": 0}, {"sample_rate": float("inf")},
    {"sample_rate": float("nan")}, {"min_frequency": 0},
    {"max_frequency": SAMPLE_RATE / 2}, {"min_frequency": float("nan")},
    {"rms_threshold": -1}, {"rms_threshold": float("nan")},
    {"trough_threshold": 0.5}, {"max_cmnd": 1.5},
])
def test_invalid_configuration_fails_early(kwargs):
    with pytest.raises(ValueError):
        PitchEstimator(**({"sample_rate": SAMPLE_RATE} | kwargs))


@pytest.mark.parametrize("limit", [0, -1])
def test_candidate_limit_must_be_positive(limit):
    with pytest.raises(ValueError, match="max_candidates"):
        PitchEstimator(SAMPLE_RATE).estimate_candidates(tone(220), limit)

