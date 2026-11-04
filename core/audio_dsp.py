import numpy as np
from PySide6.QtMultimedia import QAudioFormat

from core.pitch_stream import BLOCK_FRAMES, create_pitch_stream

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
    array = np.asarray(samples, dtype=np.float32)
    gain = float(gain)
    if not np.isfinite(gain):
        gain = 1.0
    boosted = np.clip(array * gain, -1.0, 1.0)
    return boosted.astype(np.float32, copy=False)


def apply_pcm_gain_to_bytes(data, sample_format, gain):
    """Apply gain to interleaved Qt PCM bytes while preserving their sample format."""
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
    """Resample a decoded PCM block to match the media player's playback rate."""
    rate = float(rate)
    if not np.isfinite(rate) or rate <= 0 or abs(rate - 1.0) < 0.001:
        return data
    if sample_format == QAudioFormat.SampleFormat.Float:
        dtype = np.float32
        floating = True
    elif sample_format == QAudioFormat.SampleFormat.Int16:
        dtype = np.int16
        floating = False
    elif sample_format == QAudioFormat.SampleFormat.Int32:
        dtype = np.int32
        floating = False
    else:
        raise ValueError("Playback-rate processing requires float, 16-bit, or 32-bit PCM.")

    channels = max(1, int(channels))
    samples = np.frombuffer(data, dtype=dtype)
    frame_count = len(samples) // channels
    if frame_count < 2:
        return data
    frames = samples[:frame_count * channels].reshape(frame_count, channels)
    output_count = max(1, int(round(frame_count / rate)))
    source_positions = np.minimum(
        np.arange(output_count, dtype=np.float64) * rate,
        frame_count - 1,
    )
    left_indices = np.floor(source_positions).astype(np.intp)
    right_indices = np.minimum(left_indices + 1, frame_count - 1)
    fraction = (source_positions - left_indices)[:, None]
    processed = (
        frames[left_indices].astype(np.float32) * (1.0 - fraction)
        + frames[right_indices].astype(np.float32) * fraction
    )
    if floating:
        processed = np.clip(processed, -1.0, 1.0).astype(dtype)
    else:
        limits = np.iinfo(dtype)
        processed = np.clip(np.rint(processed), limits.min, limits.max).astype(dtype)
    return processed.tobytes()


class PcmPitchProcessor:
    """One playback stream, owned by its processing thread."""

    def process(self, data, sample_format, channels, sample_rate, semitones):
        """Shift decoded PCM pitch while preserving its playback duration."""
        shift = float(semitones)
        if abs(shift) < 0.001:
            self.reset()
            return data
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
                        or abs(shift - getattr(self, "_shift", shift)) >= 12.0)
        if reset_effect:
            self.reset()
            effect = create_pitch_stream(channels, int(sample_rate), shift)
            self._state = state
            self._input = np.empty((0, channels), dtype=np.float32)
            self._pending = np.empty((0, channels), dtype=np.float32)
            self._started = False
            self._effect = effect
            self._startup_remaining = effect.latency_frames
        if not reset_effect and self._shift != shift:
            self._effect.set_semitones(shift)
        self._shift = shift
        input_buffer = np.concatenate((self._input, frames), axis=0)
        output = [self._pending]
        # Fixed internal blocks make the result independent of Qt callback sizes.
        # Keep the same processor alive: no repeated resets or synthetic joins.
        consumed = len(input_buffer) - len(input_buffer) % BLOCK_FRAMES
        for start in range(0, consumed, BLOCK_FRAMES):
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
        if frame_count > padding:
            available = min(frame_count - padding, pending.shape[0])
            if available:
                shifted[padding:padding + available] = pending[:available]
                pending = pending[available:]
                self._started = True
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


    def reset(self):
        """Drop buffered samples so seeks and format changes cannot replay stale audio."""
        effect = getattr(self, "_effect", None)
        if effect is not None:
            effect.close()
        self._state = None
        self._input = np.empty((0, 0), dtype=np.float32)
        self._pending = np.empty((0, 0), dtype=np.float32)
        self._started = False
        self._effect = None
        self._startup_remaining = 0


_default_pitch_processor = PcmPitchProcessor()


def apply_pcm_pitch_to_bytes(data, sample_format, channels, sample_rate, semitones):
    result = _default_pitch_processor.process(data, sample_format, channels, sample_rate, semitones)
    apply_pcm_pitch_to_bytes.__dict__.update(_default_pitch_processor.__dict__)
    return result


def reset_pcm_pitch_state():
    _default_pitch_processor.reset()
    apply_pcm_pitch_to_bytes.__dict__.update(_default_pitch_processor.__dict__)
