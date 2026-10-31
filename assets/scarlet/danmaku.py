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
    collect_delay: float = 0.0
    burst_remaining: float = 0.0
    scattered: bool = False
    auto_collect: bool = False


@dataclass
class ScorePopup:
    x: float
    y: float
    value: int
    remaining: float = .8


@dataclass
class PowerItem(PointItem):
    value: int = 4


@dataclass
class StarItem(PointItem):
    value: int = 100


@dataclass
class WarmupEnemy:
    x: float
    y: float
    hp: int = 8
    fire_in: float = .8
    variant: int = 0
    age: float = 0.0


class DanmakuGame:
    WIDTH = 384
    HEIGHT = 448
    DURATION = 75.0
    PHASE_DURATION = DURATION / 3
    MAX_HP = 360
    MAX_POWER = 128
    OPTION_POWER = 10
    HP_FILL_DURATION = 1.2
    BOMB_COOLDOWN = 2.0
    ITEM_SPEED = 65.0
    WARMUP_DURATION = 42.0
    WARMUP_WAVES = 12
    WARMUP_INTERVAL = 2.8
    WARMUP_POWER = 8
    PHASE_BREAK = 4.0
    RECENTER_DURATION = 1.2
    COLLECTION_LINE = HEIGHT / 4
    COLLECTION_SPEED = 480.0
    DEATHBOMB_WINDOW = .1  # 100 ms of active gameplay after a collision.
    DEATHBOMB_STEP = .025
    MIN_DEATHBOMB_WINDOW = .025
    DEATH_DURATION = .45
    PATTERNS = ("紅符「紅い螺旋」", "月符「夜に咲く花」", "終符「最後のリフレイン」")

    def __init__(self):
        self.reset()

    def reset(self):
        self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
        self.elapsed = 0.0
        self.lives = 3
        self.bombs = 3
        self.bomb_cooldown = 0.0
        self.power = self.MAX_POWER
        self.option_focus = 0.0
        self.score = 0
        self.graze = 0
        self.hp = self.MAX_HP
        self.hp_fill_remaining = self.HP_FILL_DURATION
        self._phase = 0
        self.phase_elapsed = 0.0
        self.boss_elapsed = 0.0
        self.transition_remaining = 0.0
        self.transition_is_final = False
        self._boss_transition_origin = None
        self.invulnerable = 0.0
        self.bomb_flash = 0.0
        self.deathbomb_flash = 0.0
        self.deathbomb_remaining = 0.0
        self.deathbomb_streak = 0
        self.death_remaining = 0.0
        self._pending_hit_position = None
        self.bullets = []
        self.shots = []
        self.items = []
        self.score_popups = []
        self._star_popup = None
        self.point_items = 0
        self.state = "ready"
        self.wave = 0
        self._spawn_in = .25
        self._shot_in = 0.0
        self._homing_in = 0.0
        self._homing_wave = 0
        self._sound_events = []
        self._last_countdown_second = None
        self.mode = "boss"
        self.enemies = []
        self.warmup_elapsed = 0.0
        self._warmup_wave = 0
        self._warmup_spawn_in = .4

    def start_warmup(self):
        self.mode = "warmup"
        self.power = 0
        self.state = "playing"

    @property
    def warmup_complete(self):
        return (self.warmup_elapsed >= self.WARMUP_DURATION
                or (self.warmup_elapsed >= 36 and self.power >= 90
                    and not self.enemies and not self.items))

    def prepare_boss(self):
        """Keep the warm-up's resources, but begin a fresh boss entrance."""
        self.mode = "boss"
        self.state = "ready"
        self.elapsed = self.phase_elapsed = 0.0
        self.boss_elapsed = 0.0
        self.hp_fill_remaining = self.HP_FILL_DURATION
        self._spawn_in = .25
        self.wave = 0
        self.enemies.clear()
        self.bullets.clear()
        self.shots.clear()
        self.items.clear()

    def _defeat_enemy(self, enemy):
        enemy.hp = 0
        self.items.extend((PowerItem(enemy.x, enemy.y, value=self.WARMUP_POWER),
                           PointItem(enemy.x + 14, enemy.y)))

    def _update_warmup(self, dt):
        self.warmup_elapsed += dt
        self._warmup_spawn_in -= dt
        if self._warmup_wave < self.WARMUP_WAVES and self._warmup_spawn_in <= 0:
            spread = (80, 45, 65)[self._warmup_wave % 3]
            self.enemies.extend(WarmupEnemy(self.WIDTH / 2 + side * spread, 38,
                                           variant=self._warmup_wave % 3)
                                for side in (-1, 1))
            self._warmup_wave += 1
            self._warmup_spawn_in += self.WARMUP_INTERVAL
        for enemy in self.enemies:
            enemy.age += dt
            enemy.y += 12 * dt
            enemy.fire_in -= dt
            if enemy.fire_in <= 0:
                aim = atan2(self.y - enemy.y, self.x - enemy.x)
                self.bullets.append(Bullet(enemy.x, enemy.y, 80 * cos(aim), 80 * sin(aim), 1))
                enemy.fire_in += 1.4
                self._sound("bossattack1")
        self.enemies = [enemy for enemy in self.enemies if enemy.y < self.HEIGHT + 24]

    def _sound(self, name):
        # Coalesce many substep collisions into one cue per rendered frame.
        if name not in self._sound_events:
            self._sound_events.append(name)

    def take_sound_events(self):
        events, self._sound_events = self._sound_events, []
        return events

    @property
    def boss(self):
        if self.transition_remaining > 0:
            progress = min(1.0, (self.PHASE_BREAK - self.transition_remaining) / self.RECENTER_DURATION)
            eased = progress * progress * (3 - 2 * progress)
            x, y = self._boss_transition_origin
            return x + (self.WIDTH / 2 - x) * eased, y + (86 - y) * eased
        return self.WIDTH / 2 + 95 * sin(self.boss_elapsed * .65), 86 + 12 * sin(self.boss_elapsed)

    @property
    def phase(self):
        return self._phase

    @property
    def phase_remaining(self):
        return max(0.0, self.PHASE_DURATION - self.phase_elapsed)

    @property
    def deathbomb_window(self):
        return max(self.MIN_DEATHBOMB_WINDOW,
                   self.DEATHBOMB_WINDOW - self.deathbomb_streak * self.DEATHBOMB_STEP)

    @property
    def dying(self):
        return self.death_remaining > 0

    @property
    def has_options(self):
        return self.power > self.OPTION_POWER

    @property
    def option_positions(self):
        spread = 28 - 18 * self.option_focus
        height = 4 + 27 * self.option_focus
        return ((self.x - spread, self.y - height),
                (self.x + spread, self.y - height))

    @property
    def hp_fill_progress(self):
        return max(0.0, min(1.0, 1 - self.hp_fill_remaining / self.HP_FILL_DURATION))

    @property
    def boss_vulnerable(self):
        return (self.transition_remaining <= 0
                and self.hp_fill_remaining <= 1e-9 and self.wave > 0)

    @property
    def magnet_active(self):
        return (self.power >= self.MAX_POWER and self.y <= self.COLLECTION_LINE
                and not self.dying and self._pending_hit_position is None)

    def _bullets_to_stars(self):
        self.items.extend(StarItem(bullet.x, bullet.y, auto_collect=True)
                          for bullet in self.bullets)
        self.bullets.clear()

    def start_or_pause(self):
        if self.state in {"ready", "paused"}:
            self.state = "playing"
        elif self.state == "playing":
            self.state = "paused"

    def pause(self):
        if self.state == "playing":
            self.state = "paused"

    def bomb(self):
        if (self.state != "playing" or self.bombs <= 0 or self.dying
                or self.bomb_cooldown > 1e-9):
            return
        if self._pending_hit_position is not None:
            self._pending_hit_position = None
            self.deathbomb_remaining = 0.0
            self.deathbomb_flash = .65
            self.deathbomb_streak += 1
            self._sound("extendlife")
        self.bombs -= 1
        self.bomb_cooldown = self.BOMB_COOLDOWN
        self.score += len(self.bullets) * 10
        if self.mode == "boss" and self.boss_vulnerable and self.hp <= 24:
            self._bullets_to_stars()
        else:
            self.bullets.clear()
        if self.mode == "warmup":
            for enemy in self.enemies:
                self._defeat_enemy(enemy)
            self.enemies.clear()
        if self.boss_vulnerable:
            self.hp = max(0, self.hp - 24)
        self.invulnerable = max(self.invulnerable, 2.0)
        self.bomb_flash = 0.65
        self._sound("bossattack2")
        self._check_win()

    def _check_win(self):
        if (self.state != "playing" or self.mode == "warmup"
                or self.transition_remaining > 0):
            return
        if self._pending_hit_position is not None or self.dying:
            return  # Resolve the hit (or a saving bomb) before awarding the clear.
        if self.hp <= 0 or self.phase_remaining <= 1e-9:
            self._boss_transition_origin = self.boss
            self.transition_remaining = self.PHASE_BREAK
            self.transition_is_final = self.phase == len(self.PATTERNS) - 1
            self._bullets_to_stars()
            self.shots.clear()
            if not self.transition_is_final:
                self._phase += 1
                self.hp = self.MAX_HP
                self.hp_fill_remaining = self.HP_FILL_DURATION
                self.phase_elapsed = 0.0
                self.wave = 0
                self._spawn_in = .25
                self._last_countdown_second = None
                self._sound("bossattack2")
                return
            self._sound("bossdeath" if self.hp <= 0 else "timeout")
            self.hp = 0

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
        # A short outward burst reaches the upper playfield, then every item
        # falls vertically at the same constant speed as ordinary drops.
        for i in range(10):
            target_x = 24 + i * (self.WIDTH - 48) / 9
            target_y = 35 + (i % 3) * 28
            self.items.append(PointItem(
                *position, vx=(target_x - position[0]) / .25,
                vy=(target_y - position[1]) / .25,
                collect_delay=.6, burst_remaining=.25, scattered=True,
            ))

    def _commit_hit(self):
        position = self._pending_hit_position
        self._pending_hit_position = None
        self.deathbomb_remaining = 0.0
        self.lives -= 1
        self._sound("playerdeath")
        self.deathbomb_streak = 0
        self.deathbomb_flash = 0.0
        self.death_remaining = self.DEATH_DURATION
        self.bomb_cooldown = 0.0
        self.x, self.y = position
        self._scatter_points(position)
        for item in self.items:
            if not isinstance(item, StarItem):
                item.auto_collect = False

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
        transitioning = self.transition_remaining > 0
        if self.mode == "boss":
            if transitioning:
                self.transition_remaining = max(0.0, self.transition_remaining - dt)
                if self.transition_remaining <= 1e-9:
                    self.transition_remaining = 0.0
                    self.boss_elapsed = 0.0
                    if self.transition_is_final:
                        self.state = "won"
                        self.score += self.lives * 2000 + self.bombs * 500
                        return
            else:
                self.boss_elapsed += dt
                fill_step = min(dt, self.hp_fill_remaining)
                self.hp_fill_remaining = max(0.0, self.hp_fill_remaining - dt)
                self.phase_elapsed += dt - fill_step
        self.bomb_cooldown = max(0.0, self.bomb_cooldown - dt)
        for popup in self.score_popups:
            popup.y -= 32 * dt
            popup.remaining -= dt
        self.score_popups = [popup for popup in self.score_popups if popup.remaining > 1e-9]
        countdown = ceil(self.phase_remaining) if self.mode == "boss" and not transitioning else 0
        if 1 <= countdown <= 10 and countdown != self._last_countdown_second:
            self._last_countdown_second = countdown
            self._sound("timeout")
        if self.dying:
            self.death_remaining = max(0.0, self.death_remaining - dt)
            if self.death_remaining <= 1e-9:
                self.death_remaining = 0.0
                if self.lives <= 0:
                    self.state = "lost"
                    return
                self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
                self.invulnerable = 2.5
        if self._pending_hit_position is not None:
            self.deathbomb_remaining = max(0.0, self.deathbomb_remaining - dt)
            if self.deathbomb_remaining <= 1e-9:
                self._commit_hit()
        self.invulnerable = max(0.0, self.invulnerable - dt)
        self.bomb_flash = max(0.0, self.bomb_flash - dt)
        self.deathbomb_flash = max(0.0, self.deathbomb_flash - dt)
        length = max(1, hypot(dx, dy))
        speed = 95 if focus else 245
        if not self.dying:
            self.x = max(12, min(self.WIDTH - 12, self.x + dx / length * speed * dt))
            self.y = max(24, min(self.HEIGHT - 12, self.y + dy / length * speed * dt))
            target = 1.0 if focus else 0.0
            step = dt / .16
            self.option_focus += max(-step, min(step, target - self.option_focus))

        if self.mode == "warmup":
            self._update_warmup(dt)
        elif not transitioning:
            self._spawn_in -= dt
            if self._spawn_in <= 0:
                self._spawn()
                self._spawn_in += (0.65, 0.55, 0.5)[self.phase]
        self._shot_in = max(0.0, self._shot_in - dt)
        if shooting and not self.dying and self._shot_in <= 0:
            origins = self.option_positions if self.has_options else ((self.x, self.y - 14),)
            spread = .025 if focus else .09
            for x, y in origins:
                for lane in (-1, 0, 1):
                    angle = -pi / 2 + lane * spread
                    self.shots.append(Bullet(x, y, 600 * cos(angle), 600 * sin(angle)))
            self._sound("playershoot")
            self._shot_in = 0.1

        self._homing_in = max(0.0, self._homing_in - dt)
        if shooting and not self.dying and not focus and self._homing_in <= 0:
            for i in range(5):
                angle = -pi / 2 + (i - 2) * .6
                self.shots.append(HomingShot(
                    self.x, self.y - 6,
                    360 * cos(angle), 360 * sin(angle), (self._homing_wave + i) % 4,
                ))
            self._homing_wave += 1
            self._homing_in = .24

        bx, by = self.boss
        shots = []
        for shot in self.shots:
            homing = isinstance(shot, HomingShot)
            if homing:
                target = min((enemy for enemy in self.enemies if enemy.hp > 0),
                             key=lambda enemy: hypot(enemy.x - shot.x, enemy.y - shot.y),
                             default=None)
                if target:
                    tx, ty = target.x, target.y
                else:
                    tx, ty = (bx, by) if self.mode == "boss" else (self.WIDTH / 2, -100)
                self._steer_homing_shot(shot, tx, ty, dt)
            shot.x += shot.vx * dt
            shot.y += shot.vy * dt
            enemy = next((enemy for enemy in self.enemies if enemy.hp > 0
                          and hypot(shot.x - enemy.x, shot.y - enemy.y) < 18), None)
            if enemy is not None:
                enemy.hp -= 1
                self.score += 25
                self._sound("bossdamage")
                if enemy.hp == 0:
                    self._defeat_enemy(enemy)
            elif self.mode == "boss" and hypot(shot.x - bx, shot.y - by) < 28:
                if self.hp > 0 and self.boss_vulnerable:
                    self.hp -= 1
                    self.score += 25
                    self._sound("bossdamage")
                    if self.hp and self.hp % 12 == 0:
                        self.items.append(PointItem(bx, by))
                    if self.hp and self.hp % 24 == 0 and self.power < self.MAX_POWER:
                        self.items.append(PowerItem(bx - 14, by))
            elif ((homing and shot.age < 4.5) or (not homing and shot.y > -20)):
                shots.append(shot)
        self.shots = shots
        self.enemies = [enemy for enemy in self.enemies if enemy.hp > 0]

        items = []
        for item in self.items:
            item.collect_delay = max(0.0, item.collect_delay - dt)
            if self.magnet_active:
                item.auto_collect = True
            if item.auto_collect and not self.dying and not item.collect_delay:
                distance = hypot(self.x - item.x, self.y - item.y)
                travel = min(distance, self.COLLECTION_SPEED * dt)
                if distance:
                    item.x += (self.x - item.x) / distance * travel
                    item.y += (self.y - item.y) / distance * travel
                item.burst_remaining = 0.0
            else:
                burst_step = min(dt, item.burst_remaining)
                item.x += item.vx * burst_step
                item.y += item.vy * burst_step
                item.burst_remaining = max(0.0, item.burst_remaining - dt)
                if item.scattered and item.burst_remaining <= 1e-9:
                    item.vx, item.vy = 0.0, self.ITEM_SPEED
                item.y += item.vy * (dt - burst_step)
            if (not self.dying and not item.collect_delay
                    and hypot(item.x - self.x, item.y - self.y) < 20):
                if isinstance(item, PowerItem):
                    self.power = min(self.MAX_POWER, self.power + item.value)
                else:
                    value = item.value if isinstance(item, StarItem) else 1000
                    if not isinstance(item, StarItem):
                        self.point_items += 1
                    self.score += value
                    if isinstance(item, StarItem):
                        if self._star_popup is not None and self._star_popup.remaining > .4:
                            self._star_popup.value += value
                            self._star_popup.remaining = .8
                        else:
                            self._star_popup = ScorePopup(item.x, item.y - 44, value)
                            self.score_popups.append(self._star_popup)
                    else:
                        self.score_popups.append(ScorePopup(item.x, item.y - 44, value))
                self._sound("itempickup")
            elif item.auto_collect or item.y < self.HEIGHT + 12:
                items.append(item)
        self.items = items

        bullets = []
        hit = (not transitioning and hypot(self.x - bx, self.y - by) < 25 if self.mode == "boss" else
               any(hypot(self.x - enemy.x, self.y - enemy.y) < 18 for enemy in self.enemies))
        for bullet in self.bullets:
            bullet.x += bullet.vx * dt
            bullet.y += bullet.vy * dt
            if not (-24 < bullet.x < self.WIDTH + 24 and -24 < bullet.y < self.HEIGHT + 24):
                continue
            distance = hypot(bullet.x - self.x, bullet.y - self.y)
            if distance < 7:
                hit = True
            elif (distance < 23 and not bullet.grazed and not self.invulnerable
                  and self._pending_hit_position is None and not self.dying):
                bullet.grazed = True
                self.graze += 1
                self.score += 100
                self._sound("graze")
            bullets.append(bullet)
        self.bullets = bullets
        if (hit and not self.dying and not self.invulnerable
                and self._pending_hit_position is None):
            self._pending_hit_position = (self.x, self.y)
            if self.bombs > 0:
                self.deathbomb_remaining = self.deathbomb_window
            else:
                self._commit_hit()
        self._check_win()
