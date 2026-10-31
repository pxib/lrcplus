from math import hypot
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QEvent, Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtGui import QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QApplication, QTabWidget

from core.app_info import APP_VERSION
from assets.scarlet.danmaku import Bullet, DanmakuGame, HomingShot, PointItem, StarItem
from core.settings import load_all_settings, save_all_settings
from ui.about_tab import AboutTab, debug_info
from assets.scarlet.danmaku_game import DanmakuWindow, ReimuSprite
from assets.scarlet.danmaku_scene import DIALOGUE
from assets.scarlet.dialogue import BGM_CUE_INDEX, OUTRO_DIALOGUE
from ui.settings_dialog import SettingsDialog


@pytest.fixture(autouse=True)
def quiet_boss_theme(monkeypatch, fake_midi, fake_sound_effects):
    monkeypatch.setattr("assets.scarlet.boss_music.make_midi_backend", lambda path: fake_midi)
    monkeypatch.setattr("assets.scarlet.sound_effects.make_sound_effect", fake_sound_effects.factory)


def combat_game():
    """Arrange a boss whose opening health fill and first volley are complete."""
    game = DanmakuGame()
    game.hp_fill_remaining = 0
    game.wave = 1
    return game


def finish_break(game):
    for _ in range(40):
        game.update(.1)


def test_diagonal_movement_focus_and_bounds():
    game = combat_game()
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
    game = combat_game()
    game.start_or_pause()
    for lives in (2, 1, 0):
        game.invulnerable = 0
        hostile = Bullet(game.x, game.y, 0, 0)
        game.bullets = [hostile]
        game.update(1 / 120)
        game.update(game.DEATHBOMB_WINDOW)
        assert game.lives == lives
        assert game.bullets == [hostile]
        assert len([item for item in game.items if item.scattered]) == (3 - lives) * 10
        assert game.dying
        for _ in range(5):
            game.update(.1)
        if lives:
            game.bullets = [Bullet(game.x, game.y, 0, 0)]
            game.update(1 / 120)
            assert game.lives == lives
    assert game.state == "lost"


def test_graze_scores_only_once_and_bomb_clears_bullets():
    game = combat_game()
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
        for _ in range(20):
            game.update(.1)
        game.bomb()
    assert game.bombs == 0
    assert game.hp == game.MAX_HP - 72


def test_shots_hit_boss_and_award_clear_bonus_once():
    game = combat_game()
    game.start_or_pause()
    game._phase = 2
    game.hp = 1
    bx, by = game.boss
    game.shots = [Bullet(bx, by + 5, 0, -600)]
    game.update(1 / 120)
    assert game.transition_remaining == game.PHASE_BREAK
    finish_break(game)
    assert game.state == "won"
    score = game.score
    assert score == 25 + 3 * 2000 + 3 * 500
    game.update(1)
    game.bomb()
    assert game.score == score


def test_boss_patterns_advance_with_damage():
    game = combat_game()
    game.start_or_pause()
    for phase in range(3):
        if game.transition_remaining:
            finish_break(game)
        for _ in range(20):
            game.update(.1)
        assert game.phase == phase
        assert game.hp == game.MAX_HP
        game.hp = 1
        game.bomb()
        if phase < 2:
            assert game.state == "playing"
    finish_break(game)
    assert game.state == "won"


def test_patterns_timeout_and_pause():
    game = combat_game()
    game.start_or_pause()
    for phase in range(3):
        game._phase = phase
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
    game.phase_elapsed = game.PHASE_DURATION - 0.01
    game.update(0.02)
    finish_break(game)
    assert game.state == "won"


def test_full_fight_keeps_bullet_population_bounded():
    game = combat_game()
    game.start_or_pause()
    for _ in range(920):
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
        window._begin_entrance()
        window._update_story(window.ENTRANCE_DURATION)
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
        assert window.game.state == "playing"
        assert window.story_phase == "warmup"
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
    game = combat_game()
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


