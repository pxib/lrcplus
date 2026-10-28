"""Fullscreen karaoke screen with its MIDI pitch guide."""

from bisect import bisect_left
import math
import time

import numpy as np
import sounddevice as sd

from PySide6.QtCore import QObject, QTimer, Qt, Slot
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QDialog,
    QDoubleSpinBox,
    QFileDialog,
    QGraphicsDropShadowEffect,
    QHBoxLayout,
    QLabel,
    QMessageBox,
    QPushButton,
    QProgressBar,
    QVBoxLayout,
    QWidget,
)

from core.settings import get_karaoke_settings
from core.pitch_detector import PitchDetector
from lyrics.widgets import FuriganaWidget
from ui.artwork_background import ArtworkBackgroundWidget
from plugins.core.karaoke_screen.midi import MidiImportError, load_midi


from plugins.core.karaoke_screen.screen import PitchLane, KaraokeScreen


KARAOKE_LYRIC_PAIR_LOOKAHEAD_RATIO = 0.40

class KaraokeScreenPlugin(QObject):
    name = "Karaoke Screen"
    version = "0.1.0"
    description = "Full-screen lyrics synced to the main player, with an optional MIDI pitch guide."

    def on_load(self, context):
        self.context = context
        self.window = None
        self.connected = False
        self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = None
        self._playback_rate = 1.0
        self.screen = None
        self.screen_timer = None
        self.pitch_timer = None
        self._pitch_detector = None
        self.current_midi = None
        self.selected_midi_track = None
        self.square_wave_enabled = bool(
            context.api.get_setting("midi_player_enabled", True)
        )
        self.play_other_instruments = bool(
            context.api.get_setting("play_other_instruments", False)
        )
        self._synth_sample_rate = 0
        self._synth_phase = 0.0
        self._synth_stream = None
        self._synth_timer = None
        self._synth_status_last_update = 0.0
        self._synth_audio_state = None
        self._synth_callback_status = ""
        self._synth_callback_error = ""
        self._synth_cursor_seconds = None
        self._synth_callback_was_playing = False
        self._screen_line_index = None
        self._screen_next_text = None
        self._screen_next_line_index = None
        self.midi_assignments = context.api.get_setting("midi_assignments", {}) or {}
        self.midi_offsets = context.api.get_setting("midi_offsets", {}) or {}
        if not isinstance(self.midi_assignments, dict):
            self.midi_assignments = {}
        if not isinstance(self.midi_offsets, dict):
            self.midi_offsets = {}
        context.on("app_ready", self.on_app_ready)
        context.on("track_changed", self.on_track_changed)
        context.api.register_settings_widget(
            "Karaoke Game",
            self._create_settings_widget,
            apply=self._apply_settings_widget,
            description="Choose whether the selected MIDI melody plays alongside the song.",
        )
        context.log(f"Loaded {self.name} {self.version}")

    def _create_settings_widget(self, parent):
        from PySide6.QtWidgets import QCheckBox, QHBoxLayout, QVBoxLayout, QWidget

        panel = QWidget(parent)
        layout = QVBoxLayout(panel)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(6)
        midi_player = QCheckBox("MIDI Player", panel)
        midi_player.setChecked(self.square_wave_enabled)
        midi_player.setToolTip("Mix the selected MIDI melody into song playback")
        other_instruments = QCheckBox("Play Other Instruments", panel)
        other_instruments.setChecked(self.play_other_instruments)
        other_instruments.setToolTip(
            "Also play notes from the other MIDI tracks in the imported file"
        )
        other_instruments.setEnabled(self.square_wave_enabled)
        midi_player.toggled.connect(other_instruments.setEnabled)
        other_row = QHBoxLayout()
        other_row.setContentsMargins(20, 0, 0, 0)
        other_row.addWidget(other_instruments)
        other_row.addStretch(1)
        panel._midi_player_checkbox = midi_player
        panel._other_instruments_checkbox = other_instruments
        layout.addWidget(midi_player)
        layout.addLayout(other_row)
        return panel

    def _apply_settings_widget(self, panel):
        enabled = panel._midi_player_checkbox.isChecked()
        play_other = panel._other_instruments_checkbox.isChecked()
        self.context.api.set_setting("midi_player_enabled", enabled)
        self.context.api.set_setting("play_other_instruments", play_other)
        self.play_other_instruments = play_other
        self._square_wave_toggled(enabled)

    def on_app_ready(self, window):
        self.window = window
        if not self.connected:
            player = window.media_player
            player.positionChanged.connect(self.on_media_position_changed)
            player.playbackStateChanged.connect(self.on_playback_state_changed)
            if hasattr(player, "playbackRateChanged"):
                player.playbackRateChanged.connect(self.on_playback_rate_changed)
            self.connected = True
            self.on_playback_state_changed(player.playbackState())

        context_api = getattr(self.context, "api", None)
        if context_api is not None:
            context_api.add_main_window_button(
                "Karaoke Screen", self.toggle_karaoke_screen
            )
        if getattr(window, "current_audio_path", None):
            self.load_midi_for_track(str(window.current_audio_path))

    def toggle_karaoke_screen(self):
        if self.window is None:
            return
        if self.screen is not None and self.screen.isVisible():
            self.screen.close()
            return
        self.screen = KaraokeScreen(self.window)
        self._apply_sweep_style()
        self.screen.judgment_status.hide()
        self.screen.judgment_counter.hide()
        self.screen.mic_button.clicked.connect(self._toggle_microphone)
        background = getattr(self.window, "lyrics_background", None)
        if background is not None:
            self.screen.set_background_pixmap(
                getattr(background, "_source_pixmap", None)
            )
        self._screen_line_index = None
        self._screen_next_text = None
        self._screen_next_line_index = None
        self.screen.finished.connect(self._screen_closed)
        self.screen.midi_button.clicked.connect(self.choose_midi_file)
        self.screen.track_combo.currentIndexChanged.connect(
            self.on_midi_track_selected
        )
        self.screen.offset_spin.valueChanged.connect(self.on_alignment_offset_changed)
        self.screen.transpose_spin.valueChanged.connect(self.on_transpose_changed)
        self.screen_timer = QTimer(self.screen)
        self.screen_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.screen_timer.setInterval(16)
        self.screen_timer.timeout.connect(self.refresh_karaoke_screen)
        self.pitch_timer = QTimer(self.screen)
        self.pitch_timer.setTimerType(Qt.TimerType.PreciseTimer)
        self.pitch_timer.setInterval(16)
        self.pitch_timer.timeout.connect(self.refresh_pitch_position)
        self._start_square_wave_stream()
        self.screen.showFullScreen()
        if self.current_midi is not None:
            self._populate_midi_tracks()
        audio_path = getattr(self.window, "current_audio_path", None)
        offset = float(self.midi_offsets.get(str(audio_path), 0.0)) if audio_path else 0.0
        self.screen.offset_spin.setValue(offset)
        self.screen.pitch_lane.set_offset(offset)
        self.screen_timer.start()
        self.pitch_timer.start()
        self.refresh_karaoke_screen()

    def on_track_changed(self, path=None, **_kwargs):
        if path:
            self._screen_line_index = None
            self._screen_next_text = None
            self._screen_next_line_index = None
            self.load_midi_for_track(path)
            if self.screen is not None:
                background = getattr(self.window, "lyrics_background", None)
                if background is not None:
                    self.screen.set_background_pixmap(
                        getattr(background, "_source_pixmap", None)
                    )

    def load_midi_for_track(self, audio_path):
        """Use an assigned MIDI file, or a same-name sidecar when present."""
        from pathlib import Path

        audio_path = str(audio_path)
        midi_path = self.midi_assignments.get(audio_path)
        if not midi_path:
            audio = Path(audio_path)
            for suffix in (".mid", ".midi"):
                candidate = audio.with_suffix(suffix)
                if candidate.is_file():
                    midi_path = str(candidate)
                    break
        if midi_path:
            self._load_midi_path(midi_path, show_errors=False)
        else:
            self.current_midi = None
            self._populate_midi_tracks()
        self._apply_saved_alignment_offset(audio_path)

    def _apply_saved_alignment_offset(self, audio_path):
        if self.screen is None:
            return
        try:
            offset = float(self.midi_offsets.get(str(audio_path), 0.0))
        except (TypeError, ValueError):
            offset = 0.0
        self.screen.offset_spin.blockSignals(True)
        self.screen.offset_spin.setValue(offset)
        self.screen.offset_spin.blockSignals(False)
        self.screen.pitch_lane.set_offset(offset)

    def choose_midi_file(self):
        from pathlib import Path

        audio_path = getattr(self.window, "current_audio_path", None)
        directory = str(Path(audio_path).parent) if audio_path else ""
        path, _ = QFileDialog.getOpenFileName(
            self.screen,
            "Import Karaoke MIDI",
            directory,
            "MIDI Files (*.mid *.midi);;All Files (*)",
        )
        if not path:
            return
        self._load_midi_path(path, show_errors=True)
        if self.current_midi is not None and audio_path:
            self.midi_assignments[str(audio_path)] = path
            self.context.api.set_setting("midi_assignments", self.midi_assignments)

    def _load_midi_path(self, path, *, show_errors):
        try:
            self.current_midi = load_midi(path)
        except MidiImportError as error:
            self.current_midi = None
            if show_errors:
                QMessageBox.warning(self.screen or self.window, "MIDI Import", str(error))
        self._populate_midi_tracks()

    def _populate_midi_tracks(self):
        screen = self.screen
        if screen is None:
            return
        combo = screen.track_combo
        combo.blockSignals(True)
        combo.clear()
        midi = self.current_midi
        if midi is not None:
            for index, track in enumerate(midi["tracks"]):
                if track["notes"]:
                    combo.addItem(track["name"], index)
        combo.setEnabled(combo.count() > 0)
        screen.midi_button.setText(
            "Change MIDI…" if midi is not None else "Import MIDI…"
        )
        combo.blockSignals(False)
        if combo.count():
            self.on_midi_track_selected(combo.currentIndex())
        else:
            screen.pitch_lane.set_notes([])

    def on_midi_track_selected(self, combo_index):
        if self.screen is None or self.current_midi is None or combo_index < 0:
            return
        track_index = self.screen.track_combo.itemData(combo_index)
        track = self.current_midi["tracks"][track_index]
        self.selected_midi_track = track
        self.screen.pitch_lane.set_notes(self._transposed_notes(track["notes"]))

    def _transpose_semitones(self):
        return int(self.screen.transpose_spin.value()) if self.screen is not None else 0

    def _transposed_notes(self, notes):
        semitones = self._transpose_semitones()
        return [dict(note, pitch=max(0, min(127, int(note["pitch"]) + semitones)))
                for note in notes]

    def on_transpose_changed(self, _semitones):
        if self.screen is not None and self.selected_midi_track is not None:
            self.screen.pitch_lane.set_notes(
                self._transposed_notes(self.selected_midi_track["notes"])
            )

    def on_alignment_offset_changed(self, offset_seconds):
        if self.screen is not None:
            self.screen.pitch_lane.set_offset(offset_seconds)
        audio_path = getattr(self.window, "current_audio_path", None)
        if audio_path:
            self.midi_offsets[str(audio_path)] = float(offset_seconds)
            self.context.api.set_setting("midi_offsets", self.midi_offsets)

    def _square_wave_toggled(self, enabled):
        self.square_wave_enabled = bool(enabled)
        if not enabled:
            self._stop_square_wave_stream()
        elif self.screen is not None:
            self._start_square_wave_stream()

    def _start_square_wave_stream(self):
        self._stop_square_wave_stream()
        if not self.square_wave_enabled or self.screen is None:
            return
        try:
            target_name = self.window.audio_output.device().description().casefold()
            output_device = None
            for index, device in enumerate(sd.query_devices()):
                if (device.get("max_output_channels", 0) >= 2
                        and target_name in str(device.get("name", "")).casefold()):
                    output_device = index
                    break
            self._synth_audio_state = None
            self._synth_callback_status = ""
            self._synth_callback_error = ""
            self._synth_cursor_seconds = None
            self._synth_callback_was_playing = False
            self._synth_stream = sd.OutputStream(
                device=output_device,
                samplerate=48000,
                channels=2,
                dtype="float32",
                callback=self._square_wave_audio_callback,
                blocksize=0,
            )
            self._synth_stream.start()
            self._synth_timer = QTimer(self.screen)
            self._synth_timer.setInterval(15)
            self._synth_timer.timeout.connect(self._update_square_wave_state)
            self._synth_timer.start()
            device_label = "system default" if output_device is None else str(sd.query_devices(output_device)["name"])
            self._set_square_wave_status(
                f"MIDI Player: running on {device_label}; waiting for MIDI track",
                force=True,
            )
        except Exception as error:
            self._stop_square_wave_stream()
            self._set_square_wave_status(f"MIDI Player failed: {error}", force=True)
            QMessageBox.warning(self.screen, "MIDI Player unavailable", str(error))

    def _stop_square_wave_stream(self):
        if self._synth_timer is not None:
            self._synth_timer.stop()
            self._synth_timer.deleteLater()
            self._synth_timer = None
        self._synth_audio_state = None
        stream = self._synth_stream
        self._synth_stream = None
        if stream is not None:
            try:
                stream.stop()
                stream.close()
            except Exception:
                pass
        self._synth_cursor_seconds = None
        self._synth_callback_was_playing = False

    def _update_square_wave_state(self):
        if self._synth_stream is None or self.screen is None:
            return
        is_playing = (
            self.window is not None
            and self.selected_midi_track is not None
            and self.square_wave_enabled
            and self.window.media_player.playbackState()
            == QMediaPlayer.PlaybackState.PlayingState
        )
        position = self._estimated_position_seconds() if is_playing else 0.0
        playback_tracks = [self.selected_midi_track]
        if self.play_other_instruments and self.current_midi is not None:
            playback_tracks.extend(
                track for track in self.current_midi["tracks"]
                if track is not self.selected_midi_track
            )
        playback_notes = (
            note
            for track in playback_tracks
            for note in (track or {}).get("notes", [])
        )
        all_notes = tuple(
            (
                float(note["start"]),
                float(note["end"]),
                max(0, min(127, int(note["pitch"]) + self._transpose_semitones())),
                max(0.0, min(1.0, float(note.get("velocity", 90)) / 127.0)),
            )
            for note in playback_notes
        )
        offset = self.screen.offset_spin.value()
        notes = tuple(
            note for note in all_notes
            if note[1] + offset > position - 0.1
            and note[0] + offset < position + 0.5
        )
        rate = self._playback_rate
        anchor = time.monotonic()
        # Atomic pointer replacement: the PortAudio callback reads only this
        # immutable snapshot and never reaches into Qt or widgets.
        self._synth_audio_state = (is_playing, position, anchor, rate, offset, notes, len(all_notes))

        active_count = 0
        peak = 0.0
        if is_playing:
            for note_start, note_end, _pitch, velocity in notes:
                if note_start + offset <= position < note_end + offset:
                    active_count += 1
                    peak = max(peak, 0.18 * velocity)
        stream_state = "active" if self._synth_stream.active else "stopped"
        details = self._synth_callback_status or "no callback status"
        if self._synth_callback_error:
            details = self._synth_callback_error
        self._set_square_wave_status(
            f"MIDI Player: {stream_state}; {active_count}/{len(all_notes)} notes at {position:.2f}s; peak ~{peak:.3f}; {details}"
        )

    def _square_wave_audio_callback(self, outdata, frames, time_info, status):
        outdata.fill(0.0)
        if status:
            self._synth_callback_status = str(status)
        state = self._synth_audio_state
        if state is None:
            return
        playing, position, anchor, rate, offset, notes, _total_note_count = state
        if not playing or not notes:
            self._synth_cursor_seconds = None
            self._synth_callback_was_playing = False
            return
        try:
            sample_rate = float(self._synth_stream.samplerate)
            requested_start = position + max(0.0, time.monotonic() - anchor) * rate
            if (self._synth_cursor_seconds is None
                    or not self._synth_callback_was_playing
                    or abs(requested_start - self._synth_cursor_seconds) > 0.2):
                # Initial playback, resume, or a deliberate seek.
                start = requested_start
            else:
                # Ignore small Qt clock re-anchors so they cannot jump the
                # oscillator phase and create an audible click.
                start = self._synth_cursor_seconds
            self._synth_callback_was_playing = True
            times = start + np.arange(frames, dtype=np.float64) * (rate / sample_rate)
            mono = np.zeros(frames, dtype=np.float32)
            fade_seconds = 0.005
            for note_start_raw, note_end_raw, pitch, velocity in notes:
                note_start = note_start_raw + offset
                note_end = note_end_raw + offset
                active = (times >= note_start) & (times < note_end)
                if not np.any(active):
                    continue
                frequency = 440.0 * (2.0 ** ((pitch - 69) / 12.0))
                phase = 2.0 * np.pi * frequency * (times[active] - note_start)
                harmonics = [
                    harmonic for harmonic in (1, 3, 5)
                    if harmonic * frequency < sample_rate * 0.5
                ]
                if not harmonics:
                    continue
                weights = [1.0 / harmonic for harmonic in harmonics]
                weight_sum = sum(weights)
                tone = sum(
                    np.sin(phase * harmonic) * weight
                    for harmonic, weight in zip(harmonics, weights)
                ) / weight_sum
                elapsed = times[active] - note_start
                remaining = note_end - times[active]
                envelope = np.clip(
                    np.minimum(elapsed / fade_seconds, remaining / fade_seconds),
                    0.0,
                    1.0,
                )
                mono[active] += tone.astype(np.float32) * envelope.astype(np.float32) * (0.18 * velocity)
            outdata[:] = np.clip(mono[:, None], -1.0, 1.0)
            self._synth_cursor_seconds = start + frames * rate / sample_rate
        except Exception as error:
            self._synth_callback_error = f"callback error: {error}"
            outdata.fill(0.0)

    def _set_square_wave_status(self, message, *, force=False):
        now = time.monotonic()
        if self.screen is not None and (force or now - self._synth_status_last_update >= 0.25):
            self.screen.square_wave_status.setText(str(message))
            self._synth_status_last_update = now

    def _screen_closed(self, *_args):
        self._stop_microphone()
        self._stop_square_wave_stream()
        if self.screen_timer is not None:
            self.screen_timer.stop()
        if self.pitch_timer is not None:
            self.pitch_timer.stop()
        self.screen = None
        self.screen_timer = None
        self.pitch_timer = None

    def _toggle_microphone(self, checked=False):
        if self._pitch_detector is not None:
            self._stop_microphone()
            return
        if self.screen is None:
            return
        detector = PitchDetector(self.window)
        self._pitch_detector = detector
        detector.pitch_detected.connect(self._on_microphone_pitch)
        detector.status_changed.connect(self._on_microphone_status)
        detector.finished.connect(self._microphone_finished)
        detector.finished.connect(detector.deleteLater)
        self.screen.mic_button.setChecked(True)
        self.screen.mic_button.setText("Stop mic")
        self.screen.mic_pitch_status.setText("Starting mic...")
        self.screen.mic_pitch_status.setToolTip("")
        detector.start()

    def _reset_microphone_ui(self):
        if self.screen is not None:
            self.screen.mic_button.setChecked(False)
            self.screen.mic_button.setText("Start mic")
            self.screen.pitch_lane.set_live_pitch(None, 0.0)

    def _stop_microphone(self):
        detector = self._pitch_detector
        self._pitch_detector = None
        if detector is not None:
            detector.stop()
            # The audio loop checks stop after each small read. Keep the
            # worker alive under the main window if a backend is slow to exit.
            detector.wait(1000)
        self._reset_microphone_ui()
        if self.screen is not None:
            self.screen.mic_pitch_status.setText("Mic off")
            self.screen.mic_pitch_status.setToolTip("")

    @Slot(str)
    def _on_microphone_status(self, message):
        if self.screen is None or self.sender() is not self._pitch_detector:
            return
        self.screen.mic_pitch_status.setToolTip(message)
        self.screen.mic_pitch_status.setText(
            "Listening..." if message == "Listening..." else "Mic unavailable"
        )

    @Slot()
    def _microphone_finished(self):
        if self.sender() is not self._pitch_detector:
            return
        self._pitch_detector = None
        self._reset_microphone_ui()
        if self.screen is not None and self.screen.mic_pitch_status.text() != "Mic unavailable":
            self.screen.mic_pitch_status.setText("Mic off")

    @Slot(float, float)
    def _on_microphone_pitch(self, frequency, confidence):
        if self.screen is None or self.sender() is not self._pitch_detector:
            return
        lane = self.screen.pitch_lane
        if (not math.isfinite(frequency) or not math.isfinite(confidence)
                or frequency <= 0 or confidence < PitchDetector.MIN_CONFIDENCE):
            self.screen.mic_pitch_status.setText("Listening...")
            lane.set_live_pitch(None, 0.0)
            return
        pitch = 69.0 + 12.0 * math.log2(frequency / 440.0)
        note = round(pitch)
        cents = round((pitch - note) * 100)
        names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        self.screen.mic_pitch_status.setText(f"{names[note % 12]}{note // 12 - 1}  {cents:+d} ct")
        if self.window.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            lane.set_live_pitch(pitch, confidence, self._estimated_position_seconds())
        else:
            lane.set_live_pitch(None, 0.0)

    def on_media_position_changed(self, position_ms):
        try:
            self._position_anchor_ms = float(position_ms)
        except (TypeError, ValueError):
            return
        self._position_anchor_monotonic = time.monotonic()

    def on_playback_rate_changed(self, rate):
        try:
            self._playback_rate = float(rate)
        except (TypeError, ValueError):
            self._playback_rate = 1.0

    def on_playback_state_changed(self, state):
        if self.window is None:
            return
        try:
            self._position_anchor_ms = float(self.window.media_player.position())
        except (TypeError, ValueError):
            self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = (
            time.monotonic()
            if state == QMediaPlayer.PlaybackState.PlayingState
            else None
        )

    def _estimated_position_seconds(self):
        if self._position_anchor_monotonic is None:
            try:
                return float(self.window.media_player.position()) / 1000.0
            except (AttributeError, TypeError, ValueError):
                return 0.0
        elapsed = max(0.0, time.monotonic() - self._position_anchor_monotonic)
        return max(
            0.0,
            (self._position_anchor_ms + elapsed * 1000.0 * self._playback_rate)
            / 1000.0,
        )

    def refresh_pitch_position(self):
        if self.screen is None:
            return
        position_seconds = self._estimated_position_seconds()
        position_ms = position_seconds * 1000.0
        self.screen.pitch_lane.set_position(position_ms)
        self.screen.current_lyric.set_karaoke_position(position_seconds)
        self.screen.next_lyric.set_karaoke_position(position_seconds)

    def refresh_karaoke_screen(self):
        screen = self.screen
        if screen is None or not screen.isVisible():
            return

        lines = self.context.api.get_current_lyrics()
        index = getattr(self.window, "current_line", -1)
        visible_indices = [
            line_index
            for line_index, line in enumerate(lines)
            if str(line.get("text", "")).strip()
        ]
        page_indices = []
        preview_index = None
        if visible_indices and index >= 0:
            if index in visible_indices:
                current_slot = visible_indices.index(index)
            else:
                current_slot = next(
                    (slot for slot, line_index in enumerate(visible_indices)
                     if line_index > index),
                    len(visible_indices) - 1,
                )
            page_slot = (current_slot // 2) * 2
            position_ms = int(self.context.api.get_position())
            if page_slot > 0:
                previous_bottom = visible_indices[page_slot - 1]
                this_pair_start = visible_indices[page_slot]
                previous_bottom_start_ms = int(
                    lines[previous_bottom].get("start_ms", 0) or 0
                )
                this_pair_start_ms = int(
                    lines[this_pair_start].get("start_ms", 0) or 0
                )
                previous_duration_ms = max(
                    0, this_pair_start_ms - previous_bottom_start_ms
                )
                previous_lookahead_ms = int(
                    previous_duration_ms * KARAOKE_LYRIC_PAIR_LOOKAHEAD_RATIO
                )
                if position_ms < this_pair_start_ms - previous_lookahead_ms:
                    page_slot -= 2

            next_pair_slot = page_slot + 2
            if next_pair_slot < len(visible_indices):
                bottom_index = visible_indices[
                    min(page_slot + 1, len(visible_indices) - 1)
                ]
                next_pair_index = visible_indices[next_pair_slot]
                bottom_start_ms = int(
                    lines[bottom_index].get("start_ms", 0) or 0
                )
                next_pair_start_ms = int(
                    lines[next_pair_index].get("start_ms", 0) or 0
                )
                bottom_duration_ms = max(
                    0, next_pair_start_ms - bottom_start_ms
                )
                lookahead_ms = int(
                    bottom_duration_ms * KARAOKE_LYRIC_PAIR_LOOKAHEAD_RATIO
                )
                if (
                    position_ms >= next_pair_start_ms - lookahead_ms
                    and position_ms < next_pair_start_ms
                ):
                    # Preview the next lyric in the top row while keeping the
                    # bottom row visible through the end of its timestamp.
                    preview_index = next_pair_index

            page_indices = visible_indices[page_slot:page_slot + 2]
            index = page_indices[0]
            if preview_index is not None:
                index = preview_index

        upcoming = ""
        upcoming_index = None
        if len(page_indices) > 1:
            upcoming_index = page_indices[1]
            upcoming = str(lines[upcoming_index].get("text", ""))
        if 0 <= index < len(lines):
            current = str(lines[index].get("text", ""))
            if self._screen_line_index != index:
                lyric_lines = getattr(self.window, "lyric_lines", [])
                if index < len(lyric_lines):
                    lyric_line = lyric_lines[index]
                    segments = getattr(lyric_line, "segments", None) or []
                    if segments and getattr(lyric_line, "has_karaoke", False):
                        line_end = getattr(lyric_line, "end", None)
                        if line_end is None and index + 1 < len(lyric_lines):
                            line_end = lyric_lines[index + 1].start
                        screen.current_lyric.set_segments(
                            current,
                            segments,
                            line_end,
                        )
                    else:
                        screen.current_lyric.set_lyric(current, generate=True)
                else:
                    screen.current_lyric.set_lyric(current, generate=True)
                self._screen_line_index = index
        else:
            current = "♪"
            if self._screen_line_index != index:
                screen.current_lyric.set_lyric(current, generate=True)
                self._screen_line_index = index

        if (upcoming != self._screen_next_text
                or upcoming_index != self._screen_next_line_index):
            lyric_lines = getattr(self.window, "lyric_lines", [])
            if upcoming and upcoming_index is not None and upcoming_index < len(lyric_lines):
                lyric_line = lyric_lines[upcoming_index]
                segments = getattr(lyric_line, "segments", None) or []
                if segments and getattr(lyric_line, "has_karaoke", False):
                    line_end = getattr(lyric_line, "end", None)
                    if line_end is None and upcoming_index + 1 < len(lyric_lines):
                        line_end = lyric_lines[upcoming_index + 1].start
                    screen.next_lyric.set_segments(upcoming, segments, line_end)
                else:
                    screen.next_lyric.set_lyric(upcoming, generate=True)
            else:
                screen.next_lyric.set_lyric(upcoming, generate=True)
            self._screen_next_text = upcoming
            self._screen_next_line_index = upcoming_index
        screen.next_lyric.setVisible(bool(upcoming))

        path = self.context.api.get_current_track()
        screen.track_label.setText(
            (path.rsplit("/", 1)[-1].rsplit("\\", 1)[-1].rsplit(".", 1)[0]
             if path else "KARAOKE")
        )
        duration = self.context.api.get_duration()
        position = self.context.api.get_position()
        screen.progress.setRange(0, max(1, duration))
        screen.progress.setValue(min(position, max(1, duration)))

    def _apply_sweep_style(self):
        if self.screen is None:
            return
        style = get_karaoke_settings().get("sweep_style", "classic")
        for widget in (
            getattr(self.screen, "current_lyric", None),
            getattr(self.screen, "next_lyric", None),
        ):
            if widget is not None and hasattr(widget, "set_karaoke_sweep_style"):
                widget.set_karaoke_sweep_style(style)

    def on_unload(self, context):
        self._stop_microphone()
        self._stop_square_wave_stream()
        if self.screen is not None:
            self.screen.close()
            self.screen = None
        self.screen_timer = None
        if self.pitch_timer is not None:
            self.pitch_timer.stop()
            self.pitch_timer = None
        self.connected = False

        context.log(f"Unloaded {self.name}")


PLUGIN_CLASS = KaraokeScreenPlugin
