"""Local pitch processing through the out-of-process Rust audio probe."""

from importlib.util import find_spec
import os
from pathlib import Path
import sys

from PySide6.QtCore import QProcess, QTimer, Qt, QUrl, Signal
from PySide6.QtMultimedia import QMediaDevices, QMediaPlayer

from core.gapless import GaplessMediaPlayer
from core.native_audio import audio_probe_executable


def rust_playback_executable():
    if os.environ.get("LRCPLUS_RUST_PLAYBACK") != "0":
        return audio_probe_executable()


def _pitch_library():
    # Locate the DLL shipped with aubio without importing aubio or NumPy.
    if getattr(sys, "frozen", False):
        root = Path(sys._MEIPASS)
    else:
        spec = find_spec("aubio")
        if spec is None or not spec.origin:
            return None
        root = Path(spec.origin).resolve().parent.parent
    for directory in ("aubio_ledfx.libs", "aubio.libs"):
        library = next(iter(sorted((root / directory).glob("*rubberband*.dll"))), None)
        if library is not None:
            return library
    return None


State = QMediaPlayer.PlaybackState
Status = QMediaPlayer.MediaStatus
TRANSPORT_VERSION = b"4"
# 300% volume times the largest supported ReplayGain adjustment (+60 dB).
MAX_TRANSPORT_GAIN = 3000.0


