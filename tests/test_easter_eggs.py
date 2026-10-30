from pathlib import Path

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QMessageBox

from ui.easter_eggs import EasterEggs
from ui.main_window import Player


@pytest.fixture
def player(qapp):
    window = Player()
    window.show()
    qapp.processEvents()
    yield window
    window.close()
    window.deleteLater()
    qapp.processEvents()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def konami(widget):
    for key in EasterEggs.KONAMI:
        QTest.keyClick(widget, key)


def test_cat_unlock_ignores_typing_and_does_not_seek(player, qapp, monkeypatch):
    seeks = []
    monkeypatch.setattr(player, "skip_seconds", seeks.append)
    player.library_search.setFocus()
    konami(player.library_search)
    assert not player.seek_track.cat_enabled

    player.play_button.setEnabled(True)
    player.play_button.setFocus()
    konami(player.play_button)
    qapp.processEvents()
    assert player.seek_track.cat_enabled
    assert player.seek_track.cat.isVisible()
    assert seeks == []
    assert player.seek_track.cat.timer.isActive()

    player.seek_track._playback_changed(QMediaPlayer.PlaybackState.PlayingState)
    assert player.seek_track.cat.timer.isActive()
    konami(player.play_button)
    assert not player.seek_track.cat_enabled
    assert not player.seek_track.cat.timer.isActive()


def test_cat_follows_seek_position_and_stays_inside_bar(player, qapp):
    track = player.seek_track
    track.toggle_cat()
    player.seek_slider.setRange(0, 1000)
    qapp.processEvents()
    positions = []
    for value in (0, 500, 1000):
        player.seek_slider.setValue(value)
        positions.append(track.cat.x())
        assert 0 <= track.cat.x() <= track.perch.width() - track.cat.width()
    assert positions[0] < positions[1] < positions[2]


def test_bundled_cat_animates_during_playback_and_sleeps_when_paused(player, qapp):
    track = player.seek_track
    track.toggle_cat()
    qapp.processEvents()
    cat = track.cat
    assert not cat.spritesheet.isNull()
    assert cat.spritesheet.width() == 256
    assert cat.spritesheet.height() == 128
    sleeping = cat.grab().toImage()
    sleeping_interval = cat.timer.interval()
    cat._tick()
    assert cat.grab().toImage() != sleeping
    cat._tick()
    assert cat.grab().toImage() == sleeping

    track._playback_changed(QMediaPlayer.PlaybackState.PlayingState)
    running = cat.grab().toImage()
    cat._tick()
    next_frame = cat.grab().toImage()
    assert running != sleeping
    assert next_frame != running
    assert cat.timer.isActive()
    assert cat.timer.interval() == 1000

    track._playback_changed(QMediaPlayer.PlaybackState.PausedState)
    assert cat.grab().toImage() == sleeping
    assert cat.timer.isActive()
    assert cat.timer.interval() == sleeping_interval
    cat._tick()
    assert cat.grab().toImage() != sleeping
    track.toggle_cat()
    assert not cat.timer.isActive()


def test_seven_brand_clicks_and_alright_switch_to_original_player(player, qapp, monkeypatch):
    dialogs = []

    def accept(box):
        dialogs.append(box)
        assert player.brand_label.text() == "LRCMinus"
        next(button for button in box.buttons() if button.text() == "Alright?").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", accept)
    surface = player.centralWidget()
    geometry = player.geometry()
    style = player.styleSheet()
    player.playlist = [Path("example.mp3")]
    player.current_audio_path = player.playlist[0]
    player.current_index = 0
    source = player.media_player
    resets = []
    for method in ("stop", "setSource", "setPosition"):
        monkeypatch.setattr(source, method, lambda *args: resets.append(args))
    player.lyrics = [(0, "Hello")]
    player.current_line = 0
    player.lyric_widget.lyric = "Hello"
    player.lyric_widget.segments = [{"text": "Hello", "reading": "hi"}]
    for _ in range(6):
        QTest.mouseClick(player.brand_label, Qt.MouseButton.LeftButton)
    assert not dialogs
    QTest.mouseClick(player.brand_label, Qt.MouseButton.LeftButton)
    qapp.processEvents()

    minimal = player.easter_eggs.minus_player
    assert len(dialogs) == 1
    assert type(minimal).__module__ == "assets.apps.furiganaplayer"
    assert player.centralWidget() is minimal
    assert not surface.isVisible()
    assert player.windowTitle() == "Furigana Player"
    assert minimal.lyric_widget.lyric == "Hello"
    assert minimal.lyric_widget.segments[0]["reading"] == "hi"
    assert minimal.timer.isActive()
    assert not player.shortcut_play_pause.isEnabled()
    assert minimal.return_shortcut.isEnabled()
    assert not player.styleSheet()
    assert player.width() == 700
    assert player.height() == 400

    # Changes to the track title while stripped down must keep the joke title.
    player.setWindowTitle("LRCPlus — next.mp3")
    assert player.windowTitle() == "Furigana Player"
    QTest.mouseClick(minimal.return_button, Qt.MouseButton.LeftButton)
    qapp.processEvents()
    assert player.centralWidget() is surface
    assert player.windowTitle() == "LRCPlus — next.mp3"
    assert player.styleSheet() == style
    assert player.geometry() == geometry
    assert player.media_player is source
    assert resets == []
    assert player.playlist == [Path("example.mp3")]
    assert player.current_index == 0
    assert player.shortcut_play_pause.isEnabled()
    assert player.easter_eggs.minus_player is None


