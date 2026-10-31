from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QLabel, QStyleFactory, QVBoxLayout, QWidget

from core.audio_controller import AudioController
from ui.player_dialogs import ClickableSlider


@pytest.fixture
def player(qapp):
    class Playback(AudioController, QWidget):
        def __init__(self):
            super().__init__()
            self.seeks = []
            self.is_seeking = False
            self.seek_slider = ClickableSlider(Qt.Orientation.Horizontal)
            self.current_time_label = QLabel()
            self.duration_label = QLabel()
            self.media_player = SimpleNamespace(
                duration=lambda: 180000, position=lambda: 30000,
                setPosition=self.seeks.append,
            )
            self.seek_slider.sliderPressed.connect(self.start_seeking)
            self.seek_slider.sliderReleased.connect(self.finish_seeking)
            QVBoxLayout(self).addWidget(self.seek_slider)
            self.resize(400, 60)

        def _flush_pcm_output(self):
            pass

        def update_lyrics(self, position):
            pass

    window = Playback()
    window.show()
    qapp.processEvents()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("style", QStyleFactory.keys())
@pytest.mark.parametrize("ratio", [0.0, 0.5, 1.0])
def test_groove_click_commits_seek_including_endpoints(player, style, ratio):
    slider = player.seek_slider
    slider.setStyle(QStyleFactory.create(style))
    player.duration_changed(180000)
    QTest.mouseClick(slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(round((slider.width() - 1) * ratio), slider.height() // 2))
    assert len(player.seeks) == 1
    assert player.seeks[0] == pytest.approx(180000 * ratio, abs=1000)
    assert not slider.isSliderDown()
    assert not player.is_seeking


def test_position_tick_recovers_zero_range_and_lost_release_without_seeking_back(player):
    slider = player.seek_slider
    slider.setRange(0, 0)
    slider.setSliderDown(True)  # Simulate losing mouse capture before release.
    player.position_changed(30000)
    assert slider.maximum() == 180000
    assert slider.value() == 30000
    assert not slider.isSliderDown()
    assert not player.is_seeking
    assert player.seeks == []
    assert player.duration_label.text() == "3:00"


def test_drag_is_not_overwritten_by_playback_ticks(player):
    slider = player.seek_slider
    player.duration_changed(180000)
    QTest.mousePress(slider, Qt.MouseButton.LeftButton,
                     pos=QPoint(slider.width() // 2, slider.height() // 2))
    try:
        selected = slider.value()
        player.position_changed(30000)
        assert slider.value() == selected
        assert slider.isSliderDown()
        QTest.mouseMove(slider, QPoint(slider.width() - 1, slider.height() // 2))
    finally:
        QTest.mouseRelease(slider, Qt.MouseButton.LeftButton,
                           pos=QPoint(slider.width() - 1, slider.height() // 2))
    assert player.seeks == [180000]
    player.position_changed(160000)
    assert slider.value() == 160000
