"""Four-lane charts and a keyboard playfield for Rhythm Games."""

from dataclasses import dataclass
from itertools import cycle
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QColor, QLinearGradient, QPainter, QPen, QPolygonF
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QWidget


LANE_KEYS = (Qt.Key.Key_D, Qt.Key.Key_F, Qt.Key.Key_J, Qt.Key.Key_K)
LANE_LABELS = ("D", "F", "J", "K")
LANE_PATTERN = (0, 2, 1, 3, 2, 0, 3, 1, 3, 1, 2, 0, 1, 3, 0, 2)
LANE_COLORS = (QColor("#81f9dd"), QColor("#b0a8ff"), QColor("#b0a8ff"), QColor("#81f9dd"))
HOLD_COLOR = QColor("#71edb2")
PERFECT_EFFECT_COLOR = QColor("#ffdd91")
FEEDBACK_SECONDS = 0.24
HIT_SPARKS = ((-0.19, 0.14, 27), (0.16, 0.18, 31), (-0.05, 0.31, 39),
              (0.07, 0.26, 45), (-0.27, 0.11, 16), (0.26, 0.12, 20))
MANIA_INSTRUCTIONS = "Tap D, F, J, or K at the hit line. Hold long notes until their tails reach the line."


@dataclass(frozen=True)
class ManiaNote:
    time_ms: int
    lane: int
    text: str = ""
    reading: str = ""
    end_ms: int | None = None

    @property
    def finish_ms(self):
        return self.time_ms if self.end_ms is None else self.end_ms


