"""Mechanical acceptance tests for the Extra boss (no original-data timing claims)."""

from math import cos, hypot, pi, sin

import pytest

from assets.scarlet.danmaku import Bullet, DanmakuGame, HomingShot, PowerItem, StarItem
from assets.scarlet.flandre import BLUE, GREEN, PURPLE, BossState, Laser, PHASES, Projectile, SPELLS


def advance(game, seconds, step=.1, **controls):
    while seconds > 1e-8:
        dt = min(step, seconds)
        game.update(dt, **controls)
        seconds -= dt


def practice(phase, difficulty="Normal"):
    game = DanmakuGame(difficulty, debug=True)
    game.start_phase(phase)
    game.debug_invulnerable = True
    return game


def test_sequence_has_eight_nonspells_then_consecutive_last_three_spells():
    assert len(PHASES) == 18
    assert [p.spell for p in PHASES] == [0, 1, 0, 2, 0, 3, 0, 4, 0, 5, 0, 6, 0, 7, 0, 8, 9, 10]
    assert len({p.id for p in PHASES}) == 18
    assert all(p.english and p.japanese for p in SPELLS)


@pytest.mark.parametrize("phase", PHASES, ids=lambda p: p.id)
@pytest.mark.parametrize("difficulty", DanmakuGame.DIFFICULTIES)
def test_each_phase_runs_its_full_timer_with_bounded_projectiles_and_terminates(phase, difficulty):
    game = practice(phase.id, difficulty)
    peak = 0
    for _ in range(round((phase.seconds + game.HP_FILL_DURATION + 1) * 10)):
        game.update(.1)
        peak = max(peak, len(game.bullets))
        assert len(game.bullets) < 1800
        assert len(game.shots) < 200
    assert peak > 0
    assert not game.encounter.running
    assert not game.encounter.lasers and not game.encounter.clones and not game.bullets
    if phase.damage_finish:
        assert game.state == "lost"  # A final-card timeout is not a victory.
    else:
        assert game.transition_remaining > 0
        assert game.phase == PHASES.index(phase) + 1


def test_damage_can_clear_every_ordinary_phase_but_cannot_clear_survival():
    game = practice(0)
    for index, phase in enumerate(PHASES):
        assert game.phase == index
        advance(game, 1.5)
        if phase.survival:
            game.hp = 0
            game._check_win()
            assert not game.transition_remaining
            game.phase_elapsed = phase.seconds
        else:
            game.hp = 0
        game._check_win()
        assert game.transition_remaining == game.PHASE_BREAK
        assert not game.encounter.running
        advance(game, game.PHASE_BREAK)
    assert game.state == "won" and game.boss_state == BossState.DEFEATED
    assert len(game.encounter.captures) == 10
    score = game.score
    game.update(.1)
    game.bomb()
    assert game.score == score


@pytest.mark.parametrize("phase", SPELLS, ids=lambda p: p.id)
def test_clearing_cancels_all_spawners_hazards_and_trails_for_entire_break(phase):
    game = practice(phase.id)
    advance(game, 4)
    game.bullets.append(Projectile(30, 40, 0, 0, phase_id=phase.id))
    count = len(game.bullets)
    if phase.survival:
        game.phase_elapsed = phase.seconds
    else:
        game.hp = 0
    game._check_win()
    assert sum(isinstance(item, StarItem) for item in game.items) == count
    assert not game.encounter.lasers and not game.encounter.clones
    for _ in range(39):
        game.update(.1)
        assert not game.bullets
    assert game.transition_remaining > 0
    assert game.boss == pytest.approx((192, 86))


def test_cranberry_has_boundary_convergence_and_snapshot_aim():
    game = practice("cranberry")
    encounter = game.encounter
    encounter.start()
    purple = [p for p in game.bullets if p.color == PURPLE]
    assert purple and all(p.x in (5, 379) for p in purple)
    assert all(p.vx > 0 if p.x == 5 else p.vx < 0 for p in purple)
    game.bullets.clear()
    encounter.frame = 24
    encounter.tick()
    blue = [p for p in game.bullets if p.color == BLUE]
    assert len(blue) == 6
    for b in (blue[1], blue[4]):
        assert b.vx * (game.y - b.y) == pytest.approx(b.vy * (game.x - b.x))
    direction = (blue[1].vx, blue[1].vy)
    game.x += 70
    encounter.advance_projectile(blue[1], .1)
    assert (blue[1].vx, blue[1].vy) == direction


