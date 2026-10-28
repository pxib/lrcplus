"""Exercise microphone wiring without opening an actual audio device."""

from types import SimpleNamespace

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import QApplication, QWidget

from plugins.core.karaoke_screen import plugin as karaoke
from plugins.core.karaoke_screen.screen import KaraokeScreen


class FakeDetector(QObject):
    pitch_detected = Signal(float, float)
    status_changed = Signal(str)
    finished = Signal()
    MIN_CONFIDENCE = 0.70

    def start(self):
        self.started = True

    def stop(self):
        self.stopped = True

    def wait(self, timeout):
        return True


@pytest.fixture
def microphone(monkeypatch):
    app = QApplication.instance() or QApplication([])
    monkeypatch.setattr(karaoke, "PitchDetector", FakeDetector)
    plugin = karaoke.KaraokeScreenPlugin()
    plugin.window = QWidget()
    plugin.window.media_player = SimpleNamespace(
        playbackState=lambda: QMediaPlayer.PlaybackState.PlayingState,
        position=lambda: 500,
    )
    plugin.screen = KaraokeScreen(plugin.window)
    plugin.screen.mic_button.clicked.connect(plugin._toggle_microphone)
    plugin._pitch_detector = None
    plugin._position_anchor_monotonic = None
    yield plugin
    plugin._stop_microphone()
    plugin.window.close()
    app.processEvents()


def test_button_starts_detector_and_voice_updates_lane(microphone):
    microphone.screen.mic_button.click()
    detector = microphone._pitch_detector
    assert detector.started
    detector.pitch_detected.emit(440.0, 0.95)
    assert microphone.screen.mic_pitch_status.text() == "A4  +0 ct"
    assert microphone.screen.pitch_lane.live_pitch_points[-1][:2] == (0.5, 69.0)
    detector.pitch_detected.emit(0.0, 0.0)
    assert microphone.screen.mic_pitch_status.text() == "Listening..."
    assert microphone.screen.pitch_lane.live_pitch_alpha == 1.0


def test_stop_preserves_trail_and_ignores_late_signals(microphone):
    microphone.screen.mic_button.click()
    detector = microphone._pitch_detector
    detector.pitch_detected.emit(440.0, 0.95)
    points = list(microphone.screen.pitch_lane.live_pitch_points)
    microphone.screen.mic_button.click()
    assert detector.stopped
    assert microphone._pitch_detector is None
    assert microphone.screen.pitch_lane.live_pitch_points == points
    assert microphone.screen.pitch_lane.live_pitch_alpha == 1.0
    detector.pitch_detected.emit(440.0, 0.95)
    assert microphone.screen.mic_pitch_status.text() == "Mic off"
    assert microphone.screen.pitch_lane.live_pitch_points == points
    microphone.screen.mic_button.click()
    assert microphone._pitch_detector is not detector
    detector.finished.emit()
    assert microphone.screen.mic_button.isChecked()


def test_device_error_allows_retry(microphone):
    microphone.screen.mic_button.click()
    detector = microphone._pitch_detector
    detector.status_changed.emit("Microphone unavailable: no input device")
    detector.finished.emit()
    assert microphone._pitch_detector is None
    assert microphone.screen.mic_pitch_status.text() == "Mic unavailable"
    assert "no input device" in microphone.screen.mic_pitch_status.toolTip()
    assert not microphone.screen.mic_button.isChecked()
    microphone.screen.mic_button.click()
    assert microphone._pitch_detector.started


def test_paused_playback_shows_note_without_extending_trail(microphone):
    microphone.window.media_player.playbackState = lambda: QMediaPlayer.PlaybackState.PausedState
    microphone.screen.mic_button.click()
    microphone._pitch_detector.pitch_detected.emit(440.0, 0.95)
    assert microphone.screen.mic_pitch_status.text() == "A4  +0 ct"
    assert not microphone.screen.pitch_lane.live_pitch_points


def test_screen_close_stops_microphone(microphone):
    microphone.screen.mic_button.click()
    detector = microphone._pitch_detector
    microphone.screen_timer = None
    microphone.pitch_timer = None
    microphone._stop_square_wave_stream = lambda: None
    microphone._screen_closed()
    assert detector.stopped
    assert microphone.screen is None


def test_trail_clears_on_page_change_and_does_not_return(microphone):
    lane = microphone.screen.pitch_lane
    lane.set_live_pitch(69, 0.95, 0.5)
    lane.set_live_pitch(70, 0.95, 0.6)
    points = list(lane.live_pitch_points)
    lane.set_position(11000)
    lane.set_live_pitch(None, 0.0)
    assert lane.live_pitch_points == points
    assert lane.live_pitch_alpha == 1.0
    lane.set_position(12000)
    assert lane.live_pitch_points == []
    assert lane.live_pitch_alpha == 0.0
    lane.set_live_pitch(71, 0.95, 12.1)
    lane.set_position(1000)
    assert lane.live_pitch_points == []


def test_seeking_within_page_preserves_recorded_pitch(microphone):
    lane = microphone.screen.pitch_lane
    lane.set_position(5000)
    lane.set_live_pitch(69, 0.95, 5.0)
    lane.set_position(1000)
    lane.set_live_pitch(70, 0.95, 1.0)
    assert [point[:2] for point in lane.live_pitch_points] == [(5.0, 69.0), (1.0, 70.0)]
