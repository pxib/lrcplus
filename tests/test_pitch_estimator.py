import math
from collections import deque

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


def test_transition_response_is_bounded_by_analysis_window_and_filter():
    estimator = PitchEstimator(SAMPLE_RATE)
    hop = PitchDetector.HOP_SIZE
    window = np.zeros(FRAME_SIZE, dtype=np.float64)
    recent_estimates = deque(maxlen=3)
    phase = 0.0
    transition_sample = SAMPLE_RATE // 2
    first_target_sample = None

    for start in range(0, SAMPLE_RATE, hop):
        positions = np.arange(hop) + start
        frequency = np.where(positions < transition_sample, 440.0, 220.0)
        phase_steps = 2.0 * np.pi * frequency / SAMPLE_RATE
        phases = phase + np.cumsum(phase_steps)
        block = 0.5 * np.sin(phases)
        phase = float(phases[-1])
        window[:-hop] = window[hop:]
        window[-hop:] = block
        estimated, confidence = estimator.estimate(window)
        if confidence >= 0.70 and estimated > 0:
            recent_estimates.append(math.log(estimated))
            filtered_estimate = math.exp(float(np.median(recent_estimates)))
        else:
            recent_estimates.clear()
            filtered_estimate = 0.0
        if (start >= transition_sample and filtered_estimate > 0
                and abs(cents_error(filtered_estimate, 220.0)) < 40):
            first_target_sample = start + hop
            break

    assert first_target_sample is not None
    # A 92.9 ms window contains substantial pre-transition audio; this bound
    # catches accidental additional smoothing or very slow transition settling.
    assert (first_target_sample - transition_sample) / SAMPLE_RATE <= 0.09

