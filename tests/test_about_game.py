from math import hypot
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontMetrics
from PySide6.QtWidgets import QApplication, QTabWidget

from core.app_info import APP_VERSION
from core.danmaku import Bullet, DanmakuGame, PointItem
from core.settings import load_all_settings, save_all_settings
from ui.about_tab import AboutTab, debug_info
from ui.danmaku_game import DanmakuWindow, ReimuSprite
from ui.danmaku_scene import DIALOGUE
from ui.settings_dialog import SettingsDialog


def test_diagonal_movement_focus_and_bounds():
    game = DanmakuGame()
    game.start_or_pause()
    x, y = game.x, game.y
    game.update(0.1, 1, -1)
    assert hypot(game.x - x, game.y - y) == pytest.approx(24.5)
    x, y = game.x, game.y
    game.update(0.1, 1, -1, focus=True)
    assert hypot(game.x - x, game.y - y) == pytest.approx(9.5)
    game.x, game.y = game.WIDTH - 13, game.HEIGHT - 13
    game.update(0.1, 1, 1)
    assert game.x == game.WIDTH - 12
    assert game.y == game.HEIGHT - 12


def test_hit_invulnerability_and_game_over():
    game = DanmakuGame()
    game.start_or_pause()
    for lives in (2, 1, 0):
        game.invulnerable = 0
        game.bullets = [Bullet(game.x, game.y, 0, 0)]
        game.update(1 / 120)
        assert game.lives == lives
        assert game.bullets == []
        if lives:
            game.bullets = [Bullet(game.x, game.y, 0, 0)]
            game.update(1 / 120)
            assert game.lives == lives
    assert game.state == "lost"


def test_graze_scores_only_once_and_bomb_clears_bullets():
    game = DanmakuGame()
    game.start_or_pause()
    game.bullets = [Bullet(game.x + 18, game.y, 0, 0)]
    game.update(0.05)
    game.update(0.05)
    assert game.graze == 1
    assert game.score == 100
    game.bomb()
    assert game.bombs == 2
    assert not game.bullets
    assert game.hp == game.MAX_HP - 24
    assert game.invulnerable > 0
    for _ in range(5):
        game.bomb()
    assert game.bombs == 0
    assert game.hp == game.MAX_HP - 72


def test_shots_hit_boss_and_award_clear_bonus_once():
    game = DanmakuGame()
    game.start_or_pause()
    game.hp = 1
    bx, by = game.boss
    game.shots = [Bullet(bx, by + 5, 0, -600)]
    game.update(1 / 120)
    assert game.state == "won"
    score = game.score
    assert score == 25 + 3 * 2000 + 3 * 500
    game.update(1)
    game.bomb()
    assert game.score == score


def test_boss_patterns_advance_with_damage():
    game = DanmakuGame()
    game.hp = 240
    assert game.phase == 1
    game.hp = 120
    assert game.phase == 2


def test_patterns_timeout_and_pause():
    game = DanmakuGame()
    game.start_or_pause()
    for phase in range(3):
        game.elapsed = phase * 25
        game._spawn()
        assert game.phase == phase
    assert len(game.bullets) == 20 + 24 + 27
    game.pause()
    elapsed = game.elapsed
    game.update(0.1, 1, 1, shooting=True)
    assert game.elapsed == elapsed
    game.bomb()
    assert game.bombs == 3
    game.start_or_pause()
    game.elapsed = game.DURATION - 0.01
    game.update(0.02)
    assert game.state == "won"


def test_full_fight_keeps_bullet_population_bounded():
    game = DanmakuGame()
    game.start_or_pause()
    for _ in range(750):
        game.invulnerable = 1
        game.update(0.1)
        assert len(game.bullets) < 500
    assert game.state == "won"


def test_debug_info_contains_runtime_and_loaded_plugins_only(qapp):
    manager = SimpleNamespace(
        plugins={"demo": SimpleNamespace(metadata={"version": "1.2"})},
        disabled_plugins={"disabled"},
        secret_media_path="private/song.mp3",
    )
    info = debug_info(manager)
    assert APP_VERSION in info
    assert "demo: 1.2" in info
    assert "Qt:" in info
    assert "private" not in info
    assert "disabled" not in info


def test_about_unlock_reuses_game_and_closing_settings_stops_timer(qapp):
    settings = SettingsDialog([], {})
    try:
        settings.show()
        tabs = settings.findChild(QTabWidget)
        tabs.setCurrentWidget(settings.about_tab)
        qapp.processEvents()
        about = settings.about_tab
        for _ in range(6):
            QTest.mouseClick(about.logo, Qt.MouseButton.LeftButton)
        assert about.game_window is None
        QTest.mouseClick(about.logo, Qt.MouseButton.LeftButton)
        qapp.processEvents()
        window = about.game_window
        assert window.isVisible()
        assert window.timer.isActive()
        QTest.keyClick(window, Qt.Key.Key_Escape)  # Skip the introduction.
        assert window.game.state == "playing"
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert window.isVisible()
        assert window.game.state == "paused"
        window.close()
        assert not window.timer.isActive()
        for _ in range(7):
            about.logo.click()
        assert about.game_window is window
        assert window.timer.isActive()
        settings.close()
        qapp.processEvents()
        assert not window.isVisible()
        assert not window.timer.isActive()
    finally:
        settings.close()
        settings.deleteLater()


