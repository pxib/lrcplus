"""Isolated MIDI theme playback for the hidden boss fight."""

import ctypes
import sys
from contextlib import suppress
from pathlib import Path
from uuid import uuid4

from PySide6.QtCore import QObject, QTimer

from assets.scarlet import BGM_DIR


THEME_PATH = BGM_DIR / "flandre_theme.MID"


class MidiPlaybackError(OSError):
    pass


class WindowsMidiFile:
    """Own exactly one Windows MCI sequencer; never change other audio devices."""

    def __init__(self, path):
        self.path = Path(path).resolve()
        self.alias = "lrcplus_boss_" + uuid4().hex
        self.opened = False
        self._winmm = ctypes.WinDLL("winmm")
        self._send = self._winmm.mciSendStringW
        self._send.argtypes = [ctypes.c_wchar_p, ctypes.c_wchar_p, ctypes.c_uint, ctypes.c_void_p]
        self._send.restype = ctypes.c_uint
        self._error_string = self._winmm.mciGetErrorStringW
        self._error_string.argtypes = [ctypes.c_uint, ctypes.c_wchar_p, ctypes.c_uint]
        self._error_string.restype = ctypes.c_int

    def command(self, text):
        buffer = ctypes.create_unicode_buffer(256)
        error = self._send(text, buffer, len(buffer), None)
        if error:
            self._error_string(error, buffer, len(buffer))
            raise MidiPlaybackError(f"MIDI error {error}: {buffer.value}")
        return buffer.value.strip()

    def play(self, restart=False):
        if not self.opened:
            self.command(f'open "{self.path}" type sequencer alias {self.alias} wait')
            self.opened = True
            try:
                self.command(f"set {self.alias} time format milliseconds")
            except MidiPlaybackError:
                self.close()
                raise
        self.command(f"play {self.alias}" + (" from 0" if restart else ""))

    def pause(self):
        if self.opened:
            self.command(f"pause {self.alias}")

    def mode(self):
        return self.command(f"status {self.alias} mode") if self.opened else "stopped"

    def close(self):
        if self.opened:
            # Still release the file/device if stop fails. Never issue 'close all'.
            with suppress(OSError):
                self.command(f"stop {self.alias} wait")
            with suppress(OSError):
                self.command(f"close {self.alias} wait")
            self.opened = False


def make_midi_backend(path):
    return WindowsMidiFile(path) if sys.platform == "win32" else None


class BossTheme(QObject):
    """Loop while the story requests music, suspending it during pauses and mute."""

    def __init__(self, parent=None, *, path=THEME_PATH, backend=None):
        super().__init__(parent)
        self.muted = False
        self.error = ""
        self._state = "ready"
        self._visible = False
        self._started = False
        self._playing = False
        self.backend = None
        self.timer = QTimer(self)
        self.timer.setInterval(250)
        self.timer.timeout.connect(self._poll)
        try:
            if not Path(path).is_file():
                raise MidiPlaybackError("Boss theme MIDI is missing")
            self.backend = backend if backend is not None else make_midi_backend(path)
        except OSError as error:
            self.error = str(error)
        if self.backend is not None:
            # QObject destruction also covers a parent deleted while still open.
            player = self.backend
            self.destroyed.connect(lambda _object=None: player.close())

    @property
    def status_label(self):
        if self.error:
            return "ＢＧＭ：再生不可"
        if self.backend is None:
            return "ＢＧＭ：未対応"
        return "ＢＧＭ：OFF［M］" if self.muted else "ＢＧＭ：ON［M］"

    def sync(self, state, visible=True):
        self._state, self._visible = state, visible
        if self.error or self.backend is None:
            return
        try:
            if visible and state == "playing" and not self.muted:
                if not self._playing:
                    restart = not self._started
                    self._started = True
                    self.backend.play(restart=restart)
                    self._playing = True
                    self.timer.start()
            elif visible and state in {"playing", "paused"}:
                if self._playing:
                    self.backend.pause()
                    self._playing = False
                self.timer.stop()
            else:
                self.stop()
        except OSError as error:
            self._fail(error)

    def toggle_mute(self):
        self.muted = not self.muted
        self.sync(self._state, self._visible)

    def _poll(self):
        if not self._playing:
            return
        try:
            if self.backend.mode() == "stopped":
                self.backend.play(restart=True)
        except OSError as error:
            self._fail(error)

    def _fail(self, error):
        self.error = str(error)
        self.stop()

    def stop(self):
        self.timer.stop()
        if self.backend is not None and self._started:
            self.backend.close()
        self._started = self._playing = False
