"""Four-lane charts and a keyboard playfield for Rhythm Games."""

from dataclasses import dataclass
from itertools import cycle, groupby, pairwise
from time import monotonic

from PySide6.QtCore import QPointF, QRectF, Qt, QTimer, QUrl, Signal
from PySide6.QtGui import QBrush, QColor, QFont, QLinearGradient, QPainter, QPainterPath, QPainterPathStroker, QPen, QPolygonF
from PySide6.QtMultimedia import QSoundEffect
from PySide6.QtWidgets import QWidget


LANE_KEYS = (Qt.Key.Key_D, Qt.Key.Key_F, Qt.Key.Key_J, Qt.Key.Key_K)
LANE_LABELS = ("D", "F", "J", "K")
LANE_PATTERN = (0, 2, 1, 3, 2, 0, 3, 1, 3, 1, 2, 0, 1, 3, 0, 2)
LANE_COLORS = (QColor("#81f9dd"), QColor("#b0a8ff"), QColor("#b0a8ff"), QColor("#81f9dd"))
CLASSIC_LANE_COLORS = (QColor("#4c99a5"), QColor("#d28d5f"), QColor("#d28d5f"), QColor("#4c99a5"))
HOLD_COLOR = QColor("#71edb2")
GOLD_NOTE_COLOR = QColor("#ffcf60")
FLICK_NOTE_COLOR = QColor("#ff9dc8")
PERFECT_EFFECT_COLOR = QColor("#ffdd91")
FEEDBACK_SECONDS = 0.24
JUDGEMENT_SECONDS = 1.0
JUDGEMENT_SCALE_SECONDS = 0.1
JUDGEMENT_LABELS = {300: "Perfect", 100: "Great", 50: "Good", 0: "Miss"}
JUDGEMENT_COLORS = {"Great": "#d9b9ff", "Good": "#ace0ff", "Bad": "#66f4f5", "Miss": "#b7bdc9"}
HIT_SPARKS = ((-0.19, 0.14, 27), (0.16, 0.18, 31), (-0.05, 0.31, 39),
              (0.07, 0.26, 45), (-0.27, 0.11, 16), (0.26, 0.12, 20))