# Use the same original click track as the Circles demo.
MANIA_DEMO = (
    ManiaNote(1000, 0, "Tap D"), ManiaNote(1125, 1, "Tap F"),
    ManiaNote(1500, 2, "Tap J"),
    ManiaNote(2000, 3, "Hold K", end_ms=3000),
    ManiaNote(2500, 1, "Tap F while holding K"),
    ManiaNote(3500, 0, "Tap D and K together"), ManiaNote(3500, 3, "Tap D and K together"),
    ManiaNote(3750, 2, "Tap J"),
    ManiaNote(4500, 1, "Hold F", end_ms=6000),
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
    for note, preferred in zip(notes, cycle(LANE_PATTERN)):
        available = [lane for lane, time in enumerate(free_at) if time <= note.time_ms]
        if not available:
            continue
        lane = preferred if preferred in available else min(available, key=free_at.__getitem__)
        end_ms = note.end_ms if note.finish_ms > note.time_ms else None
        result.append(ManiaNote(note.time_ms, lane, note.text, note.reading, end_ms))
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
        for effect in self.holds:
            effect.setLoopCount(QSoundEffect.Loop.Infinite.value)
        self.results = {name: load(name) for name in ("allperfect", "fullcombo", "clear", "fail")}
        self.effects = (*self.holds, *self.results.values(), *(effect for taps in self.taps.values() for effect in taps))

    def configure(self, device, volume):
        for effect in self.effects:
            if effect.audioDevice() != device:
                effect.setAudioDevice(device)
            effect.setVolume(volume)

    def stop(self):
        for effect in self.effects:
            effect.stop()

    @staticmethod
    def play(effect, fallback=None):
        if effect.isLoaded():
            effect.play()
        elif fallback is not None and fallback.isLoaded():
            fallback.play()

    def play_result(self, counts):
        if not any(counts[value] for value in self.taps):
            name = "fail"
        elif counts[0]:
            name = "clear"
        elif counts[100] or counts[50]:
            name = "fullcombo"
        else:
            name = "allperfect"
        self.play(self.results[name])


@dataclass(frozen=True)
class Playfield:
    center: float
    width: float
    top: float
    hit_y: float

    @property
    def travel(self):
        return self.hit_y - self.top

    def depth(self, y):
        return max(0, min(1, (y - self.top) / self.travel))

    def x(self, lane, y):
        offset = (lane / len(LANE_KEYS) - 0.5) * self.width
        return self.center + offset * (0.58 + 0.42 * self.depth(y))

    def strip(self, left, right, top, bottom):
        return QPolygonF([
            QPointF(self.x(left, top), top), QPointF(self.x(right, top), top),
            QPointF(self.x(right, bottom), bottom), QPointF(self.x(left, bottom), bottom),
        ])


def tinted(color, alpha):
    result = QColor(color)
    result.setAlpha(alpha)
    return result


class ManiaBoard(QWidget):
    pressed = Signal(int)
    released = Signal(int)
    instructions = MANIA_INSTRUCTIONS

    def __init__(self, parent, lead_ms):
        super().__init__(parent)
        self.setMinimumSize(520, 300)
        self.setAccessibleName("Four-key rhythm playfield")
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.lead_ms = lead_ms
        self.notes = ()
        self.first = self.position_ms = 0
        self.round = None
        self._held = set()
        self._feedback = {}
        self._feedback_timer = QTimer(self, interval=16)
        self._feedback_timer.timeout.connect(self._advance_feedback)

    def set_frame(self, notes, first, position_ms, round_=None):
        if notes is not self.notes:
            self.clear_feedback()
        self.notes, self.first, self.position_ms, self.round = notes, first, position_ms, round_
        self.update()

    def clear_inputs(self):
        self._held.clear()
        self.update()

    def clear_feedback(self):
        self._feedback.clear()
        self._feedback_timer.stop()
        self.update()

    def animate(self, judgement):
        lane = self.notes[judgement.index].lane
        self._feedback[lane] = judgement.value, monotonic() + FEEDBACK_SECONDS
        if not self._feedback_timer.isActive():
            self._feedback_timer.start()
        self.update()

    def _advance_feedback(self):
        now = monotonic()
        self._feedback = {lane: entry for lane, entry in self._feedback.items() if entry[1] > now}
        if not self._feedback:
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

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        field = Playfield(self.width() / 2, min(464, self.width() * 0.82), 14, self.height() - 49)
        self._draw_playfield(painter, field)
        self._draw_receptors(painter, field)
        self._draw_notes(painter, field)

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

    def _draw_notes(self, painter, field):
        painter.save()
        painter.setClipRect(QRectF(0, field.top, self.width(), field.travel + 10))
        painter.setPen(Qt.PenStyle.NoPen)
        for index in range(self.first, len(self.notes)):
            note = self.notes[index]
            until = note.time_ms - self.position_ms
            if until > self.lead_ms:
                break
            if self.round is not None and self.round.is_judged(index):
                continue
            y = field.hit_y - until * field.travel / self.lead_ms
            lane = note.lane
            is_hold = note.end_ms is not None
            color = HOLD_COLOR if is_hold else LANE_COLORS[lane]
            if is_hold:
                holding = self.round is not None and self.round.is_holding(index)
                if holding:
                    y = min(y, field.hit_y)
                tail = field.hit_y - (note.end_ms - self.position_ms) * field.travel / self.lead_ms
                ribbon_top = min(y, max(field.top, tail))
                painter.setBrush(tinted(color, 114))
                painter.drawPolygon(field.strip(lane + 0.26, lane + 0.74, ribbon_top, y))
                painter.setBrush(tinted(color, 194))
                painter.drawPolygon(field.strip(lane + 0.46, lane + 0.54, ribbon_top, y))
                if field.top <= tail < y:
                    painter.drawPolygon(field.strip(lane + 0.25, lane + 0.75, tail, min(y, tail + 3)))
                if holding:
                    self._draw_hold_aura(painter, field, lane, y)

            self._draw_note_head(painter, field, lane, y, color, is_hold)
        painter.restore()

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
            painter.setBrush(tinted(QColor("#f5fffd"), 214))
            painter.drawRoundedRect(glint, scale, scale)

    def _draw_receptors(self, painter, field):
        now = monotonic()
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        for lane, (label, color) in enumerate(zip(LANE_LABELS, LANE_COLORS)):
            pressed = lane in self._held
            value, deadline = self._feedback.get(lane, (None, 0))
            strength = max(0, (deadline - now) / FEEDBACK_SECONDS)
            perfect = value == 300
            feedback = PERFECT_EFFECT_COLOR if perfect else color
            if value == 0:
                feedback = QColor("#ff829e")
            left = field.x(lane + 0.06, field.hit_y)
            right = field.x(lane + 0.94, field.hit_y)
            width = right - left

            painter.fillRect(QRectF(left, field.hit_y - 4, width, 8), tinted(color, 75 if pressed else 38))
            painter.fillRect(QRectF(left, field.hit_y - 1, width, 2), color)
            if strength:
                painter.fillRect(QRectF(left, field.hit_y - 4, width, 8), tinted(feedback, round(195 * strength)))
                if value:
                    self._draw_hit_burst(painter, field, lane, color, strength, perfect=perfect)

            key = QRectF(left + 3, field.hit_y + 12, width - 6, 26)
            painter.setPen(QPen(feedback if strength else tinted(color, 200 if pressed else 100), 1))
            painter.setBrush(tinted(feedback, 86) if strength else tinted(color, 74 if pressed else 16))
            painter.drawRoundedRect(key, 6, 6)
            painter.setPen(QColor("#f2f8ff"))
            painter.drawText(key, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_hit_burst(self, painter, field, lane, color, strength, *, perfect):
        rise = 1 - strength
        top = field.hit_y - 34 - 27 * rise
        bottom = field.hit_y
        beam = QLinearGradient(0, top, 0, bottom)
        beam.setColorAt(0, tinted(color, 0))
        beam.setColorAt(1, tinted(color, round(144 * strength)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(beam)
        painter.drawPolygon(field.strip(lane + 0.16, lane + 0.84, top, bottom))

        accent = PERFECT_EFFECT_COLOR if perfect else color.lighter(138)
        center = field.x(lane + 0.5, bottom)
        ring_width = (field.x(lane + 1, bottom) - field.x(lane, bottom)) * (0.38 + 0.58 * rise)
        ring_height = 6 + 14 * rise
        painter.setPen(QPen(tinted(accent, round(208 * strength)), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(center - ring_width / 2, bottom - 4 - ring_height / 2, ring_width, ring_height))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tinted(accent, round(240 * strength)))
        sparks = HIT_SPARKS if perfect else HIT_SPARKS[:4]
        radius = (3.7 if perfect else 2.4) * (1 - 0.4 * rise)
        for offset, spread, travel in sparks:
            y = bottom - 9 - travel * rise
            x = field.x(lane + 0.5 + offset * (1 + spread * rise), y)
            painter.drawPolygon(QPolygonF([
                QPointF(x, y - radius * 1.4), QPointF(x + radius, y),
                QPointF(x, y + radius * 1.4), QPointF(x - radius, y),
            ]))

    def _draw_hold_aura(self, painter, field, lane, y):
        top = y - 40
        aura = QLinearGradient(0, top, 0, y)
        aura.setColorAt(0, tinted(HOLD_COLOR, 0))
        aura.setColorAt(1, tinted(HOLD_COLOR, 106))
        painter.setBrush(aura)
        painter.drawPolygon(field.strip(lane + 0.08, lane + 0.92, top, y))
        for edge in (0.21, 0.76):
            painter.drawPolygon(field.strip(lane + edge, lane + edge + 0.03, top, y))
