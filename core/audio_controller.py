import random
import math
from pathlib import Path

from PySide6.QtCore import QSignalBlocker, Qt, QUrl
from PySide6.QtMultimedia import (
    QAudioBufferOutput,
    QAudioSink,
    QMediaPlayer,
)
from PySide6.QtWidgets import QApplication, QMessageBox

from core.recent import add_to_recent_songs
from core.background_job import BackgroundJob
from core.audio_sink import dispose_audio_sink
from core.settings import save_playback_settings
from core.playlists import path_key
from core.replaygain import ReplayGain, read_replaygain
from core.audio_dsp import (
    volume_to_gain,
    snap_volume_percentage,
    apply_pcm_gain,
    apply_pcm_gain_to_bytes,
    PcmRateProcessor,
    apply_pcm_pitch_to_bytes,
    reset_pcm_pitch_state,
    flush_pcm_pitch_state,
)



class AudioController:
        def _replaygain_options(self):
            settings = getattr(self, "playback_settings", {})
            return (settings.get("replaygain_mode", "off"),
                    settings.get("replaygain_preamp", 0),
                    settings.get("replaygain_prevent_clipping", True))

        def _handle_gapless_fallback(self, error):
            self._load_replaygain()
            self.statusBar().showMessage(f"Gapless playback unavailable: {error}", 7000)

        def _replaygain_multiplier(self):
            return getattr(self, "_replaygain_tags", ReplayGain()).multiplier(*self._replaygain_options())

        def _load_replaygain(self):
            job = getattr(self, "_replaygain_job", None)
            if job is not None:
                job.cancel()
            self._replaygain_tags = ReplayGain()
            self._configure_pcm_volume(self.volume_slider.value())
            path = getattr(self, "current_audio_path", None)
            if (path is None or self._replaygain_options()[0] == "off"
                    or getattr(self.media_player, "_gapless_active", False)):
                return
            if job is None:
                job = self._replaygain_job = BackgroundJob(self, "replaygain-tags")
                job.finished.connect(self._apply_replaygain)
            job.start(lambda: (path, read_replaygain(path)))

        def _apply_replaygain(self, result):
            path, tags = result
            if path == self.current_audio_path:
                self._replaygain_tags = tags
                self._configure_pcm_volume(self.volume_slider.value())

        def _invalidate_gapless_preload(self):
            self._gapless_shuffle_choice = None
            invalidate = getattr(self.media_player, "invalidate_preload", None)
            if invalidate is not None:
                invalidate()

        def _gapless_next_path(self):
            paths = self._playback_paths()
            if not paths:
                return None
            current = self._active_index(paths)
            after = self.playback_settings.get("after_playback", "next_track")
            if after == "repeat_track":
                return str(self.current_audio_path) if self.current_audio_path else None
            priority = getattr(self, "_priority_queue_indices", [])
            if self.playback_settings.get("play_mode", "library") == "playlist" and priority:
                return str(paths[priority[0]])
            if getattr(self, "shuffle_enabled", False) and len(paths) > 1:
                forward = getattr(self, "_shuffle_forward_history", [])
                for entry in reversed(forward):
                    target = self._resolve_history_index(entry, paths)
                    if target is not None:
                        return str(paths[target])
                target = self._choose_shuffle_index(paths, current)
                self._gapless_shuffle_choice = (tuple(map(str, paths)), current, target)
                return str(paths[target])
            if current + 1 < len(paths):
                return str(paths[current + 1])
            return str(paths[0]) if after == "repeat_all" else None

        def load_audio(self, path):
            audio_path = Path(path).expanduser().resolve()

            handoff = getattr(self.media_player, "_gapless_handoff", False)
            if not handoff:
                self._flush_pcm_output()
                self.media_player.stop()

            self.current_audio_path = audio_path

            self._cancel_lyrics_search()
            self.lyric_lines = []
            self.lyrics = []
            self.lyric_times = []
            self._playback_line_indices = []
            self.current_lyrics_path = None
            self.plain_lyrics_mode = False
            self._furigana_prefetch_id = getattr(self, "_furigana_prefetch_id", 0) + 1
            self.scrolling_lyrics_widget.clear()
            self.lyrics_list_widget.clear()
            self.furigana_progress_bar.hide()

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

            current = self.current_index
            if not (0 <= current < len(self.playlist)
                    and path_key(self.playlist[current]) == path_key(audio_path)):
                self.current_index = next((i for i, entry in enumerate(self.playlist)
                                           if path_key(entry) == path_key(audio_path)), -1)
                if self.current_index < 0 and self.playback_settings.get("play_mode", "library") == "playlist":
                    self.playlist.append(audio_path)
                    self.current_index = len(self.playlist) - 1
            if self.playback_settings.get("play_mode", "library") == "playlist":
                self._priority_queue_indices = [i for i in getattr(self, "_priority_queue_indices", [])
                                                if i != self.current_index]

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

            if not handoff:
                self._flush_pcm_output()
            self._replaygain_tags = ReplayGain()
            self._configure_pcm_volume(self.volume_slider.value())

            # Load audio.
            self.media_player.setSource(
                QUrl.fromLocalFile(
                    str(audio_path)
                )
            )
            self._set_native_pitch_compensation()
            self._load_replaygain()

            # Folder walks and lyric-file reads must not delay playback or PCM
            # callbacks on the GUI thread. Only apply the current search result.
            self._start_lyrics_search(audio_path)
            add_to_recent_songs(audio_path)

            # Update UI.
            self.setWindowTitle(
                f"LRCPlus — {audio_path.name}"
            )

            if hasattr(self, "_notify_queue_changed"):
                self._notify_queue_changed()
            else:
                self.update_transport_buttons()


        def _cancel_lyrics_search(self):
            job = getattr(self, "_lyrics_search_job", None)
            if job is not None:
                job.cancel()

        def _start_lyrics_search(self, audio_path):
            if getattr(self, "_lyrics_search_job", None) is None:
                self._lyrics_search_job = BackgroundJob(self, "lyrics-search")
                self._lyrics_search_job.finished.connect(self._apply_lyrics_search_result)
                self._lyrics_search_job.failed.connect(
                    lambda error: self.statusBar().showMessage(f"Lyrics search failed: {error}", 5000)
                )

            def search():
                path = self.find_lrc_for_audio(audio_path)
                return audio_path, path if path.is_file() else None

            self._lyrics_search_job.start(search)

        def _apply_lyrics_search_result(self, result):
            audio_path, lrc_path = result
            if audio_path != self.current_audio_path or lrc_path is None:
                return
            self.load_lrc_file(str(lrc_path))
            self.update_lyrics(self.media_player.position())
            add_to_recent_songs(audio_path, lrc_path)

        def toggle_playback(self):
            if not self.current_audio_path:
                paths = self._playback_paths()
                if paths:
                    index = self._choose_shuffle_index(paths, -1) if self.shuffle_enabled else 0
                    self._load_playback_index(paths, index)
                return

            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
                self.media_player.pause()
            else:
                self.media_player.play()


        def stop_playback(self):
            self._flush_pcm_output()
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
            self._flush_pcm_output()
            self.media_player.setPosition(new_position)


        def _playback_paths(self):
            if getattr(self, "playback_settings", {}).get("play_mode", "library") == "library":
                return [Path(track.path) for track in getattr(self, "library_tracks", [])]
            return list(getattr(self, "playlist", []))


        def set_playback_source(self, source):
            if source not in {"library", "playlist"}:
                return
            if self.playback_settings.get("play_mode", "library") != source:
                self._clear_shuffle_history()
            self.playback_settings["play_mode"] = source
            self._invalidate_gapless_preload()
            save_playback_settings(self.playback_settings)
            self._update_playback_source_control()
            self.update_transport_buttons()


        def _update_playback_source_control(self):
            combo = getattr(self, "playback_source_combo", None)
            if combo is not None:
                with QSignalBlocker(combo):
                    combo.setCurrentIndex(combo.findData(self.playback_settings.get("play_mode", "library")))


        def _active_index(self, paths):
            if not paths or not getattr(self, "current_audio_path", None):
                return -1
            try:
                index = getattr(self, "current_index", -1)
                if (getattr(self, "playback_settings", {}).get("play_mode", "library") == "playlist"
                        and 0 <= index < len(paths)
                        and path_key(paths[index]) == path_key(self.current_audio_path)):
                    return index
                current = str(Path(self.current_audio_path).resolve())
                return next(i for i, path in enumerate(paths) if path_key(path) == path_key(current))
            except (OSError, StopIteration):
                return -1


        def _album_key(self, path, album_lookup=None):
            target = str(Path(path).resolve())
            if album_lookup is None:
                album_lookup = {
                    str(Path(track.path).resolve()): (
                        track.album_artist or track.artist or "",
                        track.album or Path(track.path).parent.name,
                    )
                    for track in getattr(self, "library_tracks", [])
                }
            return album_lookup.get(target, ("", Path(path).parent.name))


        def _choose_shuffle_index(self, paths, current, direction=1):
            reserved = getattr(self, "_gapless_shuffle_choice", None)
            if direction == 1 and reserved is not None:
                self._gapless_shuffle_choice = None
                if reserved[0] == tuple(map(str, paths)) and reserved[1] == current:
                    return reserved[2]
            if len(paths) <= 1:
                return current if current >= 0 else 0
            mode = getattr(self, "playback_settings", {}).get("shuffle_mode", "tracks")
            if mode == "albums":
                album_lookup = {
                    str(Path(track.path).resolve()): (
                        track.album_artist or track.artist or "",
                        track.album or Path(track.path).parent.name,
                    )
                    for track in getattr(self, "library_tracks", [])
                }
                keys = [self._album_key(path, album_lookup) for path in paths]
                current_key = keys[current] if current >= 0 else None
                groups = {}
                for i, key in enumerate(keys):
                    groups.setdefault(key, []).append(i)
                candidates = [key for key in groups if key != current_key] or list(groups)
                chosen = random.choice(candidates)
                return groups[chosen][-1 if direction < 0 else 0]
            candidates = [i for i in range(len(paths)) if i != current]
            return random.choice(candidates)


        def _load_playback_index(self, paths, index):
            if not 0 <= index < len(paths):
                return False
            if getattr(self, "playback_settings", {}).get("play_mode", "library") == "playlist":
                self.current_index = index
                self._priority_queue_indices = [i for i in getattr(self, "_priority_queue_indices", []) if i != index]
            self.load_audio(str(paths[index]))
            self.media_player.play()
            return True


        def _clear_shuffle_history(self):
            self._shuffle_history = []
            self._shuffle_forward_history = []


        def _resolve_history_index(self, entry, paths):
            index, key = entry
            if 0 <= index < len(paths) and path_key(paths[index]) == key:
                return index
            return next((i for i, path in enumerate(paths) if path_key(path) == key), None)


        def previous_track(self):
            paths = self._playback_paths()
            if not paths:
                return
            if self.media_player.position() > 3000:
                self._flush_pcm_output()
                self.media_player.setPosition(0)
                return
            current = self._active_index(paths)
            if current < 0:
                return
            if getattr(self, "shuffle_enabled", False):
                history = getattr(self, "_shuffle_history", [])
                while history:
                    target = self._resolve_history_index(history.pop(), paths)
                    if target is not None:
                        self._shuffle_forward_history.append(
                            (current, path_key(paths[current]))
                        )
                        self._load_playback_index(paths, target)
                        return
                target = self._choose_shuffle_index(paths, current, -1)
            else:
                target = current - 1
                if target < 0:
                    self._flush_pcm_output()
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
            priority = getattr(self, "_priority_queue_indices", [])
            if (getattr(self, "playback_settings", {}).get("play_mode", "library") == "playlist"
                    and priority):
                target = priority.pop(0)
                if getattr(self, "shuffle_enabled", False):
                    if current >= 0:
                        self._shuffle_history.append((current, path_key(paths[current])))
                    self._shuffle_forward_history.clear()
            elif getattr(self, "shuffle_enabled", False):
                forward_history = getattr(self, "_shuffle_forward_history", [])
                while forward_history:
                    target = self._resolve_history_index(forward_history.pop(), paths)
                    if target is not None:
                        if current >= 0:
                            self._shuffle_history.append(
                                (current, path_key(paths[current]))
                            )
                        self._load_playback_index(paths, target)
                        return
                if current >= 0:
                    self._shuffle_history.append((current, path_key(paths[current])))
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
            self.play_button.setEnabled(has_track or bool(paths))
            current = self._active_index(paths)
            priority = (getattr(self, "playback_settings", {}).get("play_mode", "library") == "playlist"
                        and bool(getattr(self, "_priority_queue_indices", [])))
            can_next = has_track and (priority or self.shuffle_enabled and len(paths) > 1 or current + 1 < len(paths))
            self.next_button.setEnabled(can_next)


        def cycle_shuffle_mode(self):
            self.shuffle_enabled = not self.shuffle_enabled
            self._clear_shuffle_history()
            self._invalidate_gapless_preload()
            self.playback_settings["shuffle_enabled"] = self.shuffle_enabled
            save_playback_settings(self.playback_settings)
            self._update_shuffle_button_text()
            self.update_transport_buttons()


        def _update_shuffle_button_text(self):
            if not hasattr(self, "shuffle_button"):
                return
            if not getattr(self, "shuffle_enabled", False):
                self.shuffle_button.setText("Shuffle: Off")
                return
            mode = getattr(self, "playback_settings", {}).get("shuffle_mode", "tracks")
            self.shuffle_button.setText(f"Shuffle: {mode.title()}")


        def start_seeking(self):
            self.is_seeking = True


        def finish_seeking(self):
            try:
                self._flush_pcm_output()
                self.media_player.setPosition(
                    self.seek_slider.value()
                )
            finally:
                # Never leave seekbar updates permanently disabled.
                self.is_seeking = False


        def _activate_pcm_route(self):
            self._pcm_route_active = True
            self.audio_output.setVolume(0.0)


        def _stop_pcm_sink(self):
            worker = getattr(self, "_pcm_worker", None)
            if getattr(worker, "owns_output", False):
                worker.reset()
            self._pcm_drain_timer.stop()
            self._pcm_pending.clear()
            self._pcm_route_active = False
            sink = getattr(self, "_pcm_sink", None)
            if sink is not None:
                dispose_audio_sink(sink)
            self._pcm_sink = None
            self._pcm_sink_io = None


        def _flush_pcm_output(self):
            self._pcm_end_pending = False
            self._stop_pcm_sink()
            rate_processor = getattr(self, "_pcm_rate_processor", None)
            if rate_processor is not None:
                rate_processor.reset()
            self._pcm_rate_format = None
            worker = getattr(self, "_pcm_worker", None)
            if worker is None:
                reset_pcm_pitch_state()
            elif not getattr(worker, "owns_output", False):
                worker.reset()
            if hasattr(self, "volume_slider"):
                self.audio_output.setVolume(min(1.0, getattr(
                    self, "_current_pcm_gain", self.volume_slider.value() / 100.0)))


        def _pcm_pitch_shift(self):
            """Return the pitch adjustment after rate resampling the PCM stream."""
            if getattr(self, "pitch_linked_to_speed", False):
                # Linked mode uses speed for pitch and disables the manual offset.
                return 0.0
            pitch = float(getattr(self, "pitch_semitones", 0.0))
            speed = max(0.01, float(getattr(self, "playback_speed", 1.0)))
            speed_pitch = 12.0 * math.log2(speed)
            # Cancel the resampling pitch change, then apply the chosen pitch.
            return pitch - speed_pitch


        def _displayed_pitch_semitones(self):
            """Return the audible pitch shift represented by the current controls."""
            pitch = float(getattr(self, "pitch_semitones", 0.0))
            if not getattr(self, "pitch_linked_to_speed", False):
                return pitch
            speed = max(0.01, float(getattr(self, "playback_speed", 1.0)))
            return 12.0 * math.log2(speed)


        def _pcm_rate_active(self):
            return abs(float(getattr(self, "playback_speed", 1.0)) - 1.0) >= 0.001


        def _configure_pcm_volume(self, value, refresh_format=False):
            value = int(value)
            decoder = getattr(self.media_player, "_decoder", None)
            if getattr(self.media_player, "_gapless_active", False) and decoder is not None:
                self._replaygain_tags = decoder.tags
            self._current_pcm_gain = volume_to_gain(value) * self._replaygain_multiplier()
            pitch_active = abs(self._pcm_pitch_shift()) >= 0.001
            rate_active = self._pcm_rate_active()
            configure_gapless = getattr(self.media_player, "configure_gapless", None)
            if configure_gapless is not None and configure_gapless(
                self.playback_settings.get("gapless_playback", False),
                volume_to_gain(value), self._replaygain_options(),
                compatible=not pitch_active and not rate_active,
            ):
                self._stop_pcm_sink()
                worker = getattr(self, "_pcm_worker", None)
                if getattr(worker, "owns_output", False):
                    worker.configure(self.audio_output.device(), 1, 0, 1,
                                     enabled=False, playing=False)
                self.media_player.setAudioBufferOutput(None)
                return
            gain_active = self._current_pcm_gain > 1.0
            worker = getattr(self, "_pcm_worker", None)
            owns_output = getattr(worker, "owns_output", False)
            if owns_output:
                worker.configure(
                    self.audio_output.device(), self.playback_speed,
                    self._pcm_pitch_shift(), self._current_pcm_gain,
                    enabled=(gain_active or pitch_active or rate_active)
                            and not getattr(self, "_audio_output_unavailable", False)
                            and getattr(self, "_audio_device_dialog", None) is None,
                    playing=self.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState,
                )
            if not gain_active and not pitch_active and not rate_active:
                # Keep the decoder's buffer output attached when returning to
                # native playback, so Qt does not reconfigure the renderer
                # during the handoff from processed audio.
                # The disabled worker (or _write_pcm_buffer's bypass guard)
                # ignores buffers until processing is needed again.
                self._stop_pcm_sink()
                worker = getattr(self, "_pcm_worker", None)
                if worker is not None:
                    worker.reset()
                else:
                    reset_pcm_pitch_state()
                self.audio_output.setVolume(self._current_pcm_gain)
                return

            output = getattr(self, "_pcm_buffer_output", None)
            if refresh_format or output is None:
                self._stop_pcm_sink()
                if output is not None:
                    output.deleteLater()
                output = QAudioBufferOutput(
                    self.audio_output.device().preferredFormat(), self
                )
                if owns_output:
                    # Qt emits from its audio renderer. Enqueue directly there;
                    # the callback only copies data into the audio mailbox.
                    output.audioBufferReceived.connect(
                        worker.receive_buffer, Qt.ConnectionType.DirectConnection,
                    )
                else:
                    output.audioBufferReceived.connect(self._write_pcm_buffer)
                self._pcm_buffer_output = output

            if self.media_player.audioBufferOutput() is not output:
                self.media_player.setAudioBufferOutput(output)

            # Keep the native player audible until the processed sink has
            # successfully started. This avoids muting playback if Qt cannot
            # provide a decoded buffer for the selected output device.
            if self._pcm_sink is not None:
                self._pcm_sink.setVolume(min(1.0, self._current_pcm_gain))
            self.audio_output.setVolume(
                0.0 if getattr(self, "_pcm_route_active", False)
                else min(1.0, self._current_pcm_gain)
            )


        def _write_pcm_buffer(self, buffer):
            worker = getattr(self, "_pcm_worker", None)
            if getattr(worker, "owns_output", False):
                worker.receive_buffer(buffer)
                return
            if (getattr(self, "_audio_output_unavailable", False)
                    or getattr(self, "_audio_device_dialog", None) is not None):
                return
            if (getattr(self, "_current_pcm_gain", 1.0) <= 1.0
                    and abs(self._pcm_pitch_shift()) < 0.001
                    and not self._pcm_rate_active()):
                return

            if buffer.isValid():
                data = bytes(buffer.data())
                if not data:
                    return
                audio_format = self._pcm_rate_format = buffer.format()
            else:
                audio_format = getattr(self, "_pcm_rate_format", None)
                if audio_format is None:
                    return
                data = b""
            worker = getattr(self, "_pcm_worker", None)
            if worker is not None:
                worker.submit(data, audio_format, self.playback_speed,
                              self._pcm_pitch_shift(), self._current_pcm_gain)
                return
            try:
                rate_processor = getattr(self, "_pcm_rate_processor", None)
                if rate_processor is None:
                    rate_processor = self._pcm_rate_processor = PcmRateProcessor()
                pcm = (rate_processor.process(
                    data, audio_format.sampleFormat(), audio_format.channelCount(),
                    self.playback_speed,
                    sample_rate=audio_format.sampleRate(),
                ) if data else rate_processor.flush())
                pcm = apply_pcm_pitch_to_bytes(
                    pcm, audio_format.sampleFormat(), audio_format.channelCount(),
                    audio_format.sampleRate(),
                    self._pcm_pitch_shift(),
                )
                if not data:
                    pcm += flush_pcm_pitch_state()
                if self._current_pcm_gain > 1.0:
                    pcm = apply_pcm_gain_to_bytes(
                        pcm, audio_format.sampleFormat(), self._current_pcm_gain,
                    )
                self._accept_processed_pcm((pcm, audio_format,
                    abs(self._pcm_pitch_shift()) < 0.001
                    or getattr(apply_pcm_pitch_to_bytes, "_started", False)))
            except (ImportError, ValueError, RuntimeError, OSError) as error:
                self._pcm_output_failed(error)


        def _accept_processed_pcm(self, result):
            pcm, audio_format, started = result
            if not pcm or not started:
                return
            try:
                if self._pcm_sink is None or self._pcm_sink.format() != audio_format:
                    self._stop_pcm_sink()
                    self._pcm_sink = QAudioSink(
                        self.audio_output.device(), audio_format, self
                    )
                    self._pcm_sink.setVolume(min(1.0, self._current_pcm_gain))
                    self._pcm_sink.setBufferSize(
                        max(audio_format.bytesForDuration(30_000), audio_format.bytesPerFrame())
                    )
                    self._pcm_sink_io = self._pcm_sink.start()
                    if self._pcm_sink_io is None:
                        raise RuntimeError("Qt could not start the PCM audio sink.")

                self._pcm_pending.extend(pcm)
                # Recover from a slow device or a burst of decoded callbacks
                # without letting old audio accumulate seconds of delay. Keep
                # at least one complete decoded block for large-buffer codecs.
                frame_size = audio_format.bytesPerFrame()
                pending_limit = max(audio_format.bytesForDuration(60_000), len(pcm))
                pending_limit -= pending_limit % frame_size
                excess = len(self._pcm_pending) - pending_limit
                if excess > 0:
                    del self._pcm_pending[:excess]
                self._drain_pcm_pending()
                if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                    self._pcm_sink.suspend()
            except (ValueError, RuntimeError, OSError) as error:
                self._pcm_output_failed(error)


        def _drain_pcm_pending(self):
            sink = getattr(self, "_pcm_sink", None)
            output = getattr(self, "_pcm_sink_io", None)
            if sink is None or output is None or not self._pcm_pending:
                self._pcm_drain_timer.stop()
                return
            if self.media_player.playbackState() == QMediaPlayer.PlaybackState.PausedState:
                sink.suspend()
                self._pcm_drain_timer.stop()
                return

            frame_size = sink.format().bytesPerFrame()
            available = max(0, sink.bytesFree())
            chunk_size = min(available, len(self._pcm_pending))
            chunk_size -= chunk_size % frame_size
            if chunk_size:
                try:
                    written = output.write(bytes(self._pcm_pending[:chunk_size]))
                except (RuntimeError, OSError) as error:
                    self._pcm_output_failed(error)
                    return
                if written < 0:
                    self._pcm_output_failed("The PCM audio sink rejected audio data.")
                    return
                del self._pcm_pending[:written]
                if written > 0:
                    self._pcm_route_active = True
                    self.audio_output.setVolume(0.0)

            if self._pcm_pending:
                if not self._pcm_drain_timer.isActive():
                    self._pcm_drain_timer.start()
            else:
                self._pcm_drain_timer.stop()


        def _pcm_output_failed(self, error):
            end_pending = getattr(self, "_pcm_end_pending", False)
            self._pcm_end_pending = False
            recover = getattr(self, "_recover_disconnected_output", None)
            if recover is not None and recover():
                return
            worker = getattr(self, "_pcm_worker", None)
            if worker is not None:
                worker.reset()
            self._stop_pcm_sink()
            self.media_player.setAudioBufferOutput(None)
            self._pcm_buffer_output = None
            self.audio_output.setVolume(1.0)
            QMessageBox.warning(
                self,
                "Audio processing unavailable",
                f"Could not process decoded audio:\n{error}",
            )
            if end_pending:
                self.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)


        def _sync_pcm_sink_state(self, state):
            if getattr(self, "_closing", False):
                return
            if (state == QMediaPlayer.PlaybackState.PlayingState
                    and getattr(self, "_pcm_end_pending", False)):
                self._flush_pcm_output()
            if (
                state == QMediaPlayer.PlaybackState.PlayingState
                and (getattr(self, "_current_pcm_gain", 1.0) > 1.0
                     or abs(self._pcm_pitch_shift()) >= 0.001
                     or self._pcm_rate_active())
                and self._pcm_buffer_output is not None
                and self.media_player.audioBufferOutput()
                is not self._pcm_buffer_output
            ):
                self.media_player.setAudioBufferOutput(self._pcm_buffer_output)

            worker = getattr(self, "_pcm_worker", None)
            if getattr(worker, "owns_output", False):
                # Qt reports StoppedState before EndOfMedia. The direct decoder
                # EOF notification lets us retain the processed tail meanwhile.
                if (state == QMediaPlayer.PlaybackState.StoppedState
                        and worker.finish()):
                    self._pcm_end_pending = True
                    return
                worker.set_playing(state == QMediaPlayer.PlaybackState.PlayingState)
            sink = getattr(self, "_pcm_sink", None)
            if state == QMediaPlayer.PlaybackState.StoppedState:
                self._flush_pcm_output()
                return
            if sink is None:
                return
            if state == QMediaPlayer.PlaybackState.PlayingState:
                sink.resume()
                self._drain_pcm_pending()
            elif state == QMediaPlayer.PlaybackState.PausedState:
                sink.suspend()
                self._pcm_drain_timer.stop()

        def _pcm_output_drained(self):
            if not getattr(self, "_pcm_end_pending", False):
                return
            self._flush_pcm_output()
            self.handle_media_status(QMediaPlayer.MediaStatus.EndOfMedia)


        def _warn_if_high_volume(self, value):
            if (
                value <= 150
                or self.playback_settings.get(
                    "high_volume_warning_shown", False
                )
            ):
                return
            self.playback_settings["high_volume_warning_shown"] = True
            save_playback_settings(self.playback_settings)
            QMessageBox.warning(
                self,
                "High volume",
                "Volume above 150% can cause severe clipping and may "
                "damage your hearing or speakers. Use care, especially "
                "with headphones.",
            )


        def handle_volume_change(self, value):
            if value > 0:
                self.last_volume = value
            self.playback_settings["volume"] = value
            self.volume_percentage_label.setText(f"{value}%")
            self._configure_pcm_volume(value)

            if (
                value > 150
                and not self.playback_settings.get(
                    "high_volume_warning_shown", False
                )
            ):
                self.playback_settings["high_volume_warning_shown"] = True
                save_playback_settings(self.playback_settings)
                QMessageBox.warning(
                    self,
                    "High volume",
                    "Volume above 150% can cause severe clipping and may "
                    "damage your hearing or speakers. Use care, especially "
                    "with headphones.",
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

            self._warn_if_high_volume(value)


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


        def _schedule_playback_settings_save(self):
            timer = getattr(self, "_playback_save_timer", None)
            if timer is None:
                save_playback_settings(self.playback_settings)
            else:
                timer.start()


        def handle_speed_change(self, value):
            previous_speed = self.playback_speed
            # Snap to 100% if close enough (within 5 units)
            snap_threshold = 5
            if abs(value - 100) <= snap_threshold:
                value = 100
                # Update slider without triggering another signal
            # The dialog slider ranges from 25% to 200%.
            self.playback_speed = value / 100.0
            self.media_player.setPlaybackRate(self.playback_speed)
            self._set_native_pitch_compensation()
            self.playback_settings["speed_percent"] = value
            self._schedule_playback_settings_save()
            self.pitch_status_label.setText(
                f"{self._displayed_pitch_semitones():+.1f} st"
            )
            if abs(previous_speed - self.playback_speed) >= 0.001:
                self._flush_pcm_output()
            self._configure_pcm_volume(self.volume_slider.value())


        def handle_pitch_link_change(self, linked):
            previous_shift = self._pcm_pitch_shift()
            self.pitch_linked_to_speed = bool(linked)
            self.playback_settings["pitch_linked_to_speed"] = self.pitch_linked_to_speed
            self._schedule_playback_settings_save()
            self._set_native_pitch_compensation()
            self.pitch_status_label.setText(
                f"{self._displayed_pitch_semitones():+.1f} st"
            )
            if abs(previous_shift - self._pcm_pitch_shift()) >= 0.001:
                self._flush_pcm_output()
            self._configure_pcm_volume(self.volume_slider.value())


        def _set_native_pitch_compensation(self):
            """Keep Qt's direct-output fallback consistent with the link option."""
            setter = getattr(self.media_player, "setPitchCompensation", None)
            if setter is None:
                return
            try:
                setter(not self.pitch_linked_to_speed)
            except (RuntimeError, TypeError):
                # Some backends expose the API but keep compensation always on.
                pass


        def handle_pitch_change(self, mode, value):
            previous_shift = self._pcm_pitch_shift()
            value = float(value)
            if mode == "Octaves":
                semitones = value * 12.0
            elif mode == "Percent":
                semitones = 12.0 * math.log2(max(0.01, value) / 100.0)
            else:
                semitones = value
            self.pitch_semitones = max(-24.0, min(24.0, float(semitones)))
            self.pitch_settings = {"mode": mode, "value": value}
            self.playback_settings["pitch_mode"] = mode
            self.playback_settings["pitch_value"] = value
            self._schedule_playback_settings_save()
            self.pitch_status_label.setText(
                f"{self._displayed_pitch_semitones():+.1f} st"
            )
            # Preserve analysis for fine adjustments. Octave jumps need fresh
            # context; native playback covers that single warmup.
            shift = self._pcm_pitch_shift()
            if ((abs(previous_shift) < 0.001) != (abs(shift) < 0.001)
                    or abs(previous_shift - shift) >= 12.0):
                self._flush_pcm_output()
            self._configure_pcm_volume(self.volume_slider.value())


        def cycle_loop_mode(self):
            self.loop_mode = (self.loop_mode + 1) % 3
        
            modes = ["Off", "All", "Current"]
            self.loop_button.setText(f"Loop: {modes[self.loop_mode]}")


        def update_play_button(self, state):
            if state == QMediaPlayer.PlaybackState.PlayingState and (
                getattr(self, "_audio_output_unavailable", False)
                or getattr(self, "_audio_device_dialog", None) is not None
            ):
                self.media_player.pause()
                return
            self._sync_pcm_sink_state(state)
            if state == QMediaPlayer.PlaybackState.PlayingState:
                self.play_button.setText("Pause")
            else:
                self.play_button.setText("Play")

            self.update_transport_buttons()


        def handle_media_status(self, status):
            if getattr(self, "_closing", False):
                return
            if (
                status != QMediaPlayer.MediaStatus.EndOfMedia
                or self.media_player.mediaStatus()
                != QMediaPlayer.MediaStatus.EndOfMedia
            ):
                return

            if getattr(self, "_pcm_end_pending", False):
                return
            worker = getattr(self, "_pcm_worker", None)
            if getattr(worker, "owns_output", False) and worker.finish(decoder_finished=True):
                self._pcm_end_pending = True
                return

            paths = self._playback_paths()
            if not paths:
                return

            after = getattr(self, "playback_settings", {}).get(
                "after_playback", "next_track"
            )

            # Repeat Track takes priority over normal progression.
            if after == "repeat_track":
                if getattr(self.media_player, "_gapless_handoff", False):
                    self.load_audio(str(self.current_audio_path))
                    self.media_player.play()
                    return
                self._flush_pcm_output()
                self.media_player.setPosition(0)
                self.media_player.play()
                return

            current = self._active_index(paths)

            # An explicit Play next request also takes precedence over shuffle
            # and can point earlier in the list after a manual reorder.
            if (getattr(self, "playback_settings", {}).get("play_mode", "library") == "playlist"
                    and getattr(self, "_priority_queue_indices", [])):
                self.next_track()
                return

            # Shuffle always selects another item when possible.
            if getattr(self, "shuffle_enabled", False):
                if len(paths) > 1:
                    self.next_track()
                elif after == "repeat_all":
                    self._load_playback_index(paths, 0)
                else:
                    self.stop_playback()
                return

            # Sequential playback: continue normally, then apply After Playback
            # only when the end of the active Playlist/Library pool is reached.
            if current + 1 < len(paths):
                self.next_track()
            elif after == "repeat_all":
                self._load_playback_index(paths, 0)
            else:
                self.stop_playback()

        def position_changed(self, position_ms):
            # Recover after a missed duration notification or transient zero.
            duration = self.media_player.duration()
            if duration > 0 and self.seek_slider.maximum() != duration:
                self.duration_changed(duration)
            # Losing mouse capture can omit the release event. Clear the drag
            # without seeking back to a stale handle value on the next tick.
            if (self.seek_slider.isSliderDown()
                    and not QApplication.mouseButtons() & Qt.MouseButton.LeftButton):
                with QSignalBlocker(self.seek_slider):
                    self.seek_slider.setSliderDown(False)
                self.is_seeking = False
            # The slider's real interaction state is authoritative.  A rare missed
            # sliderReleased signal used to leave is_seeking=True forever, freezing
            # the seekbar until the user changed tracks.
            if not self.seek_slider.isSliderDown():
                self.seek_slider.setValue(
                    position_ms
                )

            self.current_time_label.setText(
                self._format_position_time(position_ms)
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
            self.current_time_label.setText(
                self._format_position_time(self.media_player.position())
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


        def toggle_time_display(self):
            self.show_remaining_time = not getattr(
                self, "show_remaining_time", False
            )
            if hasattr(self, "current_time_label"):
                remaining = self.show_remaining_time
                self.current_time_label.setToolTip(
                    "Click to show elapsed time" if remaining
                    else "Click to show remaining time"
                )
                self.current_time_label.setText(
                    self._format_position_time(self.media_player.position())
                )


        def _format_position_time(self, position_ms):
            if getattr(self, "show_remaining_time", False):
                remaining_ms = max(
                    0, self.media_player.duration() - int(position_ms)
                )
                return f"-{self.format_time(remaining_ms)}"
            return self.format_time(position_ms)
