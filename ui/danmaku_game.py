"""A hidden, self-contained Touhou-inspired miniature boss fight."""

from pathlib import Path

from PySide6.QtCore import QElapsedTimer, QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QDialog

from core.danmaku import DanmakuGame
from core.settings import load_all_settings, save_all_settings
from ui.danmaku_scene import DIALOGUE, ScarletScene


class ReimuSprite:
    """GabrielDrawingsxD's 3 × 8 character frames, each 32 × 48 pixels.

    The spare effects below the character rows are deliberately excluded.
    Banking starts with a short transition, then loops the last four frames.
    Simulation time freezes the animation automatically during a pause.
    """

    def __init__(self):
        self.sheet = QPixmap(str(Path(__file__).resolve().parents[1] / "assets" / "reimu.png"))
        self._direction = 0
        self._started_at = 0.0
        self._last_time = 0.0

    def frame_rect(self, elapsed, direction=0):
        direction = (direction > 0) - (direction < 0)
        if direction != self._direction or elapsed < self._last_time:
            self._direction = direction
            self._started_at = elapsed
        self._last_time = elapsed
        tick = int(max(0, elapsed - self._started_at) * 10 + 1e-8)
        column = tick % 8 if direction == 0 else (tick if tick < 4 else 4 + (tick - 4) % 4)
        row = 1 if direction < 0 else 2 if direction > 0 else 0
        return QRect(column * 32, row * 48, 32, 48)


class DanmakuWindow(QDialog):
    GAME_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                 Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D,
                 Qt.Key.Key_Shift, Qt.Key.Key_Z, Qt.Key.Key_X, Qt.Key.Key_R,
                 Qt.Key.Key_P, Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return}

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setWindowTitle("紅い残響 ～ Scarlet Refrain")
        self.setWindowModality(Qt.WindowModality.NonModal)
        self.setMinimumSize(640, 480)
        self.resize(960, 720)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self.setAccessibleName("Scarlet Refrain bullet dodging game")
        self.game = DanmakuGame()
        self.player_sprite = ReimuSprite()
        self.scene = ScarletScene()
        self.dialogue_index = 0
        self.high_score = self._read_high_score()
        self.fps = 60.0
        self._fps_seconds = 0.0
        self._fps_frames = 0
        self._player_direction = 0
        self.keys = set()
        self.clock = QElapsedTimer()
        self.timer = QTimer(self)
        self.timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.timer.setInterval(16)
        self.timer.timeout.connect(self._tick)

    def showEvent(self, event):
        super().showEvent(event)
        self.clock.start()
        self.timer.start()
        self.setFocus()

    def hideEvent(self, event):
        self.timer.stop()
        self._pause()
        self._save_high_score()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        self.keys.clear()
        self._save_high_score()
        super().closeEvent(event)

    @staticmethod
    def _read_high_score():
        record = load_all_settings().get("easter_eggs", {})
        try:
            return max(0, int(record.get("scarlet_high_score", 0))) if isinstance(record, dict) else 0
        except (TypeError, ValueError, OverflowError):
            return 0

    def _save_high_score(self):
        if self.game.score <= self.high_score:
            return
        self.high_score = self.game.score
        payload = load_all_settings()
        record = payload.get("easter_eggs", {})
        record = dict(record) if isinstance(record, dict) else {}
        record["scarlet_high_score"] = max(self._read_high_score(), self.high_score)
        payload["easter_eggs"] = record
        try:
            save_all_settings(payload)
        except OSError:
            pass  # An unwritable preferences file must not interrupt the game.

    def _restart(self):
        self._save_high_score()
        self.game.reset()
        self.dialogue_index = 0
        self._player_direction = 0
        self.keys.clear()

    def _advance_dialogue(self):
        if self.dialogue_index + 1 < len(DIALOGUE):
            self.dialogue_index += 1
        else:
            self.game.start_or_pause()
        self.keys.clear()

    def _pause(self):
        self.keys.clear()
        self.game.pause()
        self.update()

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self._pause()
        elif event.type() == QEvent.Type.ShortcutOverride and event.key() in self.GAME_KEYS:
            event.accept()
            return True
        return super().event(event)

    def focusOutEvent(self, event):
        self._pause()
        super().focusOutEvent(event)

    def keyPressEvent(self, event):
        key = event.key()
        if key not in self.GAME_KEYS:
            super().keyPressEvent(event)
            return
        event.accept()
        if event.isAutoRepeat():
            return
        self.keys.add(key)
        if self.game.state == "ready":
            if key in (Qt.Key.Key_Z, Qt.Key.Key_Space, Qt.Key.Key_Return):
                self._advance_dialogue()
            elif key == Qt.Key.Key_Escape:
                self.game.start_or_pause()
                self.keys.clear()
            elif key == Qt.Key.Key_R:
                self._restart()
            self.update()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            if self.game.state in {"won", "lost"}:
                self._restart()
            else:
                self.game.start_or_pause()
            self.keys.clear()
        elif key in (Qt.Key.Key_P, Qt.Key.Key_Escape):
            self.game.start_or_pause()
            self.keys.clear()
        elif key == Qt.Key.Key_R:
            self._restart()
        elif key == Qt.Key.Key_X:
            self.game.bomb()
        self.update()

    def keyReleaseEvent(self, event):
        if event.key() in self.GAME_KEYS:
            if not event.isAutoRepeat():
                self.keys.discard(event.key())
            event.accept()
        else:
            super().keyReleaseEvent(event)

    def _tick(self):
        dt = self.clock.restart() / 1000
        self._fps_frames += 1
        self._fps_seconds += dt
        if self._fps_seconds >= .5:
            self.fps = self._fps_frames / self._fps_seconds
            self._fps_frames = 0
            self._fps_seconds = 0.0
        held = lambda *keys: any(key in self.keys for key in keys)
        horizontal = int(held(Qt.Key.Key_Right, Qt.Key.Key_D)) - int(held(Qt.Key.Key_Left, Qt.Key.Key_A))
        if self.game.state == "playing":
            self._player_direction = horizontal
        self.game.update(
            dt,
            horizontal,
            int(held(Qt.Key.Key_Down, Qt.Key.Key_S)) - int(held(Qt.Key.Key_Up, Qt.Key.Key_W)),
            held(Qt.Key.Key_Shift), held(Qt.Key.Key_Z),
        )
        if self.game.state in {"won", "lost"}:
            self._save_high_score()
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing)
        painter.fillRect(self.rect(), QColor("#000000"))
        scale = min(self.width() / 640, self.height() / 480)
        painter.translate((self.width() - 640 * scale) / 2, (self.height() - 480 * scale) / 2)
        painter.scale(scale, scale)
        self.scene.paint(
            painter, self.game, self.player_sprite, self._player_direction,
            Qt.Key.Key_Shift in self.keys, self.dialogue_index, self.high_score, self.fps,
        )
        painter.end()
