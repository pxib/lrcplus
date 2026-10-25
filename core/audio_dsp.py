import numpy as np
from pedalboard import PitchShift
from PySide6.QtMultimedia import QAudioFormat

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


def apply_pcm_pitch_to_bytes(data, sample_format, channels, sample_rate, semitones):
    """Shift decoded PCM pitch while preserving its playback duration."""
    shift = float(semitones)
    if abs(shift) < 0.001:
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
    if frame_count < 2:
        return data
    frames = samples[:frame_count * channels].reshape(frame_count, channels).astype(np.float32)
    # Rubber Band needs a useful block of context. Qt emits small chunks, so
    # accumulate them and pitch shift fixed blocks before draining output.
    state = (channels, int(sample_rate), shift)
    reset_effect = getattr(apply_pcm_pitch_to_bytes, "_state", None) != state
    if reset_effect:
        apply_pcm_pitch_to_bytes._state = state
        apply_pcm_pitch_to_bytes._input = np.empty((0, channels), dtype=np.float32)
        apply_pcm_pitch_to_bytes._pending = np.empty((0, channels), dtype=np.float32)
        apply_pcm_pitch_to_bytes._tail = None
        apply_pcm_pitch_to_bytes._started = False
    input_buffer = np.concatenate((apply_pcm_pitch_to_bytes._input, frames), axis=0)
    pending = apply_pcm_pitch_to_bytes._pending
    block_frames = 16384
    overlap_frames = 4096
    hop_frames = block_frames - overlap_frames
    while input_buffer.shape[0] >= block_frames:
        block = input_buffer[:block_frames]
        input_buffer = input_buffer[hop_frames:]
        processed = PitchShift(semitones=shift)(block.T, sample_rate, reset=True).T
        prior_tail = apply_pcm_pitch_to_bytes._tail
        if prior_tail is None:
            output_block = processed[:hop_frames]
        else:
            phase = np.linspace(0.0, np.pi / 2.0, overlap_frames, endpoint=True)
            old_gain = np.cos(phase)[:, None]
            new_gain = np.sin(phase)[:, None]
            blended = prior_tail * old_gain + processed[:overlap_frames] * new_gain
            output_block = np.concatenate(
                (blended, processed[overlap_frames:hop_frames]), axis=0
            )
        pending = np.concatenate((pending, output_block), axis=0)
        apply_pcm_pitch_to_bytes._tail = processed[hop_frames:block_frames]
    apply_pcm_pitch_to_bytes._input = input_buffer
    # Pad only the initial block latency; queued processed samples are never
    # stretched or discarded.
    shifted = np.zeros_like(frames)
    if not apply_pcm_pitch_to_bytes._started and pending.shape[0] >= block_frames * 2:
        apply_pcm_pitch_to_bytes._started = True
    if apply_pcm_pitch_to_bytes._started:
        available = min(frame_count, pending.shape[0])
        if available:
            shifted[:available] = pending[:available]
            pending = pending[available:]
    apply_pcm_pitch_to_bytes._pending = pending
    if floating:
        result = np.clip(shifted, -1.0, 1.0).astype(dtype)
    else:
        limits = np.iinfo(dtype)
        result = np.clip(np.rint(shifted), limits.min, limits.max).astype(dtype)
    return result.tobytes()


def reset_pcm_pitch_state():
    """Drop buffered samples so seeks and pitch changes cannot replay stale audio."""
    apply_pcm_pitch_to_bytes._state = None
    apply_pcm_pitch_to_bytes._input = np.empty((0, 0), dtype=np.float32)
    apply_pcm_pitch_to_bytes._pending = np.empty((0, 0), dtype=np.float32)
    apply_pcm_pitch_to_bytes._tail = None
    apply_pcm_pitch_to_bytes._started = False
