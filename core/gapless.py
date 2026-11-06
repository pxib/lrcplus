"""Optional, bounded two-track decoding into one uninterrupted Qt audio sink.

Qt remains the fallback for speed/pitch processing and when FFmpeg is absent.
Only the active track and one successor have a decoder, each with 256 KiB of
PCM in Python. Pipe backpressure stops FFmpeg when that reserve is full.
"""

from collections import deque
from pathlib import Path
import shutil
import subprocess
from threading import Condition, Thread

from PySide6.QtCore import QTimer, QUrl, Qt, Signal
from PySide6.QtMultimedia import QAudioFormat, QAudioSink, QMediaPlayer

from core.audio_dsp import apply_pcm_gain_to_bytes
from core.audio_sink import dispose_audio_sink
from core.replaygain import ReplayGain, parse_replaygain


class TrackDecoder:
    LIMIT = 256 * 1024
    BLOCK = 16 * 1024

    def __init__(self, executable, path, fmt, position=0):
        self.path = str(path)
        self.duration = 0
        self.tags = ReplayGain()
        self.error = None
        self.done = False
        self._chunks = deque()
        self._size = 0
        self._closed = False
        self._process = None
        self._condition = Condition()
        self._block_size = self.BLOCK - self.BLOCK % fmt.bytesPerFrame()
        args = [executable, "-nostdin", "-v", "error", "-threads", "1"]
        if position:
            args += ["-ss", str(position / 1000.0)]
        args += ["-i", self.path, "-map", "0:a:0", "-vn", "-sn", "-dn",
                 "-ac", str(fmt.channelCount()), "-ar", str(fmt.sampleRate()),
                 "-f", "f32le", "pipe:1"]
        self._thread = Thread(target=self._decode, args=(args,), daemon=True,
                              name="gapless-decode")
        self._thread.start()

    def _decode(self, args):
        process = None
        try:
            try:
                from mutagen import File, MutagenError
                audio = File(self.path)
                self.duration = round(float(getattr(getattr(audio, "info", None), "length", 0)) * 1000)
                self.tags = parse_replaygain(getattr(audio, "tags", None))
            except (OSError, ValueError, TypeError, MutagenError):
                pass  # FFmpeg can decode formats whose tags Mutagen cannot read.
            with self._condition:
                if self._closed:
                    return
                process = self._process = subprocess.Popen(
                    args, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE,
                    stderr=subprocess.DEVNULL,
                    creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
                )
            while True:
                with self._condition:
                    self._condition.wait_for(lambda: self._closed or self._size <= self.LIMIT - self.BLOCK)
                    if self._closed:
                        break
                data = process.stdout.read(self._block_size)
                if not data:
                    if process.wait() != 0 and not self._closed:
                        self.error = "FFmpeg could not decode this track."
                    break
                with self._condition:
                    if self._closed:
                        break
                    self._chunks.append(data)
                    self._size += len(data)
        except Exception as error:
            self.error = str(error)
        finally:
            if process is not None:
                if process.poll() is None:
                    process.kill()
                process.wait()
                process.stdout.close()
            with self._condition:
                self.done = True
                self._condition.notify_all()

    def take(self, count):
        with self._condition:
            if not self._chunks:
                return b""
            data = self._chunks.popleft()
            if len(data) > count:
                self._chunks.appendleft(data[count:])
                data = data[:count]
            self._size -= len(data)
            self._condition.notify_all()
            return data

    @property
    def exhausted(self):
        with self._condition:
            return self.done and not self._chunks

    def close(self):
        with self._condition:
            self._closed = True
            self._chunks.clear()
            self._size = 0
            process = self._process
            self._condition.notify_all()
        if process is not None and process.poll() is None:
            try:
                process.kill()
            except OSError:
                pass


