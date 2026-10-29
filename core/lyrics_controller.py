from pathlib import Path
import os
import re
from lyrics.lrcx_parser import get_lrcx_offset
from bisect import bisect_right

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMessageBox, QCheckBox

from lyrics.loader import load_lyrics, get_lyrics_generation_mode, PLAYER_LYRIC_LINE_LIMIT
from lyrics.furigana import (
    prefetch_furigana_batches,
    set_active_parser,
    set_mecab_parse_mode,
    available_parsers,
    set_reading_mode,
    get_cached_furigana_segments,
    get_reading_mode,
    set_automatic_readings_enabled,
)
from lyrics.models import LyricSegment
from core.settings import (
    get_lyrics_attachment,
    load_lrc_search_folders,
    get_effective_lyrics_settings,
    get_global_lyrics_settings,
    get_local_lyrics_overrides,
    get_appearance_settings,
    get_cantonese_reading_prompt_decision,
    save_cantonese_reading_prompt_decision,
)


from core.lyrics_search import LyricsSearchMixin

class _FuriganaProgressBridge(QObject):
    progress = Signal(int, int, int)
    finished = Signal(int)


_FURIGANA_PREFETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="LyricsPlus-FuriganaPrefetch"
)

class LyricsController(LyricsSearchMixin):
    # Lyrics files are indexed once per search folder instead of recursively
    # walking and reopening the entire collection for every track change.
    _lyrics_folder_index_cache = {}

    def _refresh_current_lyric_furigana(self):
        """Refresh cached furigana without discarding karaoke timing."""
        if not (
            hasattr(self, "lyric_widget")
            and self.lyric_widget.lyric
        ):
            return

        current_index = getattr(self, "current_line", -1)
        if (
            0 <= current_index < len(getattr(self, "lyric_lines", []))
            and getattr(self.lyric_lines[current_index], "segments", None)
        ):
            line = self.lyric_lines[current_index]
            line_end = getattr(line, "end", None)
            if line_end is None and current_index + 1 < len(self.lyric_lines):
                line_end = self.lyric_lines[current_index + 1].start
            self.lyric_widget.set_segments(
                line.text,
                line.segments,
                line_end,
            )
        else:
            self.lyric_widget.set_lyric(
                self.lyric_widget.lyric, generate=False
            )

    def refresh_lyrics_settings_for_current_file(self):
        path = self.current_audio_path

        self.global_lyrics_settings = get_global_lyrics_settings()
        self.local_lyrics_overrides = get_local_lyrics_overrides(path)

        effective = get_effective_lyrics_settings(path)
        appearance = get_appearance_settings()
        self.empty_timestamp_placeholder_enabled = appearance[
            "empty_timestamp_placeholder_enabled"
        ]
        self.empty_timestamp_placeholder = appearance[
            "empty_timestamp_placeholder"
        ]

        self.scrolling_lyrics_enabled = effective["scrolling_lyrics"]
        self.lyrics_direction = effective["lyrics_direction"]
        self.lyrics_padding = effective["lyrics_padding"]
        self.vertical_padding = effective["vertical_padding"]
        self.ruby_padding = effective["ruby_padding"]
        self.ruby_font_size = effective["ruby_font_size"]
        self.show_ruby = effective["show_ruby"]
        self.show_romaji = effective.get("show_romaji", False)
        self.all_romaji = effective.get("all_romaji", False)
        self.ruby_position = effective.get("ruby_position", "above")
        previous_parser = getattr(self, "furigana_parser", None)
        previous_mecab_parse_mode = getattr(self, "mecab_parse_mode", None)
        self.furigana_parser = effective["furigana_parser"]
        self.mecab_parse_mode = effective.get("mecab_parse_mode", "batch")

        # MeCab is intentionally optional. If the user selects it without the
        # local dependencies installed, ask what to do instead of silently
        # switching engines behind their back.
        if (
            self.furigana_parser == "mecab_local"
            and not available_parsers()["mecab_local"]["available"]
        ):
            box = QMessageBox(self)
            box.setWindowTitle("MeCab is not installed")
            box.setIcon(QMessageBox.Icon.Information)
            box.setText("The MeCab parser requires local dependencies that are not installed.")
            box.setInformativeText(
                "Install fugashi and unidic-lite to use MeCab. "
                "The app will keep using Yomi until MeCab is installed."
            )
            install = box.addButton("Install MeCab", QMessageBox.ButtonRole.AcceptRole)
            use_yomi = box.addButton("Use Yomi", QMessageBox.ButtonRole.RejectRole)
            box.exec()
            if box.clickedButton() is install:
                QMessageBox.information(
                    self,
                    "Install MeCab",
                    "Run this in the same Python environment as LyricsPlus:\n\n"
                    "python -m pip install fugashi unidic-lite"
                )
            # Do not silently fall back in the setting itself. Keep the global
            # preference, but use Yomi for this run until MeCab becomes available.
            self.furigana_parser = "yomi"

        set_active_parser(self.furigana_parser)
        set_mecab_parse_mode(self.mecab_parse_mode)
        self.font_family = effective["font_family"]
        self.font_size = effective["font_size"]
        self.lyrics_alignment = effective["lyrics_alignment"]
        self.highlight_current = effective["highlight_current"]
        self.highlight_animation = effective["highlight_animation"]
        self.highlight_animation_duration = effective[
            "highlight_animation_duration"
        ]
        self.highlight_animation_out_duration = effective[
            "highlight_animation_out_duration"
        ]
        self.highlight_animation_easing = effective[
            "highlight_animation_easing"
        ]
        self.highlight_color = effective["highlight_color"]
        self.lyrics_color = effective["lyrics_color"]
        self.scrolling_mode = effective["scrolling_mode"]
        self.continuous_scroll_speed = effective["continuous_scroll_speed"]
        self.continuous_scroll_duration_based = effective["continuous_scroll_duration_based"]
        self.continuous_scroll_nudge_current = effective["continuous_scroll_nudge_current"]
        self.limited_reading_rendering = effective["limited_reading_rendering"]
        self.limited_reading_range = effective["limited_reading_range"]
        self.fade_in_readings = effective["fade_in_readings"]
        self.scroll_animation_speed = effective["scroll_animation_speed"]
        self.scroll_animation_easing = effective["scroll_animation_easing"]
        self.scroll_behavior = effective["scroll_behavior"]

        if hasattr(self, "lyric_display_stack"):
            self.apply_lyric_display_mode()
        if (
            previous_parser is not None
            and (
                previous_parser != self.furigana_parser
                or previous_mecab_parse_mode != self.mecab_parse_mode
            )
            and getattr(self, "lyric_lines", None)
        ):
            print(f"[Furigana] Furigana parser changed: {previous_parser} -> {self.furigana_parser}; MeCab mode: {previous_mecab_parse_mode} -> {self.mecab_parse_mode}")
            self._start_furigana_prefetch()

    def apply_lyric_display_mode(self):
        # Remember the previously visible mode. Re-entering scrolling must
        # rebuild its rows: switching the stacked widget can otherwise leave
        # existing rows attached but visually empty.
        previous_scrolling = getattr(
            self,
            "_last_scrolling_lyrics_enabled",
            None,
        )
        entering_scrolling = (
            self.scrolling_lyrics_enabled
            and previous_scrolling is not True
        )
        self.lyric_widget.set_direction(self.lyrics_direction)
        self.lyric_widget.set_alignment(self.lyrics_alignment)
        self.lyric_widget.set_font(
            self.font_family,
            self.font_size,
            self.ruby_font_size,
        )
        self.lyric_widget.set_colors(self.lyrics_color, self.highlight_color)
        self.lyric_widget.set_highlight_current(self.highlight_current)
        self.lyric_widget.set_highlight_animation(
            self.highlight_animation,
            self.highlight_animation_duration,
            self.highlight_animation_easing,
            self.highlight_animation_out_duration,
        )
        self.lyric_widget.set_ruby_visible(self.show_ruby)
        self.lyric_widget.set_romaji_visible(self.show_romaji)
        self.lyric_widget.set_all_romaji_visible(self.all_romaji)
        self.lyric_widget.set_ruby_position(self.ruby_position)
        self.lyric_widget.set_ruby_padding(self.ruby_padding)

        self.scrolling_lyrics_widget.set_direction(self.lyrics_direction)
        self.scrolling_lyrics_widget.set_alignment(self.lyrics_alignment)
        self.scrolling_lyrics_widget.set_font(
            self.font_family,
            self.font_size,
            self.ruby_font_size,
        )
        self.scrolling_lyrics_widget.set_colors(
            self.lyrics_color,
            self.highlight_color,
        )
        self.scrolling_lyrics_widget.set_highlight_current(
            self.highlight_current
        )
        self.scrolling_lyrics_widget.set_highlight_animation(
            self.highlight_animation,
            self.highlight_animation_duration,
            self.highlight_animation_easing,
            self.highlight_animation_out_duration,
        )
        self.scrolling_lyrics_widget.set_padding(self.lyrics_padding)
        self.scrolling_lyrics_widget.set_vertical_padding(self.vertical_padding)
        self.scrolling_lyrics_widget.set_ruby_visible(self.show_ruby)
        self.scrolling_lyrics_widget.set_romaji_visible(self.show_romaji)
        self.scrolling_lyrics_widget.set_all_romaji_visible(self.all_romaji)
        self.scrolling_lyrics_widget.set_ruby_position(self.ruby_position)
        self.scrolling_lyrics_widget.set_ruby_padding(self.ruby_padding)
        self.scrolling_lyrics_widget.set_limited_reading_rendering(
            self.limited_reading_rendering,
            self.limited_reading_range,
            self.fade_in_readings,
        )
        # TXT has no timing information, so it is always rendered as
        # continuous scrolling. Timed LRC/LRCX keeps the user's mode.
        effective_scroll_mode = (
            "continuous"
            if getattr(self, "plain_lyrics_mode", False)
            else self.scrolling_mode
        )
        self.scrolling_lyrics_widget.set_scrolling_mode(
            effective_scroll_mode
        )
        self.scrolling_lyrics_widget.set_continuous_scroll_speed(
            self.continuous_scroll_speed
        )
        self.scrolling_lyrics_widget.set_continuous_scroll_options(
            duration_based=self.continuous_scroll_duration_based,
            nudge_current=self.continuous_scroll_nudge_current,
        )
        self.scrolling_lyrics_widget.set_scroll_animation_speed(
            self.scroll_animation_speed, self.scroll_animation_easing
        )
        self.scrolling_lyrics_widget.set_scroll_behavior(
            self.scroll_behavior
        )

        self.lyric_display_stack.setCurrentIndex(
            1 if self.scrolling_lyrics_enabled else 0
        )

        self.lyrics_list_label.setVisible(True)
        self.lyrics_list_widget.setVisible(
            not self.lyrics_list_collapsed
        )

        if self.scrolling_lyrics_enabled:
            # Always rebuild when switching back from Compact. This guarantees
            # the scrolling container gets fresh, visible lyric rows instead
            # of relying on stale widgets from a previously hidden page.
            if (
                entering_scrolling
                or not self.scrolling_lyrics_widget.rows
                or len(self.scrolling_lyrics_widget.rows) != len(self.lyrics)
            ):
                source_lines = getattr(self, "lyric_lines", None) or self.lyrics
                self.scrolling_lyrics_widget.set_lyrics(source_lines)

            # Always synchronize empty timestamp rows, even when the scrolling
            # widget was not rebuilt (for example after changing settings or
            # returning to the song). This prevents inactive rows staying blank.
            if (
                getattr(self, "empty_timestamp_placeholder_enabled", True)
                and hasattr(self.scrolling_lyrics_widget, "set_placeholder_text")
            ):
                template = str(
                    getattr(
                        self,
                        "empty_timestamp_placeholder",
                        "♪ {countdown}",
                    )
                )
                static_placeholder = (
                    template.replace("{countdown}", "")
                    .replace("  ", " ")
                    .strip()
                )
                for placeholder_index, (_, placeholder_source_text) in enumerate(self.lyrics):
                    if not str(placeholder_source_text).strip():
                        self.scrolling_lyrics_widget.set_placeholder_text(
                            placeholder_index,
                            static_placeholder,
                        )

            self.scrolling_lyrics_widget.set_current_line(
                self.current_line,
                animate=False,
            )
            # If the scrolling page was just made visible, force its geometry
            # after Qt gives the stacked page a real viewport size. This avoids
            # the first-open empty/clipped lyrics state.
            QTimer.singleShot(
                0,
                self.scrolling_lyrics_widget.refresh_layout_now,
            )
            QTimer.singleShot(
                1,
                self.scrolling_lyrics_widget.refresh_layout_now,
            )
        elif self.lyrics and 0 <= self.current_line < len(self.lyrics):
            current_text = self.lyrics[self.current_line][1]
            if current_text != self.last_lyric_text:
                self.last_lyric_text = current_text
                line = (
                    self.lyric_lines[self.current_line]
                    if self.current_line < len(self.lyric_lines)
                    else None
                )

                if line is not None and line.segments:
                    line_end = getattr(line, "end", None)
                    if line_end is None and self.current_line + 1 < len(self.lyric_lines):
                        line_end = self.lyric_lines[self.current_line + 1].start
                    self.lyric_widget.set_segments(
                        line.text,
                        line.segments,
                        line_end,
                    )
                else:
                    self.lyric_widget.set_lyric(current_text)

            self.highlight_current_lyric()

        self._last_scrolling_lyrics_enabled = self.scrolling_lyrics_enabled

    def _start_furigana_prefetch(self):
        self._furigana_prefetch_id = getattr(self, "_furigana_prefetch_id", 0) + 1
        request_id = self._furigana_prefetch_id

        lines = [
            line.text
            for line in self.lyric_lines
            if not getattr(line, "segments", None)
        ]

        if not lines:
            self._set_furigana_progress(0, 0, request_id)
            return

        progress_interval = max(1, (len(lines) + 99) // 100)

        def progress(done, total):
            if done < total and done % progress_interval:
                return
            self._furigana_progress_bridge.progress.emit(
                done, total, request_id
            )

        def worker():
            return prefetch_furigana_batches(
                lines,
                batch_size=10,
                progress_callback=progress,
                should_continue=lambda: request_id == self._furigana_prefetch_id,
            )

        future = _FURIGANA_PREFETCH_EXECUTOR.submit(worker)

        def finished(done):
            try:
                done.result()
            except Exception as error:
                print(f"[Furigana] Batch prefetch failed: {error!r}")
            self._furigana_progress_bridge.finished.emit(request_id)

        future.add_done_callback(finished)

    def _apply_prefetched_readings_to_live_lines(self):
        """Keep prefetched readings in the reading cache.

        Generated reading tokens are visual/ruby data, not karaoke timing data.
        Do not promote them into ``line.segments``: that list owns karaoke
        boundaries, and replacing a plain line with tokenizer pieces makes the
        renderer sweep each generated token independently.
        """
        return False

    def _set_furigana_progress(self, done, total, request_id):
        if request_id != getattr(self, "_furigana_prefetch_id", request_id):
            return
        if hasattr(self, "furigana_progress_bar"):
            if total <= 0:
                self.furigana_progress_bar.setVisible(False)
                return
            self.furigana_progress_bar.setVisible(done < total)
            self.furigana_progress_bar.setRange(0, total)
            self.furigana_progress_bar.setValue(done)
            self.furigana_progress_bar.setFormat(
                f"Generating furigana… {done} / {total}"
            )

    def _furigana_prefetch_finished(self, request_id):
        if request_id != getattr(self, "_furigana_prefetch_id", request_id):
            return
        if hasattr(self, "furigana_progress_bar"):
            self.furigana_progress_bar.setVisible(False)
        self.scrolling_lyrics_widget.refresh_furigana_for_texts()
        self._refresh_current_lyric_furigana()
        print("[Furigana] Batch prefetch complete")

    def load_lrc_file(self, path):
        path = Path(path)
        self.current_lyrics_path = path
        self.lyrics_offset_seconds = (
            get_lrcx_offset(path) / 1000.0
            if path.suffix.lower() == ".lrcx"
            else 0.0
        )

        try:
            # Reading mode is document-specific.  Do not let the previous
            # song's persisted mode leak into a file that has no [g:...] tag.
            #
            # Example: opening an LRCX with [g:furigana] switches the global
            # generator to Japanese furigana.  Without resetting it here,
            # returning to a Korean .lrc (which has no persisted mode) leaves
            # the player stuck in "furigana", so the Korean provider is never
            # considered and MeCab is incorrectly asked to process Hangul.
            persisted_generation_mode = get_lyrics_generation_mode(path) or "auto"
            set_reading_mode(persisted_generation_mode)
            self.lyric_lines = load_lyrics(path)
            if len(self.lyric_lines) > PLAYER_LYRIC_LINE_LIMIT:
                raise ValueError(
                    f"This file contains {len(self.lyric_lines):,} lyric rows; "
                    f"the player limit is {PLAYER_LYRIC_LINE_LIMIT:,}. "
                    "Review or reconvert its subtitles before loading it."
                )

            # PyCantonese can make the first render of a long lyric file
            # noticeably heavy. Let the user choose whether to generate now,
            # and optionally remember that choice for future Cantonese songs.
            set_automatic_readings_enabled(True)
            if get_reading_mode() in {"jyutping", "yale"} and self.lyric_lines:
                remembered = get_cantonese_reading_prompt_decision()

                if remembered is not None:
                    set_automatic_readings_enabled(remembered)
                else:
                    mode_name = (
                        "Jyutping"
                        if get_reading_mode() == "jyutping"
                        else "Yale"
                    )
                    box = QMessageBox(self)
                    box.setWindowTitle("Generate Cantonese readings?")
                    box.setIcon(QMessageBox.Icon.Information)
                    box.setText(
                        f"Generate {mode_name} readings automatically for this song?"
                    )
                    box.setInformativeText(
                        "PyCantonese may make initial loading slower for long lyric files. "
                        "You can load the song immediately without generating readings."
                    )

                    remember = QCheckBox("Remember my decision")
                    box.setCheckBox(remember)

                    generate = box.addButton(
                        "Generate Now",
                        QMessageBox.ButtonRole.AcceptRole,
                    )
                    skip = box.addButton(
                        "Load Without Generating",
                        QMessageBox.ButtonRole.RejectRole,
                    )
                    box.setDefaultButton(generate)
                    box.exec()

                    decision = box.clickedButton() is generate
                    set_automatic_readings_enabled(decision)

                    if remember.isChecked():
                        save_cantonese_reading_prompt_decision(decision)

        except (OSError, UnicodeError, ValueError) as error:
            print(f"[Lyrics] Failed to load {str(path)!r}: {error}")
            self.lyric_lines = []
            self.lyrics = []
            self.lyric_times = []
            self._playback_line_indices = []
            self.current_line = -1
            self.last_lyric_text = None
            self._furigana_prefetch_id = getattr(self, "_furigana_prefetch_id", 0) + 1
            self.lyric_widget.set_lyric("")
            self.scrolling_lyrics_widget.clear()
            self.lyrics_list_widget.clear()
            if hasattr(self, "waveform_widget"):
                self.waveform_widget.set_lyric_times([])
            if hasattr(self, "furigana_progress_bar"):
                self.furigana_progress_bar.hide()
            self._lyrics_load_error = True
            self.statusBar().showMessage(f"Could not load lyrics: {error}")
            return

        if getattr(self, "_lyrics_load_error", False):
            self.statusBar().clearMessage()
            self._lyrics_load_error = False

        # Compatibility layer for the existing displays.
        self.lyrics = [
            (line.start, line.text)
            for line in self.lyric_lines
        ]
        timeline = sorted(
            enumerate(self.lyric_lines),
            key=lambda item: (item[1].start, item[0]),
        )
        self._playback_line_indices = [
            index for index, _line in timeline
        ]
        self.lyric_times = [
            line.start for _index, line in timeline
        ]

        if hasattr(self, "waveform_widget"):
            self.waveform_widget.set_lyric_times(self.lyric_times)

        self.plain_lyrics_mode = path.suffix.lower() == ".txt"
        self.current_line = -1
        self.last_lyric_text = None
        self.lyric_widget.set_lyric("")

        self.update_lyrics_list()
        template = str(
            getattr(self, "empty_timestamp_placeholder", "♪ {countdown}")
        )
        self.scrolling_lyrics_widget._empty_timestamp_static_placeholder = (
            template.replace("{countdown}", "")
            .replace("  ", " ")
            .strip()
        )
        self.scrolling_lyrics_widget.set_lyrics(self.lyric_lines)
        if self.plain_lyrics_mode:
            self.scrolling_lyrics_widget.set_scrolling_mode("continuous")
            self.lyric_display_stack.setCurrentIndex(1)
            self.scrolling_lyrics_widget.set_current_line(-1, animate=False)
        else:
            self.scrolling_lyrics_widget.set_scrolling_mode(
                self.scrolling_mode
            )
        self._start_furigana_prefetch()

        print(
            f"[Lyrics] Loaded {len(self.lyric_lines)} lines "
            f"from: {path.name}"
        )

        if path.suffix.lower() == ".lrcx":
            segment_count = sum(
                len(line.segments)
                for line in self.lyric_lines
            )
            print(f"[LRCX] Ready: {segment_count} timed segments")

    def update_lyrics(self, position_ms):
        if not self.lyrics:
            return

        # Continuous scrolling advances from playback elapsed time only. It
        # never looks at lyric timestamps, and pauses naturally stop movement.
        if getattr(self, "scrolling_mode", "automatic") == "continuous" or getattr(self, "plain_lyrics_mode", False):
            self.scrolling_lyrics_widget.update_continuous_scroll(position_ms)

        if getattr(self, "plain_lyrics_mode", False):
            return

        position = (position_ms / 1000.0) - getattr(self, "lyrics_offset_seconds", 0.0)
        timeline_index = bisect_right(
            self.lyric_times,
            position,
        ) - 1

        if timeline_index >= 0:
            occurrence_start = self.lyric_times[timeline_index]
            while (
                timeline_index > 0
                and self.lyric_times[timeline_index - 1] == occurrence_start
            ):
                timeline_index -= 1

        line_index = (
            self._playback_line_indices[timeline_index]
            if timeline_index >= 0
            else -1
        )

        if line_index < 0:
            if self.current_line != -1:
                self.current_line = -1
                self.last_lyric_text = None
                self.lyric_widget.set_current_line(False)
                self.lyric_widget.set_lyric("")
                self.scrolling_lyrics_widget.set_current_line(
                    -1,
                    animate=False,
                )
            return

        if (
            getattr(self, "scrolling_mode", "automatic") == "continuous"
            and self.scrolling_lyrics_enabled
        ):
            # This only requests a temporary speed boost. The continuous scroll
            # loop remains the sole writer of the scrollbar.
            self.scrolling_lyrics_widget.nudge_current_line_into_view(line_index)

        text = self.lyrics[line_index][1]
        is_empty_timestamp = not str(text).strip()

        # Normal lyrics do not need any work while they remain current.
        # Empty timestamp rows are different because their countdown changes,
        # but once the row is already current we must update ONLY its text.
        # Calling set_current_line() again would schedule another scroll
        # correction and fight the existing scroll animation.
        if line_index == self.current_line:
            if not (
                is_empty_timestamp
                and getattr(self, "empty_timestamp_placeholder_enabled", True)
            ):
                return

            next_lyric_time = None
            for next_timeline_index in range(
                timeline_index + 1,
                len(self.lyric_times),
            ):
                next_line_index = self._playback_line_indices[next_timeline_index]
                next_text = self.lyrics[next_line_index][1]
                if str(next_text).strip():
                    next_lyric_time = self.lyric_times[next_timeline_index]
                    break

            remaining = (
                max(0.0, next_lyric_time - position)
                if next_lyric_time is not None
                else 0.0
            )
            countdown = (
                f"{remaining:.1f}"
                if next_lyric_time is not None
                else ""
            )

            template = getattr(
                self,
                "empty_timestamp_placeholder",
                "♪ {countdown}",
            )
            try:
                display_text = (
                    str(template)
                    .replace("{countdown}", countdown)
                    .strip()
                )
            except Exception:
                display_text = f"♪ {countdown}".strip()

            if (
                self.scrolling_lyrics_enabled
                and hasattr(
                    self.scrolling_lyrics_widget,
                    "set_placeholder_text",
                )
            ):
                self.scrolling_lyrics_widget.set_placeholder_text(
                    line_index,
                    display_text,
                )

            # Crucial: do not touch current-line state or scrolling again.
            return

        previous_line = self.current_line

        # When leaving an empty timestamp, restore its permanent static
        # placeholder. Do this before changing the current row so the old
        # countdown can never remain stuck after a seek/skip.
        if (
            self.scrolling_lyrics_enabled
            and previous_line != line_index
            and 0 <= previous_line < len(self.lyrics)
            and getattr(self, "empty_timestamp_placeholder_enabled", True)
            and hasattr(self.scrolling_lyrics_widget, "set_placeholder_text")
        ):
            previous_text = self.lyrics[previous_line][1]
            if not str(previous_text).strip():
                previous_template = str(
                    getattr(
                        self,
                        "empty_timestamp_placeholder",
                        "♪ {countdown}",
                    )
                )
                previous_static = (
                    previous_template.replace("{countdown}", "")
                    .replace("  ", " ")
                    .strip()
                )
                self.scrolling_lyrics_widget.set_placeholder_text(
                    previous_line,
                    previous_static,
                )

        self.current_line = line_index

        if (
            is_empty_timestamp
            and getattr(self, "empty_timestamp_placeholder_enabled", True)
        ):
            next_lyric_time = None
            for next_timeline_index in range(timeline_index + 1, len(self.lyric_times)):
                next_line_index = self._playback_line_indices[next_timeline_index]
                next_text = self.lyrics[next_line_index][1]
                if str(next_text).strip():
                    next_lyric_time = self.lyric_times[next_timeline_index]
                    break

            if next_lyric_time is not None:
                remaining = max(0.0, next_lyric_time - position)
                countdown = f"{remaining:.1f}"
            else:
                countdown = ""

            template = getattr(
                self,
                "empty_timestamp_placeholder",
                "♪ {countdown}",
            )
            try:
                text = str(template).replace("{countdown}", countdown).strip()
            except Exception:
                text = f"♪ {countdown}".strip()

        if self.scrolling_lyrics_enabled:
            # The scrolling display needs its own temporary row text; otherwise
            # an empty timestamp is highlighted as a literal blank gap.
            if (
                is_empty_timestamp
                and getattr(self, "empty_timestamp_placeholder_enabled", True)
                and hasattr(self.scrolling_lyrics_widget, "set_placeholder_text")
            ):
                self.scrolling_lyrics_widget.set_placeholder_text(
                    self.current_line,
                    text,
                )

            self.scrolling_lyrics_widget.set_current_line(
                self.current_line,
                animate=(previous_line != self.current_line),
            )
        else:
            # Re-render on every line change, even when two consecutive lines
            # have identical text. The timestamp changed, so the highlight
            # animation should still run.
            self.last_lyric_text = text

            line = self.lyric_lines[self.current_line]
            if line.segments:
                line_end = getattr(line, "end", None)
                if line_end is None and self.current_line + 1 < len(self.lyric_lines):
                    line_end = self.lyric_lines[self.current_line + 1].start
                self.lyric_widget.set_segments(
                    line.text,
                    line.segments,
                    line_end,
                )
            else:
                self.lyric_widget.set_lyric(text)

            self.lyric_widget.set_current_line(True)
            self.highlight_current_lyric()

    def update_lyrics_list(self):
        self.lyrics_list_widget.clear()
        for idx, (_, text) in enumerate(self.lyrics):
            display_text = text[:80] if len(text) > 80 else text
            item = QListWidgetItem(display_text)
            item.setData(Qt.ItemDataRole.UserRole, idx)
            self.lyrics_list_widget.addItem(item)

    def highlight_current_lyric(self):
        if 0 <= self.current_line < self.lyrics_list_widget.count():
            self.lyrics_list_widget.setCurrentRow(self.current_line)
            self.lyrics_list_widget.scrollToItem(
                self.lyrics_list_widget.item(self.current_line),
                QListWidget.ScrollHint.EnsureVisible,
            )

    def jump_to_lyric_item(self, item):
        idx = item.data(Qt.ItemDataRole.UserRole)
        if 0 <= idx < len(self.lyrics):
            timestamp = self.lyrics[idx][0]
            self.media_player.setPosition(int(timestamp * 1000))