def test_hit_scatter_reaches_upper_screen_and_preserves_all_danmaku():
    game = combat_game()
    game.start_or_pause()
    game._spawn_in = 100
    colliding = Bullet(game.x, game.y, 0, 0)
    moving = Bullet(40, 100, 20, 30)
    game.bullets = [colliding, moving]
    hit_position = game.x, game.y
    game.update(1 / 120)
    assert game.bullets == [colliding, moving]
    assert not game.items
    game.update(game.DEATHBOMB_WINDOW)
    assert len(game.items) == 10
    assert all(hypot(item.x - hit_position[0], item.y - hit_position[1]) < 15 for item in game.items)
    assert all(item.vy < 0 and item.collect_delay > 0 for item in game.items)
    for _ in range(10):
        game.update(.1)
    assert len(game.items) == 10
    assert max(item.y for item in game.items) < 155
    assert all(item.vx == 0 and item.vy == game.ITEM_SPEED for item in game.items)
    assert max(item.x for item in game.items) - min(item.x for item in game.items) > 200
    assert game.bullets == [colliding, moving]
    assert moving.x > 40 and moving.y > 100
    assert game.lives == 2


@pytest.mark.parametrize("position", [(20, 420), (364, 420), (20, 24), (364, 24)])
def test_unfocused_homing_shots_reach_boss_from_either_side_or_above(position):
    game = combat_game()
    game.start_or_pause()
    game._spawn_in = 100
    game.invulnerable = 100
    game.x, game.y = position
    game.update(1 / 120, shooting=True)
    game.shots = [shot for shot in game.shots if isinstance(shot, HomingShot)]
    assert len(game.shots) == 5
    for _ in range(360):
        game.update(1 / 120)
    assert game.hp < game.MAX_HP


def test_focused_fire_does_not_launch_homing_shots():
    game = combat_game()
    game.start_or_pause()
    game.update(.1, focus=True, shooting=True)
    assert game.shots
    assert not any(isinstance(shot, HomingShot) for shot in game.shots)
    game.update(.1, focus=False, shooting=True)
    seekers = [shot for shot in game.shots if isinstance(shot, HomingShot)]
    assert len(seekers) == 5
    game.update(.1, focus=True, shooting=True)
    assert all(shot in game.shots for shot in seekers)
    assert len([shot for shot in game.shots if isinstance(shot, HomingShot)]) == 5


@pytest.mark.parametrize("distance", [45, 220])
def test_individual_shot_damage_is_independent_of_distance(distance):
    game = combat_game()
    game.start_or_pause()
    game._spawn_in = 100
    bx, by = game.boss
    game.shots = [Bullet(bx, by + distance, 0, -600)]
    for _ in range(100):
        game.update(1 / 120)
    assert game.hp == game.MAX_HP - 1


def test_shift_hitbox_fades_in_and_out_without_remaining_visible(qapp):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window.clock = SimpleNamespace(restart=lambda: 50)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert window.scene.focus_alpha == 0
        QTest.keyPress(window, Qt.Key.Key_Shift)
        window._tick()
        partial = window.scene.focus_alpha
        assert 0 < partial < 1
        for _ in range(3):
            window._tick()
        assert window.scene.focus_alpha == 1
        QTest.keyRelease(window, Qt.Key.Key_Shift)
        window._tick()
        assert 0 < window.scene.focus_alpha < 1
        for _ in range(4):
            window._tick()
        assert window.scene.focus_alpha == 0
        window._restart()
        assert window.scene.focus_alpha == 0
    finally:
        window.close()
        window.deleteLater()


def test_dialogue_portraits_transition_and_return_to_rest(qapp):
    window = DanmakuWindow()
    try:
        scene = window.scene
        scene.update_presentation(.05, False, 0, True)
        first = dict(scene.portrait_emphasis)
        assert 0 < first["flandre"] < 1
        assert first["reimu"] == 0
        scene.update_presentation(.05, False, 1, True)
        assert 0 < scene.portrait_emphasis["flandre"] < first["flandre"]
        assert 0 < scene.portrait_emphasis["reimu"] < 1
        for _ in range(20):
            scene.update_presentation(.05, False, 1, True)
        assert scene.portrait_emphasis == {"reimu": 1.0, "flandre": 0.0}
        scene.reset_presentation()
        assert scene.portrait_emphasis == {"reimu": 0.0, "flandre": 0.0}
    finally:
        window.close()
        window.deleteLater()


