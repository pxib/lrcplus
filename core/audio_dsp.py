from functools import cache
from math import isfinite

from PySide6.QtMultimedia import QAudioFormat

from core.native_audio import audio_kernel
from core.pcm_resampler import PcmRateProcessor


def create_pitch_stream(channels, sample_rate, semitones):
    from core.pitch_stream import create_pitch_stream as create
    return create(channels, sample_rate, semitones)


def volume_to_gain(volume_percent):
    """Convert a UI percentage to the PCM gain multiplier used above 100%."""
    try:
        value = float(volume_percent)
    except (TypeError, ValueError):
        return 1.0
    return max(0.0, min(3.0, value / 100.0))


def snap_volume_percentage(value, threshold=5):
    value = int(value)
    return 100 if abs(value - 100) <= threshold else value


def apply_pcm_gain(samples, gain):
    """Apply the gain and clamp the resulting floating-point PCM to [-1.0, 1.0]."""
    import numpy as np

    array = np.asarray(samples, dtype=np.float32)
    gain = float(gain)
    if not np.isfinite(gain):
        gain = 1.0
    boosted = np.clip(array * gain, -1.0, 1.0)
    return boosted.astype(np.float32, copy=False)


_GAIN_FORMATS = {
    QAudioFormat.SampleFormat.UInt8: 1,
    QAudioFormat.SampleFormat.Int16: 2,
    QAudioFormat.SampleFormat.Int32: 3,
    QAudioFormat.SampleFormat.Float: 4,
}


@cache
def _native_gain():
    from ctypes import c_bool, c_double, c_size_t, c_uint8, c_void_p

    return audio_kernel("lrcplus_pcm_gain_v1", (c_void_p, c_size_t, c_uint8, c_double), c_bool)


def apply_pcm_gain_to_bytes(data, sample_format, gain):
    """Apply gain to interleaved Qt PCM bytes while preserving their sample format."""
    format_id = _GAIN_FORMATS.get(sample_format)
    if format_id is not None:
        gain = float(gain)
        native = _native_gain() if isfinite(gain) else None
        if native is not None:
            from ctypes import c_char

            view = memoryview(data)
            buffer = (c_char * view.nbytes).from_buffer_copy(view)
            if native(buffer, len(buffer), format_id, gain):
                return bytes(buffer)
    return _apply_pcm_gain_numpy(data, sample_format, gain)


def _apply_pcm_gain_numpy(data, sample_format, gain):
    import numpy as np

    if sample_format == QAudioFormat.SampleFormat.Float:
        samples = np.frombuffer(data, dtype=np.float32)
        return apply_pcm_gain(samples, gain).tobytes()

    if sample_format == QAudioFormat.SampleFormat.Int16:
        dtype, peak = np.int16, 32768.0
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        dtype, peak = np.int32, 2147483648.0
    elif sample_format == QAudioFormat.SampleFormat.UInt8:
        samples = np.frombuffer(data, dtype=np.uint8).astype(np.float32)
        normalized = (samples - 128.0) / 128.0
        boosted = apply_pcm_gain(normalized, gain)
        result = np.clip(np.rint(boosted * 128.0 + 128.0), 0, 255)
        return result.astype(np.uint8).tobytes()
    else:
        raise ValueError(f"Unsupported PCM sample format: {sample_format}")

    samples = np.frombuffer(data, dtype=dtype).astype(np.float64) / peak
    boosted = np.clip(samples * float(gain), -1.0, 1.0)
    limits = np.iinfo(dtype)
    result = np.clip(np.rint(boosted * peak), limits.min, limits.max)
    return result.astype(dtype).tobytes()


def apply_pcm_rate_to_bytes(data, sample_format, channels, rate):
    """Resample a complete PCM clip; playback must retain a PcmRateProcessor."""
    processor = PcmRateProcessor()
    return (processor.process(data, sample_format, channels, rate)
            + processor.flush())


