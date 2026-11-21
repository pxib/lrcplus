"""Stateful graphic EQ for one playback stream."""

import numpy as np
from pedalboard import PeakFilter, Pedalboard
from PySide6.QtMultimedia import QAudioFormat


FREQUENCIES = (31, 63, 125, 250, 500, 1000, 2000, 4000, 8000, 16000)
SAMPLE_FORMATS = {
    QAudioFormat.SampleFormat.Float: (np.float32, 1, 0),
    QAudioFormat.SampleFormat.Int16: (np.int16, 32768, 0),
    QAudioFormat.SampleFormat.Int32: (np.int32, 2147483648, 0),
    QAudioFormat.SampleFormat.UInt8: (np.uint8, 128, 128),
}


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

        preamp, gains = configuration
        rate, channels = fmt.sampleRate(), fmt.channelCount()
        key = (configuration, rate, channels)
        if key != self._key:
            bands = [(frequency, gain) for frequency, gain in zip(FREQUENCIES, gains)
                     if gain and frequency < rate / 2]
            # Mono boards keep filter history independent for each channel,
            # including when a decoder hands us fewer frames than channels.
            self._boards = tuple(
                Pedalboard([PeakFilter(cutoff_frequency_hz=frequency, gain_db=gain, q=1.4)
                            for frequency, gain in bands])
                for _ in range(channels)
            ) if bands else ()
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
