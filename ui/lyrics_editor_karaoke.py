"""Karaoke separation, segment timing and preview synchronization."""

import re
from PySide6.QtCore import QSignalBlocker
from PySide6.QtWidgets import QMessageBox
from lyrics.models import LyricSegment
from lyrics.writer import format_timestamp
from lyrics.karaoke_timing import next_lyric_start, segment_end
from core.debug import debug_print
from lyrics.furigana import split_reading_by_text_ranges, yomi_word_surfaces


class LyricsEditorKaraokeMixin:
    def _boundaries_from_parts(self, text, parts):
        boundaries = set()
        offset = 0
        for part in parts[:-1]:
            offset += len(part)
            if 0 < offset < len(text):
                boundaries.add(offset)
        return boundaries

    def _build_separator_buttons(self, line, parts=None):
        """Compatibility entry point for refreshing the visual separator.

        The old implementation created one button per character and separator.
        The new widget stores only boundary character positions.
        """
        if not hasattr(self, "separator_widget"):
            return
        text = line.text
        if parts is None:
            # Generated ruby tokens are allowed to split line.segments for
            # rendering, but they are not karaoke boundaries. The karaoke UI
            # must only expose real, explicitly enabled karaoke structure.
            if getattr(line, "has_karaoke", False) and line.segments:
                parts = [segment.text for segment in line.segments]
            else:
                parts = [text]
        self.separator_widget.set_content(text, self._boundaries_from_parts(text, parts))
        self.refresh_separation_preview()

    def _clear_separator_buttons(self):
        if hasattr(self, "separator_widget"):
            self.separator_widget.set_content("", set())

    def _visual_parts(self):
        _, line = self._current_line()
        if line is None or not line.text:
            return []
        text = line.text
        boundaries = self.separator_widget.boundaries() if hasattr(self, "separator_widget") else set()
        for index, char in enumerate(text):
            if char == "\n":
                boundaries.update({index, index + 1})
        parts, start = [], 0
        for boundary in sorted(boundaries):
            parts.append(text[start:boundary])
            start = boundary
        parts.append(text[start:])
        return [part for part in parts if part]

    @staticmethod
    def _merge_whitespace_only_karaoke_parts(parts):
        """Keep structural newlines separate, attaching other spaces to text."""
        merged = []
        pending_prefix = ""
        for source_part in parts:
            for part in re.split(r"(\n)", str(source_part)):
                if not part:
                    continue
                if part == "\n":
                    if pending_prefix:
                        merged.append(pending_prefix)
                        pending_prefix = ""
                    merged.append(part)
                elif not part.strip():
                    if merged and merged[-1] != "\n":
                        merged[-1] += part
                    else:
                        pending_prefix += part
                else:
                    merged.append(pending_prefix + part)
                    pending_prefix = ""
        if pending_prefix:
            if merged and merged[-1] != "\n":
                merged[-1] += pending_prefix
            else:
                merged.append(pending_prefix)
        return merged

    def refresh_separation_preview(self, *args):
        if not self.karaoke_enabled or not hasattr(self, "separation_preview"):
            return
        parts = self._visual_parts()
        self.separation_preview.setText("  |  ".join(f"[ {part} ]" for part in parts))

    def auto_separate_current_line(self):
        _, line = self._current_line()
        if line is None:
            return

        text = line.text
        parts = []

        # First use the normal tokenizer.
        if yomi_word_surfaces is not None:
            try:
                parts = yomi_word_surfaces(text)
            except Exception as error:
                debug_print(f"[Yomi] Auto-separation failed: {error!r}")
                parts = []

        # Do not import or call PyCantonese from the UI process. Its tokenizer
        # stack is intentionally isolated in the Cantonese provider worker.
        # If the generic tokenizer cannot segment this line, keep the existing
        # safe fallback below rather than risking a process-level crash.
        fallback_is_character_split = (
            parts
            and "".join(parts) == text
            and len(parts) == len(text)
            and all(len(part) <= 1 for part in parts)
        )

        if not parts or "".join(parts) != text:
            # English/Latin lyrics should separate by words, not characters.
            # Keep punctuation attached to the surrounding word and preserve
            # the original whitespace so the generated parts still reconstruct
            # the exact lyric text.  A boundary is placed at each whitespace
            # run between two non-whitespace chunks.
            latin_word_match = re.search(r"[A-Za-z]", text) is not None
            non_han_only = not any(("\u3400" <= ch <= "\u4dbf") or ("\u4e00" <= ch <= "\u9fff") or ("\uf900" <= ch <= "\ufaff") or ("\u3040" <= ch <= "\u30ff") for ch in text)
            if latin_word_match and non_han_only and re.search(r"\S+\s+\S+", text):
                parts = re.split(r"(?<=\S)(?=\s+\S)", text)
                debug_print(f"[Karaoke] Auto-separated Latin text by words: {parts!r}")
            else:
                debug_print(f"[Karaoke] Auto-separation fell back to character split for {text!r}")
                parts = [char for char in text if char]
        parts = self._merge_whitespace_only_karaoke_parts(parts)
        self._build_separator_buttons(line, parts)
        self.karaoke_hint.setText("Automatic separation ready. Click any gap in the lyric to refine the boundaries.")

    def _update_overlap_warning(self, line=None):
        if line is None:
            _, line = self._current_line()
        if line is None:
            self.overlap_warning.setVisible(False)
            return

        segments = line.segments or []
        problems = []

        # Whitespace segments are real LRCX data, but the Karaoke editor hides
        # them as visual separators. Ignore them here too: a space should not
        # generate an overlap warning, nor should it break the comparison chain
        # between the surrounding visible segments.
        visible = [
            (i, segment)
            for i, segment in enumerate(segments)
            if str(getattr(segment, "text", "")).strip()
        ]

        for visible_index, (i, segment) in enumerate(visible):
            start = getattr(segment, "start", None)
            end = getattr(segment, "end", None)

            # Explicit end before its own start.
            if start is not None and end is not None and end < start:
                problems.append(i)
                continue

            following = next(
                ((row, item) for row, item in visible[visible_index + 1:]
                 if item.visual_line == segment.visual_line),
                None,
            )
            if following is None:
                continue

            next_row, next_segment = following
            next_start = getattr(next_segment, "start", None)

            # Existing ordering check: later starts cannot go backwards.
            if (
                start is not None
                and next_start is not None
                and next_start < start
            ):
                problems.append(next_row)

            # This segment's explicit End cannot extend beyond the next
            # *visible* segment's Start. Hidden whitespace is ignored.
            if (
                end is not None
                and next_start is not None
                and next_start < end
            ):
                problems.append(next_row)

        bounds_warning = getattr(self, "_karaoke_bounds_warning", None)
        if bounds_warning:
            self.overlap_warning.setText(bounds_warning)
            self.overlap_warning.setVisible(True)
        elif problems:
            rows = ", ".join(str(i + 1) for i in sorted(set(problems)))
            self.overlap_warning.setText(
                f"⚠ Overlapping / out-of-order karaoke timing near segment row(s): {rows}. "
                "A segment ends after the next one starts, or timestamps are out of order."
            )
            self.overlap_warning.setVisible(True)
        else:
            self.overlap_warning.setVisible(False)

    def clear_all_karaoke(self):
        if not self.lines:
            return
        box = QMessageBox(self)
        box.setWindowTitle("Clear Karaoke")
        box.setIcon(QMessageBox.Icon.Warning)
        box.setText("Clear karaoke timing from every lyric line?")
        box.setInformativeText(
            "All karaoke segment start times and instant-fill flags will be reset. "
            "Lyric text and ruby readings are kept."
        )
        clear_button = box.addButton("Clear Karaoke", QMessageBox.ButtonRole.DestructiveRole)
        cancel_button = box.addButton(QMessageBox.StandardButton.Cancel)
        box.setDefaultButton(cancel_button)
        try:
            box.exec()
        finally:
            box.deleteLater()
        if box.clickedButton() is not clear_button:
            return
        self._push_undo_state()
        for line in self.lines:
            # Karaoke boundaries and ruby spans are independent layers. Before
            # collapsing the karaoke chunks, collect every ruby span in global
            # lyric-text coordinates, then attach those same spans to the new
            # single segment. Resetting karaoke must never reset readings.
            preserved_spans = []
            offset = 0
            for segment in line.segments:
                segment_text = str(getattr(segment, "text", "") or "")
                spans = list(getattr(segment, "ruby_spans", []) or [])
                if spans:
                    for a, b, reading in spans:
                        if reading and 0 <= a < b <= len(segment_text):
                            preserved_spans.append((offset + a, offset + b, reading))
                else:
                    ruby = getattr(segment, "ruby", None)
                    if ruby and segment_text:
                        preserved_spans.append((offset, offset + len(segment_text), ruby))
                offset += len(segment_text)

            collapsed = LyricSegment(
                line.start,
                line.text,
                None,
                False,
                getattr(line, "end", None),
                None,
            )
            collapsed.ruby_spans = preserved_spans
            if len(preserved_spans) == 1 and preserved_spans[0][:2] == (0, len(line.text)):
                collapsed.ruby = preserved_spans[0][2]
                collapsed.ruby_source = "manual"
            line.segments = [collapsed]
            line.has_karaoke = False
        self.karaoke_dirty = True
        self._mark_document_dirty()
        row = self.line_list.currentRow()
        if row >= 0:
            self.load_selected_line(row)
        if hasattr(self, "karaoke_hint"):
            self.karaoke_hint.setText("Karaoke timing cleared. Ruby and lyric text were kept.")

    def reset_current_separation(self):
        _, line = self._current_line()
        if line is not None:
            self._build_separator_buttons(line, [line.text])

    def apply_visual_separation(self):
        index, line = self._current_line()
        if line is None:
            return

        parts = self._merge_whitespace_only_karaoke_parts(
            self._visual_parts()
        )
        if not parts or "".join(parts) != line.text:
            self.karaoke_hint.setText(
                "The visual separation no longer matches the lyric text."
            )
            return

        self._push_undo_state()
        old = list(line.segments) or [
            LyricSegment(line.start, line.text)
        ]

        # Karaoke boundaries and reading boundaries are deliberately separate.
        #
        # `parts` is the user's/automatic separator result and is the ONLY
        # source of karaoke chunk boundaries here.  Automatic furigana may
        # internally tokenize the same text much more finely (sometimes even
        # character-by-character), but those tokens must never become timed
        # karaoke chunks.
        old_ranges = []
        offset = 0
        for segment in old:
            end_offset = offset + len(segment.text)
            old_ranges.append((offset, end_offset, segment))
            offset = end_offset

        def source_for(position):
            return next(
                (
                    segment
                    for a, b, segment in old_ranges
                    if a <= position < b
                ),
                old[-1],
            )

        # Preserve an existing ruby value only when a karaoke chunk exactly
        # matches the stored ruby segment.  Generated readings do not need to
        # be copied into karaoke structure: the renderer generates/maps them
        # from the complete lyric while keeping the timing chunk intact.
        exact_ruby = {}
        for a, b, segment in old_ranges:
            ruby = getattr(segment, "ruby", None)
            if ruby and getattr(segment, "ruby_source", None) != "generated":
                exact_ruby[(a, b)] = (
                    ruby,
                    getattr(segment, "ruby_source", None),
                )

        boundaries = [0]
        offset = 0
        for part in parts:
            offset += len(part)
            boundaries.append(offset)

        # Fresh splits must partition their parent's timing span instead of
        # giving every newly-created chunk the same start timestamp.
        next_line_start = next_lyric_start(self.lines, line)

        # LyricSegment is mutable/unhashable, so use its object identity as
        # the grouping key rather than putting the segment itself in a dict.
        source_split_groups = {}
        source_by_id = {id(segment): segment for segment in old}

        for a, b in zip(boundaries, boundaries[1:]):
            if a < b:
                source_id = id(source_for(a))
                source_split_groups.setdefault(source_id, []).append((a, b))

        source_allocations = {}
        for source_id, ranges in source_split_groups.items():
            source = source_by_id[source_id]
            source_range = next(
                ((x, y) for x, y, segment in old_ranges if segment is source),
                None,
            )
            if source_range is None or (len(ranges) == 1 and ranges[0] == source_range):
                continue

            start_time = float(source.start)
            end_time = getattr(source, "end", None)
            end_time = float(end_time) if end_time is not None else None

            # No explicit end: use the next original segment's start.
            if end_time is None or end_time <= start_time:
                source_index = next(
                    (i for i, segment in enumerate(old) if segment is source),
                    -1,
                )
                if 0 <= source_index < len(old) - 1:
                    candidate = float(old[source_index + 1].start)
                    if candidate > start_time:
                        end_time = candidate

            if (end_time is None or end_time <= start_time) and line.end is not None:
                if line.end > start_time:
                    end_time = float(line.end)

            # Final original segment: its natural boundary is the next lyric.
            if (end_time is None or end_time <= start_time) and next_line_start is not None:
                if next_line_start > start_time:
                    end_time = next_line_start

            if end_time is None or end_time <= start_time:
                continue

            source_a, source_b = source_range
            duration = end_time - start_time
            width = max(1, source_b - source_a)
            allocations = {
                (a, b): (
                    start_time + duration * (a - source_a) / width,
                    start_time + duration * (min(b, source_b) - source_a) / width,
                )
                for a, b in ranges
            }

            source_allocations[source_id] = allocations

        new = []
        for a, b in zip(boundaries, boundaries[1:]):
            if a >= b:
                continue

            source = source_for(a)
            ruby, ruby_source = exact_ruby.get((a, b), (None, None))

            # Interpolate new boundaries within the original timed span.
            # Existing exact chunks retain their authored starts and ends.
            allocation = source_allocations.get(id(source), {}).get((a, b))
            child_start = (
                allocation[0]
                if allocation is not None
                else float(source.start)
            )

            source_range = next(
                ((x, y) for x, y, segment in old_ranges if segment is source),
                (a, b),
            )
            source_a, source_b = source_range

            # Keep an existing instant-fill marker only for an unchanged,
            # exact karaoke chunk. A newly split chunk is a normal timed span.
            exact_chunk = a == source_a and b == source_b

            # A split child gets its allocated end, rather than inheriting the
            # entire parent's end and overlapping every later child.
            child_end = (
                getattr(source, "end", None)
                if exact_chunk
                else (allocation[1] if allocation is not None else None)
            )

            tail_source = source_for(b - 1)
            if tail_source is not source:
                tail_a, tail_b, _ = next(
                    entry for entry in old_ranges if entry[2] is tail_source
                )
                tail_index = next(i for i, segment in enumerate(old) if segment is tail_source)
                tail_end = segment_end(line, tail_index, next_line_start)
                if tail_end is not None and b < tail_b:
                    child_end = tail_source.start + (tail_end - tail_source.start) * (b - tail_a) / max(1, tail_b - tail_a)
                else:
                    child_end = tail_end

            child = LyricSegment(
                child_start,
                line.text[a:b],
                ruby,
                source.instant if exact_chunk else False,
                child_end,
                ruby_source,
            )

            child.visual_line = line.text[:a].count("\n")

            # Rebase every existing inline ruby span that overlaps this new
            # karaoke chunk. Splitting or re-separating karaoke must preserve
            # readings; only the timing boundaries are changing.
            for old_a, old_b, old_segment in old_ranges:
                if old_b <= a or old_a >= b:
                    continue
                old_spans = list(getattr(old_segment, "ruby_spans", []) or [])
                if not old_spans:
                    old_ruby = getattr(old_segment, "ruby", None)
                    if old_ruby and getattr(old_segment, "ruby_source", None) != "generated":
                        old_spans = [(0, old_b - old_a, old_ruby)]
                for span_a, span_b, span_reading in old_spans:
                    global_a = old_a + span_a
                    global_b = old_a + span_b
                    overlap_a = max(a, global_a)
                    overlap_b = min(b, global_b)
                    if overlap_a >= overlap_b:
                        continue
                    child_reading = span_reading
                    if (overlap_a != global_a or overlap_b != global_b) and split_reading_by_text_ranges is not None:
                        child_reading = split_reading_by_text_ranges(
                            line.text[global_a:global_b],
                            span_reading,
                            [(overlap_a - global_a, overlap_b - global_a)],
                        )[0]
                    if child_reading:
                        child.ruby_spans.append((overlap_a - a, overlap_b - a, child_reading))

            if len(child.ruby_spans) == 1 and child.ruby_spans[0][:2] == (0, len(child.text)):
                child.ruby = child.ruby_spans[0][2]
                child.ruby_source = "manual"
            new.append(child)

        # Do not derive line.start from the first karaoke chunk.
        # The line may intentionally begin before segment 0.
        line.segments = new
        line.has_karaoke = True

        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.loading = True
        self.segment_table.setRowCount(0)
        self._populate_segments(line)
        self.loading = False
        self.karaoke_hint.setText(
            "Separation applied. Ruby tokens do not create karaoke chunks."
        )
        self._refresh_ruby_preview(line)
        self.refresh_line_label(index)

        # _populate_segments() selects the first visible segment while loading,
        # but that selection signal is intentionally blocked. Refresh the
        # waveform explicitly so the first segment's selected marker/handles
        # appear immediately after a fresh separation.
        self._refresh_karaoke_waveform_editor()

    def _default_segment_start(self, row, line):
        if row > 0 and self.segment_table.item(row - 1, 1) is not None:
            try:
                return self._parse_time(self.segment_table.item(row - 1, 1).text())
            except Exception:
                pass
        if 0 <= row < len(line.segments):
            return float(line.segments[row].start)
        return float(line.start)

    def _default_segment_end(self, row, line, start_time):
        if row + 1 < self.segment_table.rowCount():
            next_item = self.segment_table.item(row + 1, 0)
            if next_item is not None and next_item.text().strip():
                try:
                    return max(start_time, self._parse_time(next_item.text()))
                except Exception:
                    pass
        if getattr(line, "end", None) is not None:
            return max(start_time, float(line.end))
        line_index = self._current_line_index()
        if line_index is not None and 0 <= line_index < len(self.lines) - 1:
            return max(start_time, float(self.lines[line_index + 1].start))
        if 0 <= row < len(line.segments):
            existing = getattr(line.segments[row], "end", None)
            if existing is not None:
                return max(start_time, float(existing))
        return start_time

    def _set_segment_timing(self, row, line, *, start_time=None, end_time=None,
                            change_start=False, change_end=False):
        """Update canonical karaoke timing without reading table fallbacks."""
        if line is None or not (0 <= row < len(line.segments)):
            return None

        old = line.segments[row]
        new_start = float(start_time) if change_start else float(old.start)
        new_end = end_time if change_end else getattr(old, "end", None)
        if new_end is not None:
            new_end = float(new_end)
            if new_end < new_start:
                # A manual start edit can move a chunk past an old explicit
                # end boundary. That end is now stale; clamping it to the new
                # start would still serialize an extra/phantom <timestamp>.
                # Drop the invalid explicit end and let the next chunk (or
                # line end) provide the natural boundary instead.
                new_end = None

        replacement = LyricSegment(
            new_start,
            old.text,
            old.ruby,
            old.instant,
            new_end,
            getattr(old, "ruby_source", None),
        )
        # Timing-only edits must preserve inline ruby spans.
        replacement.ruby_spans = list(getattr(old, "ruby_spans", []) or [])
        replacement.visual_line = old.visual_line
        line.segments[row] = replacement

        # Segment 0 is independent from line.start. Moving its left edge can
        # create or adjust a leading pause without moving the lyric timestamp.

        if row == len(line.segments) - 1 and change_end:
            if line.end is None or line.end == old.end or (new_end is not None and new_end > line.end):
                line.end = new_end

        return line.segments[row]

    def instant_changed(self, row, checked):
        if self.loading:
            return
        item = self.segment_table.item(row, 2)
        if item is not None:
            self.segment_changed(item)

    def segment_changed(self, item):
        if self.loading:
            return

        index, line = self._current_line()
        row = item.row()
        if line is None or not (0 <= row < len(line.segments)):
            return

        if item.column() in (0, 1) and item.text().strip():
            try:
                value = self._parse_time(item.text())
                if item.column() == 1 and value < line.segments[row].start:
                    raise ValueError("The segment end cannot be before its start.")
            except ValueError as error:
                old_value = line.segments[row].start if item.column() == 0 else line.segments[row].end
                blocker = QSignalBlocker(self.segment_table)
                item.setText(format_timestamp(old_value) if old_value is not None else "")
                del blocker
                QMessageBox.warning(self, "Invalid Timestamp", str(error))
                return
        self._push_undo_state()
        # Timing edits and text/ruby edits both preserve the canonical segment
        # object. Only the column that was actually edited may change timing.
        old = line.segments[row]
        start_time = float(old.start)
        end_time = getattr(old, "end", None)

        if item.column() == 0:
            text = item.text().strip()
            try:
                start_time = self._parse_time(text) if text else self._default_segment_start(row, line)
            except Exception:
                start_time = self._default_segment_start(row, line)
            self._set_segment_timing(
                row, line, start_time=start_time, change_start=True
            )

        elif item.column() == 1:
            text = item.text().strip()
            try:
                end_time = self._parse_time(text) if text else None
            except Exception:
                end_time = getattr(line.segments[row], "end", None)
            self._set_segment_timing(
                row, line, end_time=end_time, change_end=True
            )

        else:
            text_item = self.segment_table.item(row, 2)
            ruby_item = self.segment_table.item(row, 3)
            text_value = text_item.text() if text_item is not None else old.text
            ruby = ruby_item.text().strip() if ruby_item is not None else ""
            ruby = ruby or None
            instant_box = self.segment_table.cellWidget(row, 4)
            instant = instant_box.isChecked() if instant_box else old.instant
            replacement = LyricSegment(
                old.start,
                text_value,
                ruby,
                instant,
                getattr(old, "end", None),
                getattr(old, "ruby_source", None),
            )
            old_spans = list(getattr(old, "ruby_spans", []) or [])
            replacement.visual_line = old.visual_line
            if item.column() == 3:
                replacement.ruby_source = "manual" if ruby else None
            if item.column() == 3 and len(old_spans) == 1:
                # The Karaoke Ruby column edits the existing inline span without
                # changing its text range (e.g. 描き -> {描|が}き).
                a, b, _ = old_spans[0]
                if ruby:
                    replacement.ruby_spans = [(a, b, ruby)]
                    if not (a == 0 and b == len(text_value)):
                        replacement.ruby = None
                else:
                    replacement.ruby_spans = []
            elif item.column() != 3:
                # Timing/text/instant edits must not silently erase ruby spans.
                replacement.ruby_spans = old_spans
            line.segments[row] = replacement

        line.text = "".join(segment.text for segment in line.segments)
        line.has_karaoke = (
            line.has_karaoke or item.column() in (0, 1) or len(line.segments) > 1
            or any(segment.instant for segment in line.segments)
        )
        self.karaoke_dirty = True
        self._mark_document_dirty()
        self.refresh_line_label(index)
        self._refresh_ruby_preview(line)
        self._update_furigana_editor_availability(line)
        self._update_overlap_warning(line)
        self._refresh_karaoke_preview()
        self._refresh_karaoke_waveform_editor()

    def _refresh_karaoke_preview(self, position_ms=None, rebuild_segments=True):
        if not self.karaoke_enabled or not hasattr(self, "karaoke_renderer_preview"):
            return

        index, line = self._current_line()
        if line is None:
            if rebuild_segments or self._preview_segments_line_index is not None:
                self.karaoke_renderer_preview.set_lyric("", generate=False)
                self._preview_segments_line_index = None
            return

        line_changed = self._preview_segments_line_index != index
        if rebuild_segments or line_changed:
            line_end = getattr(line, "end", None)
            if line_end is None:
                timeline = sorted(
                    (candidate.start, candidate_index)
                    for candidate_index, candidate in enumerate(self.lines)
                )
                timeline_index = next(
                    (i for i, (_start, candidate_index) in enumerate(timeline)
                     if candidate_index == index),
                    None,
                )
                if timeline_index is not None and timeline_index + 1 < len(timeline):
                    line_end = timeline[timeline_index + 1][0]
                elif self.player is not None and hasattr(self.player, "media_player"):
                    duration = self.player.media_player.duration()
                    if duration > 0:
                        line_end = self._audio_to_lyric_seconds(duration)
                if line_end is None:
                    line_end = line.segments[-1].start if line.segments else line.start
            self.karaoke_renderer_preview.set_segments(
                line.text,
                line.segments,
                line_end,
                style_spans=line.style_spans,
                is_blockquote=line.is_blockquote,
                translation_lines=line.translation_lines,
            )
            self._preview_segments_line_index = index
            if line_changed:
                self.karaoke_renderer_preview.set_current_line(True)
                self.karaoke_renderer_preview.start_line_entrance()

        if position_ms is None and self.player is not None and hasattr(self.player, "media_player"):
            position_ms = self.player.media_player.position()
        if position_ms is None:
            position_ms = self._lyric_to_audio_ms(line.start)

        # This is the only per-position operation required for the preview.
        self.karaoke_renderer_preview.set_karaoke_position(
            self._audio_to_lyric_seconds(position_ms)
        )
