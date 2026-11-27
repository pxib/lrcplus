import numpy as np
import pytest

from core import rhythm_melody
from core.rhythm_melody import FIRST_MIDI_NOTE, LAST_MIDI_NOTE, MelodyAnalyzer


def test_rust_melody_peaks_match_python_across_stereo_passes():
    if rhythm_melody._native_peak_collector() is None:
        pytest.skip("The optional source-build Rust audio library is unavailable.")
    analyzer = MelodyAnalyzer(22050, 512)
    bins = analyzer._high_bin + 2
    pitches = np.zeros((128, LAST_MIDI_NOTE - FIRST_MIDI_NOTE + 1), dtype=np.float32)
    expected = pitches.copy()
    rng = np.random.default_rng(19)
    mid = rng.uniform(0, 0.2, (len(pitches), bins)).astype(np.float32)
    mid[0] = 0
    mid[1] = 0
    mid[1, round(220 / analyzer._bin_hz)] = 0.5
    mid[1, round(440 / analyzer._bin_hz)] = 0.2
    mid[1, round(659 / analyzer._bin_hz)] = 0.1
    mid[2, analyzer._low_bin] = np.nan
    # The strided side spectrum must be copied for the native batch call.
    side = rng.uniform(0, 0.2, (len(pitches), 2 * bins)).astype(np.float32)[:, ::2]

    analyzer._collect_peaks_python(mid, expected)
    analyzer._collect_peaks(mid, pitches)
    np.testing.assert_array_equal(pitches, expected)
    assert list(np.flatnonzero(pitches[1]) + FIRST_MIDI_NOTE) == [57]
    assert not pitches[[0, 2]].any()

    analyzer._collect_peaks_python(side, expected)
    analyzer._collect_peaks(side, pitches)
    np.testing.assert_array_equal(pitches, expected)


def test_float64_spectra_keep_their_python_precision(monkeypatch):
    analyzer = MelodyAnalyzer(22050, 512)
    spectra = np.zeros((1, analyzer._high_bin + 2), dtype=np.float64)
    spectra[0, round(440 / analyzer._bin_hz)] = 0.5
    pitches = np.zeros((1, LAST_MIDI_NOTE - FIRST_MIDI_NOTE + 1), dtype=np.float32)
    monkeypatch.setattr(rhythm_melody, "_native_peak_collector",
                        lambda: pytest.fail("float64 spectra must retain their precision"))
    analyzer._collect_peaks(spectra, pitches)
    assert pitches[0, 69 - FIRST_MIDI_NOTE] == 0.5
