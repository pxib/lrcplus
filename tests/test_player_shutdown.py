"""Closing must cancel playback before backend EOF and slow UI cleanup."""

from types import SimpleNamespace

import pytest
from PySide6.QtGui import QCloseEvent
from PySide6.QtMultimedia import QMediaPlayer

from ui.main_window import Player


@pytest.fixture
def window(qapp, monkeypatch):
    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    yield window
    window._library_scan_worker = None
    window.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("playing", [False, True])
def test_close_cancels_processed_tail_before_backend_stop(window, monkeypatch, playing):
    worker = window._pcm_worker
    worker.configure(None, 1, 0, 1.5, enabled=True, playing=playing)
    # Model buffered audio awaiting natural EOF without opening a real device.
    with worker._lock:
        worker._has_input = True
        worker._end_of_stream = True
    stops = []

    def stop():
        stops.append(True)
        assert window.audio_output.isMuted()
        assert worker._closed
        assert not worker.finish(decoder_finished=True)
        # Backend stop notifications must neither resume the tail nor advance
        # or repeat a track while the window is closing.
        window._sync_pcm_sink_state(QMediaPlayer.PlaybackState.StoppedState)
        window.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)
        assert worker._controls.playing is playing
        assert not getattr(window, "_pcm_end_pending", False)

    monkeypatch.setattr(window.media_player, "stop", stop)
    monkeypatch.setattr(window.media_player, "mediaStatus",
                        lambda: QMediaPlayer.MediaStatus.EndOfMedia)
    monkeypatch.setattr(window, "_playback_paths",
                        lambda: pytest.fail("Closing must not advance tracks"))
    window.close()
    assert stops == [True]
    assert worker._thread.wait(3000)


def test_deferred_close_silences_audio_before_waiting_for_scan(window):
    interruptions = []
    window._library_scan_worker = SimpleNamespace(
        requestInterruption=lambda: interruptions.append(True),
    )
    event = QCloseEvent()
    window.closeEvent(event)
    assert not event.isAccepted()
    assert interruptions == [True]
    assert window.audio_output.isMuted()
    assert window._pcm_worker._closed
    assert window.media_player.audioBufferOutput() is None
    assert window._pcm_worker._thread.wait(3000)