class RustMediaPlayer(GaplessMediaPlayer):
    rustFallback = Signal(str)

    def __init__(self, parent=None, *, executable, **kwargs):
        super().__init__(parent, **kwargs)
        self._rust_executable = str(executable)
        self._rust_prefer_all = os.environ.get("LRCPLUS_RUST_PLAYBACK") == "1"
        self._rust_pitch_library = _pitch_library()
        self._rust_pitch = None
        self._rust_url = None
        self._rust_process = None
        self._rust_ready = False
        self._rust_compatible = False
        self._rust_preferred = False
        self._rust_gain = 1.0
        self._rust_rate = 1.0
        self.rust_audible_rate = 1.0
        self._rust_device_id = b""
        self._rust_position = self._rust_duration = self._rust_epoch = 0
        self.rust_output_sample_rate = 0
        self.rust_underrun_frames = 0
        self._rust_state = State.StoppedState
        self._rust_status = Status.NoMedia
        self._qt_resume = None
        self._rust_startup = QTimer(self)
        self._rust_startup.setSingleShot(True)
        self._rust_startup.timeout.connect(lambda: self._fallback_to_qt("Audio probe startup timed out."))
        self.mediaStatusChanged.connect(
            self._restore_qt_transport, Qt.ConnectionType.QueuedConnection,
        )

    @property
    def rust_active(self):
        return self._rust_url is not None

    @property
    def rust_independent_pitch(self):
        return self.rust_active and self._rust_pitch is not None

    def configure_rust(self, gain, *, compatible, pitch_linked, semitones):
        output = self.audioOutput()
        device = output.device() if output is not None else None
        rate = self.playbackRate()
        pitch_required = not pitch_linked and (abs(rate - 1.0) >= 0.001 or abs(semitones) >= 0.001)
        self._rust_compatible = (
            compatible and 0.0 <= gain <= MAX_TRANSPORT_GAIN
            and 0.25 <= rate <= 2.0
            and (not pitch_required or (
                self._rust_pitch_library is not None and -24.0 <= semitones <= 24.0
            ))
            and device is not None and not device.isNull()
            and device == QMediaDevices.defaultAudioOutput()
        )
        self._rust_preferred = self._rust_compatible and (pitch_required or self._rust_prefer_all)
        previous_gain = self._rust_gain
        previous_pitch = self._rust_pitch
        self._rust_gain = float(gain) if self._rust_compatible else 1.0
        self._rust_pitch = None if pitch_linked or self._rust_pitch_library is None else float(semitones)
        if self.rust_active and (not self._rust_compatible or device.id() != self._rust_device_id):
            self._fallback_to_qt()
        if self.rust_active and self._rust_process is not None:
            if (previous_pitch is None) != (self._rust_pitch is None):
                self._release_process()
                if self._rust_status != Status.EndOfMedia:
                    self._rust_epoch += 1
                    self._set_rust_status(Status.LoadingMedia)
                    self._start_process()
            elif self._rust_ready:
                if self._rust_gain != previous_gain:
                    self._send(f"volume {self._rust_gain}")
                if self._rust_pitch != previous_pitch:
                    self._send(f"pitch {self._rust_pitch}")
        return self.rust_active

    def setSource(self, url):
        previous = self.rust_active
        previous_state = self.playbackState()
        previous_seekable = self.isSeekable()
        self._qt_resume = None
        self._release_process()
        self._rust_url = None
        if not self._rust_preferred or not url.isLocalFile():
            super().setSource(url)
            if previous and url.isEmpty():
                self.sourceChanged.emit(url)
                self.mediaStatusChanged.emit(Status.NoMedia)
            if previous_seekable and previous and not self.isSeekable():
                self.seekableChanged.emit(False)
            if previous:
                self.durationChanged.emit(self.duration())
                self.positionChanged.emit(self.position())
                if previous_state != self.playbackState():
                    self.playbackStateChanged.emit(self.playbackState())
            return

        super().setSource(QUrl())
        self._rust_url = QUrl(url)
        self._rust_rate = self.playbackRate()
        self.rust_audible_rate = self._rust_rate
        self._rust_device_id = self.audioOutput().device().id()
        self._rust_position = self._rust_duration = self._rust_epoch = 0
        self.rust_output_sample_rate = 0
        self.rust_underrun_frames = 0
        self._rust_state = State.StoppedState
        self._rust_status = Status.LoadingMedia
        self._start_process()
        self.sourceChanged.emit(url)
        self.durationChanged.emit(0)
        self.positionChanged.emit(0)
        if previous_seekable:
            self.seekableChanged.emit(False)
        if previous and previous_state != State.StoppedState:
            self.playbackStateChanged.emit(State.StoppedState)
        self.mediaStatusChanged.emit(Status.LoadingMedia)

    def source(self):
        return QUrl(self._rust_url) if self.rust_active else super().source()

    def playbackState(self):
        return self._rust_state if self.rust_active else super().playbackState()

    def mediaStatus(self):
        return self._rust_status if self.rust_active else super().mediaStatus()

    def duration(self):
        return self._rust_duration if self.rust_active else super().duration()

    def isSeekable(self):
        return self._rust_duration > 0 if self.rust_active else super().isSeekable()

    def position(self):
        return self._rust_position if self.rust_active else super().position()

    def setPlaybackRate(self, rate):
        super().setPlaybackRate(rate)
        if self.rust_active and rate != self._rust_rate:
            if not 0.25 <= rate <= 2.0:
                self._fallback_to_qt()
                return
            self._rust_rate = rate
            if self._rust_ready:
                self._send(f"speed {rate}")

    def play(self):
        if not self.rust_active:
            if self._qt_resume is not None:
                self._qt_resume = (self._qt_resume[0], State.PlayingState)
                return
            return super().play()
        if self._rust_state == State.PlayingState:
            return
        if self._rust_status == Status.EndOfMedia:
            self.playbackRestarting.emit()
            self.setPosition(0)
        if self._rust_process is None:
            self._set_rust_status(Status.LoadingMedia)
            self._start_process()
        if self._rust_ready:
            self._send("play")
        self._set_rust_state(State.PlayingState)

    def pause(self):
        if not self.rust_active:
            if self._qt_resume is not None:
                self._qt_resume = (self._qt_resume[0], State.PausedState)
                return
            return super().pause()
        if self._rust_state != State.PlayingState:
            return
        if self._rust_ready:
            self._send("pause")
        self._set_rust_state(State.PausedState)

    def stop(self):
        self._qt_resume = None
        if not self.rust_active:
            return super().stop()
        self._release_process()
        self._rust_epoch = 0
        self._set_rust_position(0)
        self._set_rust_state(State.StoppedState)
        self._set_rust_status(Status.LoadedMedia if self._rust_duration else Status.LoadingMedia)

    def _set_position(self, position):
        if not self.rust_active:
            if self._qt_resume is not None:
                self._qt_resume = (max(0, int(position)), self._qt_resume[1])
                return
            return super()._set_position(position)
        position = max(0, int(position))
        if self._rust_duration:
            position = min(position, self._rust_duration)
        self._rust_epoch += 1
        self.rust_audible_rate = self._rust_rate
        self.rust_underrun_frames = 0
        if self._rust_status == Status.EndOfMedia:
            self._set_rust_status(Status.LoadedMedia)
        if self._rust_ready:
            self._send(f"seek {self._rust_epoch} {position}")
        self._set_rust_position(position)

    def _set_rust_status(self, status):
        if self._rust_status != status:
            self._rust_status = status
            self.mediaStatusChanged.emit(status)

    def _set_rust_state(self, state):
        if self._rust_state != state:
            self._rust_state = state
            self.playbackStateChanged.emit(state)

    def _set_rust_position(self, position):
        if self._rust_position != position:
            self._rust_position = position
            self.positionChanged.emit(position)

    def _start_process(self):
        process = self._rust_process = QProcess(self)
        self._rust_stdout = b""
        self._rust_stderr = b""
        process.readyReadStandardOutput.connect(lambda: self._read_stdout(process))
        process.readyReadStandardError.connect(lambda: self._read_stderr(process))
        process.errorOccurred.connect(lambda error: self._process_error(process, error))
        process.finished.connect(lambda *_: self._process_finished(process))
        process.finished.connect(process.deleteLater)
        self._rust_startup.start(5000)
        arguments = [
            self._rust_url.toLocalFile(), "--transport", "--speed", str(self._rust_rate),
            "--volume", str(self._rust_gain),
        ]
        if self._rust_pitch is not None:
            arguments.extend(("--pitch-library", str(self._rust_pitch_library), "--pitch", str(self._rust_pitch)))
        process.start(self._rust_executable, arguments)

    def _release_process(self):
        self._rust_startup.stop()
        process, self._rust_process = self._rust_process, None
        self._rust_ready = False
        if process is not None:
            if process.state() == QProcess.ProcessState.NotRunning:
                process.deleteLater()
            else:
                process.kill()

    def shutdown_rust_playback(self):
        self._release_process()
        for process in self.findChildren(QProcess):
            if process.state() != QProcess.ProcessState.NotRunning:
                process.waitForFinished(250)

    def _send(self, command):
        process = self._rust_process
        if process is not None:
            process.write((command + "\n").encode("ascii"))

    def _read_stdout(self, process):
        data = bytes(process.readAllStandardOutput())
        if process is not self._rust_process:
            return
        *lines, self._rust_stdout = (self._rust_stdout + data).split(b"\n")
        for line in lines:
            if process is not self._rust_process:
                break
            fields = line.split()
            if not fields or not all(field.isdigit() for field in fields[1:]):
                continue
            event = fields[0]
            if event == b"ready" and not self._rust_ready:
                if len(fields) != 4 or fields[-1] != TRANSPORT_VERSION:
                    self._fallback_to_qt("Rebuild the audio probe to use the current playback controls.")
                    break
                duration, sample_rate = map(int, fields[1:3])
                self._rust_startup.stop()
                self._rust_ready = True
                was_seekable = self.isSeekable()
                self._rust_duration = duration
                self.rust_output_sample_rate = sample_rate
                self.rust_underrun_frames = 0
                self._send(f"volume {self._rust_gain}")
                self._send(f"speed {self._rust_rate}")
                if self._rust_pitch is not None:
                    self._send(f"pitch {self._rust_pitch}")
                if self._rust_epoch:
                    self._send(f"seek {self._rust_epoch} {self._rust_position}")
                if self._rust_state == State.PlayingState:
                    self._send("play")
                self.durationChanged.emit(duration)
                if process is self._rust_process and was_seekable != self.isSeekable():
                    self.seekableChanged.emit(self.isSeekable())
                if process is self._rust_process:
                    self._set_rust_status(Status.LoadedMedia)
            elif self._rust_ready and event in (b"position", b"ended") and len(fields) == 5:
                epoch, position, underruns, speed = map(int, fields[1:])
                if epoch != self._rust_epoch:
                    continue
                self.rust_underrun_frames = underruns
                self.rust_audible_rate = speed / 10_000
                self._set_rust_position(position)
                if event == b"ended" and epoch == self._rust_epoch and process is self._rust_process:
                    self._rust_status = Status.EndOfMedia
                    self._set_rust_state(State.StoppedState)
                    if process is self._rust_process and self._rust_status == Status.EndOfMedia:
                        self.mediaStatusChanged.emit(Status.EndOfMedia)

    def _read_stderr(self, process):
        data = bytes(process.readAllStandardError())
        if process is self._rust_process:
            self._rust_stderr = (self._rust_stderr + data)[-2048:]

    def _process_error(self, process, error):
        if process is self._rust_process and error == QProcess.ProcessError.FailedToStart:
            self._fallback_to_qt(process.errorString())

    def _process_finished(self, process):
        if process is self._rust_process:
            self._read_stderr(process)
            error = self._rust_stderr.decode("utf-8", errors="replace").strip()
            self._fallback_to_qt(error or "Audio probe exited.")

    def _fallback_to_qt(self, error=""):
        if not self.rust_active:
            return
        url, position, state = self.source(), self.position(), self.playbackState()
        self._release_process()
        self._rust_url = None
        self.rust_output_sample_rate = 0
        QMediaPlayer.setSource(self, url)
        if state != State.StoppedState:
            self.playbackStateChanged.emit(State.StoppedState)
        self._qt_resume = (
            position if self._rust_status != Status.EndOfMedia else 0, state,
        )
        self.rustFallback.emit(error)
        self._restore_qt_transport()

    def _restore_qt_transport(self, *_args):
        if self._qt_resume is None:
            return
        status = self.mediaStatus()
        if status == Status.InvalidMedia:
            self._qt_resume = None
            return
        if status not in (Status.LoadedMedia, Status.BufferedMedia):
            return
        position, state = self._qt_resume
        self._qt_resume = None
        if position and self.duration() and self.isSeekable():
            super()._set_position(min(position, self.duration()))
        if state == State.PlayingState:
            super().play()
        elif state == State.PausedState:
            super().pause()