class PcmPitchProcessor:
    """One playback stream, owned by its processing thread."""

    def process(self, data, sample_format, channels, sample_rate, semitones, *, continuous=False):
        """Shift decoded PCM pitch while preserving its playback duration."""
        if getattr(self, "_flushed", False):
            self.reset()
        shift = float(semitones)
        if abs(shift) < 0.001 and not continuous:
            self.reset()
            return data
        import numpy as np
        from core.pitch_stream import BLOCK_FRAMES

        if sample_format == QAudioFormat.SampleFormat.Float:
            dtype, floating = np.float32, True
        elif sample_format == QAudioFormat.SampleFormat.Int16:
            dtype, floating = np.int16, False
        elif sample_format == QAudioFormat.SampleFormat.Int32:
            dtype, floating = np.int32, False
        else:
            raise ValueError("Pitch processing requires floating-point, 16-bit, or 32-bit PCM.")
        samples = np.frombuffer(data, dtype=dtype)
        channels = max(1, int(channels))
        frame_count = len(samples) // channels
        if frame_count == 0:
            return data
        frames = samples[:frame_count * channels].reshape(frame_count, channels).astype(np.float32)
        if not floating:
            frames /= float(-np.iinfo(dtype).min)
        state = (channels, int(sample_rate), sample_format)
        reset_effect = (getattr(self, "_state", None) != state
                        or (not continuous and abs(shift - getattr(self, "_shift", shift)) >= 12.0))
        if reset_effect:
            self.reset()
            effect = create_pitch_stream(channels, int(sample_rate), shift)
            self._state = state
            self._input = np.empty((0, channels), dtype=np.float32)
            self._pending = np.empty((0, channels), dtype=np.float32)
            self._started = False
            self._effect = effect
            self._startup_remaining = effect.latency_frames
            self._current_shift = shift
            self._ramp_remaining = 0
        elif self._shift != shift:
            # Ramp in audio time, rather than jumping once per decoder callback.
            # A new slider target continues from the pitch actually reached.
            self._ramp_remaining = max(1, round(sample_rate * (0.008 if continuous else 0.03)))
        self._shift = shift
        input_buffer = np.concatenate((self._input, frames), axis=0)
        output = [self._pending]
        # Fixed internal blocks make the result independent of Qt callback sizes.
        # Keep the same processor alive: no repeated resets or synthetic joins.
        consumed = len(input_buffer) - len(input_buffer) % BLOCK_FRAMES
        for start in range(0, consumed, BLOCK_FRAMES):
            if self._ramp_remaining:
                step = min(BLOCK_FRAMES, self._ramp_remaining)
                self._current_shift += (shift - self._current_shift) * step / self._ramp_remaining
                self._ramp_remaining -= step
                self._effect.set_semitones(self._current_shift)
            output.append(self._effect.process(
                input_buffer[start:start + BLOCK_FRAMES],
            ))
        pending = np.concatenate(output, axis=0)
        input_buffer = input_buffer[consumed:]
        self._input = input_buffer
        # Preserve duration with one startup reserve, then drain every sample in
        # order. The controller keeps native playback audible during startup.
        shifted = np.zeros_like(frames)
        padding = min(frame_count, self._startup_remaining)
        self._startup_remaining -= padding
        available = min(frame_count - padding, pending.shape[0])
        if available:
            shifted[padding:padding + available] = pending[:available]
            pending = pending[available:]
            self._started = True
        # The output clock can distinguish original audio from inserted silence.
        self.content_frames = (padding, available)
        self._pending = pending
        if floating:
            result = np.clip(shifted, -1.0, 1.0).astype(dtype)
        else:
            limits = np.iinfo(dtype)
            result = np.clip(
                np.rint(shifted.astype(np.float64) * float(-limits.min)),
                limits.min, limits.max,
            ).astype(dtype)
        return result.tobytes()

    def flush(self):
        """Drain the stream's lookahead once, including a partial input block.

        Both pitch backends retain audio internally. Feed one latency reserve
        of silence through the same history to release the last real samples.
        No new track samples or processor recreation are needed.
        """
        effect = getattr(self, "_effect", None)
        if effect is None or getattr(self, "_flushed", False):
            return b""
        import numpy as np

        channels, sample_rate, sample_format = self._state
        dtype = {QAudioFormat.SampleFormat.Float: np.float32,
                 QAudioFormat.SampleFormat.Int16: np.int16,
                 QAudioFormat.SampleFormat.Int32: np.int32}[sample_format]
        tail = self.process(np.zeros((effect.latency_frames, channels), dtype).tobytes(),
                            sample_format, channels, sample_rate, self._shift, continuous=True)
        self._flushed = True
        return tail


    def reset(self):
        """Drop buffered samples so seeks and format changes cannot replay stale audio."""
        effect = getattr(self, "_effect", None)
        if effect is not None:
            effect.close()
        self._state = None
        # Idle/reset processors do not need NumPy. Real pitch processing
        # allocates typed arrays when it creates a stream above.
        self._input = ()
        self._pending = ()
        self._started = False
        self.content_frames = (0, 0)
        self._effect = None
        self._startup_remaining = 0
        self._ramp_remaining = 0
        self._flushed = False


_default_pitch_processor = PcmPitchProcessor()


def apply_pcm_pitch_to_bytes(data, sample_format, channels, sample_rate, semitones):
    result = _default_pitch_processor.process(data, sample_format, channels, sample_rate, semitones)
    apply_pcm_pitch_to_bytes.__dict__.update(_default_pitch_processor.__dict__)
    return result


def reset_pcm_pitch_state():
    _default_pitch_processor.reset()
    apply_pcm_pitch_to_bytes.__dict__.update(_default_pitch_processor.__dict__)


def flush_pcm_pitch_state():
    result = _default_pitch_processor.flush()
    apply_pcm_pitch_to_bytes.__dict__.update(_default_pitch_processor.__dict__)
    return result