def test_cancel_budget_cut_leaves_full_player(player, monkeypatch):
    surface = player.centralWidget()

    def cancel(box):
        next(button for button in box.buttons() if button.text() == "Keep LRCPlus").click()
        return 0

    monkeypatch.setattr(QMessageBox, "exec", cancel)
    player.easter_eggs.show_budget_dialog()
    assert player.easter_eggs.minus_player is None
    assert player.centralWidget() is surface
    assert player.brand_label.text() == "LRCPlus"


def test_minus_play_escape_and_reentry(player, monkeypatch, qapp):
    toggles = []
    monkeypatch.setattr(player, "toggle_playback", lambda: toggles.append(True))
    player.current_audio_path = Path("example.mp3")
    for _ in range(2):
        player.easter_eggs.enter_minus()
        qapp.processEvents()
        minimal = player.easter_eggs.minus_player
        QTest.keyClick(minimal.play_button, Qt.Key.Key_Space)
        assert len(toggles) == _ + 1
        QTest.keyClick(minimal.play_button, Qt.Key.Key_Escape)
        qapp.processEvents()
        assert player.easter_eggs.minus_player is None


def test_closing_minus_saves_full_window_geometry(player, qapp):
    from core.settings import load_all_settings

    geometry = player.normalGeometry()
    player.easter_eggs.enter_minus()
    qapp.processEvents()
    player.close()
    saved = load_all_settings()["window_state"]["geometry"]
    assert saved["width"] == geometry.width()
    assert saved["height"] == geometry.height()


def test_minus_preserves_inline_readings_in_scrolling_mode(player):
    from lyrics.models import LyricLine, LyricSegment

    player.lyrics = [(0, "Japan")]
    player.lyric_lines = [LyricLine(0, "Japan", [
        LyricSegment(0, "Japan", ruby_spans=[(0, 5, "nihon")]),
    ])]
    player.current_line = 0
    player.easter_eggs.enter_minus()
    assert player.easter_eggs.minus_player.lyric_widget.segments == [
        {"text": "Japan", "reading": "nihon"},
    ]


def test_minus_restores_maximized_window_and_cat(player, qapp):
    player.showMaximized()
    player.seek_track.toggle_cat()
    qapp.processEvents()
    player.easter_eggs.enter_minus()
    assert not player.isMaximized()
    assert not player.seek_track.cat.timer.isActive()
    player.easter_eggs.return_to_plus()
    qapp.processEvents()
    assert player.isMaximized()
    assert player.seek_track.cat_enabled
    assert player.seek_track.cat.isVisible()


def test_minus_hides_extra_windows_until_return(player, qapp):
    from PySide6.QtWidgets import QWidget

    floating = QWidget()
    hidden = QWidget()
    try:
        floating.show()
        qapp.processEvents()
        player.easter_eggs.enter_minus()
        assert not floating.isVisible()
        floating.show()
        assert not floating.isVisible()
        hidden.show()
        assert not hidden.isVisible()
        player.easter_eggs.return_to_plus()
        assert floating.isVisible()
        assert not hidden.isVisible()
    finally:
        floating.close()
        hidden.close()
        floating.deleteLater()
        hidden.deleteLater()
