from types import SimpleNamespace

import numpy as np
import pytest

from PySide6.QtMultimedia import QAudioFormat, QMediaPlayer

from core.audio_controller import (
    AudioController,
    apply_pcm_gain,
    apply_pcm_gain_to_bytes,
    snap_volume_percentage,
    volume_to_gain,
)
from core.audio_dsp import _apply_pcm_gain_numpy, _native_gain
from lyrics.waveform import _WaveformWorker


def test_volume_to_gain_scales_from_percent():
    assert volume_to_gain(0) == 0.0
    assert volume_to_gain(100) == 1.0
    assert volume_to_gain(150) == 1.5
    assert volume_to_gain(300) == 3.0


def test_volume_percentage_snaps_near_100():
    assert snap_volume_percentage(94) == 94
    assert snap_volume_percentage(95) == 100
    assert snap_volume_percentage(105) == 100
    assert snap_volume_percentage(106) == 106


def test_apply_pcm_gain_clips_without_exceeding_one():
    samples = np.array([-1.0, -0.5, 0.25, 0.75], dtype=np.float32)
    boosted = apply_pcm_gain(samples, volume_to_gain(200))

    assert np.allclose(boosted, np.array([-1.0, -1.0, 0.5, 1.0], dtype=np.float32))
    assert np.max(np.abs(boosted)) <= 1.0


def test_apply_pcm_gain_to_float_bytes_boosts_samples():
    samples = np.array([-0.4, 0.25, 0.8], dtype=np.float32)
    gained = apply_pcm_gain_to_bytes(
        samples.tobytes(), QAudioFormat.SampleFormat.Float, 2.0
    )

    assert np.allclose(
        np.frombuffer(gained, dtype=np.float32),
        np.array([-0.8, 0.5, 1.0], dtype=np.float32),
    )


def test_apply_pcm_gain_to_int16_bytes_saturates_at_sample_limits():
    samples = np.array([-16384, 8192, 30000], dtype=np.int16)
    gained = apply_pcm_gain_to_bytes(
        samples.tobytes(), QAudioFormat.SampleFormat.Int16, 2.0
    )

    assert np.array_equal(
        np.frombuffer(gained, dtype=np.int16),
        np.array([-32768, 16384, 32767], dtype=np.int16),
    )


@pytest.mark.parametrize(("format_name", "dtype", "samples"), [
    ("UInt8", np.uint8, [0, 64, 125, 127, 128, 129, 131, 192, 255]),
    ("Int16", np.int16, [-32768, -7, -5, -1, 0, 1, 5, 7, 32767]),
    ("Int32", np.int32, [-2147483648, -7, -5, -1, 0, 1, 5, 7, 2147483647]),
    ("Float", np.float32, [-1.5, -0.5, -0.0, 0, 0.5, 1.5]),
])
def test_native_gain_matches_numpy_rounding_and_clipping(format_name, dtype, samples):
    if _native_gain() is None:
        pytest.skip("Rust gain library is unavailable")
    data = np.array(samples, dtype=dtype).tobytes()
    fmt = getattr(QAudioFormat.SampleFormat, format_name)
    for gain in (0, 0.5, 1, 1.5, 2.3, 3000):
        assert apply_pcm_gain_to_bytes(data, fmt, gain) == _apply_pcm_gain_numpy(data, fmt, gain)
    assert apply_pcm_gain_to_bytes(b"", fmt, 2) == b""


def test_stale_end_of_media_does_not_advance_playlist():
    progression_calls = []
    controller = SimpleNamespace(
        media_player=SimpleNamespace(
            mediaStatus=lambda: QMediaPlayer.MediaStatus.LoadingMedia
        ),
        _playback_paths=lambda: progression_calls.append(True),
    )

    AudioController.handle_media_status(
        controller,
        QMediaPlayer.MediaStatus.EndOfMedia,
    )

    assert progression_calls == []


def test_waveform_worker_cancel_terminates_ffmpeg_process():
    class RunningProcess:
        terminated = False

        def poll(self):
            return None

        def terminate(self):
            self.terminated = True

    worker = _WaveformWorker("track.mp3")
    process = RunningProcess()
    worker._process = process

    worker.cancel()

    assert worker._is_cancelled()
    assert process.terminated
