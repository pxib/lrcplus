from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QStackedWidget, QVBoxLayout, QWidget

from plugins.core.karaoke.plugin import KaraokePlugin


class MediaPlayer(QObject):
    playbackStateChanged = Signal(object)
    positionChanged = Signal(int)
    playbackRateChanged = Signal(float)

    def __init__(self, parent):
        super().__init__(parent)
        self.state = QMediaPlayer.PlaybackState.StoppedState
        self.position_ms = 0

    def playbackState(self):
        return self.state

    def position(self):
        return self.position_ms

    def play(self):
        self.state = QMediaPlayer.PlaybackState.PlayingState
        self.playbackStateChanged.emit(self.state)

    def pause(self):
        self.state = QMediaPlayer.PlaybackState.PausedState
        self.playbackStateChanged.emit(self.state)


class Display(QWidget):
    def __init__(self):
        super().__init__()
        self.positions = []

    def set_karaoke_position(self, position):
        self.positions.append(position)

    def set_karaoke_sweep_style(self, style):
        pass


@pytest.fixture
def karaoke(qapp):
    window = QWidget()
    window.workspace_stack = QStackedWidget()
    window.library_page = QWidget()
    window.lyrics_page = QWidget()
    window.workspace_stack.addWidget(window.library_page)
    window.workspace_stack.addWidget(window.lyrics_page)
    QVBoxLayout(window).addWidget(window.workspace_stack)
    window.display_stack = QStackedWidget()
    QVBoxLayout(window.lyrics_page).addWidget(window.display_stack)
    window.lyric_widget = Display()
    window.scrolling_lyrics_widget = Display()
    window.display_stack.addWidget(window.lyric_widget)
    window.display_stack.addWidget(window.scrolling_lyrics_widget)
    window.current_lyrics_path = Path("song.lrcx")
    window.lyric_lines = [SimpleNamespace(has_karaoke=True)]
    window.media_player = MediaPlayer(window)
    plugin = KaraokePlugin()
    plugin.on_load(SimpleNamespace(on=lambda *args: None))
    plugin.on_app_ready(window)
    try:
        yield window, plugin
    finally:
        plugin.on_unload(None)
        window.close()
        window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_hidden_lyrics_stop_karaoke_and_resume_at_current_position(karaoke, qapp):
    window, plugin = karaoke
    window.show()
    qapp.processEvents()
    window.media_player.play()
    assert not plugin.timer.isActive()

    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    qapp.processEvents()
    assert plugin.timer.isActive()
    assert window.lyric_widget.positions
    assert window.scrolling_lyrics_widget.positions == []

    window.workspace_stack.setCurrentWidget(window.library_page)
    qapp.processEvents()
    count = len(window.lyric_widget.positions)
    QTest.qWait(50)
    window.media_player.position_ms = 12000
    window.media_player.positionChanged.emit(12000)
    plugin.update_karaoke_position()
    assert not plugin.timer.isActive()
    assert len(window.lyric_widget.positions) == count

    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    qapp.processEvents()
    assert plugin.timer.isActive()
    assert window.lyric_widget.positions[-1] == pytest.approx(12, abs=0.1)


def test_playback_before_first_show_and_paused_tab_switches(karaoke, qapp):
    window, plugin = karaoke
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.media_player.play()
    assert not plugin.timer.isActive()
    window.show()
    qapp.processEvents()
    assert plugin.timer.isActive()

    window.media_player.pause()
    window.workspace_stack.setCurrentWidget(window.library_page)
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    qapp.processEvents()
    assert not plugin.timer.isActive()
    window.media_player.play()
    assert plugin.timer.isActive()


def test_only_visible_lyric_display_receives_updates(karaoke, qapp):
    window, plugin = karaoke
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    window.media_player.play()
    qapp.processEvents()
    window.display_stack.setCurrentWidget(window.scrolling_lyrics_widget)
    qapp.processEvents()
    count = len(window.lyric_widget.positions)
    for _ in range(3):
        plugin.update_karaoke_position()
    assert plugin.timer.isActive()
    assert len(window.lyric_widget.positions) == count
    assert window.scrolling_lyrics_widget.positions


def test_untimed_lyrics_do_not_run_karaoke_timer(karaoke, qapp):
    window, plugin = karaoke
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    window.media_player.play()
    qapp.processEvents()
    assert plugin.timer.isActive()
    window.current_lyrics_path = Path("song.lrc")
    window.media_player.positionChanged.emit(1000)
    assert not plugin.timer.isActive()
    assert window.lyric_widget.positions[-1] is None


def test_plain_lrcx_segments_and_readings_do_not_activate_karaoke(karaoke, qapp, tmp_path):
    from lyrics.loader import load_lyrics

    path = tmp_path / "plain.lrcx"
    path.write_text(
        "[format:LRCX]\n[version:1]\n[g:auto]\n"
        "[00:00.650]**{超最強|ちょうさいきょう}**{\\n}LOL!\n"
        "[00:03.050]超最強 な～んです\n", encoding="utf-8",
    )
    window, plugin = karaoke
    window.current_lyrics_path = path
    window.lyric_lines = load_lyrics(path)
    assert window.lyric_lines[0].segments
    assert window.lyric_lines[0].style_spans
    assert not any(line.has_karaoke for line in window.lyric_lines)
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    window.media_player.play()
    qapp.processEvents()
    assert not plugin.timer.isActive()
    assert window.lyric_widget.positions[-1] is None
