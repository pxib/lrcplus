"""Four-lane charts and a keyboard playfield for Rhythm Games."""

from dataclasses import dataclass
from itertools import cycle, groupby, pairwise
from math import sin, tau
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, QSize, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import (
    QBrush, QColor, QFont, QIcon, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker,
    QPen, QPixmap, QPolygonF, QRadialGradient,
)
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QHBoxLayout, QLabel, QProgressBar, QPushButton, QSizePolicy, QVBoxLayout, QWidget


LANE_KEYS = (Qt.Key.Key_D, Qt.Key.Key_F, Qt.Key.Key_J, Qt.Key.Key_K)
LANE_LABELS = ("D", "F", "J", "K")
LANE_PATTERN = (0, 2, 1, 3, 2, 0, 3, 1, 3, 1, 2, 0, 1, 3, 0, 2)
LANE_COLORS = (QColor("#81f9dd"), QColor("#b0a8ff"), QColor("#b0a8ff"), QColor("#81f9dd"))
CLASSIC_LANE_COLORS = (QColor("#4c99a5"), QColor("#d28d5f"), QColor("#d28d5f"), QColor("#4c99a5"))
HOLD_COLOR = QColor("#71edb2")
GOLD_NOTE_COLOR = QColor("#ffcf60")
FLICK_NOTE_COLOR = QColor("#ff9dc8")
PERFECT_EFFECT_COLOR = QColor("#ffdd91")
HIT_CORE_COLOR = QColor("#f5fffd")
FEEDBACK_SECONDS = 0.24
BURST_SECONDS = 0.48
JUDGEMENT_SECONDS = 1.0
JUDGEMENT_SCALE_SECONDS = 0.1
COMBO_GROW_SECONDS = 0.1
COMBO_ECHO_SECONDS = 0.2
COMBO_ECHO_SCALE = 1.45
MANIA_BAD = -1
JUDGEMENT_LABELS = {300: "Perfect", 100: "Great", 50: "Good", MANIA_BAD: "Bad", 0: "Miss"}
JUDGEMENT_COLORS = {"Great": "#d9b9ff", "Good": "#ace0ff", "Bad": "#66f4f5", "Miss": "#b7bdc9"}
# Spawn offset and horizontal drift in lane widths, rise in pixels, and delay within a burst.
HIT_SPARKS = ((-0.08, -0.40, 35, 0.00), (0.10, 0.35, 45, 0.03),
              (-0.04, -0.14, 68, 0.05), (0.04, 0.15, 54, 0.01),
              (-0.21, -0.33, 21, 0.09), (0.17, 0.35, 25, 0.07),
              (-0.02, 0.23, 82, 0.13), (0.13, -0.35, 59, 0.15))
MANIA_INSTRUCTIONS = "Tap D, F, J, or K at the hit line, including arrow notes. Hold long notes until their tails reach the line."
FLICK_GAP_MS = 1700
MAX_LIFE = 1000
SCORE_METER_MAX = 1_300_000
SCORE_RANKS = (("C", 50_000), ("B", 450_000), ("A", 900_000), ("S", 1_150_000))


@dataclass(frozen=True)
class ManiaNote:
    time_ms: int
    lane: int
    text: str = ""
    reading: str = ""
    end_ms: int | None = None
    golden: bool = False
    flick: bool = False

    @property
    def finish_ms(self):
        return self.time_ms if self.end_ms is None else self.end_ms


# Use the same original click track as the Circles demo.
MANIA_DEMO = (
    ManiaNote(1000, 0, "Tap D"), ManiaNote(1125, 1, "Tap F"),
    ManiaNote(1500, 2, "Tap J"),
    ManiaNote(2000, 3, "Hold K", end_ms=3000),
    ManiaNote(2500, 1, "Tap F while holding K"),
    ManiaNote(3500, 0, "Tap D and K together", golden=True, flick=True),
    ManiaNote(3500, 3, "Tap D and K together", golden=True),
    ManiaNote(3750, 2, "Tap J", flick=True),
    ManiaNote(4500, 1, "Hold F", end_ms=6000, golden=True),
    ManiaNote(5000, 2), ManiaNote(5500, 3),
    *(ManiaNote(time, lane) for time, lane in zip(range(6750, 7251, 125), (0, 2, 1, 3, 0))),
    ManiaNote(7750, 1), ManiaNote(7750, 2), ManiaNote(8000, 3), ManiaNote(8250, 0),
    *(ManiaNote(time, lane) for time, lane in zip(range(9000, 9751, 250), (2, 0, 3, 1))),
    *(ManiaNote(time, lane) for time, lane in zip(range(10500, 11001, 100), (0, 2, 1, 3, 2, 0))),
    ManiaNote(11500, 0, end_ms=12500), ManiaNote(11750, 2), ManiaNote(12000, 1),
    ManiaNote(12250, 3), ManiaNote(12500, 2),
    *(ManiaNote(time, lane) for time, lane in zip(range(13250, 14626, 125), cycle(LANE_PATTERN))),
)


def assign_lanes(notes):
    """Keep each hold in one lane and spread successive attacks across hands."""
    free_at = [float("-inf")] * len(LANE_KEYS)
    result = []
    next_flick = 0
    for (note, following), preferred in zip(pairwise((*notes, None)), cycle(LANE_PATTERN)):
        available = [lane for lane, time in enumerate(free_at) if time <= note.time_ms]
        if not available:
            continue
        lane = preferred if preferred in available else min(available, key=free_at.__getitem__)
        end_ms = note.end_ms if note.finish_ms > note.time_ms else None
        gap = following.time_ms - note.time_ms if following is not None else float("inf")
        flick = (bool(result) and end_ms is None and note.time_ms >= next_flick
                 and gap >= max(400, note.beat_length_ms or 500))
        if flick:
            next_flick = note.time_ms + FLICK_GAP_MS
        result.append(ManiaNote(note.time_ms, lane, note.text, note.reading, end_ms,
                               golden=note.accented, flick=flick))
        free_at[lane] = max(note.time_ms + 1, note.finish_ms)
    return tuple(result)


