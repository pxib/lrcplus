"""Small, deterministic bullet-hell simulation, independent of the GUI."""

from dataclasses import dataclass
from math import atan2, cos, hypot, pi, sin


@dataclass
class Bullet:
    x: float
    y: float
    vx: float
    vy: float
    color: int = 0
    grazed: bool = False


@dataclass
class PointItem:
    x: float
    y: float


class DanmakuGame:
    WIDTH = 384
    HEIGHT = 448
    DURATION = 75.0
    MAX_HP = 360
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
        self.bullets = []
        self.shots = []
        self.items = []
        self.point_items = 0
        self.state = "ready"
        self.wave = 0
        self._spawn_in = 1.0
        self._shot_in = 0.0

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
        self.bombs -= 1
        self.score += len(self.bullets) * 10
        self.bullets.clear()
        self.hp = max(0, self.hp - 24)
        self.invulnerable = max(self.invulnerable, 2.0)
        self.bomb_flash = 0.65
        self._check_win()

    def _check_win(self):
        if self.hp <= 0 or self.elapsed >= self.DURATION:
            self.state = "won"
            self.score += self.lives * 2000 + self.bombs * 500

    def _spawn(self):
        bx, by = self.boss
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
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.bomb_flash = max(0.0, self.bomb_flash - dt)
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

        bx, by = self.boss
        shots = []
        for shot in self.shots:
            shot.y += shot.vy * dt
            if hypot(shot.x - bx, shot.y - by) < 28:
                self.hp = max(0, self.hp - 1)
                self.score += 25
                if self.hp and self.hp % 12 == 0:
                    self.items.append(PointItem(bx, by))
            elif shot.y > -20:
                shots.append(shot)
        self.shots = shots

        items = []
        for item in self.items:
            item.y += 65 * dt
            if hypot(item.x - self.x, item.y - self.y) < 20:
                self.point_items += 1
                self.score += 1000
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
            elif distance < 23 and not bullet.grazed and not self.invulnerable:
                bullet.grazed = True
                self.graze += 1
                self.score += 100
            bullets.append(bullet)
        self.bullets = bullets
        if hit and not self.invulnerable:
            self.lives -= 1
            self.invulnerable = 2.5
            self.bullets.clear()
            self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
            if self.lives == 0:
                self.state = "lost"
                return
        self._check_win()
