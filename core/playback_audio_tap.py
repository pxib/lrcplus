"""A bounded PCM snapshot shared by the playback routes and the UI."""

from threading import Lock


PLAYBACK_SAMPLE_FRAMES = 4096


class PlaybackAudioTap:
    def __init__(self):
        self._lock = Lock()
        self.collecting = False
        self._source = None
        self._data = bytearray()
        self._updated = False

    def set_collecting(self, collecting):
        with self._lock:
            if self.collecting == collecting:
                return
            self.collecting = collecting
            self._clear()

    def _clear(self):
        self._source = None
        self._data.clear()
        self._updated = False

    def clear(self):
        with self._lock:
            self._clear()

    def capture(self, data, audio_format, route):
        if not self.collecting or not data:
            return
        frame_size = audio_format.bytesPerFrame()
        if frame_size <= 0:
            return
        end = len(data) - len(data) % frame_size
        if not end:
            return
        limit = PLAYBACK_SAMPLE_FRAMES * frame_size
        source = (route, audio_format.sampleRate(), audio_format.channelCount(), audio_format.sampleFormat())
        with self._lock:
            if not self.collecting:
                return
            if source != self._source:
                self._data.clear()
                self._source = source
            self._data.extend(memoryview(data)[max(0, end - limit):end])
            excess = len(self._data) - limit
            if excess > 0:
                del self._data[:excess]
            self._updated = True

    def take_latest(self):
        """Return only new samples, without exposing the writable output buffer."""
        with self._lock:
            if not self._updated:
                return None
            self._updated = False
            return bytes(self._data), *self._source[1:]
