"""Exercise the actual streaming path, including capture without hardware."""

import math
import sys
from types import SimpleNamespace

import numpy as np
import pytest

from core.pitch_detector import PitchDetector, PitchTracker


def cents(estimated, expected):
    return 1200 * math.log2(estimated / expected) if estimated > 0 else math.inf


def voice(sample_rate, frequencies):
    return 0.5 * np.sin(2 * np.pi * np.cumsum(frequencies) / sample_rate)


def track(tracker, samples, hop):
    return [(start + len(samples[start:start + hop]),
             *tracker.process(samples[start:start + hop]))
            for start in range(0, len(samples), hop)]


@pytest.mark.parametrize("sample_rate", [16000, 44100, 48000, 96000])
@pytest.mark.parametrize("frequency", [55.0, 110.0, 440.0, 1300.0])
def test_onset_is_prompt_and_never_uses_zero_padded_audio(sample_rate, frequency):
    tracker = PitchTracker(sample_rate)
    hop = round(256 * sample_rate / 44100)
    samples = voice(sample_rate, np.full(round(sample_rate * 0.2), frequency))
    results = track(tracker, samples, hop)
    assert all(hz == 0 for end, hz, _ in results if end < sample_rate * 0.04)
    voiced = [(end, hz) for end, hz, _ in results if hz > 0]
    assert voiced
    assert voiced[0][0] / sample_rate <= 0.080
    assert abs(cents(voiced[-1][1], frequency)) < 15


@pytest.mark.parametrize("sample_rate", [16000, 44100, 48000, 96000])
@pytest.mark.parametrize("before,after", [
    (440, 220), (220, 440), (220, 233.08), (440, 55), (55, 440),
])
def test_real_note_changes_settle_within_80_ms(sample_rate, before, after):
    transition = round(sample_rate * 0.3)
    frequencies = np.concatenate((np.full(transition, before),
                                  np.full(transition, after)))
    tracker = PitchTracker(sample_rate)
    results = track(tracker, voice(sample_rate, frequencies),
                    round(256 * sample_rate / 44100))
    preceding = [hz for end, hz, _ in results
                 if transition - round(sample_rate * 0.02) <= end <= transition]
    assert preceding
    assert all(abs(cents(hz, before)) < 15 for hz in preceding)
    # Require every later estimate to be correct, rather than counting one
    # accidental crossing as a successful transition.
    settled = [(hz, score) for end, hz, score in results
               if end >= transition + round(sample_rate * 0.080)]
    assert settled
    assert all(abs(cents(hz, after)) < 30 and score >= 0.70 for hz, score in settled)


@pytest.mark.parametrize("sample_rate", [16000, 44100, 48000, 96000])
def test_silence_releases_within_20_ms_and_new_note_starts_fresh(sample_rate):
    tracker = PitchTracker(sample_rate)
    hop = round(256 * sample_rate / 44100)
    first = voice(sample_rate, np.full(round(sample_rate * 0.2), 220))
    assert abs(cents(track(tracker, first, hop)[-1][1], 220)) < 15
    silence = track(tracker, np.zeros(round(sample_rate * 0.1)), hop)
    assert all(hz == 0 for end, hz, _ in silence if end >= sample_rate * 0.020)
    second = voice(sample_rate, np.full(round(sample_rate * 0.2), 440))
    results = track(tracker, second, hop)
    assert all(hz == 0 for end, hz, _ in results if end < sample_rate * 0.04)
    assert all(abs(cents(hz, 440)) < 15 for _, hz, _ in results if hz)


def test_irregular_chunks_and_invalid_input_recover_without_old_note():
    tracker = PitchTracker(44100)
    samples = voice(44100, np.full(22050, 220))
    offset = 0
    sizes = [0, 37, 509, 117, 200, 4096]
    result = (0, 0)
    while offset < len(samples):
        for count in sizes:
            result = tracker.process(samples[offset:offset + count])
            offset += count
            if offset >= len(samples):
                break
    assert abs(cents(result[0], 220)) < 15
    assert tracker.process([np.nan] * 256) == (0, 0)
    assert tracker.process(np.zeros((256, 2))) == (0, 0)
    assert tracker.process(samples[:256]) == (0, 0)
    assert abs(cents(tracker.process(samples)[0], 220)) < 15


def test_vibrato_remains_visible_and_tracks_delayed_voice():
    sample_rate = 44100
    time = np.arange(sample_rate) / sample_rate
    frequencies = 220 * 2 ** (35 * np.sin(2 * np.pi * 5 * time) / 1200)
    results = track(PitchTracker(sample_rate), voice(sample_rate, frequencies), 256)
    voiced = [(end, hz) for end, hz, _ in results if end > sample_rate * 0.2]
    # Compare with the center of the short window plus the smoothing delay.
    errors = [abs(cents(hz, frequencies[end - 1 - round(sample_rate * 0.029)]))
              for end, hz in voiced]
    assert max(errors) < 15
    deviations = [cents(hz, 220) for _, hz in voiced]
    assert max(deviations) > 25
    assert min(deviations) < -25


