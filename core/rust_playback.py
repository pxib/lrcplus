"""Playback through the out-of-process Rust audio transport."""

from importlib.util import find_spec
import os
from pathlib import Path
import shutil
import sys

from PySide6.QtCore import QProcess, QTimer, Qt, QUrl, Signal
from PySide6.QtMultimedia import QAudioFormat, QMediaDevices, QMediaPlayer

from core.background_job import BackgroundJob
from core.gapless import GaplessMediaPlayer
from core.native_audio import audio_probe_executable
from core.replaygain import ReplayGain, read_replaygain


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
TRANSPORT_VERSION = b"6"
# 300% volume times the largest supported ReplayGain adjustment (+60 dB).
MAX_TRANSPORT_GAIN = 3000.0


class RustMediaPlayer(GaplessMediaPlayer):
    rustFallback = Signal(str)

    def __init__(self, parent=None, *, executable, **kwargs):
        super().__init__(parent, **kwargs)
        self._rust_executable = str(executable)
        default = "1" if getattr(sys, "frozen", False) else ""
        self._rust_prefer_all = os.environ.get("LRCPLUS_RUST_PLAYBACK", default) == "1"
        self._rust_pitch_library = _pitch_library()
        self._rust_pitch = None
        self._rust_url = None
        self._rust_network = False
        self._rust_stream_ffmpeg = self._rust_stream_ffprobe = None
        self._rust_process = None
        self._rust_ready = False
        self._rust_compatible = False
        self._rust_preferred = False
        self._rust_gapless = self._rust_gapless_preferred = False
        self._rust_crossfade_ms = 0
        self._rust_next = None
        self._rust_next_tags = None
        self._rust_next_tags_job = None
        self._rust_preload_resolved = False
        self._rust_next_id = 0
        self._rust_transition = None
        self._rust_gain = 1.0
        self._rust_normalization = 1.0
        self._rust_rg_options = ("off", 0, True)
        self._rust_eq = None
        self._rust_observe = False
        self.rust_preloaded_tags = None
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

    @property
    def rust_gapless_boundary(self):
        return self._rust_transition is not None

    def configure_rust_network(self, is_station):
        self._rust_stream_ffprobe = shutil.which("ffprobe") if self._rust_prefer_all and not is_station else None
        self._rust_stream_ffmpeg = (shutil.which("ffmpeg")
                                    if self._rust_prefer_all and (is_station or self._rust_stream_ffprobe) else None)

    def set_rust_audio_observation(self, enabled):
        self._rust_observe = enabled
        if self._rust_ready and self._rust_prefer_all:
            self._send(f"observe {int(enabled)}")

    def configure_rust(self, gain, *, compatible, pitch_linked, semitones, gapless=False, crossfade=0,
                       rg_options=("off", 0, True), normalization=1.0, equalizer=None):
        output = self.audioOutput()
        device = output.device() if output is not None else None
        rate = self.playbackRate()
        transitions = bool(gapless or crossfade) and rate == 1.0 and abs(semitones) < 0.001
        pitch_required = not pitch_linked and (abs(rate - 1.0) >= 0.001 or abs(semitones) >= 0.001)
        self._rust_compatible = (
            compatible and 0.0 <= gain <= MAX_TRANSPORT_GAIN
            and (equalizer is None or self._rust_prefer_all)
            and (not self._rust_observe or self._rust_prefer_all)
            and 0.25 <= rate <= 2.0
            and (not (gapless or crossfade) or self._rust_prefer_all)
            and (not pitch_required or (
                self._rust_pitch_library is not None and -24.0 <= semitones <= 24.0
            ))
            and device is not None and not device.isNull()
            and (self._rust_prefer_all or device == QMediaDevices.defaultAudioOutput())
        )
        self._rust_preferred = self._rust_compatible and (pitch_required or self._rust_prefer_all)
        self._rust_gapless_preferred = self._rust_preferred and transitions
        previous_gain = self._rust_gain
        previous_pitch = self._rust_pitch
        previous_normalization = self._rust_normalization
        previous_eq = self._rust_eq
        self._rust_eq = equalizer
        crossfade_ms = int(crossfade * 1000)
        if crossfade_ms != self._rust_crossfade_ms:
            self._rust_crossfade_ms = crossfade_ms
            self.invalidate_preload()
            if self._rust_ready:
                self._send(f"crossfade {crossfade_ms}")
        if self._rust_rg_options != rg_options:
            self._rust_rg_options = rg_options
            self.invalidate_preload()
        self._rust_gain = float(gain) if self._rust_compatible else 1.0
        if not self.rust_gapless_boundary:
            self._rust_normalization = normalization
        live_stream = self._rust_network and not self._rust_duration
        self._rust_pitch = (None if live_stream or transitions or pitch_linked or self._rust_pitch_library is None
                            else float(semitones))
        if self.rust_active and not self._rust_compatible:
            self._fallback_to_qt()
        if self.rust_active:
            if self._rust_gapless and not transitions:
                self.invalidate_preload()
                self._rust_gapless = False
            device_changed = device.id() != self._rust_device_id
            self._rust_device_id = device.id()
            if self._rust_process is not None and (device_changed or (previous_pitch is None) != (self._rust_pitch is None)):
                self._release_process()
                if self._rust_status != Status.EndOfMedia:
                    if live_stream:
                        self._rust_epoch = 0
                        self._set_rust_position(0)
                    else:
                        self._rust_epoch += 1
                    self._set_rust_status(Status.LoadingMedia)
                    self._start_process()
            elif self._rust_ready:
                if self._rust_gain != previous_gain:
                    self._send(f"volume {self._rust_gain}")
                if self._rust_normalization != previous_normalization:
                    self._send(f"normalization {self._rust_normalization}")
                if self._rust_eq != previous_eq:
                    self._send_equalizer()
                if self._rust_pitch != previous_pitch:
                    self._send(f"pitch {self._rust_pitch}")
        return self.rust_active

    def setSource(self, url):
        if self._rust_transition is not None and self._rust_next is not None and url == self._rust_next[1]:
            duration, position = self._rust_transition
            self._send(f"accept {self._rust_epoch} {self._rust_next[0]}")
            self.rust_preloaded_tags = self._rust_next_tags
            self._rust_normalization = self._rust_next_tags.multiplier(*self._rust_rg_options)
            self._rust_transition = self._rust_next = None
            self._rust_next_tags = None
            self._rust_preload_resolved = False
            previous_seekable = self.isSeekable()
            self._rust_url = QUrl(url)
            self._rust_duration = duration
            self._rust_position = position
            self._rust_status = Status.BufferedMedia
            self.sourceChanged.emit(url)
            self.durationChanged.emit(duration)
            self.positionChanged.emit(position)
            if previous_seekable != self.isSeekable():
                self.seekableChanged.emit(self.isSeekable())
            self.mediaStatusChanged.emit(Status.BufferedMedia)
            return

        previous = self.rust_active
        previous_state = self.playbackState()
        previous_seekable = self.isSeekable()
        self._qt_resume = None
        self._release_process()
        self._rust_url = None
        self.rust_preloaded_tags = None
        self._rust_network = bool(self._rust_stream_ffmpeg and url.scheme() in ("http", "https"))
        if not self._rust_preferred or not (url.isLocalFile() or self._rust_network):
            self._rust_network = False
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
        self._rust_gapless = self._rust_gapless_preferred and not self._rust_network
        self._rust_rate = 1.0 if self._rust_network else self.playbackRate()
        if self._rust_network:
            self._rust_pitch = None
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
        if self.rust_active and (not self._rust_network or self._rust_duration) and rate != self._rust_rate:
            if not 0.25 <= rate <= 2.0:
                self._fallback_to_qt()
                return
            if self._rust_gapless and rate != 1.0:
                self.invalidate_preload()
                self._rust_gapless = False
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
        if self._rust_state == State.PausedState and (self._gapless_handoff or not self.prepare_resume()):
            return
        if self._rust_status == Status.EndOfMedia:
            self.playbackRestarting.emit()
            self.setPosition(0)
        if self._rust_process is None:
            self._set_rust_status(Status.LoadingMedia)
            self._start_process()
        if self._rust_ready:
            self._preload_rust_successor()
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
        if self._rust_network and not self._rust_duration:
            return
        position = max(0, int(position))
        if self._rust_duration:
            position = min(position, self._rust_duration)
        self._rust_epoch += 1
        self._rust_next = None
        self._rust_next_tags = None
        if self._rust_next_tags_job is not None:
            self._rust_next_tags_job.cancel()
        self._rust_preload_resolved = False
        self.rust_audible_rate = self._rust_rate
        self.rust_underrun_frames = 0
        if self._rust_status == Status.EndOfMedia:
            self._set_rust_status(Status.LoadedMedia)
        if self._rust_ready:
            self._send(f"seek {self._rust_epoch} {position}")
        self._set_rust_position(position)

    def invalidate_preload(self):
        if not self.rust_active or not self._rust_gapless:
            return super().invalidate_preload()
        if self._rust_next is not None:
            self._send(f"clear-next {self._rust_epoch}")
            self._rust_next = None
            self._rust_next_tags = None
            if self._rust_next_tags_job is not None:
                self._rust_next_tags_job.cancel()
        self._rust_preload_resolved = False

    def transition_status(self):
        if self.rust_active and self._rust_gapless:
            status = ("No eligible next track." if self._rust_preload_resolved and self._rust_next is None
                      else "Enabled for eligible Rust transitions.")
            crossfade = f"{self._rust_crossfade_ms / 1000:g} s configured. {status}" if self._rust_crossfade_ms else "Off"
            return status, crossfade
        return super().transition_status()

    def _preload_rust_successor(self):
        if not self._rust_gapless or not self._rust_ready or self._rust_preload_resolved:
            return
        process, epoch = self._rust_process, self._rust_epoch
        path = self.next_path()
        if process is not self._rust_process or epoch != self._rust_epoch:
            return
        self._rust_preload_resolved = True
        if path:
            self._rust_next_id += 1
            self._rust_next = (self._rust_next_id, QUrl.fromLocalFile(str(path)))
            if self._rust_rg_options[0] == "off":
                self._send_rust_successor(ReplayGain())
            else:
                if self._rust_next_tags_job is None:
                    job = self._rust_next_tags_job = BackgroundJob(self, "rust-next-replaygain")
                    job.finished.connect(self._send_rust_successor)
                    job.failed.connect(lambda _: self._send_rust_successor(ReplayGain()))
                self._rust_next_tags_job.start(read_replaygain, path)

    def _send_rust_successor(self, tags):
        if self._rust_next is None or not self._rust_ready:
            return
        self._rust_next_tags = tags
        request, url = self._rust_next
        gain = tags.multiplier(*self._rust_rg_options)
        self._send(f"next {self._rust_epoch} {request} {gain} {url.toLocalFile()}")

    def _handoff_rust_successor(self, process, duration, position):
        if self._rust_duration:
            self._rust_position = self._rust_duration
        self._rust_transition = (duration, position)
        self._rust_status = Status.EndOfMedia
        self._gapless_handoff = True
        try:
            self.mediaStatusChanged.emit(Status.EndOfMedia)
        finally:
            self._gapless_handoff = False
        if self._rust_transition is not None and process is self._rust_process:
            # The queue or sleep timer declined the handoff. Drop prefed audio.
            self._release_process()
            self._rust_status = Status.EndOfMedia
            if self._rust_state != State.PausedState:
                self._set_rust_state(State.StoppedState)
        elif process is self._rust_process and self._rust_state == State.PlayingState:
            self._preload_rust_successor()

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
        self._rust_stderr_partial = b""
        process.readyReadStandardOutput.connect(lambda: self._read_stdout(process))
        process.readyReadStandardError.connect(lambda: self._read_stderr(process))
        process.errorOccurred.connect(lambda error: self._process_error(process, error))
        process.finished.connect(lambda *_: self._process_finished(process))
        process.finished.connect(process.deleteLater)
        self._rust_startup.start(5000)
        source = (bytes(self._rust_url.toEncoded()).decode("ascii") if self._rust_network
                  else self._rust_url.toLocalFile())
        arguments = [
            source, "--transport", "--speed", str(self._rust_rate),
            "--volume", str(self._rust_gain),
        ]
        device = self.audioOutput().device()
        if device != QMediaDevices.defaultAudioOutput():
            arguments.extend(("--device", device.description()))
        if self._rust_network:
            arguments.extend(("--stream-ffmpeg", self._rust_stream_ffmpeg))
            if self._rust_stream_ffprobe:
                arguments.extend(("--stream-ffprobe", self._rust_stream_ffprobe))
        if self._rust_pitch is not None:
            arguments.extend(("--pitch-library", str(self._rust_pitch_library), "--pitch", str(self._rust_pitch)))
        process.start(self._rust_executable, arguments)

    def _release_process(self):
        self._rust_startup.stop()
        process, self._rust_process = self._rust_process, None
        self._rust_ready = False
        self._rust_transition = self._rust_next = None
        self._rust_next_tags = None
        if self._rust_next_tags_job is not None:
            self._rust_next_tags_job.cancel()
        self._rust_preload_resolved = False
        if process is not None:
            if process.state() == QProcess.ProcessState.NotRunning:
                process.deleteLater()
            elif self._rust_network:
                process.write(b"quit\n")
                timeout = QTimer(process)
                timeout.setSingleShot(True)
                timeout.timeout.connect(process.kill)
                timeout.start(250)
            else:
                process.kill()

    def shutdown_rust_playback(self):
        self._release_process()
        if self._rust_next_tags_job is not None:
            self._rust_next_tags_job.close()
            self._rust_next_tags_job = None
        for process in self.findChildren(QProcess):
            if process.state() != QProcess.ProcessState.NotRunning:
                if not process.waitForFinished(250):
                    process.kill()
                    process.waitForFinished(250)

    def _send(self, command):
        process = self._rust_process
        if process is not None:
            process.write((command + "\n").encode("utf-8"))

    def _send_equalizer(self):
        preamp, bands = self._rust_eq or (0, ())
        fields = [f"equalizer {preamp}"]
        fields.extend(f"{band.shape} {band.frequency} {band.gain} {band.q}" for band in bands)
        self._send(" ".join(fields))

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
                self._send(f"normalization {self._rust_normalization}")
                if self._rust_eq is not None:
                    self._send_equalizer()
                if self._rust_observe:
                    self._send("observe 1")
                if self._rust_gapless and self._rust_crossfade_ms:
                    self._send(f"crossfade {self._rust_crossfade_ms}")
                self._send(f"speed {self._rust_rate}")
                if self._rust_pitch is not None:
                    self._send(f"pitch {self._rust_pitch}")
                if self._rust_epoch:
                    self._send(f"seek {self._rust_epoch} {self._rust_position}")
                if self._rust_state == State.PlayingState:
                    self._preload_rust_successor()
                    if process is self._rust_process and self._rust_state == State.PlayingState:
                        self._send("play")
                self.durationChanged.emit(duration)
                if process is self._rust_process and was_seekable != self.isSeekable():
                    self.seekableChanged.emit(self.isSeekable())
                if process is self._rust_process:
                    self._set_rust_status(Status.LoadedMedia)
            elif self._rust_ready and self._rust_next is not None and event in (b"transition", b"unavailable"):
                if len(fields) < 3 or tuple(map(int, fields[1:3])) != (self._rust_epoch, self._rust_next[0]):
                    continue
                if event == b"transition" and len(fields) == 5:
                    self._handoff_rust_successor(process, *map(int, fields[3:]))
                elif event == b"unavailable" and len(fields) == 3:
                    self._rust_next = None
            elif self._rust_ready and event in (b"position", b"ended") and len(fields) == 5:
                epoch, position, underruns, speed = map(int, fields[1:])
                if epoch != self._rust_epoch:
                    continue
                self.rust_underrun_frames = underruns
                self.rust_audible_rate = speed / 10_000
                self._set_rust_position(position)
                if (event == b"position" and process is self._rust_process
                        and self._rust_state == State.PlayingState):
                    self._preload_rust_successor()
                if event == b"ended" and epoch == self._rust_epoch and process is self._rust_process:
                    self._rust_status = Status.EndOfMedia
                    self._set_rust_state(State.StoppedState)
                    if process is self._rust_process and self._rust_status == Status.EndOfMedia:
                        self.mediaStatusChanged.emit(Status.EndOfMedia)

    def _read_stderr(self, process):
        data = bytes(process.readAllStandardError())
        if process is not self._rust_process:
            return
        *lines, self._rust_stderr_partial = (self._rust_stderr_partial + data).split(b"\n")
        self._rust_stderr_partial = self._rust_stderr_partial[-66_000:]
        for line in lines:
            if line.startswith(b"pcm "):
                self._receive_rust_pcm(line)
            else:
                self._rust_stderr = (self._rust_stderr + line + b"\n")[-2048:]

    def _receive_rust_pcm(self, line):
        tap = self._audio_tap
        if not self._rust_observe or tap is None or not tap.collecting:
            return
        try:
            _, epoch, rate, channels, samples = line.split(maxsplit=4)
            epoch, rate, channels = int(epoch), int(rate), int(channels)
            if epoch != self._rust_epoch or rate != self.rust_output_sample_rate or channels not in (1, 2):
                return
            if len(samples) > 4096 * channels * 8:
                return
            data = bytes.fromhex(samples.decode("ascii"))
        except (UnicodeError, ValueError):
            return
        fmt = QAudioFormat()
        fmt.setSampleRate(rate)
        fmt.setChannelCount(channels)
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        tap.capture(data, fmt, "rust")

    def _process_error(self, process, error):
        if process is self._rust_process and error == QProcess.ProcessError.FailedToStart:
            self._fallback_to_qt(process.errorString())

    def _process_finished(self, process):
        if process is self._rust_process:
            self._read_stderr(process)
            partial = self._rust_stderr_partial if not self._rust_stderr_partial.startswith(b"pcm ") else b""
            error = (self._rust_stderr + partial).decode("utf-8", errors="replace").strip()
            self._fallback_to_qt(error or "Audio probe exited.")

    def _fallback_to_qt(self, error=""):
        if not self.rust_active:
            return
        url, position, state = self.source(), self.position(), self.playbackState()
        self._release_process()
        self._rust_url = None
        self._rust_network = False
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