MANIA_INSTRUCTIONS = "Tap D, F, J, or K at the hit line, including arrow notes. Hold long notes until their tails reach the line."
FLICK_GAP_MS = 1700


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
        self.golden_flicks = tuple(load("maniahitflickcritical") for _ in LANE_KEYS)
        self.golden_holds = tuple(load("maniaholdcritical") for _ in LANE_KEYS)
        for effect in (*self.holds, *self.golden_holds):
            effect.setLoopCount(QSoundEffect.Loop.Infinite.value)
        self.results = {name: load(name) for name in ("allperfect", "fullcombo", "clear", "fail")}
        self.effects = (*self.holds, *self.golden_taps, *self.golden_flicks, *self.golden_holds, *self.results.values(),
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
        if note.golden:
            variants = (self.golden_flicks[lane], golden[lane]) if note.flick else (golden[lane],)
            self.play(*variants, normal[lane], fallback)
        else:
            self.play(normal[lane], fallback)

    def stop_hold(self, lane):
        self.holds[lane].stop()
        self.golden_holds[lane].stop()

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


class ManiaBoard(QWidget):
    pressed = Signal(int)
    released = Signal(int)
    instructions = MANIA_INSTRUCTIONS

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
        self._held = set()
        self._feedback = {}
        self._judgement_label = None
        self._feedback_timer = QTimer(self, interval=16)
        self._feedback_timer.timeout.connect(self._advance_feedback)

    def set_theme(self, theme):
        self.theme = theme
        self.update()

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
        self._judgement_label = None
        self._feedback_timer.stop()
        self.update()

    def animate(self, judgement, *, early_release=False):
        note = self.notes[judgement.index]
        now = monotonic()
        self._feedback[note.lane] = judgement.value, now + FEEDBACK_SECONDS, note.golden
        label = "Bad" if early_release else JUDGEMENT_LABELS[judgement.value]
        self._judgement_label = label, now
        if not self._feedback_timer.isActive():
            self._feedback_timer.start()
        self.update()

    def _advance_feedback(self):
        now = monotonic()
        self._feedback = {lane: entry for lane, entry in self._feedback.items() if entry[1] > now}
        if self._judgement_label is not None and now - self._judgement_label[1] >= JUDGEMENT_SECONDS:
            self._judgement_label = None
        if not self._feedback and self._judgement_label is None:
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
        center, hit_y = self.width() / 2, self.height() - 49
        if self.theme == "classic":
            field = Playfield(center, min(408, self.width() - 32), 10, hit_y, top_scale=1)
            self._draw_classic_playfield(painter, field)
        else:
            field = Playfield(center, min(464, self.width() * 0.82), 14, hit_y)
            self._draw_playfield(painter, field)
            self._draw_receptors(painter, field)
        self._draw_notes(painter, field)
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
            value, deadline, golden = self._feedback.get(lane, (None, 0, False))
            if deadline > now:
                fill = QColor("#b44859") if value == 0 else GOLD_NOTE_COLOR if golden else color
                text = QColor("#17191c") if value else QColor("white")
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
        self._draw_chord_guides(painter, field, visible)
        painter.setPen(Qt.PenStyle.NoPen)
        classic = self.theme == "classic"
        colors = CLASSIC_LANE_COLORS if classic else LANE_COLORS
        for note, y, holding in visible:
            lane = note.lane
            is_hold = note.end_ms is not None
            color = HOLD_COLOR if is_hold and not classic else colors[lane]
            if note.flick:
                color = GOLD_NOTE_COLOR if note.golden else FLICK_NOTE_COLOR
                self._draw_flick_chevron(painter, field, lane, y, color)
            elif note.golden:
                color = GOLD_NOTE_COLOR
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
                        self._draw_hold_aura(painter, field, lane, y, color)

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
            color = self.palette().text().color() if self.theme == "classic" else QColor("#deedff")
            if all(note.golden for note, _ in pending):
                color = GOLD_NOTE_COLOR.darker(155) if self.theme == "classic" else PERFECT_EFFECT_COLOR
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
            painter.setBrush(tinted(QColor("#f5fffd"), 214))
            painter.drawRoundedRect(glint, scale, scale)

    def _draw_receptors(self, painter, field):
        now = monotonic()
        font = painter.font()
        font.setBold(True)
        painter.setFont(font)
        for lane, (label, color) in enumerate(zip(LANE_LABELS, LANE_COLORS)):
            pressed = lane in self._held
            value, deadline, golden = self._feedback.get(lane, (None, 0, False))
            strength = max(0, (deadline - now) / FEEDBACK_SECONDS)
            bright = golden or value == 300
            feedback = PERFECT_EFFECT_COLOR if bright else color
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
                    beam_color = GOLD_NOTE_COLOR if golden else color
                    self._draw_hit_burst(painter, field, lane, beam_color, strength, bright=bright)

            key = QRectF(left + 3, field.hit_y + 12, width - 6, 26)
            painter.setPen(QPen(feedback if strength else tinted(color, 200 if pressed else 100), 1))
            painter.setBrush(tinted(feedback, 86) if strength else tinted(color, 74 if pressed else 16))
            painter.drawRoundedRect(key, 6, 6)
            painter.setPen(QColor("#f2f8ff"))
            painter.drawText(key, Qt.AlignmentFlag.AlignCenter, label)

    def _draw_hit_burst(self, painter, field, lane, color, strength, *, bright):
        rise = 1 - strength
        top = field.hit_y - 34 - 27 * rise
        bottom = field.hit_y
        beam = QLinearGradient(0, top, 0, bottom)
        beam.setColorAt(0, tinted(color, 0))
        beam.setColorAt(1, tinted(color, round(144 * strength)))
        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(beam)
        painter.drawPolygon(field.strip(lane + 0.16, lane + 0.84, top, bottom))

        accent = PERFECT_EFFECT_COLOR if bright else color.lighter(138)
        center = field.x(lane + 0.5, bottom)
        ring_width = (field.x(lane + 1, bottom) - field.x(lane, bottom)) * (0.38 + 0.58 * rise)
        ring_height = 6 + 14 * rise
        painter.setPen(QPen(tinted(accent, round(208 * strength)), 1.6))
        painter.setBrush(Qt.BrushStyle.NoBrush)
        painter.drawEllipse(QRectF(center - ring_width / 2, bottom - 4 - ring_height / 2, ring_width, ring_height))

        painter.setPen(Qt.PenStyle.NoPen)
        painter.setBrush(tinted(accent, round(240 * strength)))
        sparks = HIT_SPARKS if bright else HIT_SPARKS[:4]
        radius = (3.7 if bright else 2.4) * (1 - 0.4 * rise)
        for offset, spread, travel in sparks:
            y = bottom - 9 - travel * rise
            x = field.x(lane + 0.5 + offset * (1 + spread * rise), y)
            painter.drawPolygon(QPolygonF([
                QPointF(x, y - radius * 1.4), QPointF(x + radius, y),
                QPointF(x, y + radius * 1.4), QPointF(x - radius, y),
            ]))

    def _draw_hold_aura(self, painter, field, lane, y, color):
        top = y - 40
        aura = QLinearGradient(0, top, 0, y)
        aura.setColorAt(0, tinted(color, 0))
        aura.setColorAt(1, tinted(color, 106))
        painter.setBrush(aura)
        painter.drawPolygon(field.strip(lane + 0.08, lane + 0.92, top, y))
        for edge in (0.21, 0.76):
            painter.drawPolygon(field.strip(lane + edge, lane + edge + 0.03, top, y))
