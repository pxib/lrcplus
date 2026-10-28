from types import SimpleNamespace

import numpy as np

from PySide6.QtMultimedia import QAudioFormat, QMediaPlayer

from core.audio_controller import (
    AudioController,
    apply_pcm_gain,
    apply_pcm_gain_to_bytes,
    snap_volume_percentage,
    volume_to_gain,
)
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
