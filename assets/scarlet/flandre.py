"""Flandre's Extra encounter. All tuning is an approximation, at 60 pattern ticks/s.

Simulation and collision are GUI independent. See flandre-encounter.md for
reference provenance, tuning units, and the remaining fidelity limitations.
"""

from dataclasses import dataclass, field
from enum import Enum
from math import atan2, cos, exp, hypot, pi, sin
from random import Random

TAU = 2 * pi
RED, BLUE, PURPLE, YELLOW, GREEN, ORANGE, CYAN = range(7)
BULLET_COLORS = ("#ff617e", "#65b9ff", "#c886ff", "#ffe36b", "#73eb92", "#ffa163", "#7ce7ee")


class BossState(str, Enum):
    ENTERING = "ENTERING"
    NON_SPELL = "NON_SPELL"
    SPELL_INTRO = "SPELL_INTRO"
    SPELL_ACTIVE = "SPELL_ACTIVE"
    SPELL_END = "SPELL_END"
    TRANSITION = "TRANSITION"
    DEFEATED = "DEFEATED"


@dataclass(frozen=True)
class Phase:
    id: str
    english: str
    japanese: str
    hp: int = 720
    seconds: float = 45
    spell: int = 0
    survival: bool = False
    damage_finish: bool = False
    start: tuple = (192, 86)
    # Counts, speeds (pixels/s), intervals (60 Hz frames), and geometry.
    tuning: dict = field(default_factory=dict)


SPELLS = (
    Phase("cranberry", 'Taboo “Cranberry Trap”', '禁忌「クランベリートラップ」', spell=1,
          tuning=dict(interval=54, count=5, speed=92, spread=.075,
                      aimed_interval=90, aimed_speed=128, aimed_spread=.095)),
    Phase("laevateinn", 'Taboo “Lævateinn”', '禁忌「レーヴァテイン」', spell=2,
          tuning=dict(cycle=300, length=355, width=14, trail_interval=10, speed=24,
                      warning=48, sweep_frames=180, active_until=240)),
    Phase("four", 'Taboo “Four of a Kind”', '禁忌「フォーオブアカインド」', spell=3,
          tuning=dict(red_interval=108, red_count=16, speed=66, clone_hp=90, special_interval=180,
                      yellow_speed=100, green_speed=102, green_spread=.18, blue_speed=92, blue_count=20)),
    Phase("kagome", 'Taboo “Kagome, Kagome”', '禁忌「カゴメカゴメ」', spell=4,
          tuning=dict(cage_interval=240, spacing=22, speed=19, yellow_interval=90, bend=48,
                      yellow_speed=115, bend_radius=70)),
    Phase("maze", 'Taboo “Maze of Love”', '禁忌「恋の迷路」', spell=5, start=(192, 214),
          tuning=dict(interval=5, arms=4, speed=78, rotation=.021, secondary_interval=150,
                      secondary_count=19, secondary_speed=54)),
    Phase("starbow", 'Forbidden Barrage “Starbow Break”', '禁弾「スターボウブレイク」', spell=6,
          tuning=dict(interval=100, count=17, speed=130, gravity=44, spread=.035, band_speed=5)),
    Phase("catadioptric", 'Forbidden Barrage “Catadioptric”', '禁弾「カタディオプトリック」', spell=7,
          tuning=dict(cycle=600, speed=152, trail_interval=8, trail_speed=28, spread=.23, side_angle=.28)),
    Phase("clock", 'Forbidden Barrage “Clock that Ticks Away the Past”', '禁弾「過去を刻む時計」', spell=8,
          tuning=dict(cycle=480, length=165, width=9, rotation=.012, aimed_interval=66, speed=115,
                      aimed_count=5, aimed_spread=.10)),
    Phase("none", 'Secret Barrage “And Then Will There Be None?”', '秘弾「そして誰もいなくなるか？」',
          spell=9, seconds=70, survival=True,
          tuning=dict(chase_frames=1800, trail_interval=9, fold_interval=120, spacing=14, speed=50,
                      trail_speed=18, fold_count=11, fold_delay=1.4, fold_stagger=.35)),
    Phase("ripples", 'Q.E.D. “Ripples of 495 Years”', 'ＱＥＤ「４９５年の波紋」', spell=10,
          hp=1000, seconds=75, damage_finish=True,
          tuning=dict(interval=155, min_interval=52, count=40, speed=72, max_speed=125)),
)