def test_sword_sweeps_leaves_trails_and_uses_capsule_collision():
    game = practice("laevateinn")
    advance(game, 1.4)
    sword = game.encounter.lasers[0]
    angle = sword.angle
    advance(game, .4)
    assert sword.active and sword.angle > angle and game.bullets
    ax, ay, bx, by = sword.segment
    assert sword.hits((ax + bx) / 2, (ay + by) / 2)
    assert not sword.hits(sword.x - 45 * cos(sword.angle), sword.y - 45 * sin(sword.angle))
    assert not sword.hits((ax + bx) / 2 - 30 * sin(sword.angle), (ay + by) / 2 + 30 * cos(sword.angle))


def test_clone_damage_is_local_and_stops_only_its_scheduler():
    game = practice("four")
    advance(game, 1.6)
    assert len(game.encounter.clones) == 3
    clone = game.encounter.clones[0]
    game.shots = [Bullet(clone.x, clone.y, 0, 0) for _ in range(clone.hp)]
    hp = game.hp
    game.update(1 / 120)
    assert len(game.encounter.clones) == 2 and game.hp == hp
    assert any(isinstance(i, PowerItem) and i.value == 32 for i in game.items)
    assert game.enemy_death_rings
    game.bullets.clear()
    game.encounter.frame = 240  # Yellow's next special volley.
    game.encounter.tick()
    assert not any(p.color == 3 for p in game.bullets)
    advance(game, 2)
    assert game.hp == hp and len(game.encounter.clones) == 2


def test_four_of_a_kind_has_exact_eight_and_seven_way_specials_and_three_blue_rings():
    game = practice("four")
    encounter = game.encounter
    encounter.start()
    counts = []
    for frame, color in ((60, 3), (90, GREEN), (72, BLUE), (90, BLUE), (108, BLUE)):
        game.bullets.clear()
        encounter.frame = frame
        encounter.tick()
        counts.append(len([p for p in game.bullets if p.color == color]))
    assert counts == [8, 7, 20, 20, 20]


def test_yellow_attack_deforms_existing_green_cage_in_firing_direction():
    game = practice("kagome")
    game.encounter.start()
    cage = list(game.bullets)
    assert all(p.color == GREEN and p.kind == "cage" for p in cage)
    game.encounter.frame = 60
    game.encounter.tick()
    affected = [p for p in cage if hypot(p.bend_x, p.bend_y) > 1]
    assert affected and len(cage) == len([p for p in game.bullets if p.kind == "cage"])
    b = affected[0]
    old = b.x, b.y
    b.advance(.2, 384, 448)
    assert hypot(b.x - old[0] - b.vx * .2, b.y - old[1] - b.vy * .2) > 0


@pytest.mark.parametrize("phase", ["maze", "starbow", "ripples"])
def test_patterns_reproduce_for_same_seed_and_different_render_rates(phase):
    games = [practice(phase) for _ in range(2)]
    advance(games[0], 8, step=1 / 30)
    advance(games[1], 8, step=1 / 60)
    assert games[0].encounter.frame == games[1].encounter.frame
    assert len(games[0].bullets) == len(games[1].bullets)
    for a, b in zip(games[0].bullets, games[1].bullets):
        assert (a.x, a.y, a.vx, a.vy) == pytest.approx((b.x, b.y, b.vx, b.vy))


def test_starbow_bullets_rise_apex_then_fall_and_seed_changes_launches():
    game = practice("starbow")
    game.encounter.start()
    bullet = game.bullets[8]
    assert bullet.vy < 0 and len({p.color for p in game.bullets}) == 7
    y = bullet.y
    for _ in range(180):
        bullet.advance(1 / 60, 384, 448)
    assert bullet.y < y and bullet.vy > 0
    other = practice("starbow")
    other.start_phase("starbow", seed=123)
    other.encounter.start()
    assert other.bullets[0].x != game.bullets[0].x


