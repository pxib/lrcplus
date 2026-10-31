"""Classic 4:3 presentation for Scarlet Refrain, drawn in 640 × 480 units."""

from math import atan2, cos, exp, pi, sin
from random import Random

from PySide6.QtCore import QPointF, QRectF, Qt
from PySide6.QtGui import (
    QAbstractTextDocumentLayout, QColor, QFont, QImage, QLinearGradient,
    QPainter, QPainterPath, QPalette, QPen, QPixmap, QPolygonF, QRadialGradient,
    QTextDocument, QTextOption,
)

from core.fonts import library_font
from assets.scarlet import CHAR_DIR, PORTRAIT_DIR
from assets.scarlet.danmaku import HomingShot
from assets.scarlet.dialogue import INTRO_DIALOGUE as DIALOGUE, NAMES


class ScarletScene:
    WIDTH, HEIGHT = 640, 480
    FIELD = QRectF(32, 16, 384, 448)

    def __init__(self):
        self.font = library_font(QFont())
        self.boss_sprite = QPixmap(str(CHAR_DIR / "flandre.png"))
        self.portraits = {
            name: QPixmap(str(PORTRAIT_DIR / f"{name}-portrait.png"))
            for name in ("reimu", "flandre")
        }
        self.texture = self._make_texture()
        self.music_status = ""
        self.language = "ja"
        self.reset_presentation()

    def reset_presentation(self):
        self.focus_alpha = 0.0
        self.portrait_emphasis = {"reimu": 0.0, "flandre": 0.0}
        self.story_phase = "entrance"
        self.dialogue_script = DIALOGUE
        self.boss_position = (self.FIELD.width() / 2, -90)
        self.entrance_progress = 0.0
        self.music_cue_remaining = 0.0

    def update_presentation(self, dt, focused, dialogue_index, in_dialogue, paused=False):
        dt = max(0.0, min(dt, .1))
        target = 1.0 if focused else 0.0
        speed = dt / (.14 if focused else .2)
        self.focus_alpha += max(-speed, min(speed, target - self.focus_alpha))
        speaker = self.dialogue_script[dialogue_index].speaker if in_dialogue else None
        if not paused:
            self.music_cue_remaining = max(0.0, self.music_cue_remaining - dt)
        blend = 0.0 if paused else 1 - exp(-dt / .1)
        for name, value in self.portrait_emphasis.items():
            target = 1.0 if name == speaker else 0.0
            value += (target - value) * blend
            self.portrait_emphasis[name] = target if abs(target - value) < .001 else value

    @staticmethod
    def _make_texture():
        rng = Random(495)
        image = QImage(128, 128, QImage.Format.Format_RGB32)
        for y in range(128):
            for x in range(128):
                grain = rng.randrange(-7, 8) + int(2 * sin(x * 0.13 + y * 0.17))
                image.setPixelColor(x, y, QColor(43 + grain, 41 + grain, 53 + grain))
        return QPixmap.fromImage(image)

    def text(self, p, x, y, text, size=14, color="#eee9e8", bold=False, italic=False):
        font = QFont(self.font)
        font.setPixelSize(size)
        font.setBold(bold)
        font.setItalic(italic)
        p.setFont(font)
        p.setPen(QColor(8, 5, 12, 220))
        p.drawText(QPointF(x + 1.4, y + 1.6), text)
        p.setPen(QColor(color))
        p.drawText(QPointF(x, y), text)

    @staticmethod
    def star(p, x, y, color, radius=6):
        points = []
        for i in range(10):
            angle = -pi / 2 + i * pi / 5
            r = radius if i % 2 == 0 else radius * 0.43
            points.append(QPointF(x + cos(angle) * r, y + sin(angle) * r))
        p.setPen(QPen(QColor("#190e1c"), 1))
        p.setBrush(QColor(color))
        p.drawPolygon(QPolygonF(points))

    def background(self, p, elapsed):
        gradient = QLinearGradient(0, 0, 0, 448)
        gradient.setColorAt(0, QColor("#410000"))
        gradient.setColorAt(0.4, QColor("#76030a"))
        gradient.setColorAt(0.74, QColor("#93383d"))
        gradient.setColorAt(1, QColor("#c48d85"))
        p.fillRect(QRectF(0, 0, 384, 448), gradient)
        # A slowly scrolling bank of painted mist, rather than the old grid.
        p.setPen(Qt.PenStyle.NoPen)
        for i in range(7):
            x = (i * 113 - elapsed * (7 + i % 3)) % 620 - 120
            y = 295 + i * 24 + 13 * sin(elapsed * 0.18 + i)
            fog = QRadialGradient(QPointF(x, y), 145)
            fog.setColorAt(0, QColor(239, 200, 187, 28))
            fog.setColorAt(1, QColor(239, 200, 187, 0))
            p.setBrush(fog)
            p.drawEllipse(QPointF(x, y), 175, 35)
        for i in range(-1, 7):
            p.save()
            p.translate(-8 + 12 * sin(i * 2), (i * 94 + elapsed * 12) % 650 - 100)
            p.setBrush(Qt.BrushStyle.NoBrush)
            path = QPainterPath(QPointF(0, -20))
            path.cubicTo(85, -8, 62, 20, 25, 12)
            path.cubicTo(-5, 6, 4, 34, 40, 32)
            path.cubicTo(100, 22, 100, 54, 60, 56)
            path.cubicTo(10, 59, 52, 84, 82, 72)
            p.setPen(QPen(QColor(12, 0, 4, 65), 7))
            p.drawPath(path)
            p.setPen(QPen(QColor(216, 190, 174, 65), 2.5))
            p.drawPath(path)
            p.restore()
        shade = QLinearGradient(0, 0, 384, 0)
        shade.setColorAt(0, QColor(0, 0, 0, 75))
        shade.setColorAt(0.2, QColor(0, 0, 0, 0))
        shade.setColorAt(0.85, QColor(0, 0, 0, 0))
        shade.setColorAt(1, QColor(0, 0, 0, 55))
        p.fillRect(QRectF(0, 0, 384, 448), shade)

    @staticmethod
    def magic_circle(p, x, y, elapsed):
        p.save()
        p.translate(x, y)
        p.rotate(elapsed * 16)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, alpha in ((7, 18), (3, 45), (1, 170)):
            p.setPen(QPen(QColor(255, 203, 198, alpha), width))
            p.drawEllipse(QPointF(0, 0), 70, 70)
            p.drawEllipse(QPointF(0, 0), 62, 62)
            for offset in (0, pi):
                points = [QPointF(59 * cos(offset - pi / 2 + i * 2 * pi / 3),
                                  59 * sin(offset - pi / 2 + i * 2 * pi / 3)) for i in range(3)]
                p.drawPolygon(QPolygonF(points))
        p.setPen(QPen(QColor(255, 213, 205, 120), 1))
        for i in range(36):
            angle = i * pi / 18
            p.drawLine(QPointF(64 * cos(angle), 64 * sin(angle)),
                       QPointF(68 * cos(angle + .025), 68 * sin(angle + .025)))
        p.restore()

    @staticmethod
    def character(p, x, y, boss=False):
        p.save()
        p.translate(x, y)
        p.setPen(Qt.PenStyle.NoPen)
        if boss:
            p.setPen(QPen(QColor("#191322"), 2))
            for direction in (-1, 1):
                p.drawPolyline(QPolygonF([QPointF(5 * direction, -5), QPointF(20 * direction, -18),
                                          QPointF(34 * direction, -10)]))
                for i, color in enumerate(("#84d8fa", "#a1eb84", "#fff394", "#ffaf7b")):
                    xx = direction * (16 + i * 5)
                    yy = -16 + i * 2
                    p.setPen(Qt.PenStyle.NoPen)
                    p.setBrush(QColor(color))
                    p.drawPolygon(QPolygonF([QPointF(xx, yy), QPointF(xx - 3, yy + 6),
                                             QPointF(xx, yy + 10), QPointF(xx + 3, yy + 6)]))
            p.setPen(Qt.PenStyle.NoPen)
        p.setBrush(QColor("#d72635"))
        p.drawPolygon(QPolygonF([QPointF(-6, -3), QPointF(-13, 17), QPointF(14, 17), QPointF(6, -3)]))
        p.setBrush(QColor("#fff1da"))
        p.drawEllipse(QPointF(0, -10), 7, 8)
        p.setBrush(QColor("#ebd17b" if boss else "#473447"))
        p.drawEllipse(QRectF(-8, -18, 16, 10))
        if boss:
            p.drawEllipse(QRectF(-9, -12, 4, 14))
            p.drawEllipse(QRectF(5, -12, 4, 14))
            p.setBrush(QColor("#fff3df"))
            p.drawEllipse(QRectF(-11, -22, 22, 8))
        p.setBrush(QColor("#d72635"))
        p.drawPolygon(QPolygonF([QPointF(-8, -22), QPointF(0, -18), QPointF(-8, -14)]))
        p.drawPolygon(QPolygonF([QPointF(8, -22), QPointF(0, -18), QPointF(8, -14)]))
        p.setBrush(QColor("#fff5e5"))
        p.drawEllipse(QRectF(-13, -3, 7, 6))
        p.drawEllipse(QRectF(6, -3, 7, 6))
        p.restore()

    def actors(self, p, game, sprite, direction):
        bx, by = self.boss_position if self.boss_position is not None else game.boss
        self.magic_circle(p, bx, by, game.elapsed)
        if self.boss_sprite.isNull():
            self.character(p, bx, by, boss=True)
        else:
            p.save()
            p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
            # The supplied 49 × 71 sprite has wings above and to the right.
            # Anchor her body, rather than the image bounds, to the boss center.
            p.drawPixmap(QPointF(bx - 17, by - 39), self.boss_sprite)
            p.restore()
        p.setPen(Qt.PenStyle.NoPen)
        for shot in game.shots:
            if isinstance(shot, HomingShot):
                p.save()
                p.translate(shot.x, shot.y)
                p.rotate(atan2(shot.vy, shot.vx) * 180 / pi + 90)
                colors = ("#ff8fbb", "#84e6ff", "#ffe985", "#bc9cff")
                p.setPen(QPen(QColor("#fff5df"), .8))
                p.setBrush(QColor(colors[shot.color]))
                p.drawPolygon(QPolygonF([QPointF(0, -9), QPointF(6, 0),
                                         QPointF(0, 9), QPointF(-6, 0)]))
                p.setBrush(QColor("#fffce8"))
                p.drawEllipse(QPointF(0, 0), 1.5, 3)
                p.restore()
            else:
                p.setBrush(QColor("#c7fff4"))
                p.drawRoundedRect(QRectF(shot.x - 2, shot.y - 9, 4, 18), 1.5, 1.5)
        colors = ("#ff7990", "#80caff", "#dba2ff")
        for bullet in game.bullets:
            p.setBrush(QColor(colors[bullet.color]))
            p.drawEllipse(QPointF(bullet.x, bullet.y), 4.5, 6)
            p.setBrush(QColor("#fff5e9"))
            p.drawEllipse(QPointF(bullet.x - .5, bullet.y - 1), 2, 3)
        for item in game.items:
            p.fillRect(QRectF(item.x - 6, item.y - 6, 12, 12), QColor("#4376dc"))
            self.text(p, item.x - 3, item.y + 4, "点", 9, bold=True)
        if not game.invulnerable or int(game.invulnerable * 12) % 2 == 0:
            if sprite.sheet.isNull():
                self.character(p, game.x, game.y)
            else:
                p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, False)
                p.drawPixmap(QRectF(game.x - 16, game.y - 24, 32, 48), sprite.sheet,
                             QRectF(sprite.frame_rect(game.elapsed, direction)))
        if self.focus_alpha > 0:
            p.save()
            p.setOpacity(self.focus_alpha)
            p.setPen(QPen(QColor("#ffccdd"), 1))
            p.setBrush(Qt.BrushStyle.NoBrush)
            p.drawEllipse(QPointF(game.x, game.y), 20, 20)
            p.setPen(QPen(QColor("#fff8f2"), 1))
            p.setBrush(QColor("#e52f45"))
            p.drawEllipse(QPointF(game.x, game.y), 2.5, 2.5)
            p.restore()
        if game.bomb_flash:
            p.fillRect(QRectF(0, 0, 384, 448), QColor(255, 222, 210, int(130 * game.bomb_flash / .65)))
        if game.deathbomb_flash > 0:
            p.save()
            p.setOpacity(min(1.0, game.deathbomb_flash / .2))
            label = "喰らいボム！" if self.language == "ja" else "Deathbomb!"
            self.text(p, max(8, min(292, game.x - 42)), game.y - 33, label, 12, "#fff0c9", bold=True)
            p.restore()

    def portrait(self, p, name, target):
        pixmap = self.portraits[name]
        if pixmap.isNull():
            return
        p.save()
        emphasis = self.portrait_emphasis[name]
        target = target.translated((12 if name == "reimu" else -12) * emphasis, 0)
        p.setOpacity(.43 + .57 * emphasis)
        p.setRenderHint(QPainter.RenderHint.SmoothPixmapTransform, True)
        scaled = pixmap.size().scaled(target.size().toSize(), Qt.AspectRatioMode.KeepAspectRatio)
        rect = QRectF(target.center().x() - scaled.width() / 2,
                      target.bottom() - scaled.height(), scaled.width(), scaled.height())
        p.drawPixmap(rect, pixmap, QRectF(pixmap.rect()))
        p.restore()

    def dialogue_document(self, line):
        document = QTextDocument()
        document.setDocumentMargin(0)
        font = QFont(self.font)
        font.setPixelSize(15 if self.language == "ja" else 13)
        document.setDefaultFont(font)
        option = QTextOption()
        option.setWrapMode(QTextOption.WrapMode.WrapAtWordBoundaryOrAnywhere)
        document.setDefaultTextOption(option)
        document.setPlainText(line.text(self.language))
        document.setTextWidth(296)
        return document

    def dialogue(self, p, index):
        line = self.dialogue_script[index]
        name = NAMES[line.speaker][self.language]
        self.portrait(p, "reimu", QRectF(-24, 185, 238, 288))
        self.portrait(p, "flandre", QRectF(155, 156, 254, 316))
        document = self.dialogue_document(line)
        top = 431 - max(67, document.size().height() + 35)
        p.fillRect(QRectF(36, top, 321, 431 - top), QColor(5, 3, 11, 192))
        self.text(p, 47, top - 8, name, 12, "#ffdfcd")
        context = QAbstractTextDocumentLayout.PaintContext()
        for offset, color in ((1.4, QColor(8, 5, 12, 220)), (0, QColor("#eee1ff"))):
            p.save()
            p.translate(49 + offset, top + 9 + offset)
            context.palette.setColor(QPalette.ColorRole.Text, color)
            document.documentLayout().draw(p, context)
            p.restore()
        hint = ("Z / Space：次へ　Esc：スキップ　L：English" if self.language == "ja"
                else "Z / Space: Next    Esc: Skip    L: 日本語")
        self.text(p, 47, 422, hint, 9, "#d1bfcd")

    def entrance_title(self, p):
        if self.entrance_progress <= .35:
            return
        p.save()
        p.setOpacity(min(1.0, (self.entrance_progress - .35) / .25))
        lines = (("悪魔の妹", "フランドール・スカーレット") if self.language == "ja"
                 else ("The Devil's Little Sister", "Flandre Scarlet"))
        for y, text, size in ((222, lines[0], 15), (252, lines[1], 20)):
            font = QFont(self.font)
            font.setPixelSize(size)
            p.setFont(font)
            p.setPen(QColor("#fff0e5"))
            p.drawText(QRectF(0, y, 384, 30), Qt.AlignmentFlag.AlignCenter, text)
        p.restore()

    def overlay(self, p, game):
        p.fillRect(QRectF(0, 0, 384, 448), QColor(10, 3, 13, 175))
        title, subtitle = {
            "paused": ("一時停止", "Space / P / Esc で再開"),
            "won": ("弾幕クリア！", "Space でもう一度"),
            "lost": ("満身創痍", "Space でもう一度"),
        }[game.state]
        font = QFont(self.font)
        font.setPixelSize(30)
        p.setFont(font)
        p.setPen(QColor("#fff1df"))
        p.drawText(QRectF(0, 185, 384, 48), Qt.AlignmentFlag.AlignCenter, title)
        font.setPixelSize(13)
        p.setFont(font)
        p.drawText(QRectF(0, 240, 384, 30), Qt.AlignmentFlag.AlignCenter, subtitle)

    def sidebar(self, p, game, high_score, fps):
        self.text(p, 434, 67, "最高得点", 14)
        self.text(p, 497, 67, f"{max(high_score, game.score):09d}", 19, italic=True)
        self.text(p, 434, 92, "得点", 14)
        self.text(p, 497, 92, f"{game.score:09d}", 19, italic=True)
        self.text(p, 434, 133, "Player", 16, "#e6d9d3")
        self.text(p, 434, 157, "Bomb", 16, "#c5ddab")
        for i in range(game.lives):
            self.star(p, 507 + i * 16, 127, "#db4556")
        for i in range(game.bombs):
            self.star(p, 507 + i * 16, 151, "#8bbd83")
        self.text(p, 434, 195, "Power", 16, "#ec6678")
        p.fillRect(QRectF(497, 180, 126, 17), QColor("#aaa1b7"))
        self.text(p, 500, 194, "ＭＡＸ", 15, "#ffffff")
        self.text(p, 434, 218, "Graze", 16, "#6caf5e")
        self.text(p, 497, 218, f"{game.graze:03d}", 19, italic=True)
        self.text(p, 434, 241, "点", 15)
        self.text(p, 497, 241, f"{game.point_items:03d}", 19, italic=True)
        self.text(p, 434, 266, self.music_status, 10, "#c9baca")
        language_hint = "会話：日本語［L: English］" if self.language == "ja" else "Dialogue: English [L: 日本語]"
        self.text(p, 434, 284, language_hint, 10, "#c9baca")
        # An original title seal in the arrangement of classic Touhou sidebars.
        self.text(p, 441, 332, "東方", 39, "#ada7b0", bold=True)
        p.setBrush(Qt.BrushStyle.NoBrush)
        for width, color in ((8, "#60222e"), (4, "#d83a45"), (1, "#ffd4bc")):
            p.setPen(QPen(QColor(color), width))
            p.drawEllipse(QPointF(548, 369), 54, 54)
        p.save()
        p.translate(548, 369)
        for i in range(24):
            p.save()
            p.rotate(i * 15)
            p.setPen(QPen(QColor("#f8b0a9"), 1))
            p.drawLine(QPointF(-2, -51), QPointF(2, -47))
            p.restore()
        p.restore()
        self.text(p, 522, 393, "紅", 59, "#dd2637", bold=True)
        self.text(p, 521, 438, "残響", 36, "#b8b0bb", bold=True)
        self.text(p, 477, 455, "Scarlet Refrain", 10, "#d9c7cf", italic=True)
        self.text(p, 553, 476, f"{fps:05.2f}fps", 13, italic=True)

    def paint(self, p, game, sprite, direction, dialogue_index, high_score, fps):
        p.drawTiledPixmap(QRectF(0, 0, 640, 480), self.texture)
        p.save()
        p.translate(self.FIELD.topLeft())
        p.setClipRect(QRectF(0, 0, 384, 448))
        self.background(p, game.elapsed)
        self.actors(p, game, sprite, direction)
        if self.story_phase == "entrance":
            self.entrance_title(p)
        elif self.story_phase in {"intro", "outro"}:
            self.dialogue(p, dialogue_index)
        else:
            p.fillRect(QRectF(5, 4, 374, 4), QColor("#3b1526"))
            p.fillRect(QRectF(5, 4, 374 * game.hp / game.MAX_HP, 4), QColor("#f5d5cc"))
            self.text(p, 7, 25, game.PATTERNS[game.phase], 12)
            self.text(p, 339, 25, f"{max(0, int(game.DURATION - game.elapsed)):02d}", 15)
            if game.state in {"paused", "won", "lost"}:
                self.overlay(p, game)
        if self.music_cue_remaining > 0:
            p.save()
            p.setOpacity(min(1.0, self.music_cue_remaining / .5))
            cue = "♪ U.N.オーエンは彼女なのか？" if self.language == "ja" else "♪ U.N. Owen was Her?"
            self.text(p, 12, 46, cue, 12, "#f5ddc2")
            p.restore()
        p.restore()
        p.setPen(QPen(QColor("#18131d"), 1))
        p.setBrush(Qt.BrushStyle.NoBrush)
        p.drawRect(self.FIELD)
        self.sidebar(p, game, high_score, fps)
        self.text(p, 32, 476, "移動：矢印 / WASD　低速：Shift　射撃：Z　ボム：X　停止：Esc", 8, "#c0b1bf")
