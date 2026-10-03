from pathlib import Path
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QMediaPlayer
import random

from core.recent import add_to_recent_songs

class AudioController:
        def load_audio(self, path):
            audio_path = Path(path)

            self.media_player.stop()

            self.current_audio_path = audio_path

            if getattr(self, "plugin_manager", None) is not None:
                self.plugin_manager.emit("track_changed", path=str(audio_path))

            # A missed sliderReleased event can leave the old boolean stuck True,
            # which previously froze seekbar updates until another media load.
            # Reset both the legacy flag and the actual slider interaction state.
            self.is_seeking = False
            if hasattr(self, "seek_slider"):
                self.seek_slider.setSliderDown(False)

            # Update the lyrics background immediately when the track changes.
            # This currently uses embedded artwork; future sources (album
            # lookup/booru) can feed the same background widget.
            if hasattr(self, "update_background_for_audio"):
                self.update_background_for_audio(audio_path)

            # Local (File) lyric settings are resolved whenever the track changes.
            self.refresh_lyrics_settings_for_current_file()

            if audio_path not in self.playlist:
                self.playlist.append(audio_path)
            self.current_index = self.playlist.index(audio_path)

            self.current_line = -1
            self.last_lyric_text = None

            self.lyric_widget.set_lyric("")

            # Reset the seek UI before the new media reports its duration.
            self.seek_slider.setRange(0, 0)
            self.seek_slider.setValue(0)
            self.current_time_label.setText("0:00")
            self.duration_label.setText("0:00")
            if hasattr(self, "waveform_widget"):
                self.waveform_widget.set_position(0)
                self.waveform_widget.set_duration(0)
                self.waveform_widget.set_lyric_times([])
                self.waveform_widget.load_audio(audio_path)

            # Load audio.
            self.media_player.setSource(
                QUrl.fromLocalFile(
                    str(audio_path)
                )
            )

            # Look for matching LRCX/LRC lyrics.
            lrc_path = self.find_lrc_for_audio(audio_path)

            if lrc_path.exists():
                self.load_lrc_file(
                    str(lrc_path)
                )
                add_to_recent_songs(audio_path, lrc_path)
            else:
                print(
                    f"[Lyrics] No matching lyrics found "
                    f"for: {audio_path.name}"
                )

                self.lyric_lines = []
                self.lyrics = []
                self.lyric_times = []
                self.current_lyrics_path = None
                self.scrolling_lyrics_widget.clear()
                if hasattr(self, "waveform_widget"):
                    self.waveform_widget.set_lyric_times([])
                add_to_recent_songs(audio_path)

            # Update UI.
            self.setWindowTitle(
                f"LRC+ — {audio_path.name}"
            )

            self.update_transport_buttons()


        def toggle_playback(self):
            if not self.current_audio_path:
                return

            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.media_player.pause()
            else:
                self.media_player.play()


        def stop_playback(self):
            self.media_player.stop()
            self.current_line = -1
            self.last_lyric_text = None
            self.seek_slider.setValue(0)
            self.current_time_label.setText("0:00")
            self.lyric_widget.set_lyric("")
            self.scrolling_lyrics_widget.set_current_line(-1, animate=False)


        def skip_seconds(self, seconds):
            duration = self.media_player.duration()
            if duration <= 0:
                return
            new_position = max(0, min(duration, self.media_player.position() + int(seconds * 1000)))
            self.media_player.setPosition(new_position)


        def _playback_paths(self):
            if getattr(self, "playback_settings", {}).get("play_mode", "playlist") == "library":
                return [Path(track.path) for track in getattr(self, "library_tracks", [])]
            return list(getattr(self, "playlist", []))


        def _active_index(self, paths):
            if not paths or not getattr(self, "current_audio_path", None):
                return -1
            try:
                current = str(Path(self.current_audio_path).resolve())
                return next(i for i, path in enumerate(paths) if str(Path(path).resolve()) == current)
            except (OSError, StopIteration):
                return -1


        def _album_key(self, path):
            target = str(Path(path).resolve())
            for track in getattr(self, "library_tracks", []):
                if str(Path(track.path).resolve()) == target:
                    return (track.album_artist or track.artist or "", track.album or Path(path).parent.name)
            return ("", Path(path).parent.name)


        def _choose_shuffle_index(self, paths, current, direction=1):
            if len(paths) <= 1:
                return current if current >= 0 else 0
            mode = getattr(self, "playback_settings", {}).get("shuffle_mode", "tracks")
            if mode == "albums":
                current_key = self._album_key(paths[current]) if current >= 0 else None
                groups = {}
                for i, path in enumerate(paths):
                    groups.setdefault(self._album_key(path), []).append(i)
                candidates = [key for key in groups if key != current_key] or list(groups)
                chosen = random.choice(candidates)
                return groups[chosen][-1 if direction < 0 else 0]
            candidates = [i for i in range(len(paths)) if i != current]
            return random.choice(candidates)


        def _load_playback_index(self, paths, index):
            if not 0 <= index < len(paths):
                return False
            if getattr(self, "playback_settings", {}).get("play_mode", "playlist") == "playlist":
                self.current_index = index
            self.load_audio(str(paths[index]))
            self.media_player.play()
            return True


        def previous_track(self):
            paths = self._playback_paths()
            if not paths:
                return
            if self.media_player.position() > 3000:
                self.media_player.setPosition(0)
                return
            current = self._active_index(paths)
            if current < 0:
                return
            if getattr(self, "shuffle_enabled", False):
                target = self._choose_shuffle_index(paths, current, -1)
            else:
                target = current - 1
                if target < 0:
                    self.media_player.setPosition(0)
                    return
            self._load_playback_index(paths, target)


        def next_track(self):
            paths = self._playback_paths()
            if not paths:
                return
            current = self._active_index(paths)
            if current < 0:
                current = -1
            if getattr(self, "shuffle_enabled", False):
                target = self._choose_shuffle_index(paths, current, 1)
            else:
                target = current + 1
                if target >= len(paths):
                    self.stop_playback()
                    return
            self._load_playback_index(paths, target)


        def update_transport_buttons(self):
            has_track = self.current_audio_path is not None
            for button in (self.previous_button, self.rewind_button, self.play_button, self.stop_button, self.forward_button):
                button.setEnabled(has_track)
            paths = self._playback_paths()
            current = self._active_index(paths)
            can_next = has_track and (self.shuffle_enabled and len(paths) > 1 or current + 1 < len(paths))
            self.next_button.setEnabled(can_next)


        def cycle_shuffle_mode(self):
            self.shuffle_enabled = not self.shuffle_enabled
            self._update_shuffle_button_text()


        def _update_shuffle_button_text(self):
            if not hasattr(self, "shuffle_button"):
                return
            if not getattr(self, "shuffle_enabled", False):
                self.shuffle_button.setText("Shuffle: Off")
                return
            mode = getattr(self, "playback_settings", {}).get("shuffle_mode", "tracks")
            self.shuffle_button.setText(f"Shuffle: {mode.title()}")


        def cycle_loop_mode(self):
            self.loop_mode = (self.loop_mode + 1) % 3
            modes = ["Off", "All", "Current"]
            self.loop_button.setText(f"Loop: {modes[self.loop_mode]}")


        def update_play_button(self, state):
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.play_button.setText("Pause")
            else:
                self.play_button.setText("Play")
            self.update_transport_buttons()


        def handle_media_status(self, status):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                if self.loop_mode == 2:
                    self.media_player.setPosition(0)
                    self.media_player.play()
                else:
                    paths = self._playback_paths()
                    current = self._active_index(paths)
                    if self.loop_mode == 1 or (current >= 0 and current + 1 < len(paths)) or getattr(self, "shuffle_enabled", False):
                        if self.loop_mode == 1 and not getattr(self, "shuffle_enabled", False) and current + 1 >= len(paths):
                            self._load_playback_index(paths, 0)
                        else:
                            self.next_track()


        def start_seeking(self):
            self.is_seeking = True


        def finish_seeking(self):
            try:
                self.media_player.setPosition(
                    self.seek_slider.value()
                )
            finally:
                # Never leave seekbar updates permanently disabled.
                self.is_seeking = False


        def handle_volume_change(self, value):
            self.last_volume = value
            self.audio_output.setVolume(
                value / 100.0
            )

            if value == 0:
                self.audio_output.setMuted(True)
                self.is_muted = True
            else:
                self.audio_output.setMuted(False)
                self.is_muted = False

            self.mute_button.setText(
                "Unmute" if self.is_muted else "Mute"
            )


        def toggle_mute(self):
            self.is_muted = not self.is_muted

            if self.is_muted:
                self.last_volume = max(
                    1,
                    self.volume_slider.value(),
                )
                self.audio_output.setMuted(True)
                self.volume_slider.setValue(0)
            else:
                self.audio_output.setMuted(False)
                self.volume_slider.setValue(
                    self.last_volume
                )

            self.mute_button.setText(
                "Unmute" if self.is_muted else "Mute"
            )


        def handle_speed_change(self, value):
            # Snap to 100% if close enough (within 5 units)
            snap_threshold = 5
            if abs(value - 100) <= snap_threshold:
                value = 100
                # Update slider without triggering another signal
                self.speed_slider.blockSignals(True)
                self.speed_slider.setValue(100)
                self.speed_slider.blockSignals(False)
        
            # Value ranges from 50 to 200, representing 50% to 200% speed
            self.playback_speed = value / 100.0
            self.media_player.setPlaybackRate(self.playback_speed)
            self.speed_label.setText(f"{abs(value)}%")


        def cycle_loop_mode(self):
            self.loop_mode = (self.loop_mode + 1) % 3
        
            modes = ["Off", "All", "Current"]
            self.loop_button.setText(f"Loop: {modes[self.loop_mode]}")


        def update_play_button(self, state):
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.play_button.setText("Pause")
            else:
                self.play_button.setText("Play")

            self.update_transport_buttons()


        def handle_media_status(self, status):
            if status == QMediaPlayer.MediaStatus.EndOfMedia:
                if self.loop_mode == 1:  # Loop all
                    self.next_track()
                elif self.loop_mode == 2:  # Loop current
                    self.media_player.setPosition(0)
                    self.media_player.play()


        def position_changed(self, position_ms):
            # The slider's real interaction state is authoritative.  A rare missed
            # sliderReleased signal used to leave is_seeking=True forever, freezing
            # the seekbar until the user changed tracks.
            if not self.seek_slider.isSliderDown():
                self.seek_slider.setValue(
                    position_ms
                )

            self.current_time_label.setText(
                self.format_time(position_ms)
            )

            if hasattr(self, "waveform_widget"):
                self.waveform_widget.set_position(position_ms)

            self.update_lyrics(position_ms)

            if getattr(self, "plugin_manager", None) is not None:
                # Plugins rarely need backend-rate position ticks. Throttle the
                # broadcast while preserving immediate updates after seeks.
                last = getattr(self, "_last_plugin_position_emit", None)
                if last is None or abs(int(position_ms) - last) >= 100:
                    self._last_plugin_position_emit = int(position_ms)
                    self.plugin_manager.emit("position_changed", position_ms=int(position_ms))


        def duration_changed(self, duration_ms):
            self.seek_slider.setRange(
                0,
                duration_ms
            )
            self.duration_label.setText(
                self.format_time(duration_ms)
            )

            if hasattr(self, "waveform_widget"):
                self.waveform_widget.set_duration(duration_ms)

            if getattr(self, "plugin_manager", None) is not None:
                self.plugin_manager.emit("duration_changed", duration_ms=int(duration_ms))


        def format_time(self, milliseconds):
            total_seconds = max(0, int(milliseconds / 1000))
            minutes, seconds = divmod(total_seconds, 60)
            hours, minutes = divmod(minutes, 60)

            if hours:
                return f"{hours}:{minutes:02d}:{seconds:02d}"

            return f"{minutes}:{seconds:02d}"