def test_reflections_mirror_overshoot_preserve_other_component_and_exit_bottom():
    p = Projectile(376, 100, 120, 40, radius=9, reflect=True)
    assert p.advance(.1, 384, 448)
    assert p.vx == -120 and p.vy == 40 and p.x == pytest.approx(362)
    p = Projectile(200, 8, 30, -100, radius=9, reflect=True)
    p.advance(.1, 384, 448)
    assert p.vx == 30 and p.vy == 100 and p.y == 20
    for _ in range(600):
        alive = p.advance(1 / 60, 384, 448)
        if not alive:
            break
    assert not alive and p.y > 448 and p.bounces <= p.max_bounces


def test_catadioptric_parents_emit_independent_trails_after_bouncing():
    game = practice("catadioptric")
    advance(game, 2)
    parents = [p for p in game.bullets if p.kind == "trail_parent"]
    assert parents and any(p.bounces > 0 for p in parents)
    assert any(p.radius == 3 and not p.reflect for p in game.bullets)


def test_clock_lasers_share_moving_center_and_quarter_turn_offsets():
    game = practice("clock")
    advance(game, 2)
    beams = game.encounter.lasers
    assert len(beams) == 4 and all(l.active for l in beams)
    assert len({(l.x, l.y) for l in beams}) == 1
    assert [l.angle - beams[0].angle for l in beams] == pytest.approx([0, pi / 2, pi, 3 * pi / 2])
    x, angle = beams[0].x, beams[0].angle
    advance(game, .4)
    assert beams[0].x != x and beams[0].angle != angle
    assert any(p.color == 0 for p in game.bullets)
    game.bomb()
    assert not any(l.active for l in beams) and not game.bullets


def test_survival_hides_target_ignores_shots_and_bombs_and_folds_formations():
    game = practice("none")
    advance(game, 1.5)
    assert not game.boss_visible and not game.boss_vulnerable
    hp = game.hp
    before = list(game.encounter.emitters)
    game.shots = [Bullet(*game.boss, 0, 0), HomingShot(192, 150, 0, -360)]
    game.bomb()
    advance(game, .5)
    assert game.hp == hp and game.encounter.emitters != before
    assert game.shots and not game.transition_remaining
    advance(game, 32)
    assert any(p.kind == "fold" and p.folded for p in game.bullets)
    game.phase_elapsed = game.PHASE_DURATION
    game._check_win()
    assert game.boss_visible and game.phase_definition.id == "ripples"
    assert game.encounter.captures[-1] == ("none", False)


def test_ripples_escalate_with_damage_and_timeout_cannot_award_victory():
    games = [practice("ripples") for _ in range(2)]
    games[1].hp = 100
    for g in games:
        advance(g, 6)
    assert len(games[1].bullets) > len(games[0].bullets)
    assert max(hypot(b.vx, b.vy) for b in games[1].bullets) > max(hypot(b.vx, b.vy) for b in games[0].bullets)
    games[0].phase_elapsed = games[0].PHASE_DURATION
    games[0]._check_win()
    assert games[0].state == "lost"
    games[1].hp = 0
    games[1]._check_win()
    advance(games[1], games[1].PHASE_BREAK)
    assert games[1].state == "won"


@pytest.mark.parametrize("disqualifier", ["none", "bomb", "death", "timeout"])
def test_capture_requires_damage_clear_without_death_or_bomb(disqualifier):
    game = practice("cranberry")
    advance(game, 1.5)
    if disqualifier == "bomb":
        game.bomb()
    elif disqualifier == "death":
        game._pending_hit_position = (game.x, game.y)
        game._commit_hit()
        advance(game, .5)
    if disqualifier == "timeout":
        game.phase_elapsed = game.PHASE_DURATION
    else:
        game.hp = 0
    game._check_win()
    assert game.encounter.captures[-1] == ("cranberry", disqualifier == "none")