# Eight non-spells precede the first eight spells; the last three named
# attacks are consecutive. Non-spells are separately tunable approximations.
PHASES = tuple(phase for i, spell in enumerate(SPELLS[:8]) for phase in (
    Phase(f"nonspell_{i + 1}", f"Non-spell {i + 1}", f"通常弾幕 {i + 1}", hp=360, seconds=25,
          tuning=dict(interval=66 - i * 3, count=20 + i * 2, speed=78 + i * 5)), spell,
)) + SPELLS[8:]


@dataclass(slots=True)
class Projectile:
    x: float
    y: float
    vx: float
    vy: float
    color: int = BLUE
    grazed: bool = False
    phase_id: str = ""
    radius: float = 4
    age: float = 0
    lifetime: float = 12
    ay: float = 0
    reflect: bool = False
    bounces: int = 0
    max_bounces: int = 6
    trail_interval: float = 0
    trail_clock: float = 0
    kind: str = "straight"
    # Formation coordinates, directional deformation and folding metadata.
    base_x: float = 0
    base_y: float = 0
    bend_x: float = 0
    bend_y: float = 0
    fold_at: float = 0
    folded: bool = False

    def advance(self, dt, width, height):
        self.age += dt
        self.x += self.vx * dt
        self.y += self.vy * dt + .5 * self.ay * dt * dt
        self.vy += self.ay * dt
        if self.kind == "cage":
            # A short, smooth impulse bends a row without destroying topology.
            blend = 1 - exp(-dt * 3)
            self.x += self.bend_x * blend
            self.y += self.bend_y * blend
            self.bend_x *= 1 - blend
            self.bend_y *= 1 - blend
        if self.fold_at and not self.folded and self.age >= self.fold_at:
            self.vx, self.vy = -self.vy, self.vx
            self.folded = True
        if self.reflect and self.bounces < self.max_bounces:
            # Reflect top and sides, leaving the bottom open. Mirroring the
            # overshoot prevents sticking and preserves distance travelled.
            low, high = self.radius, width - self.radius
            if self.x < low and self.vx < 0:
                self.x, self.vx = 2 * low - self.x, -self.vx
                self.bounces += 1
            elif self.x > high and self.vx > 0:
                self.x, self.vx = 2 * high - self.x, -self.vx
                self.bounces += 1
            if self.y < self.radius and self.vy < 0:
                self.y, self.vy = 2 * self.radius - self.y, -self.vy
                self.bounces += 1
        # Ballistic bullets may leave the top and must survive until descent.
        top = -500 if self.ay else -28
        margin = 110 if self.kind == "fold" else 28
        return (self.age < self.lifetime and -margin < self.x < width + margin
                and top < self.y < height + 28)


@dataclass(slots=True)
class Laser:
    x: float
    y: float
    angle: float
    length: float
    width: float
    phase_id: str
    color: int = RED
    active: bool = False
    grazed: bool = False
    inner: float = 22

    @property
    def segment(self):
        dx, dy = cos(self.angle), sin(self.angle)
        return (self.x + self.inner * dx, self.y + self.inner * dy,
                self.x + self.length * dx, self.y + self.length * dy)

    def distance(self, x, y):
        ax, ay, bx, by = self.segment
        dx, dy = bx - ax, by - ay
        t = max(0, min(1, ((x - ax) * dx + (y - ay) * dy) / max(1e-9, dx * dx + dy * dy)))
        return hypot(x - ax - t * dx, y - ay - t * dy)

    def hits(self, x, y, radius=3):
        return self.active and self.distance(x, y) < self.width / 2 + radius


@dataclass(slots=True)
class Clone:
    x: float
    y: float
    role: str
    hp: int
    clock: int = 0