def test_boss_music_follows_dialogue_pause_mute_restart_and_close(qapp, fake_midi):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        assert fake_midi.calls == []
        window._begin_entrance()
        window._update_story(window.ENTRANCE_DURATION)
        for _ in DIALOGUE:
            QTest.keyClick(window, Qt.Key.Key_Z)
        assert fake_midi.calls == [("play", True)]
        QTest.keyClick(window, Qt.Key.Key_M)
        assert fake_midi.calls[-1] == ("pause",)
        assert "OFF" in window.scene.music_status
        QTest.keyClick(window, Qt.Key.Key_M)
        assert fake_midi.calls[-1] == ("play", False)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert fake_midi.calls[-1] == ("pause",)
        QTest.keyClick(window, Qt.Key.Key_Space)
        assert fake_midi.calls[-1] == ("play", False)
        QTest.keyClick(window, Qt.Key.Key_R)
        assert window.game.state == "playing"
        assert window.story_phase == "warmup"
        assert fake_midi.calls[-1] == ("close",)
        QTest.keyClick(window, Qt.Key.Key_Escape)
        assert fake_midi.calls[-1] == ("play", True)
        window.close()
        assert fake_midi.current_mode == "stopped"
        assert not window.music.timer.isActive()
    finally:
        window.close()
        window.deleteLater()


def test_flandre_enters_from_offscreen_before_dialogue_without_starting_combat(qapp, fake_midi):
    window = DanmakuWindow()
    try:
        window._begin_entrance()
        assert window.story_phase == "entrance"
        assert window.scene.boss_position[1] + 70 < 0
        window._advance_dialogue()
        assert window.story_phase == "entrance"
        window._update_story(window.ENTRANCE_DURATION / 2)
        middle = window.scene.boss_position[1]
        assert -90 < middle < window.game.boss[1]
        assert window.story_phase == "entrance"
        window._update_story(window.ENTRANCE_DURATION / 2)
        assert window.story_phase == "intro"
        assert window.scene.boss_position == window.game.boss
        assert window.game.elapsed == 0
        assert window.game.state == "ready"
        assert fake_midi.calls == []
    finally:
        window.close()
        window.deleteLater()


def test_entrance_pauses_on_focus_loss_and_resumes_on_activation(qapp):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._begin_entrance()
        window.clock = SimpleNamespace(restart=lambda: 100)
        window._pause()
        elapsed = window.entrance_elapsed
        window._tick()
        assert window.entrance_elapsed == elapsed
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
        window._tick()
        assert window.entrance_elapsed > elapsed
        assert window.game.elapsed == 0
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("kills_boss", [False, True])
def test_x_deathbomb_saves_last_life_and_only_starts_outro_on_clear(qapp, fake_midi, kills_boss):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._start_combat()
        window.game.lives = 1
        window.game._spawn_in = 100
        if kills_boss:
            window.game._phase = 2
            window.game.hp_fill_remaining = 0
            window.game.wave = 1
            window.game.hp = 24
        window.game.bullets = [Bullet(window.game.x, window.game.y, 0, 0)]
        window.game.update(1 / 120)
        window._check_battle_end()
        assert window.game.state == "playing"
        assert window.story_phase == "combat"
        QTest.keyClick(window, Qt.Key.Key_X)
        assert window.game.lives == 1
        assert window.game.bombs == 2
        assert all(isinstance(item, StarItem) for item in window.game.items)
        assert window.game.deathbomb_remaining == 0
        assert window.game.deathbomb_flash > 0
        if kills_boss:
            assert window.story_phase == "combat"
            finish_break(window.game)
            window._check_battle_end()
            assert window.story_phase == "outro"
            assert fake_midi.current_mode == "stopped"
        else:
            assert window.story_phase == "combat"
            assert fake_midi.current_mode == "playing"
    finally:
        window.close()
        window.deleteLater()