def test_white_noise_does_not_become_a_stream_of_notes():
    samples = np.random.default_rng(921).normal(0, 0.2, 44100)
    assert all(hz == 0 for _, hz, _ in track(PitchTracker(44100), samples, 256))


def test_single_sample_blocks_accumulate_a_valid_analysis_window():
    sample_rate = 16000
    samples = voice(sample_rate, np.full(1600, 220))
    results = track(PitchTracker(sample_rate), samples, 1)
    assert results[round(sample_rate * 0.035)][1] == 0
    assert abs(cents(results[-1][1], 220)) < 15


def test_brief_octave_burst_does_not_jump_the_trail():
    hop = 256
    frequencies = np.full(hop * 80, 220.0)
    frequencies[hop * 40:hop * 41] = 440.0
    results = track(PitchTracker(44100), voice(44100, frequencies), hop)
    assert all(abs(cents(hz, 220)) < 30 for _, hz, _ in results[38:])


def fake_microphone(monkeypatch, detector, blocks, *, fallback_rate=None,
                    open_error=None, stop_during_read=False):
    state = SimpleNamespace(checked=[], opened=[], closed=False, reads=0)
    blocks = iter(blocks)

    def check_input_settings(**kwargs):
        state.checked.append(kwargs["samplerate"])
        if fallback_rate and kwargs["samplerate"] == detector.SAMPLE_RATE:
            raise ValueError("unsupported sample rate")

    class Stream:
        def __init__(self, **kwargs):
            state.opened.append(kwargs)

        def __enter__(self):
            if open_error:
                raise RuntimeError(open_error)
            return self

        def __exit__(self, *args):
            state.closed = True

        def read(self, count):
            state.reads += 1
            try:
                samples, overflowed = next(blocks)
            except StopIteration:
                detector.stop()
                samples, overflowed = np.zeros(count), False
            if stop_during_read:
                detector.stop()
            return np.asarray(samples).reshape(-1, 1), overflowed

    monkeypatch.setitem(sys.modules, "sounddevice", SimpleNamespace(
        check_input_settings=check_input_settings,
        query_devices=lambda **kwargs: {"default_samplerate": fallback_rate},
        InputStream=Stream,
    ))
    return state


def test_capture_scales_to_device_rate_and_clears_history_on_overflow(monkeypatch):
    sample_rate = 48000
    hop = round(256 * sample_rate / 44100)
    samples = voice(sample_rate, np.full(hop * 30, 220))
    blocks = [(samples[i:i + hop], False) for i in range(0, len(samples), hop)]
    blocks += [(np.zeros(0), False)]
    blocks += [(samples[i:i + hop], False) for i in range(0, len(samples), hop)]
    blocks += [(samples[:hop], True)]
    detector = PitchDetector()
    estimates, statuses = [], []
    detector.pitch_detected.connect(lambda f, c: estimates.append((f, c)))
    detector.status_changed.connect(statuses.append)
    state = fake_microphone(monkeypatch, detector, blocks, fallback_rate=sample_rate)
    detector.run()

    assert state.checked == [44100, 48000]
    assert state.opened[0]["samplerate"] == sample_rate
    assert state.opened[0]["blocksize"] == hop
    assert statuses == ["Listening..."]
    assert abs(cents(estimates[29][0], 220)) < 15
    assert estimates[30] == (0, 0)  # Empty block resets safely.
    assert abs(cents(estimates[-2][0], 220)) < 15
    assert estimates[-1] == (0, 0)  # Gap must discard the old note.
    assert state.closed


def test_device_open_failure_does_not_report_listening(monkeypatch):
    detector = PitchDetector()
    statuses = []
    detector.status_changed.connect(statuses.append)
    fake_microphone(monkeypatch, detector, [], open_error="device busy")
    detector.run()
    assert statuses == ["Microphone unavailable: device busy"]


def test_stop_before_start_never_opens_device(monkeypatch):
    detector = PitchDetector()
    state = fake_microphone(monkeypatch, detector, [])
    detector.stop()
    detector.run()
    assert state.opened == []


def test_stop_during_read_closes_stream_without_emitting_late_pitch(monkeypatch):
    detector = PitchDetector()
    estimates = []
    detector.pitch_detected.connect(lambda f, c: estimates.append((f, c)))
    state = fake_microphone(monkeypatch, detector, [(np.ones(256), False)],
                            stop_during_read=True)
    detector.run()
    assert estimates == []
    assert state.closed