class ManiaSounds:
    def __init__(self, parent, directory):
        def load(name):
            effect = QSoundEffect(parent)
            path = directory / f"{name}.wav"
            if path.is_file():
                effect.setSource(QUrl.fromLocalFile(str(path)))
            return effect

        # A voice per lane lets chords and simultaneous holds play independently.
        self.taps = {
            value: tuple(load(name) for _ in LANE_KEYS)
            for value, name in ((300, "maniahitperfect"), (100, "maniahitgreat"), (50, "maniahitgood"))
        }
        self.holds = tuple(load("maniahithold") for _ in LANE_KEYS)
        self.golden_taps = tuple(load("maniahitcritical") for _ in LANE_KEYS)
        self.flicks = tuple(load("maniahitflick") for _ in LANE_KEYS)
        self.golden_flicks = tuple(load("maniahitflickcritical") for _ in LANE_KEYS)
        self.golden_holds = tuple(load("maniaholdcritical") for _ in LANE_KEYS)
        for effect in (*self.holds, *self.golden_holds):
            effect.setLoopCount(QSoundEffect.Loop.Infinite.value)
        self.results = {name: load(name) for name in ("allperfect", "fullcombo", "clear", "fail")}
        self.effects = (*self.holds, *self.golden_taps, *self.flicks, *self.golden_flicks,
                        *self.golden_holds, *self.results.values(),
                        *(effect for taps in self.taps.values() for effect in taps))

    def configure(self, device, volume):
        for effect in self.effects:
            if effect.audioDevice() != device:
                effect.setAudioDevice(device)
            effect.setVolume(volume)

    def stop(self):
        for effect in self.effects:
            effect.stop()

    @staticmethod
    def play(*effects):
        for effect in effects:
            if effect.isLoaded():
                effect.play()
                return

    def play_note(self, note, value, fallback):
        normal = self.holds if value is None else self.taps[value]
        golden = self.golden_holds if value is None else self.golden_taps
        lane = note.lane
        effects = (normal[lane], fallback)
        if note.golden:
            effects = (golden[lane], *effects)
        if note.flick:
            flicks = self.golden_flicks if note.golden else self.flicks
            effects = (flicks[lane], *effects)
        self.play(*effects)

    def stop_hold(self, lane):
        self.holds[lane].stop()
        self.golden_holds[lane].stop()

    def play_result(self, counts, *, failed):
        if failed:
            name = "fail"
        elif counts[0] or counts.get(MANIA_BAD, 0) or counts[50]:
            name = "clear"
        elif counts[100]:
            name = "fullcombo"
        else:
            name = "allperfect"
        self.play(self.results[name])


@dataclass(frozen=True)
class HitFeedback:
    note: ManiaNote
    value: int
    started: float

    def strength(self, now):
        return max(0, 1 - (now - self.started) / FEEDBACK_SECONDS)


@dataclass(frozen=True)
class Playfield:
    center: float
    width: float
    top: float
    hit_y: float
    top_scale: float = 0.58

    @property
    def travel(self):
        return self.hit_y - self.top

    def depth(self, y):
        return max(0, min(1, (y - self.top) / self.travel))

    def x(self, lane, y):
        offset = (lane / len(LANE_KEYS) - 0.5) * self.width
        return self.center + offset * (self.top_scale + (1 - self.top_scale) * self.depth(y))

    def strip(self, left, right, top, bottom):
        return QPolygonF([
            QPointF(self.x(left, top), top), QPointF(self.x(right, top), top),
            QPointF(self.x(right, bottom), bottom), QPointF(self.x(left, bottom), bottom),
        ])


def tinted(color, alpha):
    result = QColor(color)
    result.setAlpha(alpha)
    return result


def neon_note_color(note):
    if note.golden:
        return GOLD_NOTE_COLOR
    if note.flick:
        return FLICK_NOTE_COLOR
    if note.end_ms is not None:
        return HOLD_COLOR
    return LANE_COLORS[note.lane]


def draw_glow(painter, center, y, width, height, color, opacity):
    glow = QRadialGradient(0, 0, 0.5)
    glow.setColorAt(0, tinted(color, round(opacity)))
    glow.setColorAt(0.44, tinted(color, round(opacity * 0.38)))
    glow.setColorAt(1, tinted(color, 0))
    painter.save()
    painter.translate(center, y)
    painter.scale(width, height)
    painter.setPen(Qt.PenStyle.NoPen)
    painter.setBrush(glow)
    painter.drawEllipse(QRectF(-0.5, -0.5, 1, 1))
    painter.restore()


def draw_ripple(painter, center, y, width, height, color, opacity):
    ring = QRectF(center - width / 2, y - height / 2, width, height)
    painter.setBrush(Qt.BrushStyle.NoBrush)
    painter.setPen(QPen(tinted(color, round(opacity * 0.27)), 3.5))
    painter.drawEllipse(ring)
    painter.setPen(QPen(tinted(color.lighter(135), round(opacity)), 1.25))
    painter.drawEllipse(ring)


