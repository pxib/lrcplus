"""Queue operations shared by the player and queue dialog."""

from pathlib import Path

from core.playlists import path_key
from core.settings import save_playback_settings


class QueueControllerMixin:
    def _notify_queue_changed(self):
        self.update_transport_buttons()
        self.queue_changed.emit()

    def _activate_queue(self):
        if self.playback_settings.get("play_mode", "playlist") != "playlist":
            settings = dict(self.playback_settings, play_mode="playlist")
            save_playback_settings(settings)
            self.playback_settings = settings

    def enqueue_tracks(self, paths, *, play_next=False):
        paths = [Path(path).expanduser().resolve() for path in paths]
        if not paths:
            return
        self._activate_queue()
        index = self.current_index + 1 if play_next else len(self.playlist)
        index = max(0, min(index, len(self.playlist)))
        priority = [i + len(paths) if i >= index else i
                    for i in getattr(self, "_priority_queue_indices", [])]
        self.playlist[index:index] = paths
        if play_next:
            priority = list(range(index, index + len(paths))) + priority
        self._priority_queue_indices = priority
        if self.current_index >= index:
            self.current_index += len(paths)
        if self.current_audio_path is None:
            self.current_index = 0
            self.load_audio(str(self.playlist[0]))
            self._priority_queue_indices = [i for i in priority if i != 0]
        self._notify_queue_changed()

    def reorder_queue(self, order):
        # Indices identify occurrences, so repeated tracks stay distinct.
        if len(order) != len(self.playlist) or sorted(order) != list(range(len(self.playlist))):
            return
        inverse = {old: new for new, old in enumerate(order)}
        self.playlist = [self.playlist[i] for i in order]
        self.current_index = inverse.get(self.current_index, -1)
        priority = {inverse[i] for i in getattr(self, "_priority_queue_indices", []) if i in inverse}
        self._priority_queue_indices = sorted(priority)
        self._notify_queue_changed()

    def move_queue_tracks_next(self, indices):
        selected = sorted({i for i in indices if 0 <= i < len(self.playlist) and i != self.current_index})
        if not selected:
            return
        self._activate_queue()
        remaining = [i for i in range(len(self.playlist)) if i not in selected]
        insert_at = remaining.index(self.current_index) + 1 if self.current_index in remaining else 0
        order = remaining[:insert_at] + selected + remaining[insert_at:]
        priority = selected + [i for i in self._priority_queue_indices if i not in selected]
        self.reorder_queue(order)
        self._priority_queue_indices = [order.index(i) for i in priority]
        self._notify_queue_changed()

    def play_queue_index(self, index):
        if not 0 <= index < len(self.playlist):
            return
        self._activate_queue()
        self.current_index = index
        self._priority_queue_indices = [i for i in getattr(self, "_priority_queue_indices", []) if i != index]
        self.load_audio(str(self.playlist[index]))
        self.media_player.play()

    def remove_queue_tracks(self, indices):
        removed = {i for i in indices if 0 <= i < len(self.playlist)}
        if not removed:
            return
        current = self.current_index
        remaining = [i for i in range(len(self.playlist)) if i not in removed]
        inverse = {old: new for new, old in enumerate(remaining)}
        self.playlist = [self.playlist[i] for i in remaining]
        self._priority_queue_indices = [inverse[i] for i in getattr(self, "_priority_queue_indices", []) if i in inverse]
        if not self.playlist:
            self.clear_queue()
            return
        if current in removed:
            was_playing = self.media_player.isPlaying()
            self.current_index = min(sum(i < current for i in remaining), len(self.playlist) - 1)
            self.load_audio(str(self.playlist[self.current_index]))
            if was_playing:
                self.media_player.play()
        else:
            self.current_index = inverse.get(current, -1)
        self._notify_queue_changed()

    def clear_queue(self):
        self.playlist = []
        self.current_index = -1
        self._priority_queue_indices = []
        self.stop_playback()
        self.current_audio_path = None
        self.current_lyrics_path = None
        self.lyric_lines = []
        self.lyrics = []
        self.lyric_times = []
        self.scrolling_lyrics_widget.clear()
        self.waveform_widget.set_lyric_times([])
        self.setWindowTitle("LyricsPlus")
        self._notify_queue_changed()

    def replace_queue(self, paths):
        paths = [Path(path).expanduser().resolve() for path in paths]
        self._activate_queue()
        self._priority_queue_indices = []
        current = path_key(self.current_audio_path) if self.current_audio_path else None
        self.playlist = paths
        if not paths:
            self.clear_queue()
            return
        self.current_index = next((i for i, path in enumerate(paths) if path_key(path) == current), -1)
        # Keep playing when the current file also exists in the replacement.
        if self.current_index < 0:
            self.current_index = 0
            self.load_audio(str(paths[0]))
        self._notify_queue_changed()
