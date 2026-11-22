"""Stateful parametric filters for one playback stream."""

from dataclasses import dataclass

import numpy as np
from pedalboard import HighShelfFilter, LowShelfFilter, PeakFilter, Pedalboard
from PySide6.QtMultimedia import QAudioFormat


FREQUENCIES = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
THIRD_OCTAVE_FREQUENCIES = (
    20, 25, 31.5, 40, 50, 63, 80, 100, 125, 160, 200, 250, 315, 400, 500, 630,
    800, 1000, 1250, 1600, 2000, 2500, 3150, 4000, 5000, 6300, 8000, 10000,
    12500, 16000, 20000,
)
FREQUENCIES_BY_COUNT = {10: FREQUENCIES, 31: THIRD_OCTAVE_FREQUENCIES}
FILTERS = {"bell": PeakFilter, "low_shelf": LowShelfFilter, "high_shelf": HighShelfFilter}
SAMPLE_FORMATS = {
    QAudioFormat.SampleFormat.Float: (np.float32, 1, 0),
    QAudioFormat.SampleFormat.Int16: (np.int16, 32768, 0),
    QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648, 0),
    QAudioFormat.SampleFormat.UInt8: (np.uint8, 128, 128),
}


@dataclass(frozen=True)
class Band:
    frequency: float
    gain: float = 0
    q: float = 1.4
    shape: str = "bell"


def graphic_bands(gains):
    frequencies = FREQUENCIES_BY_COUNT[len(gains)]
    # Approximate a third-octave bandwidth for the more closely spaced bands.
    q = 4.32 if len(frequencies) == 31 else 1.4
    return tuple(Band(frequency, gain, q) for frequency, gain in zip(frequencies, gains))


class EqualizerProcessor:
    def __init__(self, configuration):
        self._configuration = configuration
        self.reset()

    def reset(self):
        self._key = None
        self._boards = ()

    def process(self, data, fmt):
        configuration = self._configuration()
        if configuration is None:
            self.reset()
            return data
        if not data:
            return data

        preamp, bands = configuration
        rate, channels = fmt.sampleRate(), fmt.channelCount()
        key = (configuration, rate, channels)
        if key != self._key:
            active_bands = [band for band in bands if band.gain and band.frequency < rate / 2]
            # Mono boards keep filter history independent for each channel,
            # including when a decoder hands us fewer frames than channels.
            self._boards = tuple(
                Pedalboard([
                    FILTERS[band.shape](cutoff_frequency_hz=band.frequency, gain_db=band.gain, q=band.q)
                    for band in active_bands
                ])
                for _ in range(channels)
            ) if active_bands else ()
            self._key = key

        if not self._boards and not preamp:
            return data
        dtype, scale, bias = SAMPLE_FORMATS[fmt.sampleFormat()]
        samples = np.frombuffer(data, dtype=dtype).reshape(-1, channels)
        # Keep conversion precision for 32-bit integer PCM until the filters.
        samples = (samples.astype(np.float64) - bias) * (10 ** (preamp / 20) / scale)
        for channel, board in enumerate(self._boards):
            samples[:, channel] = board(samples[:, channel], rate, reset=False)
        if dtype is np.float32:
            return np.clip(samples, -1, 1).astype(dtype).tobytes()
        limits = np.iinfo(dtype)
        return np.clip(np.rint(samples * scale + bias), limits.min, limits.max).astype(dtype).tobytes()
