"""Built-in EQ curves, specified at the ten graphic EQ frequencies."""

from plugins.core.equalizer.dsp import FREQUENCIES, FREQUENCIES_BY_COUNT


PRESETS = {
    "Bass Boost": (5, 5, 4, 2, 0, 0, 0, 0, 0, 0),
    "Bass Cut": (-7, -6, -4, -2, 0, 0, 0, 0, 0, 0),
    "Treble Boost": (0, 0, 0, 0, 0, 0, 1, 3, 4, 4),
    "Treble Cut": (0, 0, 0, 0, 0, 0, -1, -3, -4, -5),
    "Vocal Clarity": (-4, -3, -2, -1, 0, 2, 4, 3, 1, 0),
    "Loudness": (5, 4, 2, 0, -1, -1, 0, 2, 3, 4),
}


def preset_gains(name, count):
    import numpy as np

    anchored = PRESETS[name]
    gains = np.interp(np.log2(FREQUENCIES_BY_COUNT[count]), np.log2(FREQUENCIES), anchored)
    return tuple(int(round(gain)) for gain in gains)