def draw_spark(painter, x, y, size, *, glint=False):
    corners = ((0, -1.35), (1, 0), (0, 1.35), (-1, 0))
    if glint:
        corners = ((0, -1.35), (0.26, -0.26), (1, 0), (0.26, 0.26),
                   (0, 1.35), (-0.26, 0.26), (-1, 0), (-0.26, -0.26))
    painter.drawPolygon(QPolygonF([QPointF(x + dx * size, y + dy * size) for dx, dy in corners]))


def life_heart():
    heart = QPixmap(24, 24)
    heart.fill(Qt.GlobalColor.transparent)
    painter = QPainter(heart)
    painter.setRenderHint(QPainter.RenderHint.Antialiasing)
    shape = QPainterPath(QPointF(12, 22))
    shape.cubicTo(8, 19, 1, 13, 1, 8)
    shape.cubicTo(1, 1, 9, 0, 12, 6)
    shape.cubicTo(15, 0, 23, 1, 23, 8)
    shape.cubicTo(23, 13, 16, 19, 12, 22)
    painter.fillPath(shape, QColor("#89ff99"))
    painter.end()
    return heart


class ScoreMeter(QProgressBar):
    def __init__(self):
        super().__init__()
        self.setAccessibleName("Score progress")
        self.setRange(0, SCORE_METER_MAX)
        self.setTextVisible(False)
        self.setFixedHeight(33)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        track = QRectF(1, 18, self.width() - 2, 12)
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(QColor("#363b60"))
        painter.drawRoundedRect(track, 6, 6)
        if self.value():
            fill = QLinearGradient(track.topLeft(), track.topRight())
            fill.setColorAt(0, QColor("#90ffce"))
            fill.setColorAt(0.55, QColor("#80dff8"))
            fill.setColorAt(1, QColor("#aa9cff"))
            painter.save()
            painter.setClipRect(QRectF(track.left(), track.top(), track.width() * self.value() / self.maximum(), track.height()))
            painter.setBrush(fill)
            painter.drawRoundedRect(track, 6, 6)
            painter.restore()

        font = painter.font()
        font.setBold(True)
        font.setPixelSize(11)
        painter.setFont(font)
        painter.setPen(QColor("#edf3ff"))
        for rank, threshold in SCORE_RANKS:
            x = track.left() + track.width() * threshold / self.maximum()
            painter.drawText(QRectF(max(0, x - 7), 0, 14, 16), Qt.AlignmentFlag.AlignCenter, rank)
            painter.drawLine(QPointF(x, 16), QPointF(x, 25))


class ComboNumber(QLabel):
    def __init__(self):
        super().__init__("0")
        self.setObjectName("comboNumber")
        self.setAccessibleName("Combo")
        self.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Fixed)
        self._pulses = []
        self._timer = QTimer(self, interval=16)
        self._timer.timeout.connect(self._advance)

    def sizeHint(self):
        hint = super().sizeHint()
        return QSize(hint.width(), round(hint.height() * COMBO_ECHO_SCALE))

    def set_combo(self, combo):
        previous = int(self.text())
        if combo == previous:
            return
        text = str(combo)
        self.setText(text)
        if combo < previous:
            self.clear_animation()
            return
        self._pulses.append((text, monotonic()))
        if not self._timer.isActive():
            self._timer.start()

    def clear_animation(self):
        self._pulses.clear()
        self._timer.stop()
        self.update()

    def _advance(self):
        now = monotonic()
        duration = COMBO_GROW_SECONDS + COMBO_ECHO_SECONDS
        self._pulses = [(text, started) for text, started in self._pulses if now - started < duration]
        if not self._pulses:
            self._timer.stop()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setFont(self.font())
        painter.setPen(QColor("#f4faff"))
        now = monotonic()
        scale = 1.0
        if self._pulses:
            progress = min(1.0, (now - self._pulses[-1][1]) / COMBO_GROW_SECONDS)
            scale = 1.0 - 0.5 * (1.0 - progress) ** 2
            for text, started in reversed(self._pulses):
                age = now - started - COMBO_GROW_SECONDS
                if 0 <= age < COMBO_ECHO_SECONDS:
                    fade = age / COMBO_ECHO_SECONDS
                    self._draw_number(painter, text, 1.0 + (COMBO_ECHO_SCALE - 1.0) * fade, 0.4 * (1.0 - fade))
                    break
        self._draw_number(painter, self.text(), scale, 1.0)

    def _draw_number(self, painter, text, scale, opacity):
        text_width = self.fontMetrics().horizontalAdvance(text)
        fit = min(1.0, self.width() / (text_width * COMBO_ECHO_SCALE))
        painter.save()
        painter.setOpacity(opacity)
        painter.translate(self.width() / 2, self.height() / 2)
        painter.scale(scale * fit, scale * fit)
        bounds = QRectF(-self.width() / 2, -self.height() / 2, self.width(), self.height())
        painter.drawText(bounds, Qt.AlignmentFlag.AlignCenter, text)
        painter.restore()


