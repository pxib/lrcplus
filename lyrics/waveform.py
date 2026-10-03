from pathlib import Path
import math
import shutil
import subprocess
from array import array
import sys

from PySide6.QtCore import Qt, Signal, QThread, QRectF
from PySide6.QtGui import QPainter, QPen, QColor, QBrush, QPixmap
from PySide6.QtWidgets import QWidget


def find_ffmpeg():
    return shutil.which("ffmpeg")


class _WaveformWorker(QThread):
    loaded = Signal(list, int)
    failed = Signal(str)

    def __init__(self, path, samples=1400, parent=None):
        super().__init__(parent)
        self.path = str(path)
        self.samples = max(200, int(samples))

    def run(self):
        try:
            # Decode through ffmpeg so MP3/FLAC/M4A/etc. work uniformly.
            # A mono 8 kHz stream is plenty for a visual overview and keeps
            # memory/CPU use low even for long songs.
            ffmpeg = find_ffmpeg()
            if ffmpeg is None:
                raise FileNotFoundError("ffmpeg was not found.")

            cmd = [
                ffmpeg, "-v", "error",
                "-i", self.path,
                "-ac", "1",
                "-ar", "2000",
                "-f", "s16le",
                "-",
            ]
            proc = subprocess.run(
                cmd,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                timeout=120,
                check=False,
            )
            if proc.returncode != 0 or not proc.stdout:
                raise RuntimeError(proc.stderr.decode("utf-8", "replace").strip() or "ffmpeg could not decode the audio.")

            # Decode directly into native signed-short values.  This avoids
            # millions of tiny bytes slices/int.from_bytes calls on long tracks.
            samples = array("h")
            samples.frombytes(proc.stdout)
            if sys.byteorder != "little":
                samples.byteswap()
            total = len(samples)
            if total <= 0:
                raise RuntimeError("No audio samples were decoded.")

            # Collapse the stream into a fixed number of RMS buckets. The
            # 2 kHz decode rate is far above the visual resolution of ~1400
            # bars while reducing decoding and Python-side work by 75%.
            bucket_count = min(self.samples, total)
            peaks = []
            scale = 32768.0 * 32768.0
            for i in range(bucket_count):
                start = (i * total) // bucket_count
                end = max(start + 1, ((i + 1) * total) // bucket_count)
                bucket = samples[start:end]
                count = len(bucket)
                acc = sum(sample * sample for sample in bucket)
                peaks.append(min(1.0, math.sqrt(acc / (count * scale))))

            self.loaded.emit(peaks, max(1, round(total / 2000 * 1000)))
        except FileNotFoundError:
            self.failed.emit("ffmpeg was not found.")
        except Exception as exc:
            self.failed.emit(str(exc))


class WaveformWidget(QWidget):
    """Interactive audio waveform with playback position and lyric markers."""

    seekRequested = Signal(int)

    segmentPlayRequested = Signal(int, int)
    karaokeTimingRequested = Signal(str, int)
    karaokeTimingFinished = Signal()
    lyricTimingRequested = Signal(str, int)

    def __init__(self, parent=None):
        super().__init__(parent)
        self.setMinimumHeight(74)
        self.setMaximumHeight(110)
        self.setMouseTracking(True)
        self._peaks = []
        self._duration_ms = 0
        self._position_ms = 0
        self._lyric_times = []
        self._worker = None
        self._hover_ms = None
        self._loading = False
        self._karaoke_start_ms = None
        self._karaoke_end_ms = None
        self._next_lyric_boundary_ms = None
        self._karaoke_segment_index = -1
        self._karaoke_view_line_index = None
        self._selected_lyric_start_ms = None
        self._selected_lyric_end_ms = None
        self._lyric_preview_start_ms = None
        self._lyric_preview_end_ms = None
        self._drag_handle = None
        self._karaoke_drag_origin_ms = None
        self._karaoke_drag_origin_x = None
        self._karaoke_linked_boundary = None
        self._lyric_drag_handle = None
        self._lyric_hard_boundary_ms = None
        self._lyric_zoom = 1.0
        self._lyric_pan_ms = 0.0
        self._lyric_pan_dragging = False
        self._lyric_pan_last_x = None
        self._error = None
        # Karaoke segment editor state. Unlike line-level A/B, these represent
        # the actual internal timing units shown in the Karaoke tab.
        self._karaoke_segments = []
        # Independent lyric-line start: yellow Karaoke barrier.
        self._karaoke_line_start_ms = None
        self._segment_zoom = 1.0
        self._segment_view_start_ms = 0
        self._segment_view_end_ms = None
        self._segment_pan_ms = 0.0
        self._pan_dragging = False
        self._pan_drag_last_x = None
        self._karaoke_snap_target_ms = None
        self._karaoke_auto_focused = False
        # Cached waveform layer. The expensive per-column peak aggregation is
        # rebuilt only when the visible audio viewport/size changes, not for
        # every playhead tick or mouse move.
        self._waveform_cache_key = None
        self._waveform_cache_gray = QPixmap()
        # Keep one cached waveform layer. The played portion is painted from
        # cached bar geometry instead of storing a second full-size pixmap.
        self._waveform_bars = []

    def load_audio(self, path):
        self._peaks = []
        self._duration_ms = 0
        self._position_ms = 0
        self._error = None
        self._loading = False
        self._stop_worker()

        if find_ffmpeg() is None:
            self._error = "ffmpeg was not found."
            self.update()
            return

        self._worker = _WaveformWorker(path, parent=self)
        self._loading = True
        self._worker.loaded.connect(self._on_loaded)
        self._worker.failed.connect(self._on_failed)
        self._worker.finished.connect(self._worker_finished)
        self._worker.start()
        self.update()

    def set_duration(self, duration_ms):
        self._duration_ms = max(0, int(duration_ms))
        self.update()

    def set_position(self, position_ms):
        position_ms = max(0, int(position_ms))
        if position_ms == self._position_ms:
            return
        self._position_ms = position_ms
        self.update()

    def set_karaoke_segments(self, segments, selected_index=-1, line_start_ms=None, line_end_ms=None, line_identity=None):
        normalized = []
        for i, item in enumerate(segments or []):
            try:
                start = int(round(float(item[0]) * 1000))
                end = int(round(float(item[1]) * 1000))
                label = str(item[2]) if len(item) > 2 and item[2] is not None else ""
            except (TypeError, ValueError, IndexError):
                continue

            # A genuinely empty chunk is not a karaoke segment. Do NOT strip
            # whitespace here: " " and "　" are valid timed lyric content.
            if label == "":
                continue

            normalized.append((start, max(start + 1, end), i, label))

        view_identity = (
            int(line_identity)
            if line_identity is not None
            else (int(line_start_ms) if line_start_ms is not None else None)
        )
        boundary = None if line_end_ms is None else int(line_end_ms)
        line_start = None if line_start_ms is None else int(line_start_ms)
        state = (
            tuple(normalized), int(selected_index), view_identity, boundary, line_start
        )
        if state == getattr(self, "_karaoke_segments_state", None):
            return
        self._karaoke_segments_state = state
        self._karaoke_segments = normalized
        self._karaoke_segment_index = int(selected_index)
        self._karaoke_line_start_ms = line_start

        # Preserve zoom/pan while editing the same lyric line. Re-center only
        # when the editor switches to another line.
        line_changed = (getattr(self, "_karaoke_view_line_index", None) != view_identity)
        if line_start_ms is not None and line_end_ms is not None and line_changed:
            self._segment_view_start_ms = max(0, int(line_start_ms))
            self._segment_view_end_ms = max(self._segment_view_start_ms + 1, int(line_end_ms))
            self._segment_pan_ms = 0.0
        self._karaoke_view_line_index = view_identity
        if hasattr(self, "_next_lyric_boundary_ms"):
            self._next_lyric_boundary_ms = boundary
        self.update()

    def clear_karaoke_segments(self):
        self._karaoke_segments = []
        self._karaoke_segment_index = -1
        self._karaoke_view_line_index = None
        self._karaoke_line_start_ms = None
        self._next_lyric_boundary_ms = None
        self.update()

    def _segment_viewport(self):
        # The karaoke line is the initial viewport size, not a hard wall.
        # Pan may travel all the way to the beginning/end of the audio.
        base_start = self._segment_view_start_ms
        base_end = self._segment_view_end_ms or self._duration_ms
        base_span = max(1.0, base_end - base_start)
        span = max(100.0, base_span / self._segment_zoom)
        span = min(float(self._duration_ms), span)

        min_pan = -float(base_start)
        max_pan = max(min_pan, float(self._duration_ms) - float(base_start) - span)
        pan = max(min_pan, min(max_pan, self._segment_pan_ms))
        return base_start + pan, base_start + pan + span

    def wheelEvent(self, event):
        if not self._karaoke_segments or self._duration_ms <= 0:
            return super().wheelEvent(event)

        delta = event.angleDelta().y()
        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            factor = 1.25 if delta > 0 else 1 / 1.25
            old_zoom = self._segment_zoom
            self._segment_zoom = max(1.0, min(24.0, self._segment_zoom * factor))

            # Keep the point under the cursor approximately stationary.
            rect_width = max(1.0, self.width() - 16.0)
            ratio = max(0.0, min(1.0, (event.position().x() - 8.0) / rect_width))
            old_start, old_end = self._segment_viewport()
            anchor = old_start + ratio * (old_end - old_start)
            new_start, new_end = self._segment_viewport()
            self._segment_pan_ms += anchor - (new_start + ratio * (new_end - new_start))
        else:
            # Normal wheel = horizontal timeline pan.
            base_start = self._segment_view_start_ms
            base_end = self._segment_view_end_ms or self._duration_ms
            base_span = max(1.0, base_end - base_start)
            visible_span = base_span / self._segment_zoom
            step = max(25.0, visible_span * 0.12)
            self._segment_pan_ms += (-step if delta > 0 else step)
        # Keep the stored pan within the actual audio bounds.
        base_start = float(self._segment_view_start_ms)
        base_end = float(self._segment_view_end_ms or self._duration_ms)
        base_span = max(1.0, base_end - base_start)
        visible_span = min(float(self._duration_ms), max(100.0, base_span / self._segment_zoom))
        min_pan = -base_start
        max_pan = max(min_pan, float(self._duration_ms) - base_start - visible_span)
        self._segment_pan_ms = max(min_pan, min(max_pan, self._segment_pan_ms))

        self.update()
        event.accept()


    def focus_karaoke_segments(self):
        """Center and zoom the waveform around the karaoke section."""
        if not self._karaoke_segments or self._duration_ms <= 0:
            return
        first = min(v[0] for v in self._karaoke_segments)
        last = max(v[1] for v in self._karaoke_segments)
        span = max(1.0, float(last-first))
        pad = max(150.0, span * 0.12)
        target_start = max(0.0, float(first)-pad)
        target_end = min(float(self._duration_ms), float(last)+pad)
        target_span = max(100.0, target_end-target_start)

        base_start = float(self._segment_view_start_ms)
        base_end = float(self._segment_view_end_ms or self._duration_ms)
        base_span = max(1.0, base_end-base_start)
        self._segment_zoom = max(1.0, base_span/target_span)
        self._segment_pan_ms = target_start-base_start

        visible = min(float(self._duration_ms), max(100.0, base_span/self._segment_zoom))
        min_pan = -base_start
        max_pan = max(min_pan, float(self._duration_ms)-base_start-visible)
        self._segment_pan_ms = max(min_pan, min(max_pan, self._segment_pan_ms))
        self.update()

    def set_karaoke_timing(self, start_ms=None, end_ms=None, segment_index=-1):
        self._karaoke_start_ms = None if start_ms is None else int(start_ms)
        self._karaoke_end_ms = None if end_ms is None else int(end_ms)
        self._karaoke_segment_index = int(segment_index)
        if self._karaoke_segments and not getattr(self, "_karaoke_auto_focused", False):
            self._karaoke_auto_focused = True
            self.focus_karaoke_segments()
            return
        self.update()

    def set_lyric_hard_boundary(self, timestamp_ms):
        self._lyric_hard_boundary_ms = (
            None
            if timestamp_ms is None
            else max(0, min(int(timestamp_ms), int(self._duration_ms or timestamp_ms)))
        )

        # Never leave the stored B preview beyond the hard limit.
        if (
            self._lyric_hard_boundary_ms is not None
            and self._lyric_preview_end_ms is not None
        ):
            self._lyric_preview_end_ms = min(
                self._lyric_preview_end_ms,
                self._lyric_hard_boundary_ms,
            )
        self.update()

    def set_selected_lyric_timing(self, start_ms=None, end_ms=None):
        """Set the two visual LRC boundary markers.

        The preview positions are kept separately from the authoritative
        model values so dragging one handle cannot make the other marker jump.
        """
        self._selected_lyric_start_ms = None if start_ms is None else int(start_ms)
        self._selected_lyric_end_ms = None if end_ms is None else int(end_ms)

        if (
            self._lyric_hard_boundary_ms is not None
            and self._selected_lyric_end_ms is not None
        ):
            self._selected_lyric_end_ms = min(
                self._selected_lyric_end_ms,
                self._lyric_hard_boundary_ms,
            )

        # Defensive normalization for externally edited timestamps: keep the
        # visual A/B pair ordered even if an upstream widget briefly supplies
        # an inverted pair.
        if (self._selected_lyric_start_ms is not None
                and self._selected_lyric_end_ms is not None
                and self._selected_lyric_end_ms < self._selected_lyric_start_ms):
            self._selected_lyric_end_ms = self._selected_lyric_start_ms + 1

        if (
            self._lyric_hard_boundary_ms is not None
            and self._selected_lyric_start_ms is not None
        ):
            self._selected_lyric_start_ms = min(
                self._selected_lyric_start_ms,
                max(0, self._lyric_hard_boundary_ms - 1),
            )
        if (
            self._lyric_hard_boundary_ms is not None
            and self._selected_lyric_end_ms is not None
        ):
            self._selected_lyric_end_ms = min(
                self._selected_lyric_end_ms,
                self._lyric_hard_boundary_ms,
            )
            if (
                self._selected_lyric_start_ms is not None
                and self._selected_lyric_end_ms <= self._selected_lyric_start_ms
            ):
                self._selected_lyric_start_ms = max(
                    0,
                    self._selected_lyric_end_ms - 1,
                )

        if self._lyric_drag_handle is None:
            self._lyric_preview_start_ms = self._selected_lyric_start_ms
            self._lyric_preview_end_ms = self._selected_lyric_end_ms
        self.update()

    def clear_selected_lyric_timing(self):
        self._selected_lyric_start_ms = None
        self._selected_lyric_end_ms = None
        self._lyric_preview_start_ms = None
        self._lyric_preview_end_ms = None
        self._lyric_drag_handle = None
        self._lyric_hard_boundary_ms = None
        self.update()

    def set_lyric_times(self, times):
        values = []
        for value in times or []:
            try:
                ms = int(float(value) * 1000)
            except (TypeError, ValueError):
                continue
            if self._duration_ms <= 0 or 0 <= ms <= self._duration_ms:
                values.append(ms)
        self._lyric_times = sorted(set(values))
        self.update()

    def _stop_worker(self):
        # Capture the worker locally so a concurrent finished-signal handler
        # cannot replace self._worker with None between the checks and wait().
        worker = self._worker
        self._worker = None

        if worker is not None:
            try:
                if worker.isRunning():
                    worker.requestInterruption()
                    worker.terminate()
                    worker.wait(300)
            except RuntimeError:
                # Qt may already have destroyed the QThread wrapper.
                pass

    def _worker_finished(self):
        # Keep the QThread object alive until Qt delivers the signal handlers,
        # then release it safely.
        worker = self.sender()
        if worker is self._worker and worker is not None:
            worker.deleteLater()
            self._worker = None

    def _on_loaded(self, peaks, duration_ms):
        self._peaks = peaks
        if duration_ms > 0:
            self._duration_ms = duration_ms
        self._loading = False
        self._error = None
        self.update()

    def _on_failed(self, message):
        self._loading = False
        self._error = message
        self.update()

    def _lyrics_viewport(self):
        """Visible time range for the ordinary Lyrics waveform."""
        if self._duration_ms <= 0:
            return 0.0, 0.0
        span = max(250.0, float(self._duration_ms) / max(1.0, self._lyric_zoom))
        span = min(float(self._duration_ms), span)
        max_pan = max(0.0, float(self._duration_ms) - span)
        self._lyric_pan_ms = max(0.0, min(max_pan, self._lyric_pan_ms))
        return self._lyric_pan_ms, self._lyric_pan_ms + span

    def _focus_lyrics_interval(self):
        if self._selected_lyric_start_ms is None or self._duration_ms <= 0:
            return
        a = float(self._selected_lyric_start_ms)
        b = float(
            self._lyric_hard_boundary_ms
            if self._lyric_hard_boundary_ms is not None
            else (self._selected_lyric_end_ms or a + 5000)
        )
        target_span = max(250.0, b - a)
        target_span = min(float(self._duration_ms), target_span * 1.15)
        self._lyric_zoom = max(1.0, float(self._duration_ms) / target_span)
        center = (a + b) / 2.0
        self._lyric_pan_ms = max(
            0.0,
            min(
                float(self._duration_ms) - target_span,
                center - target_span / 2.0,
            ),
        )

    def _is_ms_visible(self, ms, margin_ms=0.0):
        """True only when a timestamp belongs to the currently visible viewport."""
        if self._karaoke_segments:
            start, end = self._segment_viewport()
        else:
            start, end = self._lyrics_viewport()
        return (start - margin_ms) <= float(ms) <= (end + margin_ms)

    def _x_for_ms(self, ms):
        if self._duration_ms <= 0:
            return 0.0
        left = 8.0
        right = max(left, self.width() - 8.0)
        if self._karaoke_segments:
            start, end = self._segment_viewport()
        else:
            start, end = self._lyrics_viewport()
        return left + (right-left) * max(0.0, min(1.0, (ms-start)/max(1.0,end-start)))

    def _ms_for_x(self, x):
        left = 8.0
        right = max(left, self.width() - 8.0)
        ratio = max(0.0, min(1.0, (x - left) / max(1.0, right - left)))
        if self._karaoke_segments:
            start, end = self._segment_viewport()
        else:
            start, end = self._lyrics_viewport()
        return round(start + ratio * (end - start))

    def mouseMoveEvent(self, event):
        if self._pan_dragging and self._duration_ms > 0:
            x = event.position().x()
            dx = x - self._pan_drag_last_x
            self._pan_drag_last_x = x
            if self._karaoke_segments:
                base_start = self._segment_view_start_ms
                base_end = self._segment_view_end_ms or self._duration_ms
                base_span = max(1.0, base_end - base_start)
                visible_span = base_span / self._segment_zoom
                ms_per_px = visible_span / max(1.0, self.width() - 16.0)
                self._segment_pan_ms -= dx * ms_per_px

                min_pan = -float(base_start)
                max_pan = max(
                    min_pan,
                    float(self._duration_ms) - float(base_start) - visible_span
                )
                self._segment_pan_ms = max(min_pan, min(max_pan, self._segment_pan_ms))
            else:
                view_start, view_end = self._lyrics_viewport()
                visible_span = max(1.0, view_end - view_start)
                ms_per_px = visible_span / max(1.0, self.width() - 16.0)
                self._lyric_pan_ms -= dx * ms_per_px
                max_pan = max(0.0, float(self._duration_ms) - visible_span)
                self._lyric_pan_ms = max(0.0, min(max_pan, self._lyric_pan_ms))
            self.update()
            event.accept()
            return
        if self._duration_ms > 0:
            x = event.position().x()
            self._hover_ms = self._ms_for_x(x)
            if self._drag_handle and self._karaoke_segments and 0 <= self._karaoke_segment_index:
                # Relative drag: moving right always increases the timestamp,
                # moving left always decreases it. This avoids the marker jumping
                # when press coordinates and drawing coordinates differ slightly.
                vs, ve = self._segment_viewport()
                ms_per_px = (ve - vs) / max(1.0, self.width() - 16.0)
                origin_x = self._karaoke_drag_origin_x
                origin_ms = self._karaoke_drag_origin_ms
                if origin_x is not None and origin_ms is not None:
                    value = round(origin_ms + (x - origin_x) * ms_per_px)
                else:
                    value = self._ms_for_x(x)
                selected_pos = next((i for i, v in enumerate(self._karaoke_segments)
                                     if v[2] == self._karaoke_segment_index), None)
                selected = next((v for v in self._karaoke_segments
                                 if v[2] == self._karaoke_segment_index), None)
                if selected is not None and selected_pos is not None:
                    minimum_gap_ms = 1
                    ss, ee, _, label = selected

                    # Independent boundaries: gaps are allowed.
                    if self._drag_handle == "start":
                        hard_start = (
                            self._karaoke_line_start_ms
                            if self._karaoke_line_start_ms is not None
                            else 0
                        )
                        value = max(
                            hard_start,
                            min(value, ee - minimum_gap_ms),
                        )
                    else:
                        hard_end = (
                            self._next_lyric_boundary_ms
                            if self._next_lyric_boundary_ms is not None
                            else self._duration_ms
                        )
                        # Never cross into the next lyric line.
                        value = max(ss + minimum_gap_ms, min(value, hard_end))

                    # Snap to nearby segment boundaries unless Shift is held.
                    self._karaoke_snap_target_ms = None
                    if not (event.modifiers() & Qt.KeyboardModifier.ShiftModifier):
                        vs, ve = self._segment_viewport()
                        radius = max(12.0, ((ve - vs) / max(1.0, self.width() - 16.0)) * 14.0)
                        hard_end = (
                            self._next_lyric_boundary_ms
                            if self._next_lyric_boundary_ms is not None
                            else self._duration_ms
                        )
                        # Karaoke boundaries are shared. Only snap to the
                        # adjacent segment boundary, never to an unrelated
                        # marker elsewhere in the line.
                        adjacent = None
                        if self._drag_handle == "start" and selected_pos > 0:
                            adjacent = self._karaoke_segments[selected_pos - 1][1]
                        elif self._drag_handle == "end" and selected_pos + 1 < len(self._karaoke_segments):
                            adjacent = self._karaoke_segments[selected_pos + 1][0]

                        if adjacent is not None and adjacent <= hard_end and abs(adjacent - value) <= radius:
                            value = adjacent
                            self._karaoke_snap_target_ms = adjacent

                    linked = getattr(self, "_karaoke_linked_boundary", None)

                    if linked == "start" and selected_pos > 0:
                        # Move the one shared boundary: previous.end == selected.start.
                        prev_start, _prev_end, prev_index, prev_label = self._karaoke_segments[selected_pos - 1]
                        value = max(prev_start + minimum_gap_ms, value)
                        value = min(value, ee - minimum_gap_ms)
                        ss = value
                        self._karaoke_segments[selected_pos - 1] = (
                            prev_start, value, prev_index, prev_label
                        )
                        emit_handle = "linked_start"
                    elif linked == "end" and selected_pos + 1 < len(self._karaoke_segments):
                        # Move the one shared boundary: selected.end == next.start.
                        _next_start, next_end, next_index, next_label = self._karaoke_segments[selected_pos + 1]
                        value = max(ss + minimum_gap_ms, value)
                        if next_end is not None:
                            value = min(value, next_end - minimum_gap_ms)
                        ee = value
                        self._karaoke_segments[selected_pos + 1] = (
                            value, next_end, next_index, next_label
                        )
                        emit_handle = "linked_end"
                    elif self._drag_handle == "start":
                        ss = value
                        emit_handle = "start"
                    else:
                        ee = value
                        emit_handle = "end"

                    self._karaoke_segments[selected_pos] = (
                        ss, ee, self._karaoke_segment_index, label
                    )
                    self.karaokeTimingRequested.emit(emit_handle, value)
                    self.update()
                    event.accept()
                    return
            if self._lyric_drag_handle:
                value = self._ms_for_x(x)

                # Hard A/B barrier: the selected interval must always remain
                # ordered. Dragging A can approach B, but never pass it;
                # dragging B can approach A, but never go behind it.
                # Keep a 1 ms gap so both timestamps remain distinct.
                minimum_gap_ms = 1
                if self._lyric_drag_handle == "start":
                    if self._lyric_preview_end_ms is not None:
                        value = min(
                            value,
                            self._lyric_preview_end_ms - minimum_gap_ms,
                        )

                    if self._lyric_hard_boundary_ms is not None:
                        value = min(
                            value,
                            max(0, self._lyric_hard_boundary_ms - minimum_gap_ms),
                        )

                    value = max(0, value)
                    self._lyric_preview_start_ms = value
                else:
                    if self._lyric_preview_start_ms is not None:
                        value = max(
                            value,
                            self._lyric_preview_start_ms + minimum_gap_ms,
                        )

                    # The special Lyrics timing-limit marker is the absolute
                    # ceiling. No drag path may move B beyond it.
                    if self._lyric_hard_boundary_ms is not None:
                        value = min(value, self._lyric_hard_boundary_ms)

                    if self._duration_ms > 0:
                        value = min(value, self._duration_ms)

                    self._lyric_preview_end_ms = value

                self.lyricTimingRequested.emit(self._lyric_drag_handle, value)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._drag_handle:
                value = self._ms_for_x(x)
                self.karaokeTimingRequested.emit(self._drag_handle, value)
                self.setCursor(Qt.CursorShape.SizeHorCursor)
            elif self._karaoke_segments and 0 <= self._karaoke_segment_index:
                selected = next((v for v in self._karaoke_segments
                                 if v[2] == self._karaoke_segment_index), None)
                if selected:
                    ss, ee, _, _ = selected
                    start_dist = abs(x - self._x_for_ms(ss))
                    end_dist = abs(x - self._x_for_ms(ee))
                    if (self._karaoke_segment_index != 0 and start_dist <= 16) or end_dist <= 16:
                        self.setCursor(Qt.CursorShape.SizeHorCursor)
                    else:
                        self.setCursor(Qt.CursorShape.ArrowCursor)
                else:
                    self.setCursor(Qt.CursorShape.ArrowCursor)
            else:
                self.setCursor(Qt.CursorShape.ArrowCursor)
            self.setToolTip(self._format_time(self._hover_ms))
        else:
            self._hover_ms = None
        self.update()
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._karaoke_snap_target_ms = None
        if self._pan_dragging and event.button() == Qt.MouseButton.MiddleButton:
            self._pan_dragging = False
            self._pan_drag_last_x = None
            self.unsetCursor()
            event.accept()
            return
        if event.button() in (Qt.MouseButton.LeftButton, Qt.MouseButton.RightButton):
            self._drag_handle = None
            self._lyric_drag_handle = None
            self._karaoke_drag_origin_ms = None
            self._karaoke_drag_origin_x = None
            self._karaoke_linked_boundary = None
            if event.button() == Qt.MouseButton.LeftButton:
                self._lyric_drag_handle = None
            self.setCursor(Qt.CursorShape.ArrowCursor)
            self.karaokeTimingFinished.emit()
            event.accept()
            return
        super().mouseReleaseEvent(event)

    def leaveEvent(self, event):
        self._hover_ms = None
        self.setToolTip("")
        self.update()
        super().leaveEvent(event)

    def wheelEvent(self, event):
        if self._duration_ms <= 0:
            event.ignore()
            return

        steps = event.angleDelta().y() / 120.0
        if steps == 0:
            event.ignore()
            return

        old_start, old_end = (
            self._segment_viewport()
            if self._karaoke_segments
            else self._lyrics_viewport()
        )
        old_span = max(1.0, old_end - old_start)

        if event.modifiers() & Qt.KeyboardModifier.ControlModifier:
            # Ctrl + wheel: zoom around the cursor.
            cursor_x = event.position().x()
            left = 8.0
            right = max(left, self.width() - 8.0)
            ratio = max(0.0, min(
                1.0,
                (cursor_x - left) / max(1.0, right - left),
            ))

            focus_ms = old_start + ratio * old_span
            zoom_factor = 1.20 ** steps
            new_span = max(1.0, old_span / zoom_factor)
            new_start = focus_ms - ratio * new_span

            if self._karaoke_segments:
                self._segment_zoom = max(
                    1.0,
                    min(100.0, self._segment_zoom * zoom_factor),
                )
                self._segment_pan_ms = (
                    new_start - float(self._segment_view_start_ms)
                )
            else:
                self._lyric_zoom = max(
                    1.0,
                    min(100.0, self._lyric_zoom * zoom_factor),
                )
                self._lyric_pan_ms = (
                    new_start
                    - float(getattr(self, "_lyric_view_start_ms", 0.0))
                )
        else:
            # Plain wheel: preserve normal horizontal panning.
            pan_delta = -steps * old_span * 0.12
            if self._karaoke_segments:
                self._segment_pan_ms += pan_delta
            else:
                self._lyric_pan_ms += pan_delta

        self.update()
        event.accept()

    def mousePressEvent(self, event):
        # Ctrl + RMB: play the clicked karaoke segment.
        # This must ALWAYS consume the event so RMB editing cannot move
        # the red/end marker underneath it.
        if (
            event.button() == Qt.MouseButton.RightButton
            and event.modifiers() & Qt.KeyboardModifier.ControlModifier
        ):
            if self._karaoke_segments:
                start_ms, end_ms = self._segment_viewport()
                span = max(1.0, end_ms - start_ms)
                left = 8.0
                right = max(left, self.width() - 8.0)
                x = max(left, min(right, event.position().x()))
                clicked_ms = start_ms + (
                    (x - left) / max(1.0, right - left)
                ) * span

                for segment in self._karaoke_segments:
                    # Karaoke segments in this widget are tuples:
                    # (start_ms, end_ms, original_index, label)
                    if isinstance(segment, (tuple, list)):
                        seg_start = float(segment[0])
                        seg_end = float(segment[1])
                    else:
                        seg_start = float(getattr(segment, "start", 0.0))
                        seg_end = getattr(segment, "end", seg_start)
                        seg_end = (
                            seg_start
                            if seg_end is None
                            else float(seg_end)
                        )

                    if seg_start <= clicked_ms <= seg_end:
                        self.segmentPlayRequested.emit(
                            int(round(seg_start)),
                            int(round(seg_end)),
                        )
                        break

            event.accept()
            return

        if event.button() == Qt.MouseButton.MiddleButton:
            self._pan_dragging = True
            self._pan_drag_last_x = event.position().x()
            self.setCursor(Qt.CursorShape.ClosedHandCursor)
            event.accept()
            return

        # Unified A/B editing:
        #   LMB -> left/start (blue)
        #   RMB -> right/end (red)
        # Clicking anywhere places that boundary immediately under the cursor,
        # then mouseMoveEvent continues to track it while the button is held.
        # Karaoke and ordinary Lyrics waveform editing now use the same behavior.
        has_karaoke = bool(
            self._karaoke_segments
            and 0 <= self._karaoke_segment_index
        )
        has_lyrics_interval = bool(
            not has_karaoke
            and self._lyric_preview_start_ms is not None
        )

        if ((has_karaoke or has_lyrics_interval)
                and event.button() in (
                    Qt.MouseButton.LeftButton,
                    Qt.MouseButton.RightButton,
                )
                and self._duration_ms > 0):

            x = event.position().x()

            if has_karaoke:
                selected_pos = next(
                    (
                        i for i, v in enumerate(self._karaoke_segments)
                        if v[2] == self._karaoke_segment_index
                    ),
                    None,
                )
                selected = (
                    self._karaoke_segments[selected_pos]
                    if selected_pos is not None else None
                )

                # Segment 0 has a fixed start, so LMB is not an active handle.
                if selected is not None:
                    ss, ee, _, label = selected
                    if (
                        event.button() == Qt.MouseButton.LeftButton
                        and False  # segment 0 start is independently editable
                    ):
                        pass
                    else:
                        self._drag_handle = (
                            "start"
                            if event.button() == Qt.MouseButton.LeftButton
                            else "end"
                        )

                        # Alt links only an already-connected adjacent boundary.
                        # Gaps and overlaps deliberately remain independent.
                        self._karaoke_linked_boundary = None
                        if event.modifiers() & Qt.KeyboardModifier.AltModifier:
                            connected_tolerance_ms = 1
                            if (
                                self._drag_handle == "start"
                                and selected_pos > 0
                            ):
                                previous = self._karaoke_segments[selected_pos - 1]
                                if abs(previous[1] - ss) <= connected_tolerance_ms:
                                    self._karaoke_linked_boundary = "start"
                            elif (
                                self._drag_handle == "end"
                                and selected_pos + 1 < len(self._karaoke_segments)
                            ):
                                following = self._karaoke_segments[selected_pos + 1]
                                if abs(ee - following[0]) <= connected_tolerance_ms:
                                    self._karaoke_linked_boundary = "end"

                        gap = 1
                        value = self._ms_for_x(x)

                        if self._drag_handle == "start":
                            hard_start = (
                                self._karaoke_line_start_ms
                                if self._karaoke_line_start_ms is not None
                                else 0
                            )
                            value = max(hard_start, min(value, ee - gap))
                            if self._karaoke_linked_boundary == "start":
                                previous = self._karaoke_segments[selected_pos - 1]
                                value = max(previous[0] + gap, value)
                                self._karaoke_segments[selected_pos - 1] = (
                                    previous[0], value, previous[2], previous[3]
                                )
                            ss = value
                        else:
                            hard_end = (
                                self._next_lyric_boundary_ms
                                if getattr(
                                    self, "_next_lyric_boundary_ms", None
                                ) is not None
                                else self._duration_ms
                            )
                            value = max(
                                ss + gap,
                                min(value, hard_end),
                            )
                            if self._karaoke_linked_boundary == "end":
                                following = self._karaoke_segments[selected_pos + 1]
                                if following[1] is not None:
                                    value = min(value, following[1] - gap)
                                self._karaoke_segments[selected_pos + 1] = (
                                    value, following[1], following[2], following[3]
                                )
                            ee = value

                        self._karaoke_snap_target_ms = None
                        self._karaoke_drag_origin_ms = value
                        self._karaoke_drag_origin_x = x

                        self._karaoke_segments[selected_pos] = (
                            ss,
                            ee,
                            self._karaoke_segment_index,
                            label,
                        )
                        emit_handle = (
                            "linked_start"
                            if self._karaoke_linked_boundary == "start"
                            else "linked_end"
                            if self._karaoke_linked_boundary == "end"
                            else self._drag_handle
                        )
                        self.karaokeTimingRequested.emit(
                            emit_handle,
                            value,
                        )
                        self.update()
                        event.accept()
                        return

            else:
                # Ordinary Lyrics waveform: the exact same A/B interaction.
                # B is provisional until Commit, but is still editable here.
                self._drag_handle = (
                    "start"
                    if event.button() == Qt.MouseButton.LeftButton
                    else "end"
                )
                value = self._ms_for_x(x)

                start_ms = self._lyric_preview_start_ms
                end_ms = self._lyric_preview_end_ms

                if self._drag_handle == "start":
                    # A must remain before B when B exists.
                    if end_ms is not None:
                        value = max(0, min(value, max(0, end_ms - 1)))
                    else:
                        value = max(0, value)
                else:
                    value = max(start_ms, value)

                # The editor may provide a hard lyric boundary. Never let the
                # red/B marker cross it, even during direct waveform dragging.
                hard_boundary = getattr(self, "_lyric_hard_boundary_ms", None)
                if hard_boundary is not None:
                    if self._drag_handle == "start":
                        value = min(value, max(start_ms, hard_boundary - 1))
                    else:
                        value = min(value, hard_boundary)

                self._lyric_drag_handle = self._drag_handle
                self._karaoke_drag_origin_ms = value
                self._karaoke_drag_origin_x = x
                self._waveform_lyric_preview_ms = value
                self.lyricTimingRequested.emit(
                    self._drag_handle,
                    value,
                )
                self.update()
                event.accept()
                return

        # Ordinary non-marker click in the Lyrics waveform remains seek.
        if event.button() == Qt.MouseButton.LeftButton and self._duration_ms > 0:
            self.seekRequested.emit(self._ms_for_x(event.position().x()))
            event.accept()
            return

        super().mousePressEvent(event)

    @staticmethod
    def _format_time(ms):
        total = max(0, int(ms / 1000))
        minutes, seconds = divmod(total, 60)
        hours, minutes = divmod(minutes, 60)
        return f"{hours}:{minutes:02d}:{seconds:02d}" if hours else f"{minutes}:{seconds:02d}"

    def _waveform_view(self):
        # The waveform renderer must use the same viewport as the coordinate
        # system. Otherwise Lyrics zoom only moves markers while the waveform
        # itself remains rendered at full-song scale.
        if self._karaoke_segments:
            return self._segment_viewport()
        return self._lyrics_viewport()

    def _ensure_waveform_cache(self, rect, view_start, view_end):
        """Build one waveform pixmap and reusable bar geometry per viewport."""
        key = (
            rect.width(), rect.height(), len(self._peaks),
            int(round(view_start)), int(round(view_end)),
            self._duration_ms,
        )
        if key == self._waveform_cache_key:
            return

        size = rect.size()
        gray = QPixmap(size)
        gray.fill(Qt.GlobalColor.transparent)
        count = len(self._peaks)
        if count <= 0 or self._duration_ms <= 0:
            self._waveform_cache_key = key
            self._waveform_cache_gray = gray
            self._waveform_bars = []
            return

        width = max(1, rect.width())
        height = max(1, rect.height())
        center_y = height / 2.0
        half_height = max(10.0, height * 0.40)
        duration = max(1.0, float(self._duration_ms))
        span = max(1.0, float(view_end - view_start))
        bars = []
        for px in range(width):
            t0 = view_start + span * (px / width)
            t1 = view_start + span * ((px + 1) / width)
            i0 = max(0, min(count - 1, int(math.floor((t0 / duration) * (count - 1)))))
            i1 = max(i0 + 1, min(count, int(math.ceil((t1 / duration) * (count - 1)))))
            peak = self._peaks[i0] if i1 <= i0 else max(self._peaks[i0:i1])
            bars.append(max(1.0, float(peak) * half_height))

        painter = QPainter(gray)
        painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
        painter.setPen(QPen(QColor(180, 180, 188, 180), 1))
        for px, bar in enumerate(bars):
            painter.drawLine(px, round(center_y - bar), px, round(center_y + bar))
        painter.end()
        self._waveform_cache_key = key
        self._waveform_cache_gray = gray
        self._waveform_bars = bars

    def _is_x_visible(self, x, margin=0):
        """Return True only when an X coordinate is inside the viewport."""
        return -margin <= x <= self.width() + margin

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)

            rect = self.rect().adjusted(8, 7, -8, -7)
            center_y = rect.center().y()
            half_height = max(10.0, rect.height() * 0.40)

            # Background track.
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QBrush(QColor(60, 60, 65, 120)))
            painter.drawRoundedRect(rect, 6, 6)

            if self._loading:
                painter.setPen(QPen(QColor(190, 190, 195), 1))
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, "Loading waveform…")
                return

            if self._error or not self._peaks:
                painter.setPen(QPen(QColor(170, 170, 175), 1))
                text = "Waveform unavailable" if self._error else "No waveform"
                painter.drawText(rect, Qt.AlignmentFlag.AlignCenter, text)
                return

            width = max(1, rect.width())
            count = len(self._peaks)

            # Selected regular-LRC line interval. This is an inferred visual
            # boundary only: the line itself stores only its start timestamp.
            if self._lyric_preview_start_ms is not None:
                start_ms = self._lyric_preview_start_ms
                end_ms = self._lyric_preview_end_ms
                if end_ms is None:
                    end_ms = self._selected_lyric_start_ms

                # Intersect the highlighted interval with the visible time
                # range before converting to X. _x_for_ms clamps off-screen
                # values, which previously produced phantom lines at x = 0.
                view_start, view_end = self._lyrics_viewport()
                clipped_start = max(float(start_ms), float(view_start))
                clipped_end = min(float(end_ms), float(view_end))

                if clipped_start <= clipped_end:
                    left_x = self._x_for_ms(clipped_start)
                    right_x = self._x_for_ms(clipped_end)
                    left = min(left_x, right_x)
                    right = max(left_x, right_x)
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(80, 160, 255, 38)))
                    painter.drawRoundedRect(
                        int(left), rect.top(),
                        max(2, int(right - left)),
                        rect.height(), 4, 4,
                    )

                # Draw actual endpoint lines only when their timestamps are
                # genuinely inside the current viewport.
                if self._is_ms_visible(start_ms):
                    start_x = self._x_for_ms(start_ms)
                    painter.setPen(QPen(QColor(80, 160, 255, 245), 2))
                    painter.drawLine(
                        round(start_x), rect.top(),
                        round(start_x), rect.bottom(),
                    )

                if (
                    end_ms is not None
                    and end_ms > self._selected_lyric_start_ms
                    and self._is_ms_visible(end_ms)
                ):
                    end_x = self._x_for_ms(end_ms)
                    painter.setPen(QPen(QColor(255, 195, 90, 225), 2))
                    painter.drawLine(
                        round(end_x), rect.top(),
                        round(end_x), rect.bottom(),
                    )

            # Draw the cached waveform. Zooming/panning changes the cache key,
            # while playback only clips the already-rendered blue layer.
            view_start, view_end = self._waveform_view()
            self._ensure_waveform_cache(rect, view_start, view_end)
            painter.drawPixmap(rect.topLeft(), self._waveform_cache_gray)

            if self._duration_ms > 0 and self._waveform_bars:
                # Paint the played region from cached geometry. This trades a
                # tiny amount of draw work for eliminating a second full-size
                # QPixmap, which lowers steady-state waveform memory.
                play_ratio = (
                    (self._position_ms - view_start)
                    / max(1.0, view_end - view_start)
                )
                play_ratio = max(0.0, min(1.0, play_ratio))
                played_width = int(rect.width() * play_ratio)
                if played_width > 0:
                    painter.save()
                    painter.setRenderHint(QPainter.RenderHint.Antialiasing, False)
                    painter.setPen(QPen(QColor(95, 170, 255, 235), 1))
                    local_center_y = rect.top() + rect.height() / 2.0
                    for px, bar in enumerate(self._waveform_bars[:played_width]):
                        x = rect.left() + px
                        painter.drawLine(x, round(local_center_y - bar), x, round(local_center_y + bar))
                    painter.restore()

            # Next lyric timestamp: visible reference + hard boundary.
            if self._karaoke_segments and self._next_lyric_boundary_ms is not None:
                boundary_x = self._x_for_ms(self._next_lyric_boundary_ms)
                if rect.left() - 4 <= boundary_x <= rect.right() + 4:
                    painter.setPen(QPen(QColor(255, 215, 90, 255), 3))
                    painter.drawLine(
                        round(boundary_x), rect.top() + 1,
                        round(boundary_x), rect.bottom() - 1
                    )
                    painter.setPen(QPen(QColor(255, 240, 180, 255), 1))
                    painter.drawText(
                        round(boundary_x + 5), rect.top() + 14,
                        "Next lyric"
                    )

            # Karaoke lyric-start barrier. Yellow = lyric timestamp;
            # blue = actual first karaoke segment start.
            if (
                self._karaoke_segments
                and self._karaoke_line_start_ms is not None
                and self._is_ms_visible(self._karaoke_line_start_ms)
            ):
                line_start_x = self._x_for_ms(self._karaoke_line_start_ms)
                painter.setPen(QPen(QColor(255, 215, 90, 255), 3))
                painter.drawLine(
                    round(line_start_x), rect.top() + 1,
                    round(line_start_x), rect.bottom() - 1,
                )
                painter.setPen(QPen(QColor(255, 240, 180, 255), 1))
                painter.drawText(
                    round(line_start_x + 5), rect.top() + 14,
                    "Lyric start",
                )

            # Karaoke segment-level editor. Each segment occupies its real timing
            # interval; the selected segment is visibly highlighted.
            if self._karaoke_segments:
                for start_ms, end_ms, index, label in self._karaoke_segments:
                    sx, ex = self._x_for_ms(start_ms), self._x_for_ms(end_ms)
                    left, right = min(sx, ex), max(sx, ex)
                    selected = index == self._karaoke_segment_index
                    painter.setPen(Qt.PenStyle.NoPen)
                    painter.setBrush(QBrush(QColor(95, 170, 255, 95 if selected else 35)))
                    painter.drawRoundedRect(int(left), rect.top()+4, max(2, int(right-left)), rect.height()-8, 3, 3)
                    # Labels stay inside their own segment and disappear when the
                    # segment is too narrow to avoid visual overlap.
                    if label and right - left >= 24:
                        label_rect = QRectF(left + 3, rect.top() + 5, max(1, right-left-6), rect.height()-10)
                        painter.setPen(QPen(QColor(255, 255, 255, 245 if selected else 190), 1))
                        painter.drawText(label_rect, Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextSingleLine, label)
                    if selected:
                        # Blue = left/start/LMB; red = right/end/RMB.
                        # Segment 0 is independent from the lyric timestamp.
                        painter.setPen(QPen(QColor(70, 150, 255, 255), 3))
                        painter.drawLine(round(sx), rect.top()+2, round(sx), rect.bottom()-2)
                        painter.setBrush(QBrush(QColor(70, 150, 255, 255)))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawEllipse(QRectF(sx-5, rect.center().y()-5, 10, 10))

                        painter.setPen(QPen(QColor(255, 85, 85, 255), 3))
                        painter.drawLine(round(ex), rect.top()+2, round(ex), rect.bottom()-2)
                        painter.setBrush(QBrush(QColor(255, 85, 85, 255)))
                        painter.setPen(Qt.PenStyle.NoPen)
                        painter.drawEllipse(QRectF(ex-5, rect.center().y()-5, 10, 10))

                        if self._karaoke_snap_target_ms is not None:
                            snap_x = self._x_for_ms(self._karaoke_snap_target_ms)
                            painter.setPen(QPen(QColor(80,220,120,230), 2, Qt.PenStyle.DashLine))
                            painter.drawLine(round(snap_x), rect.top()+1, round(snap_x), rect.bottom()-1)
                    else:
                        painter.setPen(QPen(QColor(210,210,215,140), 1))
                        painter.drawLine(round(sx), rect.top()+2, round(sx), rect.bottom()-2)
                        painter.drawLine(round(ex), rect.top()+2, round(ex), rect.bottom()-2)

            # Lyric boundaries.
            painter.setPen(QPen(QColor(255, 215, 90, 155), 1))
            for ms in self._lyric_times:
                if not self._is_ms_visible(ms):
                    continue
                x = self._x_for_ms(ms)
                painter.drawLine(round(x), rect.top() + 2, round(x), rect.bottom() - 2)

            # Selected regular-LRC line: A is this line's start, B is the next
            # line's start or a provisional next timestamp.
            if (
                self._lyric_preview_start_ms is not None
                and self._is_ms_visible(self._lyric_preview_start_ms)
            ):
                sx = self._x_for_ms(self._lyric_preview_start_ms)
                painter.setPen(QPen(QColor(80, 160, 255, 245), 3))
                painter.drawLine(round(sx), rect.top() - 2, round(sx), rect.bottom() + 2)
                painter.setBrush(QBrush(QColor(80, 160, 255, 245)))
                painter.drawEllipse(round(sx - 5), rect.top() - 5, 10, 10)
                painter.setPen(QPen(QColor(230, 240, 255), 1))
                painter.drawText(round(sx + 7), rect.top() + 12, "A")

            # Special Lyrics timing-limit marker. Like Karaoke's "Next lyric"
            # boundary, this is a reference/hard-stop marker rather than an
            # editable A/B handle.
            if (
                not self._karaoke_segments
                and self._lyric_hard_boundary_ms is not None
                and self._is_ms_visible(self._lyric_hard_boundary_ms)
            ):
                lx = self._x_for_ms(self._lyric_hard_boundary_ms)
                painter.setPen(QPen(QColor(255, 215, 90, 235), 3, Qt.PenStyle.DashLine))
                painter.drawLine(
                    round(lx), rect.top() - 1,
                    round(lx), rect.bottom() + 1
                )
                painter.setPen(QPen(QColor(255, 240, 175, 255), 1))
                painter.drawText(
                    round(lx + 6), rect.top() + 13,
                    "Timing limit"
                )

            if (
                self._lyric_preview_end_ms is not None
                and self._is_ms_visible(self._lyric_preview_end_ms)
            ):
                ex = self._x_for_ms(self._lyric_preview_end_ms)
                painter.setPen(QPen(QColor(255, 90, 105, 245), 3))
                painter.drawLine(round(ex), rect.top() - 2, round(ex), rect.bottom() + 2)
                painter.setBrush(QBrush(QColor(255, 90, 105, 245)))
                painter.drawEllipse(round(ex - 5), rect.top() - 5, 10, 10)
                painter.setPen(QPen(QColor(255, 235, 238), 1))
                painter.drawText(round(ex + 7), rect.top() + 12, "B")

            # Current position line.
            if self._duration_ms > 0 and self._is_ms_visible(self._position_ms):
                x = self._x_for_ms(self._position_ms)
                painter.setPen(QPen(QColor(255, 255, 255, 245), 2))
                painter.drawLine(round(x), rect.top(), round(x), rect.bottom())

            # Hover line.
            if (
                self._hover_ms is not None
                and self._duration_ms > 0
                and self._is_ms_visible(self._hover_ms)
            ):
                x = self._x_for_ms(self._hover_ms)
                painter.setPen(QPen(QColor(255, 255, 255, 110), 1, Qt.PenStyle.DashLine))
                painter.drawLine(round(x), rect.top(), round(x), rect.bottom())
        finally:
            painter.end()

