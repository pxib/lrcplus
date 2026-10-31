"""Small, deterministic bullet-hell simulation, independent of the GUI."""

from dataclasses import dataclass
from math import atan2, ceil, cos, hypot, pi, sin

from assets.scarlet.flandre import BossState, FlandreEncounter, PHASES, Projectile


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
class HitParticle:
    x: float
    y: float
    vx: float
    vy: float
    radius: float
    remaining: float = .5


@dataclass
class EnemyDeathRing:
    x: float
    y: float
    remaining: float = .5


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
    entry_x: float | None = None
    target_x: float | None = None
    entry_duration: float = .8

    @property
    def entering(self):
        return self.entry_x is not None and self.age < self.entry_duration


class DanmakuGame:
    DIFFICULTIES = ("Normal", "Lunatic")
    WIDTH = 384
    HEIGHT = 448
    DURATION = sum(phase.seconds for phase in PHASES)
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
    ENEMY_EFFECT_DURATION = .5
    MAX_HIT_PARTICLES = 256
    PATTERNS = tuple(phase.japanese for phase in PHASES)
    PHASES = PHASES

    def __init__(self, difficulty="Normal", *, seed=495, debug=False):
        if difficulty not in self.DIFFICULTIES:
            raise ValueError(f"Unknown difficulty: {difficulty}")
        self.difficulty = difficulty
        self.seed = seed
        self.debug = debug
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
        self._phase = 0
        self.encounter = FlandreEncounter(self, self.seed, self.PHASES)
        self.hp = self.MAX_HP
        self.hp_fill_remaining = self.HP_FILL_DURATION
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
        self.hit_particles = []
        self.enemy_death_rings = []
        self._hit_burst = 0
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
        self.debug_invulnerable = False
        self.debug_speed = 1.0

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
        self._phase = 0
        self.encounter = FlandreEncounter(self, self.seed, self.PHASES)
        self.hp = self.MAX_HP
        self.transition_remaining = 0.0
        self.transition_is_final = False
        self.hp_fill_remaining = self.HP_FILL_DURATION
        self._spawn_in = .25
        self.wave = 0
        self.enemies.clear()
        self.bullets.clear()
        self.shots.clear()
        self.items.clear()
        self.hit_particles.clear()
        self.enemy_death_rings.clear()

    def _defeat_enemy(self, enemy):
        enemy.hp = 0
        self.enemy_death_rings.append(EnemyDeathRing(enemy.x, enemy.y))
        self.items.extend((PowerItem(enemy.x, enemy.y, value=self.WARMUP_POWER),
                           PointItem(enemy.x + 14, enemy.y)))

    def _emit_hit_particles(self, x, y):
        # Vary each burst deterministically without affecting gameplay randomness.
        self._hit_burst += 1
        for i in range(3):
            angle = self._hit_burst * 2.4 + i * 2 * pi / 3
            speed = 120 + 32 * ((self._hit_burst + i) % 4)
            self.hit_particles.append(HitParticle(
                x + 8 * cos(angle), y + 8 * sin(angle),
                speed * cos(angle), speed * sin(angle),
                6.5 + 1.5 * ((self._hit_burst + i) % 3),
            ))
        self.hit_particles = self.hit_particles[-self.MAX_HIT_PARTICLES:]

    def _update_enemy_effects(self, dt):
        for particle in self.hit_particles:
            particle.x += particle.vx * dt
            particle.y += particle.vy * dt
            particle.remaining -= dt
        self.hit_particles = [particle for particle in self.hit_particles if particle.remaining > 1e-9]
        for ring in self.enemy_death_rings:
            ring.remaining -= dt
        self.enemy_death_rings = [ring for ring in self.enemy_death_rings if ring.remaining > 1e-9]

    def _update_warmup(self, dt):
        self.warmup_elapsed += dt
        self._warmup_spawn_in -= dt
        if self._warmup_wave < self.WARMUP_WAVES and self._warmup_spawn_in <= 0:
            spread = (80, 45, 65)[self._warmup_wave % 3]
            self.enemies.extend(WarmupEnemy(
                                    self.WIDTH / 2 + side * (spread + 45), -24,
                                    variant=self._warmup_wave % 3,
                                    entry_x=self.WIDTH / 2 + side * (spread + 45),
                                    target_x=self.WIDTH / 2 + side * spread)
                                for side in (-1, 1))
            self._warmup_wave += 1
            self._warmup_spawn_in += self.WARMUP_INTERVAL
        for enemy in self.enemies:
            previous_age = enemy.age
            enemy.age += dt
            active_dt = dt
            if enemy.entry_x is not None:
                progress = min(1.0, enemy.age / enemy.entry_duration)
                eased = 1 - (1 - progress) ** 3
                enemy.x = enemy.entry_x + (enemy.target_x - enemy.entry_x) * eased
                enemy.y = -24 + 62 * eased
                active_dt = max(0.0, enemy.age - max(previous_age, enemy.entry_duration))
                enemy.y += 12 * max(0.0, enemy.age - enemy.entry_duration)
            else:
                enemy.y += 12 * dt
            enemy.fire_in -= active_dt
            if enemy.fire_in <= 0:
                aim = atan2(self.y - enemy.y, self.x - enemy.x)
                lunatic = self.difficulty == "Lunatic"
                speed = 120 if lunatic else 80
                for offset in ((-.18, 0, .18) if lunatic else (0,)):
                    self.bullets.append(Bullet(enemy.x, enemy.y,
                                               speed * cos(aim + offset),
                                               speed * sin(aim + offset), 1))
                enemy.fire_in += .95 if lunatic else 1.4
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
        self._sync_encounter()
        return self.encounter.position(self.boss_elapsed)

    def _sync_encounter(self):
        if self.encounter.index != self.phase and not self.transition_remaining:
            self.encounter.begin(self.phase)

    @property
    def phase_definition(self):
        return self.PHASES[self.phase]

    @property
    def MAX_HP(self):
        return self.phase_definition.hp

    @property
    def PHASE_DURATION(self):
        return self.phase_definition.seconds

    @property
    def boss_state(self):
        if self.state == "won":
            return BossState.DEFEATED
        if self.mode == "warmup" or self.state == "ready":
            return BossState.ENTERING
        if self.transition_remaining:
            return BossState.TRANSITION
        if self.hp_fill_remaining > 1e-9:
            return BossState.SPELL_INTRO if self.phase_definition.spell else BossState.ENTERING
        return BossState.SPELL_ACTIVE if self.phase_definition.spell else BossState.NON_SPELL

    @property
    def boss_visible(self):
        return self.transition_remaining > 0 or self.encounter.visible

    def start_phase(self, phase, *, seed=None):
        """Development/practice entry point. Accept a phase id or sequence index."""
        if isinstance(phase, str):
            phase = next(i for i, definition in enumerate(self.PHASES) if definition.id == phase)
        if not 0 <= phase < len(self.PHASES):
            raise ValueError("Unknown boss phase")
        if seed is not None:
            self.seed = seed
        self.mode, self.state = "boss", "playing"
        self._phase = phase
        self.encounter.seed = self.seed
        self.encounter.begin(phase)
        self.hp = self.MAX_HP
        self.hp_fill_remaining = self.HP_FILL_DURATION
        self.elapsed = self.phase_elapsed = self.boss_elapsed = 0.
        self.transition_remaining = 0.
        self.transition_is_final = False
        self._boss_transition_origin = None
        self.wave = 0
        self._spawn_in = .25
        self._shot_in = self._homing_in = 0.
        self._homing_wave = 0
        self._last_countdown_second = None
        self.death_remaining = self.deathbomb_remaining = 0.
        self._pending_hit_position = None
        self.x, self.y = self.WIDTH / 2, self.HEIGHT - 65
        self.option_focus = 0.
        self.invulnerable = self.bomb_cooldown = self.bomb_flash = self.deathbomb_flash = 0.
        self.deathbomb_streak = 0
        self.lives = self.bombs = 3
        self.power = self.MAX_POWER
        self._star_popup = None
        self._sound_events.clear()
        for objects in (self.bullets, self.shots, self.items, self.enemies,
                        self.hit_particles, self.enemy_death_rings, self.score_popups):
            objects.clear()

    def debug_command(self, command):
        if not self.debug:
            raise RuntimeError("Start with debug=True to use development controls")
        if command == "restart":
            self.start_phase(self.phase)
        elif command == "next":
            self.start_phase((self.phase + 1) % len(self.PHASES))
        elif command == "invulnerable":
            self.debug_invulnerable = not self.debug_invulnerable
        elif command == "slow":
            self.debug_speed = .25 if self.debug_speed == 1 else 1.
        else:
            raise ValueError(command)

    def _damage_clone(self, clone, amount):
        if clone not in self.encounter.clones or clone.hp <= 0:
            return
        clone.hp -= amount
        self._emit_hit_particles(clone.x, clone.y)
        self.score += 25
        self._sound("bossdamage")
        if clone.hp <= 0:
            self.encounter.clones.remove(clone)
            self.enemy_death_rings.append(EnemyDeathRing(clone.x, clone.y))
            self.items.append(PowerItem(clone.x, clone.y, value=32))

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
                and self.hp_fill_remaining <= 1e-9 and self.wave > 0
                and not self.phase_definition.survival)

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
        if self.mode == "boss":
            self.encounter.failed_capture = True
            self.encounter.bomb_suppression = .65
            for laser in self.encounter.lasers:
                laser.active = False
            for clone in tuple(self.encounter.clones):
                self._damage_clone(clone, 24)
        self.bomb_cooldown = self.BOMB_COOLDOWN
        self.score += len(self.bullets) * 10
        if self.mode == "boss" and self.boss_vulnerable and self.hp <= 24:
            self._bullets_to_stars()
        else:
            self.bullets.clear()
        if self.mode == "warmup":
            for enemy in self.enemies:
                self._emit_hit_particles(enemy.x, enemy.y)
                self._defeat_enemy(enemy)
            self.enemies.clear()
        if self.mode == "boss" and self.boss_vulnerable:
            if self.hp > 0:
                self._emit_hit_particles(*self.boss)
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
        definition = self.phase_definition
        timeout = self.phase_remaining <= 1e-9
        # Q.E.D. requires damage for victory; timing it out ends the run.
        if timeout and definition.damage_finish and self.hp > 0:
            self.encounter.stop()
            self.bullets.clear()
            self.shots.clear()
            self.state = "lost"
            self._sound("timeout")
            return
        cleared = (timeout if definition.survival else self.hp <= 0 or timeout)
        if cleared:
            self._boss_transition_origin = self.boss
            if definition.spell:
                captured = not self.encounter.failed_capture and (definition.survival or self.hp <= 0)
                self.encounter.captures.append((definition.id, captured))
            self.encounter.stop()
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
        if self.transition_remaining or self.mode != "boss":
            return
        self._sync_encounter()
        self.encounter.start()

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
        if self.mode == "boss":
            self.encounter.failed_capture = True
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
        remaining = max(0.0, min(dt, 0.1)) * (self.debug_speed if self.debug else 1)
        while remaining > 1e-8 and self.state == "playing":
            step = min(remaining, 1 / 120)
            self._step(step, dx, dy, focus, shooting)
            remaining -= step

    def _step(self, dt, dx, dy, focus, shooting):
        self.elapsed += dt
        self._update_enemy_effects(dt)
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
                    self.encounter.begin(self.phase)
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
                self._spawn_in = float("inf")
            self.encounter.advance(dt)
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
            # Discard before steering as well as after moving: a homing shot
            # must never turn back into the field after leaving it.
            if not (0 <= shot.x <= self.WIDTH and 0 <= shot.y <= self.HEIGHT):
                continue
            homing = isinstance(shot, HomingShot)
            if homing:
                targets = self.enemies if self.mode == "warmup" else self.encounter.clones
                target = min((enemy for enemy in targets if enemy.hp > 0),
                             key=lambda enemy: hypot(enemy.x - shot.x, enemy.y - shot.y),
                             default=None)
                if (self.mode == "boss" and self.boss_visible and
                        (target is None or hypot(bx - shot.x, by - shot.y) <=
                         hypot(target.x - shot.x, target.y - shot.y))):
                    target = None
                if target:
                    tx, ty = target.x, target.y
                else:
                    tx, ty = (bx, by) if self.mode == "boss" and self.boss_visible else (shot.x, -100)
                self._steer_homing_shot(shot, tx, ty, dt)
            shot.x += shot.vx * dt
            shot.y += shot.vy * dt
            if not (0 <= shot.x <= self.WIDTH and 0 <= shot.y <= self.HEIGHT):
                continue
            enemy = next((enemy for enemy in self.enemies if enemy.hp > 0
                          and hypot(shot.x - enemy.x, shot.y - enemy.y) < 18), None)
            clone = next((clone for clone in self.encounter.clones if clone.hp > 0
                          and hypot(shot.x - clone.x, shot.y - clone.y) < 22), None)
            if clone is not None and self.boss_vulnerable:
                self._damage_clone(clone, 1)
            elif enemy is not None:
                enemy.hp -= 1
                self._emit_hit_particles(enemy.x, enemy.y)
                self.score += 25
                self._sound("bossdamage")
                if enemy.hp == 0:
                    self._defeat_enemy(enemy)
            elif self.mode == "boss" and self.boss_visible and hypot(shot.x - bx, shot.y - by) < 28:
                if self.hp > 0 and self.boss_vulnerable:
                    self.hp -= 1
                    self._emit_hit_particles(bx, by)
                    self.score += 25
                    self._sound("bossdamage")
                    if self.hp and self.hp % 12 == 0:
                        self.items.append(PointItem(bx, by))
                    if self.hp and self.hp % 24 == 0 and self.power < self.MAX_POWER:
                        self.items.append(PowerItem(bx - 14, by))
            elif not homing or shot.age < 4.5:
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
        hit = (not transitioning and self.boss_visible and hypot(self.x - bx, self.y - by) < 25 if self.mode == "boss" else
               any(hypot(self.x - enemy.x, self.y - enemy.y) < 18 for enemy in self.enemies))
        if not transitioning and self.mode == "boss":
            hit |= any(hypot(self.x - clone.x, self.y - clone.y) < 22 for clone in self.encounter.clones)
            for laser in self.encounter.lasers:
                hit |= laser.hits(self.x, self.y)
                if (laser.active and not laser.grazed and not self.invulnerable and not self.dying
                        and self._pending_hit_position is None
                        and laser.distance(self.x, self.y) < laser.width / 2 + 19
                        and not laser.hits(self.x, self.y)):
                    laser.grazed = True
                    self.graze += 1
                    self.score += 100
                    self._sound("graze")
        # Child trails append to a separate buffer, never to the iterated list.
        moving, self.bullets = self.bullets, []
        for bullet in moving:
            if isinstance(bullet, Projectile):
                if bullet.phase_id != self.phase_definition.id or not self.encounter.advance_projectile(bullet, dt):
                    continue
            else:
                bullet.x += bullet.vx * dt
                bullet.y += bullet.vy * dt
                if not (-24 < bullet.x < self.WIDTH + 24 and -24 < bullet.y < self.HEIGHT + 24):
                    continue
            distance = hypot(bullet.x - self.x, bullet.y - self.y)
            hit_radius = getattr(bullet, "radius", 4) + 3
            if distance < hit_radius:
                hit = True
            elif (distance < hit_radius + 16 and not bullet.grazed and not self.invulnerable
                  and self._pending_hit_position is None and not self.dying):
                bullet.grazed = True
                self.graze += 1
                self.score += 100
                self._sound("graze")
            bullets.append(bullet)
        self.bullets = bullets + self.bullets
        if (hit and not self.dying and not self.invulnerable
                and self._pending_hit_position is None and not (self.debug and self.debug_invulnerable)):
            self._pending_hit_position = (self.x, self.y)
            if self.bombs > 0:
                self.deathbomb_remaining = self.deathbomb_window
            else:
                self._commit_hit()
        self._check_win()