def test_sfx_follow_game_events_and_mute_pause_close(qapp, fake_sound_effects):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._begin_entrance()
        window._update_story(window.ENTRANCE_DURATION)
        QTest.keyClick(window, Qt.Key.Key_Z)
        assert "menuinteract" in fake_sound_effects.played
        window._start_combat()
        window.game._spawn()
        window._play_game_sounds()
        assert fake_sound_effects.played[-1] == "bossattack1"
        QTest.keyClick(window, Qt.Key.Key_M)
        assert window.sfx.muted
        assert not any(voice.playing for voice in fake_sound_effects.voices)
        count = len(fake_sound_effects.played)
        window.game._spawn()
        window._play_game_sounds()
        assert len(fake_sound_effects.played) == count
        QTest.keyClick(window, Qt.Key.Key_M)
        assert not window.sfx.muted
        window.sfx.play("itempickup")
        window._pause()
        assert not any(voice.playing for voice in fake_sound_effects.voices)
        window.sfx.play("graze")
        window.close()
        assert not any(voice.playing for voice in fake_sound_effects.voices)
    finally:
        window.close()
        window.deleteLater()


def test_game_over_silences_attack_voices_before_player_death(qapp, fake_sound_effects):
    window = DanmakuWindow()
    try:
        window._start_combat()
        window.sfx.play("bossattack1")
        window.game.lives = 1
        window.game.bombs = 0
        window.game.bullets = [Bullet(window.game.x, window.game.y, 0, 0)]
        window.game.update(1 / 120)
        window._play_game_sounds()
        assert fake_sound_effects.played[-1] == "playerdeath"
        active = [voice for voice in fake_sound_effects.voices if voice.playing]
        assert len(active) == 1
        assert active[0].source.toLocalFile().endswith("playerdeath.wav")
    finally:
        window.close()
        window.deleteLater()


def test_story_music_starts_at_supplied_cue_and_continues_into_combat(qapp, fake_midi):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._begin_entrance()
        window._update_story(window.ENTRANCE_DURATION)
        for _ in range(BGM_CUE_INDEX - 1):
            window._advance_dialogue()
        assert not fake_midi.calls
        window._advance_dialogue()
        assert window.dialogue_index == BGM_CUE_INDEX
        assert window.game.state == "ready"
        assert fake_midi.calls == [("play", True)]
        assert window.scene.music_cue_remaining > 0
        window._pause()
        assert fake_midi.calls[-1] == ("pause",)
        QApplication.sendEvent(window, QEvent(QEvent.Type.WindowActivate))
        assert fake_midi.calls[-1] == ("play", False)
        while window.story_phase == "intro":
            window._advance_dialogue()
        assert window.story_phase == "combat"
        assert window.game.state == "playing"
        assert len([call for call in fake_midi.calls if call == ("play", True)]) == 1
    finally:
        window.close()
        window.deleteLater()


def test_victory_plays_entire_outro_before_clear_screen(qapp, fake_midi):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._start_combat()
        window.game._phase = 2
        window.game.hp = 0
        window.game._check_win()
        finish_break(window.game)
        score = window.game.score
        window._check_battle_end()
        assert window.story_phase == "outro"
        assert window.scene.dialogue_script is OUTRO_DIALOGUE
        assert fake_midi.calls[-1] == ("close",)
        for index, line in enumerate(OUTRO_DIALOGUE):
            assert window.story_phase == "outro"
            assert window.dialogue_index == index
            assert line.text("ja") and line.text("en")
            window._advance_dialogue()
        assert window.story_phase == "complete"
        assert window.game.state == "won"
        assert window.game.score == score
        window._check_battle_end()
        assert window.story_phase == "complete"
    finally:
        window.close()
        window.deleteLater()