def test_game_deactivation_clears_held_keys_and_pauses(qapp):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        QTest.keyClick(window, Qt.Key.Key_Escape)
        QTest.keyPress(window, Qt.Key.Key_Left)
        assert window.keys
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowDeactivate))
        assert window.game.state == "paused"
        assert not window.keys
        window.game.bullets = [Bullet(220, 300, 0, 100)]
        assert not window.grab().isNull()
    finally:
        window.close()
        window.deleteLater()


def test_game_accepts_controls_inside_modal_settings(qapp):
    settings = SettingsDialog([], {})
    failures = []

    def play():
        try:
            settings.findChild(QTabWidget).setCurrentWidget(settings.about_tab)
            for _ in range(7):
                QTest.mouseClick(settings.about_tab.logo, Qt.MouseButton.LeftButton)
            window = settings.about_tab.game_window
            assert window.isVisible()
            QTest.keyClick(window, Qt.Key.Key_Escape)
            assert window.game.state == "playing"
            QTest.keyClick(window, Qt.Key.Key_X)
            assert window.game.bombs == 2
            QTest.keyClick(window, Qt.Key.Key_Escape)
            assert window.game.state == "paused"
            window.close()
        except Exception as error:
            failures.append(error)
        finally:
            settings.reject()

    QTimer.singleShot(0, play)
    try:
        settings.exec()
        if failures:
            raise failures[0]
    finally:
        settings.deleteLater()


def test_copy_debug_info(qapp):
    about = AboutTab()
    try:
        about.copy_button.click()
        assert APP_VERSION in QApplication.clipboard().text()
        assert about.copy_button.text() == "Copied!"
    finally:
        about.deleteLater()


def test_reimu_sheet_has_transparent_animated_flight_and_banking(qapp):
    sprite = ReimuSprite()
    assert not sprite.sheet.isNull()
    sheet = sprite.sheet.toImage()
    assert sheet.hasAlphaChannel()
    assert sheet.pixelColor(0, 0).alpha() == 0
    poses = []
    elapsed = 0.0
    for direction in (0, -1, 1):
        frames = []
        for i in range(24):
            rect = sprite.frame_rect(elapsed, direction)
            assert sprite.sheet.rect().contains(rect)
            assert rect.bottom() < 144  # Never include the spare effects below.
            frame = sprite.sheet.copy(rect).toImage()
            assert any(frame.pixelColor(x, y).alpha() for x in range(32) for y in range(48))
            frames.append(frame)
            elapsed += 0.1
        assert frames[0] != frames[1]
        poses.append(frames[0])
    assert poses[0] != poses[1]
    assert poses[1] != poses[2]


def test_reimu_animation_freezes_with_simulation_and_resets(qapp):
    sprite = ReimuSprite()
    sprite.frame_rect(0, -1)
    banked = sprite.frame_rect(1.5, -1)
    assert sprite.frame_rect(1.5, -1) == banked
    assert sprite.frame_rect(0, 0).x() == 0
    assert sprite.frame_rect(0, 0).y() == 0
    assert sprite.frame_rect(0.1, 0).x() > 0


def test_japanese_dialogue_advances_without_firing_or_running_simulation(qapp):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        for index in range(len(DIALOGUE)):
            assert window.game.state == "ready"
            assert window.dialogue_index == index
            window.game.update(.1, shooting=True)
            assert window.game.elapsed == 0
            assert not window.game.shots
            assert not window.grab().isNull()
            QTest.keyClick(window, Qt.Key.Key_Z)
        assert window.game.state == "playing"
        assert not window.keys
        assert window.game.bombs == 3
        QTest.keyClick(window, Qt.Key.Key_R)
        assert window.game.state == "ready"
        assert window.dialogue_index == 0
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert window.game.state == "playing"
    finally:
        window.close()
        window.deleteLater()


def test_japanese_font_and_portrait_assets_are_available(qapp):
    window = DanmakuWindow()
    try:
        metrics = QFontMetrics(window.scene.font)
        for char in "最高得点博麗霊夢紅残響満身創痍":
            assert metrics.inFontUcs4(ord(char))
        for portrait in window.scene.portraits.values():
            assert not portrait.isNull()
            assert portrait.hasAlphaChannel()
    finally:
        window.close()
        window.deleteLater()


def test_high_score_survives_reopening_without_overwriting_other_settings(qapp):
    payload = load_all_settings()
    payload["appearance"] = {"theme_mode": "dark", "theme_color": "#4477cc"}
    payload["easter_eggs"] = {"other_secret": True}
    save_all_settings(payload)
    window = DanmakuWindow()
    try:
        window.game.score = 12345
        window._save_high_score()
        window.game.score = 25
        window._save_high_score()
        saved = load_all_settings()
        assert saved["easter_eggs"] == {"other_secret": True, "scarlet_high_score": 12345}
        assert saved["appearance"] == payload["appearance"]
        reopened = DanmakuWindow()
        try:
            assert reopened.high_score == 12345
        finally:
            reopened.close()
            reopened.deleteLater()
    finally:
        window.close()
        window.deleteLater()


def test_point_items_drop_and_can_be_collected():
    game = DanmakuGame()
    game.start_or_pause()
    game.hp = 349
    bx, by = game.boss
    game.shots = [Bullet(bx, by + 5, 0, -600)]
    game.update(1 / 120)
    assert len(game.items) == 1
    assert game.point_items == 0
    game.items = [PointItem(game.x, game.y)]
    score = game.score
    game.update(1 / 120)
    assert game.point_items == 1
    assert game.score == score + 1000
    assert not game.items
