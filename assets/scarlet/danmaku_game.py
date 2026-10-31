"""A hidden, self-contained Touhou-inspired miniature boss fight."""

from PySide6.QtCore import QElapsedTimer, QEvent, QRect, Qt, QTimer
from PySide6.QtGui import QColor, QPainter, QPixmap
from PySide6.QtWidgets import QDialog

from assets.scarlet import CHAR_DIR
from assets.scarlet.danmaku import DanmakuGame
from assets.scarlet.boss_music import BossTheme
from core.settings import load_all_settings, save_all_settings
from assets.scarlet.danmaku_scene import ScarletScene
from assets.scarlet.dialogue import BGM_CUE_INDEX, OUTRO_DIALOGUE
from assets.scarlet.sound_effects import SoundEffects


class ReimuSprite:
    """GabrielDrawingsxD's 3 × 8 character frames, each 32 × 48 pixels.

    The spare effects below the character rows are deliberately excluded.
    Banking starts with a short transition, then loops the last four frames.
    Simulation time freezes the animation automatically during a pause.
    """

    def __init__(self):
        self.sheet = QPixmap(str(CHAR_DIR / "reimu.png"))
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
    ENTRANCE_DURATION = 1.8
    GAME_KEYS = {Qt.Key.Key_Left, Qt.Key.Key_Right, Qt.Key.Key_Up, Qt.Key.Key_Down,
                 Qt.Key.Key_W, Qt.Key.Key_A, Qt.Key.Key_S, Qt.Key.Key_D,
                 Qt.Key.Key_Shift, Qt.Key.Key_Z, Qt.Key.Key_X, Qt.Key.Key_R,
                 Qt.Key.Key_P, Qt.Key.Key_M, Qt.Key.Key_L, Qt.Key.Key_Escape, Qt.Key.Key_Space, Qt.Key.Key_Return}

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
        self.music = BossTheme(self)
        self.sfx = SoundEffects(self)
        self.dialogue_index = 0
        self.entrance_elapsed = 0.0
        self._story_suspended = False
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
        self._story_suspended = False
        self.clock.start()
        self.timer.start()
        self.setFocus()
        self._sync_music()

    def hideEvent(self, event):
        self.timer.stop()
        self._pause()
        self._save_high_score()
        super().hideEvent(event)

    def closeEvent(self, event):
        self.timer.stop()
        self.keys.clear()
        self.music.stop()
        self.sfx.stop()
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
        self.music.stop()
        self.sfx.stop()
        self._save_high_score()
        self.game.reset()
        self.scene.reset_presentation()
        self.dialogue_index = 0
        self.entrance_elapsed = 0.0
        self._story_suspended = False
        self._player_direction = 0
        self.keys.clear()
        self.sfx.play("menuinteract")

    def _sync_music(self):
        state = self.game.state if self.story_phase == "combat" else "ready"
        if self.story_phase == "intro" and self.dialogue_index >= BGM_CUE_INDEX:
            state = "paused" if self._story_suspended else "playing"
        self.music.sync(state, self.isVisible())
        self.sfx.set_muted(self.music.muted)
        self.scene.music_status = self.music.status_label

    def _play_game_sounds(self):
        events = self.game.take_sound_events()
        if self.game.state == "lost" and "playerdeath" in events:
            self.sfx.stop()
            events = ["playerdeath"]
        self.sfx.play_many(events)

    @property
    def story_phase(self):
        return self.scene.story_phase

    def _update_story(self, dt):
        if self.story_phase != "entrance":
            return
        self.entrance_elapsed = min(self.ENTRANCE_DURATION, self.entrance_elapsed + max(0, dt))
        progress = self.entrance_elapsed / self.ENTRANCE_DURATION
        self.scene.entrance_progress = progress
        eased = 1 - (1 - progress) ** 3
        self.scene.boss_position = (self.game.WIDTH / 2, -90 + 176 * eased)
        if progress >= 1:
            self.scene.story_phase = "intro"
            self.scene.boss_position = self.game.boss
            self.keys.clear()

    def _start_combat(self):
        self.scene.story_phase = "combat"
        self.scene.boss_position = None
        if self.game.state == "ready":
            self.game.start_or_pause()
        self.keys.clear()
        self._sync_music()

    def _check_battle_end(self):
        if self.game.state == "won" and self.story_phase == "combat":
            self.sfx.stop()
            self.scene.story_phase = "outro"
            self.scene.dialogue_script = OUTRO_DIALOGUE
            self.scene.boss_position = self.game.boss
            self.scene.music_cue_remaining = 0.0
            self.dialogue_index = 0
            self.game.bullets.clear()
            self.game.shots.clear()
            self.keys.clear()
            self._save_high_score()
            self._sync_music()

    def _advance_dialogue(self):
        if self.story_phase not in {"intro", "outro"}:
            return
        self.sfx.play("menuinteract")
        if self.dialogue_index + 1 < len(self.scene.dialogue_script):
            self.dialogue_index += 1
            if self.story_phase == "intro" and self.scene.dialogue_script[self.dialogue_index].start_bgm:
                self.scene.music_cue_remaining = 3.0
        elif self.story_phase == "intro":
            self._start_combat()
        else:
            self.scene.story_phase = "complete"
        self.keys.clear()
        self._sync_music()

    def _pause(self):
        self.keys.clear()
        self.sfx.stop()
        self.game.take_sound_events()
        self._story_suspended = True
        self.game.pause()
        self._sync_music()
        self.update()

    def event(self, event):
        if event.type() == QEvent.Type.WindowDeactivate:
            self._pause()
        elif event.type() == QEvent.Type.WindowActivate:
            self._story_suspended = False
            self._sync_music()
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
        if key == Qt.Key.Key_M:
            self.music.toggle_mute()
            self.keys.discard(key)
            self._sync_music()
            self.sfx.play("menuinteract")
            self.update()
            return
        if key == Qt.Key.Key_L:
            self.scene.language = "en" if self.scene.language == "ja" else "ja"
            self.keys.discard(key)
            self.sfx.play("menuinteract")
            self.update()
            return
        if self.story_phase in {"entrance", "intro", "outro"}:
            if key in (Qt.Key.Key_Z, Qt.Key.Key_Space, Qt.Key.Key_Return):
                self._advance_dialogue()
            elif key == Qt.Key.Key_Escape:
                self.sfx.play("menuinteract")
                if self.story_phase == "outro":
                    self.scene.story_phase = "complete"
                    self.keys.clear()
                else:
                    self._start_combat()
            elif key == Qt.Key.Key_R:
                self._restart()
            self._sync_music()
            self.update()
            return
        if key in (Qt.Key.Key_Space, Qt.Key.Key_Return):
            if self.game.state in {"won", "lost"}:
                self._restart()
            else:
                self.game.start_or_pause()
            self.keys.clear()
            if self.game.state == "paused":
                self.sfx.stop()
            self.sfx.play("menuinteract")
        elif key in (Qt.Key.Key_P, Qt.Key.Key_Escape):
            self.game.start_or_pause()
            self.keys.clear()
            if self.game.state == "paused":
                self.sfx.stop()
            self.sfx.play("menuinteract")
        elif key == Qt.Key.Key_R:
            self._restart()
        elif key == Qt.Key.Key_X:
            self.game.bomb()
            self._check_battle_end()
        self._play_game_sounds()
        self._sync_music()
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
        focused = held(Qt.Key.Key_Shift)
        if not self._story_suspended:
            self._update_story(min(dt, .1))
        self.scene.update_presentation(
            dt, focused and self.game.state == "playing",
            self.dialogue_index, self.story_phase in {"intro", "outro"},
            paused=self._story_suspended,
        )
        horizontal = int(held(Qt.Key.Key_Right, Qt.Key.Key_D)) - int(held(Qt.Key.Key_Left, Qt.Key.Key_A))
        if self.game.state == "playing":
            self._player_direction = horizontal
        self.game.update(
            dt,
            horizontal,
            int(held(Qt.Key.Key_Down, Qt.Key.Key_S)) - int(held(Qt.Key.Key_Up, Qt.Key.Key_W)),
            focused, held(Qt.Key.Key_Z),
        )
        self._check_battle_end()
        self._play_game_sounds()
        if self.game.state in {"won", "lost"}:
            self._save_high_score()
        self._sync_music()
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
            self.dialogue_index, self.high_score, self.fps,
        )
        painter.end()