def test_dialogue_language_toggle_keeps_story_position_and_long_speeches_fit(qapp):
    window = DanmakuWindow()
    try:
        window._begin_entrance()
        window._update_story(window.ENTRANCE_DURATION)
        window.dialogue_index = 15
        assert window.scene.language == "ja"
        QTest.keyClick(window, Qt.Key.Key_L)
        assert window.scene.language == "en"
        assert window.dialogue_index == 15
        assert window.game.state == "ready"
        for language in ("ja", "en"):
            window.scene.language = language
            for line in (*DIALOGUE, *OUTRO_DIALOGUE):
                document = window.scene.dialogue_document(line)
                assert document.toPlainText() == line.text(language)
                assert document.size().height() + 35 < 350
        window._restart()
        assert window.scene.language == "en"
        assert window.story_phase == "warmup"
        QTest.keyClick(window, Qt.Key.Key_L)
        assert window.scene.language == "ja"
        assert len(DIALOGUE) == 26 and len(OUTRO_DIALOGUE) == 15
    finally:
        window.close()
        window.deleteLater()


def test_phase_changes_keep_music_playing_and_wait_for_third_bar_before_outro(qapp, fake_midi):
    window = DanmakuWindow()
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window._start_combat()
        calls = list(fake_midi.calls)
        for phase in range(3):
            window.game.hp = 0
            window.game._check_win()
            finish_break(window.game)
            window._check_battle_end()
            window._sync_music()
            if phase < 2:
                assert window.story_phase == "combat"
                assert window.game.hp == window.game.MAX_HP
                assert fake_midi.calls == calls
                assert fake_midi.current_mode == "playing"
            else:
                assert window.story_phase == "outro"
                assert fake_midi.current_mode == "stopped"
    finally:
        window.close()
        window.deleteLater()


def test_three_health_bars_show_spent_current_and_future_health(qapp):
    window = DanmakuWindow()
    try:
        window.scene.story_phase = "combat"
        window.scene.boss_position = None
        window.game.hp_fill_remaining = 0
        for phase in range(3):
            window.game._phase = phase
            window.game.hp = window.game.MAX_HP / 2
            image = QImage(640, 480, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            window.scene.paint(painter, window.game, window.player_sprite, 0, 0, 0, 60)
            painter.end()
            for bar in range(3):
                left = image.pixelColor(32 + 90, 16 + 4 + 5 * bar).name()
                right = image.pixelColor(32 + 300, 16 + 4 + 5 * bar).name()
                if bar < phase:
                    assert left == right == "#3b1526"
                elif bar == phase:
                    assert left == "#fff0d9" and right == "#3b1526"
                else:
                    assert left == right == "#92546b"
    finally:
        window.close()
        window.deleteLater()


def test_death_sprite_flattens_widens_and_fades_before_respawn(qapp):
    window = DanmakuWindow()
    try:
        game = window.game
        game.start_or_pause()
        game.x, game.y = 192, 310
        game.bombs = 0
        game.bullets = [Bullet(game.x, game.y, 0, 0)]
        game.update(1 / 120)
        game.items.clear()
        game.bullets.clear()
        bounds = []
        alpha = []
        for progress in (0, .5, .9):
            game.death_remaining = game.DEATH_DURATION * (1 - progress)
            image = QImage(384, 448, QImage.Format.Format_ARGB32)
            image.fill(Qt.GlobalColor.transparent)
            painter = QPainter(image)
            window.scene.actors(painter, game, window.player_sprite, 0)
            painter.end()
            pixels = [(x, y, image.pixelColor(x, y).alpha())
                      for y in range(275, 345) for x in range(100, 285)
                      if image.pixelColor(x, y).alpha()]
            bounds.append((max(p[0] for p in pixels) - min(p[0] for p in pixels),
                           max(p[1] for p in pixels) - min(p[1] for p in pixels)))
            alpha.append(max(p[2] for p in pixels))
        assert bounds[0][0] < bounds[1][0] < bounds[2][0]
        assert bounds[0][1] > bounds[1][1] > bounds[2][1]
        assert alpha[0] > alpha[1] > alpha[2] > 0
    finally:
        window.close()
        window.deleteLater()
