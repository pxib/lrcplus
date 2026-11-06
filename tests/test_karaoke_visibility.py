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
        self.sweep_style = style


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


def test_animation_frames_do_not_reload_or_reapply_settings(karaoke, qapp, monkeypatch):
    window, plugin = karaoke

    def unexpected(*args):
        pytest.fail("Animation must not read or reapply settings")

    monkeypatch.setattr("plugins.core.karaoke.plugin.get_karaoke_settings", unexpected)
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    qapp.processEvents()
    window.media_player.play()
    monkeypatch.setattr("plugins.core.karaoke.plugin.apply_karaoke_settings", unexpected)
    count = len(window.lyric_widget.positions)
    for _ in range(1000):
        plugin.update_karaoke_position()
    assert len(window.lyric_widget.positions) == count + 1000


def test_changed_settings_refresh_once_and_reach_newly_visible_displays(
    karaoke, qapp, monkeypatch
):
    window, plugin = karaoke
    from core.settings import save_karaoke_settings
    import plugins.core.karaoke.plugin as module

    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    window.media_player.play()
    qapp.processEvents()
    save_karaoke_settings({"sweep_fps": 120, "sweep_style": "glow"})
    original = module.get_karaoke_settings
    reads = []

    def read_settings():
        reads.append(True)
        return original()

    monkeypatch.setattr(module, "get_karaoke_settings", read_settings)
    plugin.on_karaoke_settings_changed()
    assert plugin.timer.interval() == 8
    assert window.lyric_widget.sweep_style == "glow"
    for _ in range(1000):
        plugin.update_karaoke_position()
    window.media_player.pause()
    window.display_stack.setCurrentWidget(window.scrolling_lyrics_widget)
    qapp.processEvents()
    assert window.scrolling_lyrics_widget.sweep_style == "glow"
    assert not plugin.timer.isActive()
    assert len(reads) == 1


def test_unloaded_plugin_stops_receiving_media_signals(karaoke):
    window, plugin = karaoke
    plugin.on_unload(None)
    position = plugin._position_anchor_ms
    rate = plugin._playback_rate
    window.media_player.positionChanged.emit(4321)
    window.media_player.playbackRateChanged.emit(2.0)
    window.media_player.play()
    assert plugin._position_anchor_ms == position
    assert plugin._playback_rate == rate
    assert plugin.window is None


def test_runtime_snapshot_reports_word_highlighting_and_translation_workload(karaoke, qapp):
    from tools.profile_memory import runtime_state

    window, plugin = karaoke
    window.plugin_manager = SimpleNamespace(plugins={
        "karaoke": SimpleNamespace(instance=plugin),
    })
    window.lyric_lines[0].translation_lines = [1]
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.show()
    window.media_player.play()
    qapp.processEvents()
    state = runtime_state(window)
    assert state["loaded_plugins"] == ["karaoke"]
    assert state["karaoke_timer_active"]
    assert state["karaoke_interval_ms"] > 0
    assert state["timed_lyric_lines"] == 1
    assert state["translated_lyric_lines"] == 1
    window.media_player.pause()
    assert not runtime_state(window)["karaoke_timer_active"]


@pytest.mark.parametrize("display", ["lyric_widget", "scrolling_lyrics_widget"])
@pytest.mark.parametrize("playing", [False, True])
@pytest.mark.parametrize("position_ms,offset,expected", [
    (12000, 2.5, 9.5),
    (12000, -2.5, 14.5),
    (12000, 0.0, 12.0),
    (0, 2.5, -2.5),
])
def test_karaoke_uses_offset_lyric_time(
    karaoke, qapp, monkeypatch, display, playing, position_ms, offset, expected
):
    window, plugin = karaoke
    monkeypatch.setattr("plugins.core.karaoke.plugin.time.monotonic", lambda: 100.0)
    window.lyrics_offset_seconds = offset
    window.media_player.position_ms = position_ms
    window.workspace_stack.setCurrentWidget(window.lyrics_page)
    window.display_stack.setCurrentWidget(getattr(window, display))
    window.show()
    qapp.processEvents()
    if playing:
        window.media_player.play()

    plugin.update_karaoke_position()
    assert getattr(window, display).positions[-1] == pytest.approx(expected)

    # Reloading an offset takes effect without changing the media clock.
    window.lyrics_offset_seconds = offset + 1.0
    plugin.update_karaoke_position()
    assert getattr(window, display).positions[-1] == pytest.approx(expected - 1.0)


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