class FlandreEncounter:
    """Phase-owned tick scheduler: stopping it cancels every future spawn."""

    def __init__(self, game, seed=495, phases=PHASES):
        self.game = game
        self.seed = seed
        self.phases = phases
        self.captures = []
        self.begin(0)

    def begin(self, index):
        self.index = index
        self.definition = self.phases[index]
        self.rng = Random(self.seed + index * 997)
        self.frame = 0
        self.accumulator = 0.0
        self.running = False
        self.failed_capture = False
        self.lasers = []
        self.clones = []
        self.emitters = [(72., 110.), (312., 110.)]
        self.next_ring = 0
        self.recoil = (0., 0.)
        self.bomb_suppression = 0.0

    def stop(self):
        self.running = False
        self.lasers.clear()
        self.clones.clear()

    @property
    def visible(self):
        return not (self.definition.survival and self.running)

    def position(self, t):
        spell = self.definition.spell
        if spell == 5:
            target = self.definition.start
        elif spell in (8, 9, 10):
            target = (192, 86)
        elif spell == 3:
            target = (238, 90)
        elif spell == 7:
            frame = max(0, self.frame)
            cycle, section = divmod(frame, self.definition.tuning["cycle"])
            points = ((192, 76), (310, 100), (74, 124), (302, 142), (192, 76))
            part = min(3, section // 150)
            a, b = points[part], points[part + 1]
            u = min(1, (section % 150) / 100)
            u = u * u * (3 - 2 * u)
            target = (a[0] + (b[0] - a[0]) * u,
                      min(205, a[1] + (b[1] - a[1]) * u + 8 * min(cycle, 5)))
        elif spell == 2:
            target = (192 + 102 * sin(t * .53), 70 + 10 * sin(t * .8))
        else:
            target = (192 + 95 * sin(t * (.65 if spell != 6 else .9)), 86 + 12 * sin(t))
        u = min(1, t / 1.2)
        u = u * u * (3 - 2 * u)
        rx, ry = self.recoil
        return (192 + (target[0] - 192) * u + rx,
                86 + (target[1] - 86) * u + ry)

    def start(self):
        if self.index != self.game.phase:
            self.begin(self.game.phase)
        if self.running:
            return
        self.running = True
        if self.definition.spell == 3:
            hp = self.definition.tuning["clone_hp"]
            self.clones = [Clone(192, 86, role, hp) for role in ("yellow", "green", "red")]
        self.tick()

    def advance(self, dt):
        if not self.running:
            return
        self.bomb_suppression = max(0, self.bomb_suppression - dt)
        self.accumulator += dt
        while self.accumulator >= 1 / 60 - 1e-9:
            self.accumulator -= 1 / 60
            self.frame += 1
            self.tick()

    def bullet(self, x, y, angle, speed, color=BLUE, **kwargs):
        if not self.running:
            return
        speed *= 1.2 if self.game.difficulty == "Lunatic" else 1
        p = Projectile(x, y, cos(angle) * speed, sin(angle) * speed,
                       color=color, phase_id=self.definition.id, **kwargs)
        self.game.bullets.append(p)
        return p

    def ring(self, x, y, count, speed, color, offset=0, **kwargs):
        if self.game.difficulty == "Lunatic":
            count = round(count * 1.3)
        for i in range(count):
            self.bullet(x, y, offset + TAU * i / count, speed, color, **kwargs)

    def aimed(self, x, y, count, spacing, speed, color, **kwargs):
        angle = atan2(self.game.y - y, self.game.x - x)
        for i in range(count):
            self.bullet(x, y, angle + (i - (count - 1) / 2) * spacing, speed, color, **kwargs)
        return angle

    def due(self, interval, offset=0):
        interval = max(1, round(interval * (.82 if self.game.difficulty == "Lunatic" else 1)))
        return self.frame >= offset and (self.frame - offset) % interval == 0

    def tick(self):
        self.recoil = tuple(v * .88 for v in self.recoil)
        self.game.wave += 1
        spell = self.definition.spell
        if spell:
            getattr(self, f"_{self.definition.id}")()
        else:
            self._nonspell()

    def cue(self):
        self.game._sound("bossattack2" if self.definition.spell else "bossattack1")

    def _nonspell(self):
        c = self.definition.tuning
        if self.due(c["interval"]):
            x, y = self.game.boss
            n = int(self.definition.id.split("_")[-1])
            # Successive non-spells vary angular weave and aimed fans.
            self.ring(x, y, c["count"], c["speed"], RED, self.frame * .004 * (-1 if n % 2 else 1))
            self.aimed(x, y, 3 + 2 * (n // 3), .12, c["speed"] + 26, BLUE)
            self.cue()

    def _cranberry(self):
        c, f, g = self.definition.tuning, self.frame, self.game
        # Independent, hidden sources travel up/down the side boundaries.
        if self.due(c["interval"]):
            for side in (-1, 1):
                x = 5 if side < 0 else g.WIDTH - 5
                y = 90 + 145 * (.5 + .5 * sin(f * .018 + side))
                target = (192 + 32 * sin(f * .013), 300 + 45 * cos(f * .015))
                a = atan2(target[1] - y, target[0] - x)
                for i in range(c["count"]):
                    self.bullet(x, y, a + (i - (c["count"] - 1) / 2) * c["spread"], c["speed"], PURPLE)
            self.cue()
        if self.due(c["aimed_interval"], 24):
            for x in (6, g.WIDTH - 6):
                self.aimed(x, 62 + 110 * (.5 + .5 * sin(f * .021)), 3,
                           c["aimed_spread"], c["aimed_speed"], BLUE)

    def _laevateinn(self):
        c, f = self.definition.tuning, self.frame
        cycle, local = divmod(f, c["cycle"])
        if local == 0:
            self.lasers = [Laser(*self.game.boss, -.3, c["length"], c["width"], self.definition.id,
                                 color=ORANGE)]
            self.cue()
        sword = self.lasers[0]
        sword.x, sword.y = self.game.boss
        u = max(0, min(1, (local - c["warning"]) / c["sweep_frames"]))
        # Alternating broad rotation and a downward swing, with a rear opening.
        start, sweep = ((-.25, pi + .5) if cycle % 2 == 0 else (.18, pi - .36))
        sword.angle = start + sweep * u
        sword.active = c["warning"] <= local < c["active_until"] and self.bomb_suppression <= 0
        if sword.active and self.due(c["trail_interval"]):
            for distance in range(38, int(sword.length), 26):
                x = sword.x + cos(sword.angle) * distance
                y = sword.y + sin(sword.angle) * distance
                self.bullet(x, y, sword.angle - pi / 2, c["speed"], ORANGE, lifetime=8)

    def _four(self):
        c, f = self.definition.tuning, self.frame
        places = ((72, 96), (310, 96), (148, 154))
        u = min(1, f / 60)
        u = u * u * (3 - 2 * u)
        for clone in self.clones:
            index = ("yellow", "green", "red").index(clone.role)
            tx, ty = places[index]
            clone.x, clone.y = 192 + (tx - 192) * u, 86 + (ty - 86) * u
            clone.clock += 1
            # Destruction removes the scheduler with the clone.
            if self.due(c["red_interval"], index * 18):
                self.ring(clone.x, clone.y, c["red_count"], c["speed"], RED, index * .15 + f * .003)
            if clone.role == "yellow" and self.due(c["special_interval"], 60):
                for i in range(8):
                    self.bullet(clone.x, clone.y, i * TAU / 8, c["yellow_speed"], YELLOW, radius=8)
            if clone.role == "green" and self.due(c["special_interval"], 90):
                self.aimed(clone.x, clone.y, 7, c["green_spread"], c["green_speed"], GREEN, radius=8)
        x, y = self.game.boss
        if self.due(c["red_interval"]):
            self.ring(x, y, c["red_count"], c["speed"], RED, f * .003)
            self.cue()
        local = f % c["special_interval"]
        if local in (72, 90, 108):
            self.ring(x, y, c["blue_count"], c["blue_speed"], BLUE,
                      (local - 72) / 18 * pi / c["blue_count"], radius=8)

    def _kagome(self):
        c, g, f = self.definition.tuning, self.game, self.frame
        if self.due(c["cage_interval"]):
            # Concentric square perimeters, with one navigable gate per side.
            for inset in (8, 56):
                for x in range(inset, g.WIDTH - inset, c["spacing"]):
                    if abs(x - 192) < 22:
                        continue
                    for y, a in ((inset + 40, pi / 2), (g.HEIGHT - inset, -pi / 2)):
                        self.bullet(x, y, a, c["speed"], GREEN, kind="cage", lifetime=13,
                                    base_x=x, base_y=y)
                for y in range(inset + 40, g.HEIGHT - inset, c["spacing"]):
                    if abs(y - 290) < 22:
                        continue
                    for x, a in ((inset, 0), (g.WIDTH - inset, pi)):
                        self.bullet(x, y, a, c["speed"], GREEN, kind="cage", lifetime=13,
                                    base_x=x, base_y=y)
            self.cue()
        if self.due(c["yellow_interval"], 60):
            x, y = g.boss
            a = atan2(g.y - y, g.x - x) if (f // c["yellow_interval"]) % 2 == 0 else pi / 2 + .8 * sin(f)
            self.bullet(x, y, a, c["yellow_speed"], YELLOW, radius=10)
            dx, dy = cos(a), sin(a)
            for b in g.bullets:
                if not isinstance(b, Projectile) or b.kind != "cage":
                    continue
                along = (b.x - x) * dx + (b.y - y) * dy
                across = abs((b.x - x) * dy - (b.y - y) * dx)
                if along > 0:
                    force = c["bend"] * exp(-(across / c["bend_radius"]) ** 2)
                    b.bend_x += dx * force
                    b.bend_y += dy * force

    def _maze(self):
        c, f = self.definition.tuning, self.frame
        x, y = self.game.boss
        if self.due(c["interval"]):
            angle = f * c["rotation"]
            for arm in range(c["arms"]):
                # Paired traces define walls, leaving a continuous curved lane.
                for edge in (0, .10):
                    self.bullet(x, y, angle + arm * TAU / c["arms"] + edge, c["speed"], RED,
                                radius=3, lifetime=6)
        if self.due(c["secondary_interval"], 75):
            gap = f * c["rotation"] + pi / 4
            for i in range(c["secondary_count"]):
                self.bullet(x, y, gap + pi * i / (c["secondary_count"] - 1), c["secondary_speed"],
                            BLUE, radius=4, lifetime=7)
            self.cue()

    def _starbow(self):
        c, f = self.definition.tuning, self.frame
        if self.due(c["interval"]):
            wave = (f // max(1, round(c["interval"] * (.82 if self.game.difficulty == "Lunatic" else 1)))) % 4
            cx = (170, 238, 260, 182)[wave] + self.rng.uniform(-14, 14)
            cy = 175 + self.rng.uniform(-10, 10)
            # Each color band shares its ballistic apex geometry.
            for band, color in enumerate((RED, ORANGE, YELLOW, GREEN, CYAN, BLUE, PURPLE)):
                for i in range(c["count"]):
                    a = -pi / 2 + (i - (c["count"] - 1) / 2) * c["spread"]
                    self.bullet(cx, cy, a, c["speed"] + band * c["band_speed"], color, ay=c["gravity"],
                                radius=3, lifetime=11)
            self.cue()

    def _catadioptric(self):
        c, f = self.definition.tuning, self.frame
        local = f % c["cycle"]
        if local in (0, 150, 300) or local in (450, 474, 498, 522, 546):
            x, y = self.game.boss
            a = -pi / 2 if local == 0 or local >= 450 else (
                c["side_angle"] if local == 150 else pi - c["side_angle"])
            count = 5 if local < 450 else 1
            for i in range(count):
                self.bullet(x, y, a + (i - (count - 1) / 2) * c["spread"], c["speed"], BLUE,
                            radius=9, reflect=True, max_bounces=4, kind="trail_parent",
                            trail_interval=c["trail_interval"] / 60, lifetime=13)
            self.recoil = (-4 * cos(a), -4 * sin(a))
            self.cue()

    def _clock(self):
        c, f = self.definition.tuning, self.frame
        local = f % c["cycle"]
        if local == 0:
            self.lasers = [Laser(50, 215, i * pi / 2, c["length"], c["width"], self.definition.id,
                                 color=PURPLE, inner=12) for i in range(4)]
            self.cue()
        u = local / c["cycle"]
        for i, laser in enumerate(self.lasers):
            laser.x = 50 + 284 * (.5 - .5 * cos(u * TAU))
            laser.y = 215 + 32 * sin(u * TAU)
            laser.angle = f * c["rotation"] + i * pi / 2
            laser.active = local >= 55 and self.bomb_suppression <= 0
        if self.due(c["aimed_interval"]):
            self.aimed(*self.game.boss, c["aimed_count"], c["aimed_spread"], c["speed"], RED)

    def _none(self):
        c, f, g = self.definition.tuning, self.frame, self.game
        if f < c["chase_frames"]:
            for i, (x, y) in enumerate(self.emitters):
                # Chase a moving offset around the player's sampled position.
                angle = f * .018 + i * pi
                tx = max(20, min(g.WIDTH - 20, g.x + 95 * cos(angle)))
                ty = max(40, min(g.HEIGHT - 20, g.y + 85 * sin(angle)))
                distance = max(1, hypot(tx - x, ty - y))
                travel = min(distance, 1.6)
                x, y = x + (tx - x) / distance * travel, y + (ty - y) / distance * travel
                self.emitters[i] = (x, y)
                if self.due(c["trail_interval"]):
                    for side in (-1, 1):
                        self.bullet(x, y, angle + side * pi / 2, c["trail_speed"], BLUE, lifetime=9, radius=4)
        elif self.due(c["fold_interval"], c["chase_frames"]):
            wave = (f - c["chase_frames"]) // max(1, round(c["fold_interval"] * (.82 if g.difficulty == "Lunatic" else 1)))
            side = 1 if wave % 2 == 0 else -1
            cx = 25 if side == 1 else g.WIDTH - 25
            cy = 150 + 55 * (wave % 4)
            # Four actual moving lines; delayed quarter turns fold their corners.
            for edge in range(4):
                for i in range(c["fold_count"]):
                    q = i * c["spacing"] - 70
                    dx, dy = ((q, -70), (70, q), (-q, 70), (-70, -q))[edge]
                    self.bullet(cx + dx, cy + dy, 0 if side == 1 else pi, c["speed"], BLUE,
                                kind="fold", fold_at=c["fold_delay"] + edge * c["fold_stagger"], lifetime=10, radius=3)
            self.cue()

    def _ripples(self):
        c, g = self.definition.tuning, self.game
        intensity = min(1., max(g.phase_elapsed / self.definition.seconds, 1 - g.hp / self.definition.hp))
        if self.frame >= self.next_ring:
            interval = max(c["min_interval"], c["interval"] * (1 - .65 * intensity))
            self.next_ring = self.frame + max(1, round(interval * (.82 if g.difficulty == "Lunatic" else 1)))
            angle = self.rng.uniform(0, TAU)
            center = (self.rng.uniform(85, 299), self.rng.uniform(90, 220))
            speed = min(c["max_speed"], c["speed"] * (1 + .65 * intensity))
            self.ring(*center, c["count"], speed, RED if (self.frame // 60) % 2 else BLUE,
                      angle, reflect=True, max_bounces=2, lifetime=12)
            self.cue()

    def advance_projectile(self, bullet, dt):
        alive = bullet.advance(dt, self.game.WIDTH, self.game.HEIGHT)
        if alive and bullet.trail_interval and self.running:
            bullet.trail_clock += dt
            if bullet.trail_clock >= bullet.trail_interval:
                bullet.trail_clock %= bullet.trail_interval
                for side in (-1, 1):
                    self.bullet(bullet.x, bullet.y, atan2(bullet.vy, bullet.vx) + side * pi / 2,
                                self.definition.tuning.get("trail_speed", 28), BLUE,
                                radius=3, lifetime=7)
        return alive
