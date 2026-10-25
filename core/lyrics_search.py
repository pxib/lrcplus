import os
import re
from pathlib import Path

from core.settings import get_lyrics_attachment, load_lrc_search_folders


class LyricsSearchMixin:
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