class GaplessMediaPlayer(QMediaPlayer):
    """Keep the existing transport API and signals for lyrics and plugins."""

    gaplessFallback = Signal(str)

    def __init__(self, parent=None, *, sink_factory=QAudioSink, decoder_factory=TrackDecoder):
        super().__init__(parent)
        self._sink_factory = sink_factory
        self._decoder_factory = decoder_factory
        self._gapless_enabled = False
        self._gapless_active = False
        self._gapless_handoff = False
        self._decoder = self._next_decoder = None
        self._sink = self._io = None
        self._url = QUrl()
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._status = QMediaPlayer.MediaStatus.NoMedia
        self._offset = self._track_start_us = self._written = 0
        self._duration = 0
        self._last_position = 0
        self._pending = b""
        self._at_end = False
        self._next_path = None
        self._preload_resolved = False
        self._boundary_us = None
        self.next_path = lambda: None
        self._rg_settings = ("off", 0, True)
        self._volume = 1.0
        self._timer = QTimer(self)
        self._timer.setTimerType(Qt.TimerType.PreciseTimer)
        self._timer.setInterval(10)
        self._timer.timeout.connect(self._pump)

    def configure_gapless(self, enabled, volume, rg_settings, *, compatible=True):
        self._rg_settings = rg_settings
        self._volume = volume
        enabled = bool(enabled and compatible and shutil.which("ffmpeg"))
        self._gapless_enabled = enabled
        if self._gapless_active and (not enabled or self._sink_device != self.audioOutput().device()):
            url, position, state = self.source(), self.position(), self.playbackState()
            self._release()
            self._gapless_active = False
            super().setSource(url)
            super().setPosition(position)
            if state == QMediaPlayer.PlaybackState.PlayingState:
                super().play()
        # Route changes take effect on the next explicit track load. This avoids
        # switching decoder clocks while a track is already audible.
        if self._gapless_active:
            self.audioOutput().setVolume(0.0)
            return True
        return False

    def setSource(self, url):
        if (self._gapless_handoff and self._next_decoder is not None
                and Path(url.toLocalFile()) == Path(self._next_decoder.path)):
            self._decoder.close()
            self._decoder, self._next_decoder = self._next_decoder, None
            self._url = QUrl(url)
            self._offset = 0
            self._last_position = 0
            self._track_start_us = self._boundary_us
            self._boundary_us = None
            self._duration = self._decoder.duration
            self._at_end = False
            self._preload_resolved = False
            self._status = QMediaPlayer.MediaStatus.BufferedMedia
            self.sourceChanged.emit(url)
            self.durationChanged.emit(self._duration)
            self.positionChanged.emit(0)
            return
        self._release()
        self._gapless_active = False
        super().stop()
        if not self._gapless_enabled or url.isEmpty() or not url.isLocalFile():
            super().setSource(url)
            return
        output = self.audioOutput()
        self._sink_device = output.device()
        fmt = self._sink_device.preferredFormat()
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        if not self._sink_device.isFormatSupported(fmt):
            super().setSource(url)
            return
        # Clear native source while it is stopped. No native stream runs beside
        # the gapless sink and no PCM processing thread receives its samples.
        super().setSource(QUrl())
        self._gapless_active = True
        self._url = QUrl(url)
        self._format = fmt
        self._offset = self._track_start_us = self._written = 0
        self._duration = 0
        self._last_position = 0
        self._state = QMediaPlayer.PlaybackState.StoppedState
        self._status = QMediaPlayer.MediaStatus.LoadingMedia
        self._at_end = False
        self._preload_resolved = False
        self._decoder = self._new_decoder(url.toLocalFile())
        output.setVolume(0.0)
        self.sourceChanged.emit(url)
        self.durationChanged.emit(0)
        self.positionChanged.emit(0)

    def _new_decoder(self, path, position=0):
        return self._decoder_factory(shutil.which("ffmpeg"), path, self._format, position)

    def source(self):
        return QUrl(self._url) if self._gapless_active else super().source()

    def playbackState(self):
        return self._state if self._gapless_active else super().playbackState()

    def isPlaying(self):
        return self.playbackState() == QMediaPlayer.PlaybackState.PlayingState

    def mediaStatus(self):
        return self._status if self._gapless_active else super().mediaStatus()

    def duration(self):
        return self._duration if self._gapless_active else super().duration()

    def position(self):
        if not self._gapless_active:
            return super().position()
        elapsed = self._sink.processedUSecs() if self._sink is not None else 0
        return max(0, self._offset + (elapsed - self._track_start_us) // 1000)

    def _set_state(self, state):
        if self._state != state:
            self._state = state
            self.playbackStateChanged.emit(state)

    def play(self):
        if not self._gapless_active:
            return super().play()
        if self._at_end:
            self.setPosition(0)
        self._set_state(QMediaPlayer.PlaybackState.PlayingState)
        if self._sink is not None:
            self._sink.resume()
        self._timer.start()

    def pause(self):
        if not self._gapless_active:
            return super().pause()
        self._timer.stop()
        if self._sink is not None:
            self._sink.suspend()
        self._set_state(QMediaPlayer.PlaybackState.PausedState)

    def stop(self):
        if self._gapless_handoff:
            return
        if not self._gapless_active:
            return super().stop()
        self._release()
        self._offset = self._track_start_us = self._written = 0
        self._at_end = True
        self._set_state(QMediaPlayer.PlaybackState.StoppedState)
        self.positionChanged.emit(0)

    def setPosition(self, position):
        if not self._gapless_active:
            return super().setPosition(position)
        state = self._state
        self._release()
        self._offset = max(0, int(position))
        self._track_start_us = self._written = 0
        self._at_end = False
        self._decoder = self._new_decoder(self._url.toLocalFile(), self._offset)
        self._status = QMediaPlayer.MediaStatus.LoadingMedia
        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._timer.start()
        self.positionChanged.emit(self._offset)

    def invalidate_preload(self):
        self._preload_resolved = False
        # Queue edits near a boundary discard already-buffered successor audio
        # and restart at the audible position, so a removed track cannot play.
        if self._boundary_us is not None:
            self.setPosition(self.position())
            return
        if self._next_decoder is not None:
            self._next_decoder.close()
            self._next_decoder = None

    def _release(self):
        self._timer.stop()
        for decoder in (self._decoder, self._next_decoder):
            if decoder is not None:
                decoder.close()
        self._decoder = self._next_decoder = None
        self._pending = b""
        self._preload_resolved = False
        self._boundary_us = None
        sink, self._sink = self._sink, None
        self._io = None
        if sink is not None:
            dispose_audio_sink(sink)

    def _fallback(self, error):
        url, position, state = self.source(), self.position(), self.playbackState()
        self._release()
        self._gapless_active = False
        super().setSource(url)
        super().setPosition(position)
        self.gaplessFallback.emit(str(error))
        if state == QMediaPlayer.PlaybackState.PlayingState:
            super().play()

    def _pump(self):
        if not self._gapless_active or not self.isPlaying() or self._decoder is None:
            return
        try:
            self._pump_audio()
        except (OSError, RuntimeError, ValueError, ImportError) as error:
            self._fallback(error)

    def _pump_audio(self):
        if self._boundary_us is not None and self._sink.processedUSecs() >= self._boundary_us:
            previous = self._decoder
            self._status = QMediaPlayer.MediaStatus.EndOfMedia
            self._gapless_handoff = True
            try:
                self.mediaStatusChanged.emit(self._status)
            finally:
                self._gapless_handoff = False
            if self._decoder is previous:
                return
        decoder = self._next_decoder if self._boundary_us is not None else self._decoder
        if decoder.error:
            self._fallback(decoder.error)
            return
        if self._decoder.duration != self._duration:
            self._duration = self._decoder.duration
            self.durationChanged.emit(self._duration)
        # The successor is resolved on the GUI thread. Queue edits invalidate
        # this reservation; there is never a full-album decode or cache.
        if not self._preload_resolved and self._boundary_us is None:
            self._next_path = self.next_path()
            self._preload_resolved = True
        path = self._next_path
        if path and self._next_decoder is None:
            self._next_decoder = self._new_decoder(path)
        if not self._pending:
            self._pending = decoder.take(TrackDecoder.BLOCK)
            if self._pending:
                gain = decoder.tags.multiplier(*self._rg_settings) * self._volume
                if abs(gain - 1.0) > 1e-8:
                    self._pending = apply_pcm_gain_to_bytes(
                        self._pending, QAudioFormat.SampleFormat.Float, gain,
                    )
        if decoder.exhausted and not self._pending and self._sink is None:
            self._fallback("The decoder produced no audio.")
            return
        if self._sink is None and self._pending:
            self._sink = self._sink_factory(self._sink_device, self._format, self)
            self._sink.setBufferSize(self._format.bytesForDuration(250_000))
            self._io = self._sink.start()
            if self._io is None:
                raise RuntimeError("Could not start gapless audio output.")
            self._status = QMediaPlayer.MediaStatus.BufferedMedia
            self.mediaStatusChanged.emit(self._status)
        # Fill available space, including the next track, without restarting the
        # device. A partial write stays in _pending until the following tick.
        for _ in range(32):
            if self._io is None:
                break
            count = min(self._sink.bytesFree(), len(self._pending))
            count -= count % self._format.bytesPerFrame()
            if count:
                written = self._io.write(self._pending[:count])
                if written < 0:
                    raise RuntimeError("Gapless audio output rejected data.")
                self._pending = self._pending[written:]
                self._written += written
            if self._pending:
                break
            if decoder.exhausted:
                if self._boundary_us is not None:
                    break  # A very short successor is buffered; await its clock.
                if path and self._next_decoder is not None:
                    self._boundary_us = self._format.durationForBytes(self._written)
                    decoder = self._next_decoder
                    # Append successor samples before the device reaches this
                    # boundary, then advance lyrics/UI when it becomes audible.
                    continue
                # At the end of the pool, let buffered samples finish first.
                end_us = self._format.durationForBytes(self._written)
                if self._sink.processedUSecs() >= end_us:
                    self._offset = self.position()
                    self._release()
                    self._track_start_us = self._written = 0
                    self._at_end = True
                    self._status = QMediaPlayer.MediaStatus.EndOfMedia
                    self._set_state(QMediaPlayer.PlaybackState.StoppedState)
                    self.mediaStatusChanged.emit(self._status)
                break
            self._pending = decoder.take(TrackDecoder.BLOCK)
            if not self._pending:
                break
            gain = decoder.tags.multiplier(*self._rg_settings) * self._volume
            if abs(gain - 1.0) > 1e-8:
                self._pending = apply_pcm_gain_to_bytes(self._pending, QAudioFormat.SampleFormat.Float, gain)
        position = self.position()
        if abs(position - self._last_position) >= 40:
            self._last_position = position
            self.positionChanged.emit(position)
