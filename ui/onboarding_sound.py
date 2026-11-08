"""Nonblocking playback of bundled onboarding reactions, separate from music."""

import logging
import re
import wave
from pathlib import Path

from PySide6.QtCore import QObject, QUrl
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer


SOUND_DIR = Path(__file__).resolve().parent.parent / "assets" / "onboarding" / "sound"
SOUND_NAME = re.compile(r"(?:[a-z_0-9-]+[/\\])*[a-z_0-9-]+(?:\.(?:ogg|mp3|wav))?", re.IGNORECASE)
REACTION_LABELS = {
    "adore": "Adoring", "angry": "Angry", "confused": "Confused",
    "idea": "Inspired", "interested": "Curious", "interesting": "Intrigued",
    "love": "Delighted", "offended": "Offended", "playful": "Playful",
    "responsereceived": "Listening", "sad": "Sad", "shy": "Shy",
    "sigh": "Sigh...", "sleeping": "Sleepy", "speechless": "Speechless",
    "steaming": "Fuming", "surprised": "Surprised", "sweating": "Flustered",
    "thinking": "Thinking...",
}


def sound_expression(name):
    """Describe the script's reaction independently of audio playback or mute."""
    if not isinstance(name, str) or not SOUND_NAME.fullmatch(name):
        return ""
    stem = Path(name.replace("\\", "/")).stem.lower()
    return REACTION_LABELS.get(stem, stem.replace("_", " ").replace("-", " ").capitalize())


def resolve_sound(name, directory=SOUND_DIR):
    if not isinstance(name, str) or not SOUND_NAME.fullmatch(name):
        return None
    name = name.replace("\\", "/")
    root = directory.resolve()
    candidates = [root / name] if Path(name).suffix else [root / (name + ext) for ext in (".ogg", ".mp3", ".wav")]
    for candidate in candidates:
        resolved = candidate.resolve()
        if resolved.is_relative_to(root) and resolved.is_file():
            return resolved
    return None


def voice_duration_ms(name, directory=SOUND_DIR):
    """Return the duration of a bundled WAV voice clip, if readable."""
    path = resolve_sound(name, directory)
    if path is None or path.suffix.lower() != ".wav":
        return None
    try:
        with wave.open(str(path), "rb") as clip:
            return round(1000 * clip.getnframes() / clip.getframerate())
    except (OSError, EOFError, wave.Error, ZeroDivisionError):
        return None


def make_media_player(parent):
    return QMediaPlayer(parent)


def make_audio_output(parent):
    return QAudioOutput(parent)


class OnboardingSounds(QObject):
    def __init__(self, parent=None, *, enabled=True, directory=SOUND_DIR):
        super().__init__(parent)
        self.enabled = enabled
        self.directory = directory
        self.player = None
        self.output = None
        self._closed = False

    def play(self, name):
        if self._closed or not self.enabled:
            return False
        try:
            path = resolve_sound(name, self.directory)
        except OSError:
            path = None
        if path is None:
            logging.warning("Onboarding sound %r is missing or invalid", name)
            return False
        if self.player is None:
            self.player = make_media_player(self)
            self.output = make_audio_output(self)
            self.output.setVolume(0.45)
            self.player.setAudioOutput(self.output)
            self.player.errorOccurred.connect(self._playback_error)
        # Reactions share one channel, so rapid advances never stack voices.
        self.player.stop()
        self.player.setSource(QUrl.fromLocalFile(str(path)))
        self.player.play()
        return True

    def _playback_error(self, error, message):
        logging.warning("Unable to play onboarding sound: %s", message)

    def set_enabled(self, enabled):
        self.enabled = bool(enabled)
        if not self.enabled:
            self.stop()

    def stop(self):
        if self.player is not None:
            self.player.stop()

    def close(self):
        self._closed = True
        self.stop()
        if self.player is not None:
            self.player.setSource(QUrl())
