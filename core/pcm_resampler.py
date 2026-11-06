"""Band-limited PCM rate conversion with one phase and history per stream."""

from functools import lru_cache
import math

from PySide6.QtMultimedia import QAudioFormat


_PHASES = 1024


@lru_cache(maxsize=8)
def _filter_bank(rate):
    import numpy as np

    # Leave a small transition band below the lower Nyquist frequency. Scale
    # the support when downsampling to retain the same stop-band rejection.
    cutoff = 0.94 / max(1.0, rate)
    radius = math.ceil(32 / cutoff)
    offsets = np.arange(1 - radius, radius + 1)
    distance = np.arange(_PHASES + 1)[:, None] / _PHASES - offsets
    window = (0.42 + 0.5 * np.cos(np.pi * distance / radius)
              + 0.08 * np.cos(2 * np.pi * distance / radius))
    window[np.abs(distance) >= radius] = 0
    weights = cutoff * np.sinc(cutoff * distance) * window
    weights /= weights.sum(axis=1, keepdims=True)
    return radius, offsets, weights


class PcmRateProcessor:
    """Streaming windowed-sinc resampler, owned by a single processing thread.

    Hold a short lookahead instead of padding each decoder block. Only flush
    the final edge at end of stream; reset discards history on a discontinuity.
    """

    def __init__(self):
        self.reset()

    def reset(self):
        self._state = None
        self._frames = None
        self._total = 0
        self._start = 0
        self._output_index = 0
        self._flushed = False

    def process(self, data, sample_format, channels, rate, *, sample_rate=None):
        rate = float(rate)
        if not math.isfinite(rate) or rate <= 0 or abs(rate - 1.0) < 0.001:
            self.reset()
            return data
        import numpy as np

        if sample_format == QAudioFormat.SampleFormat.Float:
            dtype = np.float32
        elif sample_format == QAudioFormat.SampleFormat.Int16:
            dtype = np.int16
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            dtype = np.int32
        else:
            raise ValueError("Playback-rate processing requires float, 16-bit, or 32-bit PCM.")
        channels = max(1, int(channels))
        state = (sample_format, channels, sample_rate, rate)
        if state != self._state or self._flushed:
            self.reset()
            self._state = state
            self._dtype = dtype
            self._rate = rate
            self._radius, self._offsets, self._weights = _filter_bank(rate)
            self._frames = np.empty((0, channels), dtype=np.float64)
        samples = np.frombuffer(data, dtype=dtype)
        if len(samples) % channels:
            raise ValueError("PCM data must contain complete interleaved frames.")
        frames = samples.reshape(-1, channels).astype(np.float64)
        self._frames = np.concatenate((self._frames, frames))
        self._total += len(frames)
        return self._render(final=False)

    def flush(self):
        """Emit remaining frames once, extending just the final stream edge."""
        if self._state is None or self._flushed:
            return b""
        result = self._render(final=True)
        self._flushed = True
        self._frames = self._frames[:0]
        return result

    def _render(self, *, final):
        import numpy as np

        available = self._total if final else self._total - self._radius
        stop = max(0, math.ceil(available / self._rate))
        blocks = []
        # Bound temporary coefficient and sample arrays even for large codecs.
        for first in range(self._output_index, stop, 1024):
            positions = np.arange(first, min(first + 1024, stop)) * self._rate
            integer = np.floor(positions).astype(np.int64)
            phase = (positions - integer) * _PHASES
            left = phase.astype(np.intp)
            blend = (phase - left)[:, None]
            weights = (self._weights[left] * (1 - blend)
                       + self._weights[left + 1] * blend)
            indices = integer[:, None] + self._offsets - self._start
            indices = np.clip(indices, 0, len(self._frames) - 1)
            blocks.append(np.einsum("ij,ijk->ik", weights, self._frames[indices]))
        self._output_index = max(self._output_index, stop)
        if len(self._frames):
            keep_from = math.floor(self._output_index * self._rate) - self._radius + 1
            discard = max(0, min(keep_from - self._start, len(self._frames) - 1))
            self._frames = self._frames[discard:].copy()
            self._start += discard
        if not blocks:
            return b""
        result = np.concatenate(blocks)
        if self._dtype == np.float32:
            result = np.clip(result, -1.0, 1.0)
        else:
            limits = np.iinfo(self._dtype)
            result = np.clip(np.rint(result), limits.min, limits.max)
        return result.astype(self._dtype).tobytes()
