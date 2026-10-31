from math import hypot

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QImage, QPainter

from assets.scarlet.danmaku import Bullet, DanmakuGame, EnemyDeathRing, HitParticle, WarmupEnemy
from assets.scarlet.danmaku_scene import ScarletScene


def advance(game, seconds):
    while seconds > 1e-8:
        step = min(seconds, .1)
        game.update(step)
        seconds -= step


def warmup_game(hp=2):
    game = DanmakuGame()
    game.start_warmup()
    game._warmup_spawn_in = 100
    game.enemies = [WarmupEnemy(120, 120, hp=hp, fire_in=100)]
    return game


def hit_enemy(game):
    enemy = game.enemies[0]
    game.shots = [Bullet(enemy.x, enemy.y, 0, 0)]
    game.update(1 / 120)


def test_hit_particles_move_outward_and_expire_within_half_a_second():
    game = warmup_game()
    hit_enemy(game)
    assert game.enemies[0].hp == 1
    assert game.hit_particles and not game.enemy_death_rings
    origin = (game.enemies[0].x, game.enemies[0].y)
    particles = list(game.hit_particles)
    before = [hypot(p.x - origin[0], p.y - origin[1]) for p in particles]
    advance(game, .2)
    for particle, distance in zip(particles, before):
        assert hypot(particle.x - origin[0], particle.y - origin[1]) > distance
        assert 0 < particle.remaining < .5
    advance(game, .3)
    assert not game.hit_particles


def test_lethal_hit_makes_one_ring_and_one_set_of_drops_despite_simultaneous_shots():
    game = warmup_game(hp=1)
    game.shots = [Bullet(120, 120, 0, 0), Bullet(120, 120, 0, 0)]
    game.update(1 / 120)
    assert not game.enemies
    assert len(game.enemy_death_rings) == 1
    assert len(game.items) == 2 and game.score == 25
    assert game.hit_particles
    ring = game.enemy_death_rings[0]
    position = (ring.x, ring.y)
    advance(game, .25)
    assert (ring.x, ring.y) == position and ring.remaining == pytest.approx(.25)
    advance(game, .25)
    assert not game.enemy_death_rings and not game.hit_particles


def test_bomb_defeats_each_fairy_with_its_own_ring():
    game = warmup_game()
    game.enemies.append(WarmupEnemy(240, 120))
    game.bomb()
    assert not game.enemies
    assert {(ring.x, ring.y) for ring in game.enemy_death_rings} == {(120, 120), (240, 120)}
    assert len(game.items) == 4 and game.hit_particles


@pytest.mark.parametrize("protected", [False, True])
def test_boss_hit_particles_only_appear_when_damage_is_dealt(protected):
    game = DanmakuGame()
    game.start_or_pause()
    game._spawn_in = 100
    game.hp_fill_remaining = game.HP_FILL_DURATION if protected else 0
    game.wave = 1
    game.shots = [Bullet(*game.boss, 0, 0)]
    game.update(1 / 120)
    assert bool(game.hit_particles) is not protected
    assert not game.enemy_death_rings


@pytest.mark.parametrize("cleanup", ["reset", "prepare_boss"])
def test_effects_freeze_when_paused_and_clear_on_restart_or_boss_entrance(cleanup):
    game = warmup_game(hp=1)
    hit_enemy(game)
    before = [(p.x, p.y, p.remaining) for p in game.hit_particles]
    ring_time = game.enemy_death_rings[0].remaining
    game.pause()
    advance(game, 1)
    assert [(p.x, p.y, p.remaining) for p in game.hit_particles] == before
    assert game.enemy_death_rings[0].remaining == ring_time
    getattr(game, cleanup)()
    assert not game.hit_particles and not game.enemy_death_rings


def render_effects(game):
    image = QImage(160, 160, QImage.Format.Format_ARGB32)
    image.fill(Qt.GlobalColor.transparent)
    painter = QPainter(image)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    try:
        ScarletScene.enemy_effects(painter, game)
    finally:
        painter.end()
    return image


def test_death_ring_grows_fades_and_has_a_feathered_transparent_center(qapp):
    game = DanmakuGame()
    ring = EnemyDeathRing(80, 80)
    game.enemy_death_rings = [ring]
    widths, opacity = [], []
    for remaining in (.5, .3, .1):
        ring.remaining = remaining
        image = render_effects(game)
        assert image.pixelColor(80, 80).alpha() == 0
        pixels = [(x, image.pixelColor(x, y).alpha())
                  for y in range(160) for x in range(160) if image.pixelColor(x, y).alpha()]
        widths.append(max(x for x, alpha in pixels) - min(x for x, alpha in pixels))
        opacity.append(max(alpha for x, alpha in pixels))
        row = [image.pixelColor(x, 80).alpha() for x in range(80, 140)]
        peak = row.index(max(row))
        assert row[peak] > row[peak - 2] > 0
        assert row[peak] > row[peak + 2] > 0
    assert widths[0] < widths[1] < widths[2]
    assert 255 > opacity[0] > opacity[1] > opacity[2] > 0


def test_hit_circles_are_soft_red_translucent_and_fade_to_transparent(qapp):
    game = DanmakuGame()
    particle = HitParticle(80, 80, 0, 0, 8)
    game.hit_particles = [particle]
    opacity = []
    for remaining in (.5, .25, 0):
        particle.remaining = remaining
        image = render_effects(game)
        color = image.pixelColor(80, 80)
        opacity.append(color.alpha())
        assert image.pixelColor(92, 80).alpha() == 0
        if remaining:
            assert color.red() > color.green() and color.red() > color.blue()
            assert 0 < image.pixelColor(85, 80).alpha() < color.alpha()
    assert 255 > opacity[0] > opacity[1] > opacity[2] == 0
