from pathlib import Path
import os
import re
from lyrics.lrcx_parser import get_lrcx_offset
from bisect import bisect_right

from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QObject, Signal, QTimer
from PySide6.QtWidgets import QListWidget, QListWidgetItem, QMessageBox, QCheckBox

from lyrics.loader import load_lyrics, get_lyrics_generation_mode
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


class _FuriganaProgressBridge(QObject):
    progress = Signal(int, int, int)
    finished = Signal(int)


_FURIGANA_PREFETCH_EXECUTOR = ThreadPoolExecutor(
    max_workers=1, thread_name_prefix="LyricsPlus-FuriganaPrefetch"
)

class LyricsController:
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
            self.lyric_widget.set_segments(
                line.text,
                line.segments,
                getattr(line, "end", None),
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
                    self.lyric_widget.set_segments(
                        line.text,
                        line.segments,
                        getattr(line, "end", None),
                    )
                else:
                    self.lyric_widget.set_lyric(current_text)

            self.highlight_current_lyric()

        self._last_scrolling_lyrics_enabled = self.scrolling_lyrics_enabled

    @staticmethod
    def _normalise_lyrics_name(value):
        """Normalise lyric/audio filenames for forgiving matching."""
        value = str(value).casefold()

        # Remove leading track numbers: 01 - Song, 01. Song, 01) Song.
        value = re.sub(r"^\s*\d+\s*[-._)]\s*", "", value)

        # Remove bracketed release labels such as [Lyrics] or (Official Audio).
        value = re.sub(r"[\(\[\{].*?[\)\]\}]", " ", value)

        # Remove common filename noise.
        value = re.sub(
            r"\b(official|lyrics?|audio|video|music|mv|full|hd|4k)\b",
            " ",
            value,
        )

        # Treat dots, dashes and underscores as separators.
        value = re.sub(r"[_\-.]+", " ", value)

        # Ignore remaining punctuation and collapse whitespace.
        value = re.sub(r"[^\w\s]", "", value)
        return " ".join(value.split())

    @staticmethod
    def _extract_lyrics_title(value):
        """Extract the likely song-title portion from an Artist - Title filename."""
        value = str(value).strip()

        # Prefer the conventional artist/title separator. Keep the split to
        # one occurrence so titles containing hyphens survive.
        #
        # This lets a plain audio filename such as:
        #     Song Name.mp3
        # match a lyrics filename such as:
        #     Artist - Song Name.lrc
        # while still supporting en/em dashes.
        parts = re.split(r"\s+[-–—]\s+", value, maxsplit=1)
        if len(parts) == 2 and parts[1].strip():
            return parts[1].strip()

        return value

    @classmethod
    def _score_lyrics_candidate(cls, audio_path, lyrics_path):
        """Score how confidently a lyrics file belongs to an audio file."""
        audio_stem = audio_path.stem.casefold()
        lyrics_stem = lyrics_path.stem.casefold()
        audio_name = cls._normalise_lyrics_name(audio_path.stem)
        lyrics_name = cls._normalise_lyrics_name(lyrics_path.stem)

        # Split Artist - Title / Title - Artist before doing any directional
        # matching. Comparing the normalized halves as an unordered set means:
        #
        #   Title - Artist
        #   Artist - Title
        #
        # are considered the same filename.
        def split_parts(path):
            return [
                cls._normalise_lyrics_name(part)
                for part in re.split(
                    r"\s+[-–—]\s+",
                    path.stem,
                    maxsplit=1,
                )
                if part.strip()
            ]

        audio_parts = split_parts(audio_path)
        lyrics_parts = split_parts(lyrics_path)

        if (
            len(audio_parts) == 2
            and len(lyrics_parts) == 2
            and set(audio_parts) == set(lyrics_parts)
        ):
            return 900 + (
                100 if lyrics_path.parent == audio_path.parent else 0
            )

        score = 0

        if lyrics_stem == audio_stem:
            score += 1000
        elif audio_name and lyrics_name and audio_name == lyrics_name:
            score += 800
        else:
            audio_title = cls._normalise_lyrics_name(
                cls._extract_lyrics_title(audio_path.stem)
            )
            lyrics_title = cls._normalise_lyrics_name(
                cls._extract_lyrics_title(lyrics_path.stem)
            )

            if audio_title and lyrics_title and audio_title == lyrics_title:
                score += 700
            elif audio_name and lyrics_title and audio_name == lyrics_title:
                score += 650
            elif audio_title and lyrics_name and audio_title == lyrics_name:
                score += 650
            elif audio_name and lyrics_name:
                if audio_name in lyrics_name or lyrics_name in audio_name:
                    score += 300

        if lyrics_path.parent == audio_path.parent:
            score += 100

        return score

    @staticmethod
    def _build_lyrics_search_folders(audio_path, configured_folders=None):
        """
        Build the lyrics search list.

        The audio file's own folder is always searched.
        Every other folder must be explicitly configured by the user.
        No folder names are guessed automatically.
        """
        audio_path = Path(audio_path)
        folders = []
        seen = set()

        def add(folder):
            folder = Path(folder)
            try:
                key = os.path.normcase(
                    os.path.abspath(os.path.normpath(str(folder)))
                )
            except (OSError, ValueError):
                return

            if key not in seen:
                seen.add(key)
                folders.append(folder)

        add(audio_path.parent)

        for folder in configured_folders or []:
            add(folder)

        return folders

    @classmethod
    def _normalise_path_key(cls, path):
        return os.path.normcase(
            os.path.abspath(os.path.normpath(str(path)))
        )

    @classmethod
    def _split_lyrics_filename_parts(cls, path):
        return [
            cls._normalise_lyrics_name(part)
            for part in re.split(r"\s+[-–—]\s+", Path(path).stem, maxsplit=1)
            if part.strip()
        ]

    @classmethod
    def _get_lyrics_folder_index(cls, folder_path):
        """Return a cached index for one lyrics folder.

        The expensive recursive walk and [attach:] file reads happen only the
        first time a folder is encountered during this app session. Later song
        changes use in-memory dictionaries and Path objects.
        """
        folder_path = Path(folder_path)
        folder_key = cls._normalise_path_key(folder_path)
        cached = cls._lyrics_folder_index_cache.get(folder_key)
        if cached is not None:
            return cached

        entries = []
        by_name = {}
        by_title = {}
        by_parts = {}
        attachments = {}

        if folder_path.exists() and folder_path.is_dir():
            try:
                for file_path in folder_path.rglob("*"):
                    if not file_path.is_file() or file_path.suffix.casefold() not in {".lrc", ".lrcx"}:
                        continue

                    name = cls._normalise_lyrics_name(file_path.stem)
                    title = cls._normalise_lyrics_name(
                        cls._extract_lyrics_title(file_path.stem)
                    )
                    parts = cls._split_lyrics_filename_parts(file_path)
                    parts_key = frozenset(parts) if len(parts) == 2 else None

                    entry = (file_path, name, title, parts_key)
                    entries.append(entry)
                    if name:
                        by_name.setdefault(name, []).append(entry)
                    if title:
                        by_title.setdefault(title, []).append(entry)
                    if parts_key:
                        by_parts.setdefault(parts_key, []).append(entry)

                    # Attachment tags are rare, but indexing them here avoids
                    # reopening every lyrics file on every song change.
                    try:
                        text = file_path.read_text(encoding="utf-8")
                    except (OSError, UnicodeError):
                        continue

                    match = re.search(r"(?mi)^\[attach:(.*?)\]\s*$", text)
                    if match:
                        target = cls._normalise_path_key(match.group(1).strip())
                        attachments.setdefault(target, []).append(file_path)
            except OSError:
                pass

        index = {
            "entries": entries,
            "by_name": by_name,
            "by_title": by_title,
            "by_parts": by_parts,
            "attachments": attachments,
        }
        cls._lyrics_folder_index_cache[folder_key] = index
        print(f"[Lyrics] Indexed {len(entries)} lyric files: {folder_path}")
        return index

    @classmethod
    def clear_lyrics_search_index(cls):
        """Forget cached lyric-folder indexes.

        Call this after changing lyrics files/folders at runtime if immediate
        rediscovery is needed without restarting the application.
        """
        cls._lyrics_folder_index_cache.clear()
        print("[Lyrics] Cleared lyric search index")

    def find_lrc_for_audio(self, audio_path):
        """Find matching lyrics using cached per-folder indexes."""
        audio_path = Path(audio_path)
        wanted = self._normalise_path_key(audio_path)

        # Persisted explicit association is always authoritative.
        attached = get_lyrics_attachment(audio_path)
        if attached:
            attached_path = Path(attached)
            if attached_path.exists() and attached_path.is_file():
                print(f"[Lyrics] Using forced lyrics association: {attached_path}")
                return attached_path
            print(f"[Lyrics] Forced lyrics file is missing: {attached_path}")

        folders = self._build_lyrics_search_folders(
            audio_path,
            load_lrc_search_folders(),
        )
        print(f"[Lyrics] Search folders: {[str(folder) for folder in folders]}")

        indexes = [self._get_lyrics_folder_index(folder) for folder in folders]

        # 1. Explicit [attach:] tags are now dictionary lookups.
        for index in indexes:
            attached_paths = index["attachments"].get(wanted)
            if attached_paths:
                attached_path = attached_paths[0]
                print(f"[Lyrics] Using attached lyrics: {attached_path}")
                return attached_path

        # 2. Pull likely candidates directly from filename indexes.
        audio_name = self._normalise_lyrics_name(audio_path.stem)
        audio_title = self._normalise_lyrics_name(
            self._extract_lyrics_title(audio_path.stem)
        )
        audio_parts = self._split_lyrics_filename_parts(audio_path)
        audio_parts_key = frozenset(audio_parts) if len(audio_parts) == 2 else None

        candidates = {}
        for index in indexes:
            for entry in index["by_name"].get(audio_name, []):
                candidates[self._normalise_path_key(entry[0])] = entry[0]
            if audio_title:
                for entry in index["by_title"].get(audio_title, []):
                    candidates[self._normalise_path_key(entry[0])] = entry[0]
                # A plain song filename can match the full normalized name.
                for entry in index["by_name"].get(audio_title, []):
                    candidates[self._normalise_path_key(entry[0])] = entry[0]
            if audio_parts_key:
                for entry in index["by_parts"].get(audio_parts_key, []):
                    candidates[self._normalise_path_key(entry[0])] = entry[0]

        # Preserve the old substring fallback for unusual filenames. This is
        # in-memory now: no additional directory walk or file reads.
        if not candidates:
            for index in indexes:
                for file_path, name, title, _parts_key in index["entries"]:
                    if not audio_name:
                        continue
                    if (
                        audio_name in name or name in audio_name
                        or (title and (audio_name in title or title in audio_name))
                    ):
                        candidates[self._normalise_path_key(file_path)] = file_path

        ranked = []
        for file_path in candidates.values():
            score = self._score_lyrics_candidate(audio_path, file_path)
            if score > 0:
                ranked.append((score, file_path))

        if ranked:
            ranked.sort(
                key=lambda item: (
                    -item[0],
                    0 if item[1].suffix.casefold() == ".lrcx" else 1,
                    0 if item[1].parent == audio_path.parent else 1,
                    str(item[1]).casefold(),
                )
            )
            best_score, best_path = ranked[0]
            if best_score >= 300:
                print(f"[Lyrics] Best automatic match ({best_score}): {best_path}")
                return best_path

        print(f"[Lyrics] No matching lyrics found for: {audio_path.name}")
        return audio_path.with_suffix(".lrc")

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
            return

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
                self.lyric_widget.set_segments(
                    line.text,
                    line.segments,
                    getattr(line, "end", None),
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
