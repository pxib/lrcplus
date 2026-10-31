"""Small, deterministic bullet-hell simulation, independent of the GUI."""

from dataclasses import dataclass
from math import atan2, ceil, cos, hypot, pi, sin


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float
    color: int = 0
    grazed: bool = False


@dataclass
class HomingShot(Bullet):
    age: float = 0.0


@dataclass
class PointItem:
    x: float
    y: float
    vx: float = 0.0
    vy: float = 65.0
    gravity: float = 0.0
    collect_delay: float = 0.0


class DanmakuGame:
    WIDTH = 384
    HEIGHT = 448
    DURATION = 75.0
    MAX_HP = 360
    DEATHBOMB_WINDOW = .1  # 100 ms of active gameplay after a collision.
    PATTERNS = ("紅符「紅い螺旋」", "月符「夜に咲く花」", "終符「最後のリフレイン」")

    def __init__(self):
        self.reset()

    def reset(self):
        self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
        self.elapsed = 0.0
        self.lives = 3
        self.bombs = 3
        self.score = 0
        self.graze = 0
        self.hp = self.MAX_HP
        self.invulnerable = 0.0
        self.bomb_flash = 0.0
        self.deathbomb_flash = 0.0
        self.deathbomb_remaining = 0.0
        self._pending_hit_position = None
        self.bullets = []
        self.shots = []
        self.items = []
        self.point_items = 0
        self.state = "ready"
        self.wave = 0
        self._spawn_in = 1.0
        self._shot_in = 0.0
        self._homing_in = 0.0
        self._homing_wave = 0
        self._sound_events = []
        self._last_countdown_second = None

    def _sound(self, name):
        # Coalesce many substep collisions into one cue per rendered frame.
        if name not in self._sound_events:
            self._sound_events.append(name)

    def take_sound_events(self):
        events, self._sound_events = self._sound_events, []
        return events

    @property
    def boss(self):
        return self.WIDTH / 2 + 95 * sin(self.elapsed * 0.65), 86 + 12 * sin(self.elapsed)

    @property
    def phase(self):
        damage_phase = int((self.MAX_HP - self.hp) / (self.MAX_HP / 3))
        return min(2, max(int(self.elapsed / 25), damage_phase))

    def start_or_pause(self):
        if self.state in {"ready", "paused"}:
            self.state = "playing"
        elif self.state == "playing":
            self.state = "paused"

    def pause(self):
        if self.state == "playing":
            self.state = "paused"

    def bomb(self):
        if self.state != "playing" or self.bombs <= 0:
            return
        if self._pending_hit_position is not None:
            self._pending_hit_position = None
            self.deathbomb_remaining = 0.0
            self.deathbomb_flash = .65
            self._sound("extendlife")
        self.bombs -= 1
        self.score += len(self.bullets) * 10
        self.bullets.clear()
        self.hp = max(0, self.hp - 24)
        self.invulnerable = max(self.invulnerable, 2.0)
        self.bomb_flash = 0.65
        self._sound("bossattack2")
        self._check_win()

    def _check_win(self):
        if self.state == "won":
            return
        if self._pending_hit_position is not None:
            return  # Resolve the hit (or a saving bomb) before awarding the clear.
        if self.hp <= 0 or self.elapsed >= self.DURATION:
            self.state = "won"
            self.score += self.lives * 2000 + self.bombs * 500
            self._sound("bossdeath" if self.hp <= 0 else "timeout")

    def _spawn(self):
        bx, by = self.boss
        self._sound("bossattack1" if self.phase == 0 else "bossattack2")
        if self.phase == 0:
            count, speed = 20, 100
            offset = self.wave * 0.23
            angles = [offset + i * 2 * pi / count for i in range(count)]
        elif self.phase == 1:
            count, speed = 24, 120
            offset = self.wave * -0.16
            angles = [offset + i * 2 * pi / count for i in range(count)]
        else:
            speed = 145
            aim = atan2(self.y - by, self.x - bx)
            angles = [aim + i * 0.12 for i in range(-4, 5)]
            angles += [self.wave * 0.31 + i * 2 * pi / 18 for i in range(18)]
        for i, angle in enumerate(angles):
            velocity = speed + (18 * sin(i * 3 + self.wave) if self.phase == 1 else 0)
            self.bullets.append(Bullet(bx, by, cos(angle) * velocity,
                                       sin(angle) * velocity, (i + self.phase) % 3))
        self.wave += 1

    def _scatter_points(self, position):
        # Spray from the hit location toward the upper playfield. A short pickup
        # delay prevents the respawning player from collecting the whole burst.
        for i in range(10):
            horizontal = (i - 4.5) / 4.5
            self.items.append(PointItem(
                *position, vx=horizontal * 260,
                vy=-460 - 80 * (1 - abs(horizontal)),
                gravity=300, collect_delay=.6,
            ))

    def _commit_hit(self):
        position = self._pending_hit_position
        self._pending_hit_position = None
        self.deathbomb_remaining = 0.0
        self.lives -= 1
        self._sound("playerdeath")
        self.invulnerable = 2.5
        self._scatter_points(position)
        self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
        if self.lives <= 0:
            self.state = "lost"

    @staticmethod
    def _steer_homing_shot(shot, bx, by, dt):
        shot.age += dt
        heading = atan2(shot.vy, shot.vx)
        target = atan2(by - shot.y, bx - shot.x)
        difference = (target - heading + pi) % (2 * pi) - pi
        turn = max(-7 * dt, min(7 * dt, difference))
        shot.vx = 360 * cos(heading + turn)
        shot.vy = 360 * sin(heading + turn)

    def update(self, dt, dx=0, dy=0, focus=False, shooting=False):
        if self.state != "playing":
            return
        # Substeps keep collision detection reliable when frames arrive late.
        remaining = max(0.0, min(dt, 0.1))
        while remaining > 1e-8 and self.state == "playing":
            step = min(remaining, 1 / 120)
            self._step(step, dx, dy, focus, shooting)
            remaining -= step

    def _step(self, dt, dx, dy, focus, shooting):
        self.elapsed += dt
        countdown = ceil(max(0, self.DURATION - self.elapsed))
        if 1 <= countdown <= 10 and countdown != self._last_countdown_second:
            self._last_countdown_second = countdown
            self._sound("timeout")
        if self._pending_hit_position is not None:
            self.deathbomb_remaining = max(0.0, self.deathbomb_remaining - dt)
            if self.deathbomb_remaining <= 1e-9:
                self._commit_hit()
                if self.state == "lost":
                    return
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.bomb_flash = max(0.0, self.bomb_flash - dt)
        self.deathbomb_flash = max(0.0, self.deathbomb_flash - dt)
        length = max(1, hypot(dx, dy))
        speed = 95 if focus else 245
        self.x = max(12, min(self.WIDTH - 12, self.x + dx / length * speed * dt))
        self.y = max(24, min(self.HEIGHT - 12, self.y + dy / length * speed * dt))

        self._spawn_in -= dt
        if self._spawn_in <= 0:
            self._spawn()
            self._spawn_in += (0.65, 0.55, 0.5)[self.phase]
        self._shot_in = max(0.0, self._shot_in - dt)
        if shooting and self._shot_in <= 0:
            spread = 5 if focus else 11
            self.shots.extend([Bullet(self.x - spread, self.y - 14, 0, -600),
                               Bullet(self.x + spread, self.y - 14, 0, -600)])
            self._shot_in = 0.1

        self._homing_in = max(0.0, self._homing_in - dt)
        if shooting and not focus and self._homing_in <= 0:
            for i, side in enumerate((-1, 1)):
                self.shots.append(HomingShot(
                    self.x + side * 17, self.y - 6,
                    side * 270, -238, (self._homing_wave + i) % 4,
                ))
            self._homing_wave += 1
            self._homing_in = .24

        bx, by = self.boss
        shots = []
        for shot in self.shots:
            homing = isinstance(shot, HomingShot)
            if homing:
                self._steer_homing_shot(shot, bx, by, dt)
            shot.x += shot.vx * dt
            shot.y += shot.vy * dt
            if hypot(shot.x - bx, shot.y - by) < 28:
                if self.hp > 0:
                    self.hp -= 1
                    self.score += 25
                    self._sound("bossdamage")
                    if self.hp and self.hp % 12 == 0:
                        self.items.append(PointItem(bx, by))
            elif ((homing and shot.age < 4.5) or (not homing and shot.y > -20)):
                shots.append(shot)
        self.shots = shots

        items = []
        for item in self.items:
            item.collect_delay = max(0.0, item.collect_delay - dt)
            item.x += item.vx * dt
            item.y += item.vy * dt
            item.vy += item.gravity * dt
            if item.gravity:
                if item.x < 6 or item.x > self.WIDTH - 6:
                    item.x = max(6, min(self.WIDTH - 6, item.x))
                    item.vx *= -.7
                if item.y < 6:
                    item.y = 6
                    item.vy = abs(item.vy) * .15
            if not item.collect_delay and hypot(item.x - self.x, item.y - self.y) < 20:
                self.point_items += 1
                self.score += 1000
                self._sound("itempickup")
            elif item.y < self.HEIGHT + 12:
                items.append(item)
        self.items = items

        bullets = []
        hit = hypot(self.x - bx, self.y - by) < 25
        for bullet in self.bullets:
            bullet.x += bullet.vx * dt
            bullet.y += bullet.vy * dt
            if not (-24 < bullet.x < self.WIDTH + 24 and -24 < bullet.y < self.HEIGHT + 24):
                continue
            distance = hypot(bullet.x - self.x, bullet.y - self.y)
            if distance < 7:
                hit = True
            elif (distance < 23 and not bullet.grazed and not self.invulnerable
                  and self._pending_hit_position is None):
                bullet.grazed = True
                self.graze += 1
                self.score += 100
                self._sound("graze")
            bullets.append(bullet)
        self.bullets = bullets
        if hit and not self.invulnerable and self._pending_hit_position is None:
            self._pending_hit_position = (self.x, self.y)
            if self.bombs > 0:
                self.deathbomb_remaining = self.DEATHBOMB_WINDOW
            else:
                self._commit_hit()
                if self.state == "lost":
                    return
        self._check_win()