class ManiaBoard(QWidget):
    pressed = Signal(int)
    released = Signal(int)

    @property
    def instructions(self):
        if self.theme == "classic":
            return "Tap D, F, J, or K at the hit line. Hold long notes until their tails reach the line."
        return MANIA_INSTRUCTIONS

    def __init__(self, parent, lead_ms):
        super().__init__(parent)
        self.setMinimumSize(520, 300)
        self.setAccessibleName("Four-key rhythm playfield")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.theme = "neon"
        self.lead_ms = lead_ms
        self.notes = ()
        self.first = self.position_ms = 0
        self.round = None
        self._score_digits = ""
        self._held = set()
        self._feedback = {}
        self._bursts = []
        self._judgement_label = None
        self._feedback_timer = QTimer(self, interval=16)
        self._feedback_timer.timeout.connect(self._advance_feedback)
        self._build_hud()
        self._update_hud()

    def _build_hud(self):
        self.score_hud = QWidget(self)
        self.score_hud.setStyleSheet("""
            QLabel { color: #f3f6ff; font-weight: bold; }
            QLabel#scoreRank { background: #292c50; color: #a9ccff; border-radius: 9px; font-size: 27px; }
            QLabel#scoreNumber { font-size: 20px; }
        """)
        self.score_rank = QLabel()
        self.score_rank.setObjectName("scoreRank")
        self.score_rank.setAccessibleName("Score rank")
        self.score_rank.setAlignment(Qt.AlignmentFlag.AlignCenter)
        self.score_rank.setFixedSize(38, 54)
        self.score_number = QLabel()
        self.score_number.setObjectName("scoreNumber")
        self.score_number.setAccessibleName("Score")
        self.score_number.setTextFormat(Qt.TextFormat.RichText)
        self.score_meter = ScoreMeter()
        score = QVBoxLayout()
        score.setSpacing(0)
        score.addWidget(self.score_meter)
        score.addWidget(self.score_number)
        rank_and_score = QHBoxLayout(self.score_hud)
        rank_and_score.setContentsMargins(0, 0, 0, 0)
        rank_and_score.addWidget(self.score_rank, alignment=Qt.AlignmentFlag.AlignTop)
        rank_and_score.addLayout(score, 1)

        self.life_hud = QWidget(self)
        self.life_hud.setObjectName("maniaLife")
        self.life_hud.setFixedHeight(50)
        self.life_hud.setStyleSheet("""
            QWidget#maniaLife { background: #797995; border-radius: 22px; }
            QLabel { color: white; background: transparent; font-size: 14px; font-weight: bold; }
            QProgressBar { background: #303047; border: 0; border-radius: 5px; }
            QProgressBar::chunk { background: #89ff99; border-radius: 5px; }
            QPushButton { background: #fff; border: 0; border-radius: 20px; }
            QPushButton:disabled { background: #dedee9; }
            QPushButton:hover, QPushButton:focus { background: #dddefe; }
        """)
        heart = QLabel()
        heart.setPixmap(life_heart())
        self.life_meter = QProgressBar()
        self.life_meter.setAccessibleName("Life")
        self.life_meter.setRange(0, MAX_LIFE)
        self.life_meter.setValue(MAX_LIFE)
        self.life_meter.setTextVisible(False)
        self.life_meter.setFixedHeight(10)
        self.life_number = QLabel()
        self.life_number.setAccessibleName("Life remaining")
        life_heading = QHBoxLayout()
        life_heading.addWidget(QLabel("LIFE"))
        life_heading.addStretch()
        life_heading.addWidget(self.life_number)
        meter = QVBoxLayout()
        meter.setSpacing(1)
        meter.addLayout(life_heading)
        meter.addWidget(self.life_meter)
        self.pause_button = QPushButton()
        self.pause_button.setFixedSize(40, 40)
        self.pause_button.setFocusPolicy(Qt.FocusPolicy.TabFocus)
        self.pause_button.setAutoDefault(False)
        self.set_pause_state(enabled=False)
        hud = QHBoxLayout(self.life_hud)
        hud.setContentsMargins(12, 4, 6, 4)
        hud.addWidget(heart)
        hud.addLayout(meter, 1)
        hud.addWidget(self.pause_button)
        self.combo_hud = QWidget(self)
        self.combo_hud.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents)
        self.combo_hud.setStyleSheet("""
            QLabel { color: #d9c8ff; font-size: 13px; font-weight: bold; }
        """)
        combo_title = QLabel("COMBO", alignment=Qt.AlignmentFlag.AlignCenter)
        self.combo_number = ComboNumber()
        combo = QVBoxLayout(self.combo_hud)
        combo.setContentsMargins(0, 0, 0, 0)
        combo.setSpacing(0)
        combo.addWidget(combo_title)
        combo.addWidget(self.combo_number)

        header = QHBoxLayout()
        header.addWidget(self.score_hud)
        header.addStretch()
        header.addWidget(self.life_hud, alignment=Qt.AlignmentFlag.AlignTop)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(10, 8, 10, 55)
        layout.addLayout(header)
        layout.addStretch()
        layout.addWidget(self.combo_hud, alignment=Qt.AlignmentFlag.AlignRight)

    def resizeEvent(self, event):
        width = self.width()
        hud_width = min(330, round(width * 0.35))
        self.score_hud.setFixedWidth(hud_width)
        self.life_hud.setFixedWidth(hud_width)
        self.combo_hud.setFixedWidth(min(130, round((width - self._neon_lane_width()) / 2) - 9))
        self.combo_number.setStyleSheet(f"font-size: {min(46, round(width / 20))}px;")
        super().resizeEvent(event)

    def _update_hud(self):
        neon = self.theme == "neon"
        round_ = self.round if neon else None
        life = MAX_LIFE if round_ is None or round_.life is None else round_.life
        score = round_.display_score if round_ is not None else 0
        combo = round_.combo if round_ is not None else 0
        rank = round_.grade if round_ is not None else "D"
        self.life_meter.setValue(life)
        self.life_number.setText(str(life))
        self.score_meter.setValue(min(score, self.score_meter.maximum()))
        self._update_score_number(score)
        self.score_rank.setText(rank)
        self.combo_number.set_combo(combo)
        self.life_hud.setVisible(neon)
        self.score_hud.setVisible(neon)
        self.combo_hud.setVisible(neon and combo > 0)

    def _update_score_number(self, score):
        digits = f"{score:08d}"
        if digits == self._score_digits:
            return
        significant = digits.lstrip("0")
        leading_zeroes = digits[:len(digits) - len(significant)]
        self.score_number.setText(f'<span style="color: #9a9da8">{leading_zeroes}</span>{significant}')
        self._score_digits = digits

    def set_pause_state(self, *, enabled, paused=False):
        label = "Resume" if paused else "Pause"
        icon = QPixmap(16, 16)
        icon.fill(Qt.GlobalColor.transparent)
        painter = QPainter(icon)
        ink = QColor("#222140")
        if paused:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(ink)
            painter.drawPolygon(QPolygonF([QPointF(3, 0), QPointF(15, 8), QPointF(3, 16)]))
        else:
            painter.fillRect(2, 0, 4, 16, ink)
            painter.fillRect(10, 0, 4, 16, ink)
        painter.end()
        self.pause_button.setIcon(QIcon(icon))
        self.pause_button.setAccessibleName(label)
        self.pause_button.setToolTip(label)
        self.pause_button.setEnabled(enabled)

    def set_theme(self, theme):
        self.theme = theme
        self._update_hud()
        self.clear_feedback()

    def set_frame(self, notes, first, position_ms, round_=None):
        if notes is not self.notes:
            self.clear_feedback()
        self.notes, self.first, self.position_ms, self.round = notes, first, position_ms, round_
        self._update_hud()
        self.update()

    def is_pressed(self, lane):
        return lane in self._held

    def clear_inputs(self):
        self._held.clear()
        self.update()

    def clear_feedback(self):
        self._feedback.clear()
        self._bursts.clear()
        self._judgement_label = None
        self._feedback_timer.stop()
        self.combo_number.clear_animation()
        self.update()

    def animate(self, judgement, *, early_release=False):
        note = self.notes[judgement.index]
        now = monotonic()
        hit = HitFeedback(note, judgement.value, now)
        self._feedback[note.lane] = hit
        if self.theme == "neon":
            if judgement.value > 0:
                self._bursts.append(hit)
            label = "Bad" if early_release else JUDGEMENT_LABELS[judgement.value]
            self._judgement_label = label, now
        if not self._feedback_timer.isActive():
            self._feedback_timer.start()
        self.update()

    def _advance_feedback(self):
        now = monotonic()
        self._feedback = {lane: hit for lane, hit in self._feedback.items() if now - hit.started < FEEDBACK_SECONDS}
        self._bursts = [hit for hit in self._bursts if now - hit.started < BURST_SECONDS]
        if self._judgement_label is not None and now - self._judgement_label[1] >= JUDGEMENT_SECONDS:
            self._judgement_label = None
        if not self._feedback and not self._bursts and self._judgement_label is None:
            self._feedback_timer.stop()
        self.update()

    def keyPressEvent(self, event):
        if event.key() not in LANE_KEYS:
            super().keyPressEvent(event)
            return
        lane = LANE_KEYS.index(event.key())
        if not event.isAutoRepeat() and lane not in self._held:
            self._held.add(lane)
            self.pressed.emit(lane)
            self.update()
        event.accept()

    def keyReleaseEvent(self, event):
        if event.key() not in LANE_KEYS:
            super().keyReleaseEvent(event)
            return
        lane = LANE_KEYS.index(event.key())
        if not event.isAutoRepeat() and lane in self._held:
            self._held.remove(lane)
            self.released.emit(lane)
            self.update()
        event.accept()

    def focusOutEvent(self, event):
        held = sorted(self._held)
        self.clear_inputs()
        for lane in held:
            self.released.emit(lane)
        super().focusOutEvent(event)

    def _neon_lane_width(self):
        return min(620, self.width() * 0.72)

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        center, hit_y = self.width() / 2, self.height() - 49
        if self.theme == "classic":
            field = Playfield(center, min(408, self.width() - 32), 10, hit_y, top_scale=1)
            self._draw_classic_playfield(painter, field)
            self._draw_notes(painter, field)
            return

        field = Playfield(center, self._neon_lane_width(), 8, hit_y, top_scale=0.14)
        now = monotonic()
        bursts = [(hit, (now - hit.started) / BURST_SECONDS) for hit in self._bursts]
        bursts = [(hit, phase) for hit, phase in bursts if phase < 1]
        self._draw_playfield(painter, field)
        for hit, phase in bursts:
            self._draw_lane_flash(painter, field, hit, phase)
        self._draw_receptors(painter, field, now)
        self._draw_notes(painter, field)
        for hit, phase in bursts:
            self._draw_hit_burst(painter, field, hit, phase)
        self._draw_judgement(painter, field)

    def _draw_judgement(self, painter, field):
        if self._judgement_label is None:
            return
        label, started = self._judgement_label
        age = monotonic() - started
        if not 0 < age < JUDGEMENT_SECONDS:
            return
        progress = min(1.0, age / JUDGEMENT_SCALE_SECONDS)
        scale = 1 - (1 - progress) ** 3
        opacity = min(1.0, (JUDGEMENT_SECONDS - age) / 0.18)

        font = painter.font()
        font.setWeight(QFont.Weight.Black)
        font.setPixelSize(round(field.width * 0.060))
        path = QPainterPath()
        path.addText(QPointF(), font, label.upper())
        # Reinforce the weight when the selected font has only a regular face.
        weight = QPainterPathStroker()
        weight.setWidth(font.pixelSize() * 0.07)
        weight.setJoinStyle(Qt.PenJoinStyle.RoundJoin)
        path = path.united(weight.createStroke(path))
        bounds = path.boundingRect()
        center = bounds.center()
        y = field.hit_y - max(38, min(65, field.travel * 0.17))

        painter.save()
        painter.setOpacity(opacity)
        painter.translate(field.center, y)
        painter.scale(scale, scale)
        painter.translate(-center.x(), -center.y())
        shadow = path.translated(0, 2.5)
        painter.strokePath(shadow, QPen(QColor(5, 8, 25, 160), 4))
        painter.fillPath(shadow, QColor(5, 8, 25, 215))

        if label == "Perfect":
            fill = QLinearGradient(bounds.topLeft(), bounds.bottomRight())
            fill.setColorAt(0, QColor("#a6e9ff"))
            fill.setColorAt(0.53, QColor("#e5c5ff"))
            fill.setColorAt(1, QColor("#fff2af"))
            painter.fillPath(path, QBrush(fill))
        else:
            painter.fillPath(path, QColor(JUDGEMENT_COLORS[label]))
        painter.restore()

    def _draw_playfield(self, painter, field):
        backdrop = QLinearGradient(0, 0, self.width(), self.height())
        backdrop.setColorAt(0, QColor("#102a39"))
        backdrop.setColorAt(0.48, QColor("#111525"))
        backdrop.setColorAt(1, QColor("#292043"))
        painter.fillRect(self.rect(), backdrop)
        painter.setPen(Qt.PenStyle.NoPen)
        for lane, color in enumerate(LANE_COLORS):
            track = QLinearGradient(0, field.top, 0, field.hit_y)
            track.setColorAt(0, QColor("#171d30"))
            track.setColorAt(1, QColor("#172b38") if lane in (0, 3) else QColor("#22263f"))
            painter.setBrush(track)
            painter.drawPolygon(field.strip(lane, lane + 1, field.top, field.hit_y))
            if lane in self._held:
                light = QLinearGradient(0, field.top, 0, field.hit_y)
                light.setColorAt(0, tinted(color, 0))
                light.setColorAt(1, tinted(color, 82))
                painter.setBrush(light)
                painter.drawPolygon(field.strip(lane, lane + 1, field.top, field.hit_y))

        for lane in range(len(LANE_KEYS) + 1):
            outer_edge = lane in (0, len(LANE_KEYS))
            painter.setPen(QPen(QColor("#71acb8" if outer_edge else "#485575"), 1.5 if outer_edge else 1))
            painter.drawLine(QPointF(field.x(lane, field.top), field.top), QPointF(field.x(lane, field.hit_y), field.hit_y))

    def _draw_classic_playfield(self, painter, field):
        palette = self.palette()
        painter.fillRect(self.rect(), palette.base())
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        now = monotonic()
        for lane, (label, color) in enumerate(zip(LANE_LABELS, CLASSIC_LANE_COLORS)):
            x = field.x(lane, field.hit_y)
            width = field.width / len(LANE_KEYS)
            track = QRectF(x + 1, field.top, width - 2, field.travel)
            painter.fillRect(track, palette.alternateBase())
            pressed = lane in self._held
            if pressed:
                painter.fillRect(track, tinted(color, 42))
            painter.fillRect(QRectF(x + 1, field.hit_y - 1, width - 2, 3), color)

            key = QRectF(x + 5, field.hit_y + 8, width - 10, 29)
            hit = self._feedback.get(lane)
            if hit is not None and hit.strength(now) > 0:
                fill = QColor("#b44859") if hit.value == 0 else color
                text = QColor("#17191c") if hit.value else QColor("white")
            else:
                fill = color if pressed else palette.button().color()
                text = QColor("#17191c") if pressed else palette.buttonText().color()
            painter.fillRect(key, fill)
            painter.setPen(text)
            painter.drawText(key, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_notes(self, painter, field):
        visible = []
        for index in range(self.first, len(self.notes)):
            note = self.notes[index]
            until = note.time_ms - self.position_ms
            if until > self.lead_ms:
                break
            if self.round is not None and self.round.is_judged(index):
                continue
            y = field.hit_y - until * field.travel / self.lead_ms
            holding = self.round is not None and self.round.is_holding(index)
            visible.append((note, y, holding))

        painter.save()
        painter.setClipRect(QRectF(0, field.top, self.width(), field.travel + 10))
        classic = self.theme == "classic"
        if not classic:
            self._draw_chord_guides(painter, field, visible)
        painter.setPen(Qt.PenStyle.NoPen)
        for note, y, holding in visible:
            lane = note.lane
            is_hold = note.end_ms is not None
            color = CLASSIC_LANE_COLORS[lane] if classic else neon_note_color(note)
            if not classic and note.flick:
                self._draw_flick_chevron(painter, field, lane, y, color)
            if is_hold:
                if holding:
                    y = min(y, field.hit_y)
                tail = field.hit_y - (note.end_ms - self.position_ms) * field.travel / self.lead_ms
                ribbon_top = min(y, max(field.top, tail))
                if classic:
                    left = field.x(lane + 0.32, y)
                    painter.setBrush(color.darker(140))
                    painter.drawRoundedRect(QRectF(left, ribbon_top, field.x(lane + 0.68, y) - left, y - ribbon_top), 4, 4)
                else:
                    painter.setBrush(tinted(color, 114))
                    painter.drawPolygon(field.strip(lane + 0.26, lane + 0.74, ribbon_top, y))
                    painter.setBrush(tinted(color, 194))
                    painter.drawPolygon(field.strip(lane + 0.46, lane + 0.54, ribbon_top, y))
                    if field.top <= tail < y:
                        painter.drawPolygon(field.strip(lane + 0.25, lane + 0.75, tail, min(y, tail + 3)))
                    if holding:
                        self._draw_hold_aura(painter, field, note, y, color)

            if classic:
                left = field.x(lane, y) + 6
                painter.setBrush(color)
                painter.drawRoundedRect(QRectF(left, y - 7, field.x(lane + 1, y) - left - 6, 14), 4, 4)
            else:
                self._draw_note_head(painter, field, lane, y, color, is_hold)
        painter.restore()

    def _draw_chord_guides(self, painter, field, visible):
        for _, group in groupby(visible, key=lambda entry: entry[0].time_ms):
            pending = [(note, y) for note, y, holding in group if not holding]
            if len(pending) < 2:
                continue
            left = min(note.lane for note, _ in pending)
            right = max(note.lane for note, _ in pending)
            y = pending[0][1]
            start = QPointF(field.x(left + 0.5, y), y)
            end = QPointF(field.x(right + 0.5, y), y)
            color = QColor("#deedff")
            if all(note.golden for note, _ in pending):
                color = PERFECT_EFFECT_COLOR
            painter.setPen(QPen(tinted(color, 75), 4))
            painter.drawLine(start, end)
            painter.setPen(QPen(color, 1.5))
            painter.drawLine(start, end)

    def _draw_flick_chevron(self, painter, field, lane, y, color):
        scale = 0.64 + 0.36 * field.depth(y)
        center = field.x(lane + 0.5, y)
        half_width = field.x(lane + 0.73, y) - center
        chevron = QPolygonF([
            QPointF(center - half_width, y - 9 * scale), QPointF(center, y - 22 * scale),
            QPointF(center + half_width, y - 9 * scale), QPointF(center + half_width, y - 3 * scale),
            QPointF(center, y - 15 * scale), QPointF(center - half_width, y - 3 * scale),
        ])
        painter.setPen(QPen(tinted(color, 95), 3 * scale))
        painter.setBrush(color.lighter(123))
        painter.drawPolygon(chevron)
        painter.setPen(Qt.PenStyle.NoPen)

    def _draw_note_head(self, painter, field, lane, y, color, is_hold):
        scale = 0.64 + 0.36 * field.depth(y)
        height = (18 if is_hold else 12) * scale
        left = field.x(lane + 0.10, y)
        width = field.x(lane + 0.90, y) - left
        head = QRectF(left, y - height / 2, width, height)
        radius = 3.5 * scale
        for spread, opacity in ((4, 18), (2, 50)):
            glow = spread * scale
            painter.setBrush(tinted(color, opacity))
            painter.drawRoundedRect(head.adjusted(-glow, -glow, glow, glow), radius + glow, radius + glow)

        face = QLinearGradient(0, head.top(), 0, head.bottom())
        face.setColorAt(0, color.lighter(138))
        face.setColorAt(0.60, color)
        face.setColorAt(1, color.darker(134))
        painter.setBrush(face)
        painter.drawRoundedRect(head, radius, radius)

        if is_hold:
            # The recessed center matches the ribbon and gives holds a distinct silhouette inside the rim.
            inset = head.adjusted(width * 0.17, height * 0.19, -width * 0.17, -height * 0.19)
            painter.setBrush(color.darker(215))
            painter.drawRoundedRect(inset, 2 * scale, 2 * scale)
            stripe = QRectF(inset.left() + inset.width() * 0.26, y - scale, inset.width() * 0.48, 2 * scale)
            painter.setBrush(color.lighter(135))
            painter.drawRoundedRect(stripe, scale, scale)
        else:
            glint = QRectF(left + width * 0.12, head.top() + 2 * scale, width * 0.76, 1.5 * scale)
            painter.setBrush(tinted(HIT_CORE_COLOR, 214))
            painter.drawRoundedRect(glint, scale, scale)

    def _draw_receptors(self, painter, field, now):
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        for lane, (label, color) in enumerate(zip(LANE_LABELS, LANE_COLORS)):
            pressed = lane in self._held
            hit = self._feedback.get(lane)
            strength = hit.strength(now) if hit is not None else 0
            feedback = color
            if hit is not None:
                feedback = neon_note_color(hit.note).lighter(125) if hit.value > 0 else QColor("#ff829e")
            left = field.x(lane + 0.06, field.hit_y)
            right = field.x(lane + 0.94, field.hit_y)
            width = right - left

            painter.fillRect(QRectF(left, field.hit_y - 4, width, 8), tinted(color, 75 if pressed else 38))
            painter.fillRect(QRectF(left, field.hit_y - 1, width, 2), color)
            if strength:
                painter.fillRect(QRectF(left, field.hit_y - 4, width, 8), tinted(feedback, round(195 * strength)))

            key = QRectF(left + 3, field.hit_y + 12, width - 6, 26)
            painter.setPen(QPen(feedback if strength else tinted(color, 200 if pressed else 100), 1))
            painter.setBrush(tinted(feedback, 86) if strength else tinted(color, 74 if pressed else 16))
            painter.drawRoundedRect(key, 6, 6)
            painter.setPen(QColor("#f2f8ff"))
            painter.drawText(key, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_lane_flash(self, painter, field, hit, phase):
        fade = max(0, 1 - phase / 0.6) ** 2
        if not fade:
            return
        lane = hit.note.lane
        color = neon_note_color(hit.note)
        bottom = field.hit_y
        top = bottom - field.travel * (0.44 if hit.note.flick else 0.34)
        intensity = 0.72 + hit.value / 1100
        beam = QLinearGradient(0, top, 0, bottom)
        beam.setColorAt(0, tinted(color, 0))
        beam.setColorAt(0.55, tinted(color, round(46 * intensity * fade)))
        beam.setColorAt(1, tinted(color, round(150 * intensity * fade)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(beam)
        painter.drawPolygon(field.strip(lane + 0.05, lane + 0.95, top, bottom))

    def _draw_hit_burst(self, painter, field, hit, phase):
        note = hit.note
        color = neon_note_color(note)
        accent = PERFECT_EFFECT_COLOR if note.golden else color.lighter(145)
        center, y = field.x(note.lane + 0.5, field.hit_y), field.hit_y - 3
        width = field.width / len(LANE_KEYS)
        scale = min(1, width / 110)
        intensity = 0.65 + hit.value / 860
        flash = max(0, 1 - phase / 0.43) ** 2 * intensity
        if flash:
            draw_glow(painter, center, y, width * 1.45, 52 * scale, color, 235 * flash)
            draw_glow(painter, center, y, width * 1.21, 13 * scale, HIT_CORE_COLOR, 250 * flash)

        delays = (0, 0.15, 0.27) if note.golden else (0, 0.16)
        for index, delay in enumerate(delays):
            wave = (phase - delay) / (1 - delay)
            if wave < 0:
                continue
            spread = 1 - (1 - wave) ** 2
            draw_ripple(painter, center, y - 4 * spread * scale,
                        width * (0.33 + 0.80 * spread), (4 + 17 * spread) * scale, accent,
                        200 * intensity * (1 - wave) ** 2 / (1 + 0.55 * index))

        if note.flick:
            self._draw_flick_trails(painter, center, y, width, scale, color, phase, intensity)

        sparks = HIT_SPARKS if hit.value == 300 or note.golden or note.flick else HIT_SPARKS[:6]
        lift = 1.65 if note.flick else 1
        painter.setPen(Qt.PenStyle.NoPen)
        for index, (offset, drift, rise, delay) in enumerate(sparks):
            flight = (phase - delay) / (1 - delay)
            if flight < 0:
                continue
            spread = 1 - (1 - flight) ** 2
            x = center + width * (offset + drift * spread)
            spark_y = y - (5 + rise * lift * spread) * scale
            glint = index in (2, 5, 7)
            size = (4.0 if glint else 2.8) * (1 - 0.55 * flight) * scale
            painter.setBrush(tinted(HIT_CORE_COLOR if glint else accent,
                                    round(255 * intensity * (1 - flight) ** 1.6)))
            draw_spark(painter, x, spark_y, size, glint=glint)

    def _draw_flick_trails(self, painter, center, y, width, scale, color, phase, intensity):
        strength = max(0, 1 - phase / 0.72) * intensity
        if not strength:
            return
        travel = 1 - (1 - phase) ** 2
        for offset, angle, length, thickness in ((0, 0, 83, 9), (-0.16, -20, 64, 5), (0.15, 16, 69, 5)):
            tip = -(length + 62 * travel) * scale
            tail = -61 * travel * scale
            trail = QLinearGradient(0, tip, 0, tail)
            trail.setColorAt(0, tinted(color, 0))
            trail.setColorAt(0.35, tinted(HIT_CORE_COLOR, round(239 * strength)))
            trail.setColorAt(0.60, tinted(color, round(179 * strength)))
            trail.setColorAt(1, tinted(color, 0))
            painter.save()
            painter.translate(center + offset * width, y)
            painter.rotate(angle)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(trail)
            shoulder = 0.42 * tip + 0.58 * tail
            half = thickness * scale / 2
            painter.drawPolygon(QPolygonF([QPointF(0, tip), QPointF(half, shoulder),
                                           QPointF(0, tail), QPointF(-half, shoulder)]))
            painter.restore()

    def _draw_hold_aura(self, painter, field, note, y, color):
        lane = note.lane
        center = field.x(lane + 0.5, y)
        width = field.x(lane + 1, y) - field.x(lane, y)
        phase = max(0, self.position_ms - note.time_ms) / 520
        pulse = 0.5 + 0.5 * sin(tau * phase)
        height = min(78, field.travel * 0.25)
        top = y - height
        aura = QLinearGradient(0, top, 0, y)
        aura.setColorAt(0, tinted(color, 0))
        aura.setColorAt(0.65, tinted(color, round(36 + 26 * pulse)))
        aura.setColorAt(1, tinted(color, round(92 + 64 * pulse)))
        painter.setBrush(aura)
        painter.drawPolygon(field.strip(lane + 0.16, lane + 0.84, top, y))
        draw_glow(painter, center, y - 6, width * 1.25, height, color, 130 + 50 * pulse)

        for offset in (0, 0.5):
            wave = (phase + offset) % 1
            draw_ripple(painter, center, y - 6 - 12 * wave, width * (0.40 + 0.60 * wave),
                        6 + 8 * wave, color, 156 * sin(wave * tau / 2))

        painter.setPen(Qt.PenStyle.NoPen)
        for index, offset in enumerate((-0.26, 0.21, -0.12, 0.29, 0.07)):
            rise = (phase * (0.8 + 0.12 * index) + index * 0.23) % 1
            x = field.x(lane + 0.5 + offset * (1 + 0.5 * rise), y - rise * height)
            painter.setBrush(tinted(color.lighter(148), round(218 * sin(rise * tau / 2))))
            draw_spark(painter, x, y - rise * height, 1.4 + 1.4 * (1 - rise), glint=index == 2)
