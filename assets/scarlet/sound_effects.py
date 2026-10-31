"""Preloaded, bounded sound-effect voices for Scarlet Refrain."""

from time import monotonic

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QSoundEffect

from assets.scarlet import SFX_DIR


# name: (volume, minimum interval, voice count)
SOUNDS = {
    "bossattack1": (.35, .12, 2),
    "bossattack2": (.4, .12, 2),
    "bossdamage": (.22, .09, 2),
    "bossdeath": (.6, .5, 1),
    "extendlife": (.5, .5, 1),
    "graze": (.3, .06, 2),
    "itempickup": (.35, .06, 2),
    "menuinteract": (.35, .035, 2),
    "playerdeath": (.6, .5, 1),
    "playershoot": (.18, .09, 2),
    "timeout": (.45, .4, 1),
}


def make_sound_effect(parent):
    return QSoundEffect(parent)


class SoundEffects(QObject):
    def __init__(self, parent=None, *, directory=SFX_DIR, effect_factory=None, clock=monotonic):
        super().__init__(parent)
        self.muted = False
        self.effects = {}
        self._last_play = {}
        self._clock = clock
        factory = effect_factory or make_sound_effect
        for name, (volume, _interval, voices) in SOUNDS.items():
            path = directory / f"{name}.wav"
            if not path.is_file():
                continue
            pool = []
            for _ in range(voices):
                effect = factory(self)
                effect.setLoopCount(1)
                effect.setVolume(volume)
                effect.setSource(QUrl.fromLocalFile(str(path.resolve())))
                pool.append(effect)
            self.effects[name] = pool

    def play(self, name):
        if self.muted or name not in self.effects:
            return
        now = self._clock()
        if now - self._last_play.get(name, float("-inf")) < SOUNDS[name][1]:
            return
        for effect in self.effects[name]:
            # Never queue loading sounds: a delayed gunshot after pause/close
            # would be misleading. Missing audio devices also fail quietly.
            if effect.status() == QSoundEffect.Status.Ready and not effect.isPlaying():
                effect.play()
                self._last_play[name] = now
                return

    def play_many(self, events):
        events = list(dict.fromkeys(events))
        for event in events:
            if "bossdeath" in events and event in {"bossdamage", "bossattack1", "bossattack2", "playershoot"}:
                continue
            self.play(event)

    def set_muted(self, muted):
        muted = bool(muted)
        if self.muted == muted:
            return
        self.muted = muted
        if self.muted:
            self.stop()

    def stop(self):
        for pool in self.effects.values():
            for effect in pool:
                effect.stop()
        self._last_play.clear()
