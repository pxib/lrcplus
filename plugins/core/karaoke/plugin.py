"""
Simple Karaoke plugin.

Uses timed LRCX segments:
    [00:10.000]first <00:10.500>second <00:11.000>third

Segments become highlighted when playback reaches their timestamps.
"""

from bisect import bisect_left
import math
import time

import numpy as np
import sounddevice as sd

from PySide6.QtCore import QTimer, Qt
from PySide6.QtGui import QColor, QPainter, QPainterPath, QPen, QPixmap
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtWidgets import (
    QComboBox,
    QCheckBox,
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
from plugins.core.karaoke.midi import MidiImportError, load_midi


from plugins.core.karaoke.screen import PitchLane, KaraokeScreen


KARAOKE_LYRIC_PAIR_LOOKAHEAD_RATIO = 0.40

class KaraokePlugin:
    name = "Karaoke"
    version = "0.1.0"
    description = "Full-screen lyrics synced to the main player, with an optional MIDI pitch guide."

    def on_load(self, context):
        self.context = context
        self.window = None
        self.connected = False
        self._position_anchor_monotonic = None
        self.karaoke_timer = None
        self._position_anchor_ms = 0.0
        self._position_anchor_monotonic = None
        self._playback_rate = 1.0
        self.screen = None
        self.screen_timer = None
        self.pitch_timer = None
        self.mic_pitch_detector = None
        self._mic_pitch_hz = 0.0
        self._mic_pitch_confidence = 0.0
        self._mic_pitch_last_detected = None
        self._mic_pitch_status = "Mic pitch: off"
        self._judgment_samples = {}
        self._judged_note_indices = set()
        self._judgment_score = 0
        self._judgment_combo = 0
        self._judgment_counts = {name: 0 for name in ("AMAZING", "PERFECT", "GOOD", "OK", "BAD", "???", "MISS")}
        self._judgment_last = "Ready"
        self._judgment_position = None
        self.current_midi = None
        self.selected_midi_track = None
        self.square_wave_enabled = True
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
        context.log(f"Loaded {self.name} {self.version}")

    def on_app_ready(self, window):
        self.window = window

        context_api = getattr(self.context, "api", None)
        if context_api is not None:
            context_api.add_main_window_button(
                "Karaoke Screen", self.toggle_karaoke_screen
            )

        if not self.connected:
            self.karaoke_timer = QTimer(self.window)
            self.karaoke_timer.setTimerType(
                Qt.TimerType.PreciseTimer
            )
            self._apply_sweep_fps()
            self._apply_sweep_style()
            self.karaoke_timer.timeout.connect(
                self.update_karaoke_position
            )

            window.media_player.playbackStateChanged.connect(
                self.on_playback_state_changed
            )
            window.media_player.positionChanged.connect(
                self.on_media_position_changed
            )
            if hasattr(window.media_player, "playbackRateChanged"):
                window.media_player.playbackRateChanged.connect(
                    self.on_playback_rate_changed
                )

            # Start immediately if the plugin is loaded while audio is already playing.
            self.on_playback_state_changed(
                window.media_player.playbackState()
            )
            self.connected = True
        if getattr(window, "current_audio_path", None):
            self.load_midi_for_track(str(window.current_audio_path))

    def toggle_karaoke_screen(self):
        if self.window is None:
            return
        if self.screen is not None and self.screen.isVisible():
            self.screen.close()
            return
        self.screen = KaraokeScreen(self.window)
        # Mic scoring now lives in the standalone Karaoke Game plugin. Keep
        # this screen focused on the song-synced lyrics and MIDI guide lane.
        self.screen.mic_button.hide()
        self.screen.mic_pitch_status.hide()
        self.screen.judgment_status.hide()
        self.screen.judgment_counter.hide()
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
        self.screen.square_wave_checkbox.toggled.connect(self._square_wave_toggled)
        self.screen.mic_button.clicked.connect(self.toggle_mic_pitch)
        self.square_wave_enabled = self.screen.square_wave_checkbox.isChecked()
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
        self._reset_judgment(start_position=self._estimated_position_seconds())

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
            self._reset_judgment(start_position=self._estimated_position_seconds())

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
                f"Square wave: running on {device_label}; waiting for MIDI track",
                force=True,
            )
        except Exception as error:
            self._stop_square_wave_stream()
            self._set_square_wave_status(f"Square wave failed: {error}", force=True)
            QMessageBox.warning(self.screen, "Square wave unavailable", str(error))

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
        all_notes = tuple(
            (
                float(note["start"]),
                float(note["end"]),
                max(0, min(127, int(note["pitch"]) + self._transpose_semitones())),
                max(0.0, min(1.0, float(note.get("velocity", 90)) / 127.0)),
            )
            for note in (self.selected_midi_track or {}).get("notes", [])
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
            f"Square wave: {stream_state}; {active_count}/{len(all_notes)} notes at {position:.2f}s; peak ~{peak:.3f}; {details}"
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
        self._stop_mic_pitch()
        self._stop_square_wave_stream()
        if self.screen_timer is not None:
            self.screen_timer.stop()
        if self.pitch_timer is not None:
            self.pitch_timer.stop()
        self.screen = None
        self.screen_timer = None
        self.pitch_timer = None

    def refresh_pitch_position(self):
        if self.screen is None:
            return
        position_seconds = self._estimated_position_seconds()
        position_ms = position_seconds * 1000.0
        self.screen.pitch_lane.set_position(position_ms)
        detected_at = self._mic_pitch_last_detected
        age = time.monotonic() - detected_at if detected_at is not None else 1.0
        alpha = max(0.0, min(1.0, (0.8 - age) / 0.55))
        midi_pitch = None
        if (self._mic_pitch_hz > 0 and self._mic_pitch_confidence >= 0.30
                and self.mic_pitch_detector is not None and age <= 0.12):
            midi_pitch = 69 + 12 * math.log2(self._mic_pitch_hz / 440.0)
        self.screen.pitch_lane.set_live_pitch(midi_pitch, alpha, position_seconds)
        self.screen.current_lyric.set_karaoke_position(position_seconds)
        self.screen.next_lyric.set_karaoke_position(position_seconds)
        self._refresh_mic_pitch_label(position_seconds)
        self._update_judgments(position_seconds)

    def toggle_mic_pitch(self):
        if self.screen is None:
            return
        if self.mic_pitch_detector is not None:
            self._stop_mic_pitch()
            return

        detector = PitchDetector(self.screen)
        detector.pitch_detected.connect(self._on_mic_pitch_detected)
        detector.status_changed.connect(self._on_mic_pitch_status)
        detector.finished.connect(self._on_mic_pitch_finished)
        self.mic_pitch_detector = detector
        self._mic_pitch_hz = 0.0
        self._mic_pitch_confidence = 0.0
        self._mic_pitch_status = "Listening..."
        self._reset_judgment(start_position=self._estimated_position_seconds())
        self.screen.mic_button.setText("Stop Mic")
        detector.start()

    def _on_mic_pitch_detected(self, frequency, confidence):
        self._mic_pitch_hz = float(frequency)
        self._mic_pitch_confidence = float(confidence)
        if self._mic_pitch_hz > 0 and self._mic_pitch_confidence >= 0.30:
            self._mic_pitch_last_detected = time.monotonic()

    def _on_mic_pitch_status(self, message):
        self._mic_pitch_status = str(message)
        if self.screen is not None and "unavailable" in self._mic_pitch_status.casefold():
            self.screen.mic_pitch_status.setText(self._mic_pitch_status)

    def _on_mic_pitch_finished(self):
        detector = self.mic_pitch_detector
        if detector is not None and detector.isFinished():
            self.mic_pitch_detector = None
            if self.screen is not None:
                self.screen.mic_button.setText("Start Mic")

    def _stop_mic_pitch(self):
        detector = self.mic_pitch_detector
        if detector is None:
            return
        detector.stop()
        detector.wait(1500)
        self.mic_pitch_detector = None
        self._mic_pitch_hz = 0.0
        self._mic_pitch_last_detected = None
        self._mic_pitch_status = "Mic pitch: off"
        if self.screen is not None:
            self.screen.mic_button.setText("Start Mic")
            self.screen.mic_pitch_status.setText(self._mic_pitch_status)

    def _reset_judgment(self, start_position=0.0):
        position = max(0.0, float(start_position))
        notes = (self.selected_midi_track or {}).get("notes", [])
        offset = self.screen.offset_spin.value() if self.screen is not None else 0.0
        self._judgment_samples = {}
        self._judged_note_indices = {
            index for index, note in enumerate(notes)
            if float(note["end"]) + offset < position - 0.08
        }
        self._judgment_score = 0
        self._judgment_combo = 0
        self._judgment_counts = {name: 0 for name in ("AMAZING", "PERFECT", "GOOD", "OK", "BAD", "???", "MISS")}
        self._judgment_last = "Ready"
        self._judgment_position = position
        if self.screen is not None:
            self.screen.judgment_status.setText("Score 0  |  Combo 0  |  Ready")
            self.screen.judgment_counter.setText(self._format_judgment_counter())

    def _format_judgment_counter(self):
        return "  ".join(
            f"{name} {self._judgment_counts[name]}"
            for name in ("AMAZING", "PERFECT", "GOOD", "OK", "BAD", "???", "MISS")
        )

    def _update_judgments(self, position_seconds):
        if (self.screen is None or self.mic_pitch_detector is None
                or self.selected_midi_track is None):
            return
        if self.window.media_player.playbackState() != QMediaPlayer.PlaybackState.PlayingState:
            return

        position = float(position_seconds)
        # Seeking starts a fresh scoring pass at the new location. Notes skipped
        # by a seek are excluded instead of being recorded as missed.
        if (self._judgment_position is not None
                and abs(position - self._judgment_position) > 1.0):
            self._reset_judgment(position)
            return
        self._judgment_position = position

        notes = self.selected_midi_track.get("notes", [])
        offset = self.screen.offset_spin.value()
        frequency = self._mic_pitch_hz
        if frequency > 0 and self._mic_pitch_confidence >= 0.30:
            for index, note in enumerate(notes):
                if index in self._judged_note_indices:
                    continue
                start = float(note["start"]) + offset
                end = float(note["end"]) + offset
                if start <= position <= end:
                    target_pitch = max(0, min(127, int(note["pitch"]) + self._transpose_semitones()))
                    target_hz = 440.0 * (2.0 ** ((target_pitch - 69) / 12.0))
                    cents = 1200 * math.log2(frequency / target_hz)
                    self._judgment_samples.setdefault(index, []).append(abs(cents))

        for index, note in enumerate(notes):
            if index in self._judged_note_indices:
                continue
            end = float(note["end"]) + offset
            if position <= end + 0.08:
                continue

            cents_values = self._judgment_samples.get(index, [])
            # Allow a little more microphone jitter and brief pitch slips while
            # still requiring enough samples to judge the sung note reliably.
            required_samples = max(1, math.ceil((end - (float(note["start"]) + offset)) * 3))
            if len(cents_values) < required_samples:
                rating, points = "MISS", 0
            else:
                average_cents = sum(cents_values) / len(cents_values)
                # Classify by the nearest semitone: each label covers a
                # half-semitone window around its stated distance.
                if average_cents < 50:
                    rating, points = "AMAZING", 1000
                elif average_cents < 150:
                    rating, points = "PERFECT", 800
                elif average_cents < 250:
                    rating, points = "GOOD", 600
                elif average_cents < 350:
                    rating, points = "OK", 400
                elif average_cents < 600:
                    rating, points = "BAD", 200
                else:
                    rating, points = "???", 0

            self._judged_note_indices.add(index)
            self._judgment_counts[rating] += 1
            self._judgment_score += points
            self._judgment_combo = self._judgment_combo + 1 if points else 0
            self._judgment_last = f"{rating}  +{points}"
            self.screen.judgment_status.setText(
                f"{self._judgment_last}  |  Score {self._judgment_score}"
                f"  |  Combo {self._judgment_combo}"
            )
            self.screen.judgment_counter.setText(self._format_judgment_counter())

    def _refresh_mic_pitch_label(self, position_seconds):
        screen = self.screen
        if screen is None:
            return
        if self.mic_pitch_detector is None:
            if "unavailable" not in self._mic_pitch_status.casefold():
                screen.mic_pitch_status.setText("Mic pitch: off")
            return
        if "unavailable" in self._mic_pitch_status.casefold():
            screen.mic_pitch_status.setText(self._mic_pitch_status)
            return
        frequency = self._mic_pitch_hz
        if frequency <= 0:
            screen.mic_pitch_status.setText("Mic pitch: listening...")
            return

        midi_pitch = round(69 + 12 * math.log2(frequency / 440.0))
        note_names = ("C", "C#", "D", "D#", "E", "F", "F#", "G", "G#", "A", "A#", "B")
        singer_note = f"{note_names[midi_pitch % 12]}{midi_pitch // 12 - 1}"
        target = None
        offset = screen.offset_spin.value()
        for note in (self.selected_midi_track or {}).get("notes", []):
            if float(note["start"]) + offset <= position_seconds <= float(note["end"]) + offset:
                target = max(0, min(127, int(note["pitch"]) + self._transpose_semitones()))
                break
        if target is None:
            comparison = "no target note"
        else:
            target_hz = 440.0 * (2.0 ** ((target - 69) / 12.0))
            cents = round(1200 * math.log2(frequency / target_hz))
            comparison = "in tune" if abs(cents) <= 50 else f"{cents:+d} cents"
        screen.mic_pitch_status.setText(
            f"You: {singer_note}  |  {comparison}  |  {self._mic_pitch_confidence:.0%}"
        )

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

    def _apply_sweep_fps(self):
        if self.karaoke_timer is None:
            return
        fps = get_karaoke_settings().get("sweep_fps", 60)
        self.karaoke_timer.setInterval(
            max(1, round(1000 / max(1, int(fps))))
        )

    def _apply_sweep_style(self):
        if self.window is None:
            return
        style = get_karaoke_settings().get("sweep_style", "classic")
        for widget in (
            getattr(self.window, "lyric_widget", None),
            getattr(self.window, "scrolling_lyrics_widget", None),
        ):
            if widget is not None and hasattr(widget, "set_karaoke_sweep_style"):
                widget.set_karaoke_sweep_style(style)

    def _has_timed_segments(self):
        if self.window is None:
            return False

        path = getattr(self.window, "current_lyrics_path", None)
        if path is None or path.suffix.lower() != ".lrcx":
            return False

        lines = getattr(self.window, "lyric_lines", [])
        # Karaoke state is explicit. The editor may rebuild/split/merge segments,
        # so inferring this from segment timestamps makes playback depend on mutable
        # editor state and can disable highlighting entirely.
        return any(
            getattr(line, "has_karaoke", False)
            for line in lines
        )

    def on_media_position_changed(self, position_ms):
        """Re-anchor the smooth karaoke clock to Qt's media position."""
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

    def _estimated_position_seconds(self):
        if self._position_anchor_monotonic is None:
            # First frame: use the media player's current reported position.
            try:
                return (
                    float(self.window.media_player.position()) / 1000.0
                    if self.window is not None
                    else 0.0
                )
            except (TypeError, ValueError):
                return 0.0

        elapsed = max(
            0.0,
            time.monotonic() - self._position_anchor_monotonic,
        )
        return max(
            0.0,
            (
                self._position_anchor_ms
                + elapsed * 1000.0 * self._playback_rate
            ) / 1000.0,
        )

    def on_playback_state_changed(self, state):
        if self.karaoke_timer is None:
            return

        if state == QMediaPlayer.PlaybackState.PlayingState:
            self._apply_sweep_fps()
            self._apply_sweep_style()
            try:
                self._position_anchor_ms = float(
                    self.window.media_player.position()
                )
            except (TypeError, ValueError):
                self._position_anchor_ms = 0.0
            self._position_anchor_monotonic = time.monotonic()
            self.karaoke_timer.start()
            self.update_karaoke_position()
        else:
            # Pause must freeze the current karaoke frame.  Clearing the
            # position makes the lyric row fall back to its ordinary
            # "current line" highlight state, which fills the entire line.
            #
            # Keep the last authoritative playback position on the widgets;
            # simply stop the animation timer.  The Playing branch re-anchors
            # from QMediaPlayer.position() before continuing, so resume cannot
            # drift from the frozen frame.
            self.karaoke_timer.stop()
            self._position_anchor_monotonic = None

    def update_karaoke_position(self):
        if self.window is None or self.karaoke_timer is None:
            return

        # Karaoke animation has its own frame clock. QMediaPlayer.positionChanged
        # is a coarse state-change signal (~10 Hz on this backend) and is not a
        # suitable animation clock.
        if not self._has_timed_segments():
            self._clear_karaoke_position()
            return

        self._apply_sweep_style()
        position = self._estimated_position_seconds()
        self.window.lyric_widget.set_karaoke_position(position)
        self.window.scrolling_lyrics_widget.set_karaoke_position(position)

    def _clear_karaoke_position(self):
        if self.window is None:
            return
        self.window.lyric_widget.set_karaoke_position(None)
        self.window.scrolling_lyrics_widget.set_karaoke_position(None)

    def on_unload(self, context):
        self._stop_square_wave_stream()
        if self.screen is not None:
            self.screen.close()
            self.screen = None
        self.screen_timer = None
        if self.pitch_timer is not None:
            self.pitch_timer.stop()
            self.pitch_timer = None
        if self.karaoke_timer is not None:
            self.karaoke_timer.stop()
            self.karaoke_timer.deleteLater()
            self.karaoke_timer = None

        self._clear_karaoke_position()
        self.connected = False

        context.log(f"Unloaded {self.name}")


PLUGIN_CLASS = KaraokePlugin
