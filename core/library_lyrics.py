"""Search local lyric text without generating readings or touching Qt widgets."""

from collections import OrderedDict
from dataclasses import dataclass
from pathlib import Path
import re

from core.lyrics_search import LyricsSearchMixin
from core.settings import load_all_settings, load_lrc_search_folders
from lyrics.loader import load_lyrics


def normalize_lyric_query(text):
    return " ".join(text.casefold().split())


@dataclass(frozen=True)
class LyricMatch:
    text: str
    start: int
    end: int


def _first_match(source, normalized, query):
    hit = normalized.find(query)
    if hit < 0:
        return None
    # Map the normalized match back to the original spelling. Case folding
    # can expand characters (for example, ß -> ss), and whitespace collapses.
    position = 0
    start = None
    for word in re.finditer(r"\S+", source):
        if position:
            position += 1
        for offset, char in enumerate(word.group(), word.start()):
            next_position = position + len(char.casefold())
            if position <= hit < next_position:
                start = offset
            if position < hit + len(query) <= next_position:
                end = offset + 1
                line_start = source.rfind("\n", 0, start) + 1
                line_end = source.find("\n", end)
                if line_end < 0:
                    line_end = len(source)
                # Keep long lines readable with the first match in view.
                left = max(line_start, start - 16)
                right = min(line_end, end + 80)
                prefix = "…" if left > line_start else ""
                suffix = "…" if right < line_end else ""
                text = prefix + source[left:right].replace("\n", " ").replace("\r", " ") + suffix
                return LyricMatch(text, start - left + len(prefix), end - left + len(prefix))
            position = next_position
    return None


class _SearchResolver(LyricsSearchMixin):
    _lyrics_folder_index_cache = {}

    def __init__(self):
        self._folders = {}

    def _get_lyrics_folder_index(self, folder):
        # Validate each folder once per search, rather than once per track.
        key = self._normalise_path_key(folder)
        if key not in self._folders:
            self._folders[key] = super()._get_lyrics_folder_index(folder)
        return self._folders[key]


class LibraryLyricsIndex:
    """Bounded text cache; used sequentially by the library's background job."""

    def __init__(self):
        self._texts = OrderedDict()

    def invalidate(self):
        # A rescan must also notice edits to [attach:] tags inside lyric files.
        _SearchResolver.clear_lyrics_search_index()

    def _text(self, path):
        path = Path(path)
        stamp = path.stat()
        signature = (stamp.st_mtime_ns, stamp.st_size)
        cached = self._texts.get(path)
        if cached is not None and cached[0] == signature:
            self._texts.move_to_end(path)
            return cached[1]
        source = "\n".join(line.text for line in load_lyrics(path))
        text = source, normalize_lyric_query(source)
        self._texts[path] = (signature, text)
        self._texts.move_to_end(path)
        while len(self._texts) > 5000:
            self._texts.popitem(last=False)
        return text

    def search(self, paths, query, cancel, current_lyrics=None):
        resolver = _SearchResolver()
        query = normalize_lyric_query(query)
        folders = load_lrc_search_folders()
        attachments = load_all_settings().get("lyrics_attachments", {})
        attachments = ({resolver._normalise_path_key(audio): str(lyric) if lyric else None
                        for audio, lyric in attachments.items()}
                       if isinstance(attachments, dict) else {})
        matches = {}
        for audio in paths:
            if cancel.is_set():
                break
            try:
                if current_lyrics and audio == current_lyrics[0]:
                    lyric = Path(current_lyrics[1])
                else:
                    lyric = resolver.find_lrc_for_audio(
                        audio, configured_folders=folders, attachments=attachments,
                    )
                    if not lyric.is_file():
                        # Plain lyrics can also live beside the audio file.
                        lyric = Path(audio).with_suffix(".txt")
                source, normalized = self._text(lyric)
                match = _first_match(source, normalized, query) if query else None
                if match is not None:
                    matches[audio] = match
            except (OSError, UnicodeError, ValueError, OverflowError):
                # Missing, unreadable or malformed lyrics do not hide matches
                # from the rest of the collection.
                continue
        return query, matches