def test_practice_restart_debug_controls_pause_and_phase_owned_cleanup():
    game = practice("clock")
    advance(game, 2)
    game.pause()
    before = game.encounter.frame, game.phase_elapsed, game.encounter.lasers[0].angle
    advance(game, 1)
    assert before == (game.encounter.frame, game.phase_elapsed, game.encounter.lasers[0].angle)
    game.debug_command("restart")
    assert not game.bullets and not game.encounter.lasers and game.hp == game.MAX_HP
    game.debug_command("next")
    assert game.phase_definition.id == "none"
    game.debug_command("slow")
    advance(game, 1)
    assert game.boss_elapsed == pytest.approx(.25)
    with pytest.raises(RuntimeError):
        DanmakuGame().debug_command("next")


def test_all_spells_render_with_matching_laser_geometry(qapp):
    from PySide6.QtGui import QImage, QPainter
    from assets.scarlet.danmaku_game import ReimuSprite
    from assets.scarlet.danmaku_scene import ScarletScene

    scene, sprite = ScarletScene(), ReimuSprite()
    scene.story_phase, scene.boss_position = "combat", None
    for phase in SPELLS:
        game = practice(phase.id)
        advance(game, 4)
        image = QImage(640, 480, QImage.Format.Format_ARGB32)
        image.fill(0)
        painter = QPainter(image)
        try:
            scene.paint(painter, game, sprite, 0, 0, 0, 60)
        finally:
            painter.end()
        assert not image.isNull()


@pytest.mark.parametrize("spell", ["laevateinn", "clock"])
def test_active_laser_collides_with_player_and_bomb_disables_it(spell):
    game = practice(spell)
    advance(game, 1.5)
    laser = game.encounter.lasers[0]
    ax, ay, bx, by = laser.segment
    game.x, game.y = ax + (bx - ax) * .25, ay + (by - ay) * .25
    game.bullets.clear()
    game.debug_invulnerable = False
    game.update(1 / 120)
    assert game._pending_hit_position is not None
    game.bomb()
    assert game._pending_hit_position is None and not any(l.active for l in game.encounter.lasers)
    assert game.lives == 3 and game.bombs == 2


@pytest.mark.parametrize("phase", [p for p in SPELLS if not p.survival], ids=lambda p: p.id)
def test_shots_and_bombs_damage_each_non_survival_spell(phase):
    game = practice(phase.id)
    advance(game, 2)
    hp = game.hp
    game.shots = [Bullet(*game.boss, 0, 0)]
    game.update(1 / 120)
    assert game.hp == hp - 1
    game.bomb()
    assert game.hp == hp - 25


def test_survival_has_no_invisible_boss_contact_and_stale_phase_bullets_are_discarded():
    game = practice("none")
    advance(game, 2)
    game.debug_invulnerable = False
    game.x, game.y = game.boss
    game.bullets = [Projectile(192, 86, 0, 0, phase_id="cranberry")]
    game.update(1 / 120)
    assert game._pending_hit_position is None and not game.bullets


def test_debug_keys_restart_advance_toggle_invulnerability_and_slow(qapp, monkeypatch, fake_midi_factory, fake_sound_effects):
    from PySide6.QtCore import Qt
    from PySide6.QtTest import QTest
    from assets.scarlet.danmaku_game import DanmakuWindow

    monkeypatch.setattr("assets.scarlet.boss_music.make_midi_backend", fake_midi_factory)
    monkeypatch.setattr("assets.scarlet.sound_effects.make_sound_effect", fake_sound_effects.factory)
    window = DanmakuWindow(debug=True)
    try:
        window.show()
        qapp.processEvents()
        window.timer.stop()
        window.game.start_phase("clock")
        QTest.keyClick(window, Qt.Key.Key_F6)
        assert window.story_phase == "combat" and window.game.phase_definition.id == "none"
        QTest.keyClick(window, Qt.Key.Key_F7)
        assert window.game.debug_invulnerable
        QTest.keyClick(window, Qt.Key.Key_F8)
        assert window.game.debug_speed == .25
        advance(window.game, 1)
        QTest.keyClick(window, Qt.Key.Key_F5)
        assert window.game.elapsed == 0 and window.game.hp == window.game.MAX_HP
        window.game.score = 99999
        window._save_high_score()
        assert window.high_score == 0
    finally:
        window.close()
        window.deleteLater()
