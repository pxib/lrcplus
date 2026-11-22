"""Coordinate live radio lyric lookup without blocking playback."""

from collections import deque
from threading import Event

from PySide6.QtCore import QObject, QTimer

from core.background_job import BackgroundJob
from core.idle_cache import IdleCache
from core.stream_lyrics import find_stream_lyrics, song_from_title, watch_icy_titles


class StreamLyricsController(QObject):
    def __init__(self, player):
        super().__init__(player)
        self.player = player
        self._monitor = BackgroundJob(self, "radio-metadata")
        self._monitor.finished.connect(self._monitor_ended)
        self._monitor.failed.connect(self._monitor_ended)
        self._lookup = BackgroundJob(self, "radio-lyrics")
        self._lookup.finished.connect(self._apply_result)
        self._cache = IdleCache(64, max_bytes=512 * 1024)
        self._timer = QTimer(self)
        self._timer.setInterval(200)
        self._timer.timeout.connect(self._poll_title)
        self._lookup_delay = QTimer(self)
        self._lookup_delay.setSingleShot(True)
        self._lookup_delay.setInterval(1500)
        self._lookup_delay.timeout.connect(self._lookup_song)
        self._titles = deque(maxlen=1)
        self._monitor_cancel = None
        self._lookup_cancel = None
        self._song = None

    def observe(self):
        player = self.player
        if (self._monitor_cancel is not None or getattr(player, "_closing", False)
                or not player.playback_settings.get("radio_auto_lyrics")
                or not player._is_live_stream() or not player.media_player.isPlaying()
                or player.media_player.position() <= 0):
            return
        self._monitor_cancel = Event()
        self._monitor.start(watch_icy_titles, player._stream_url.toString(), self._monitor_cancel, self._titles)
        self._timer.start()

    def _poll_title(self):
        if self._titles:
            self._receive_title(self._titles.popleft())

    def _monitor_ended(self, _result):
        self._timer.stop()
        self._song = None
        self._titles.clear()
        self._cancel_lookup()
        self.player._clear_auto_stream_lyrics()

    def _receive_title(self, title):
        song = song_from_title(title)
        if song == self._song:
            return
        self._song = song
        self._cancel_lookup()
        self.player._clear_auto_stream_lyrics()
        if not song or self.player.current_lyrics_path is not None:
            return
        if song in self._cache:
            self._apply_result((song, self._cache[song]))
            return
        self._lookup_delay.start()

    def _lookup_song(self):
        song = self._song
        if not song or self.player.current_lyrics_path is not None:
            return
        self._lookup_cancel = Event()

        def lookup(cancelled):
            return song, find_stream_lyrics(song, cancelled)

        self._lookup.start(lookup, self._lookup_cancel)

    def _apply_result(self, result):
        song, lines = result
        if song == self._song:
            self._cache[song] = lines
            if lines and self.player.current_lyrics_path is None:
                self.player._show_auto_stream_lyrics(lines)

    def _cancel_lookup(self):
        self._lookup_delay.stop()
        if self._lookup_cancel is not None:
            self._lookup_cancel.set()
            self._lookup_cancel = None
        self._lookup.cancel()

    def suspend(self):
        if self._monitor_cancel is not None:
            self._monitor_cancel.set()
            self._monitor_cancel = None
        self._monitor.cancel()
        self._timer.stop()
        self._titles = deque(maxlen=1)
        self._song = None
        self._cancel_lookup()

    def reset(self):
        self.suspend()
        self.player._clear_auto_stream_lyrics()

    def close(self):
        self.suspend()
        self._monitor.close()
        self._lookup.close()
