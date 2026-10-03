from __future__ import annotations

from dataclasses import dataclass, asdict
from pathlib import Path
from typing import Iterable
import os
from collections import OrderedDict

try:
    from mutagen import File as MutagenFile
except Exception:  # pragma: no cover
    MutagenFile = None

SUPPORTED_EXTENSIONS = {
    '.mp3', '.flac', '.m4a', '.aac', '.ogg', '.opus', '.wav', '.wma', '.aiff', '.ape'
}


@dataclass
class LibraryTrack:
    path: str
    title: str
    artist: str = ''
    album: str = ''
    album_artist: str = ''
    date: str = ''
    track_number: str = ''
    disc_number: str = ''
    duration: float = 0.0
    codec: str = ''
    bitrate: int = 0
    file_size: int = 0
    modified: float = 0.0

    @property
    def display_title(self) -> str:
        return self.title or Path(self.path).stem

    def to_dict(self):
        return asdict(self)

    @classmethod
    def from_dict(cls, data):
        values = {field: data.get(field, getattr(cls, field, None)) for field in cls.__dataclass_fields__}
        return cls(**values)


# Reuse metadata during repeated rescans when the file is unchanged.
_TRACK_CACHE: OrderedDict[str, tuple[int, int, LibraryTrack]] = OrderedDict()
_TRACK_CACHE_LIMIT = 5000


def _first(tags, *keys):
    if not tags:
        return ''
    for key in keys:
        value = tags.get(key)
        if value is None:
            continue
        if isinstance(value, (list, tuple)):
            value = value[0] if value else ''
        return str(value).strip()
    return ''


def _track_number(value: str) -> str:
    return str(value or '').split('/', 1)[0].strip()


def read_track(path: str | Path) -> LibraryTrack:
    path = Path(path)
    stat = path.stat()
    resolved = str(path.resolve())
    cached = _TRACK_CACHE.get(resolved)
    if cached is not None and cached[0] == stat.st_mtime_ns and cached[1] == stat.st_size:
        _TRACK_CACHE.move_to_end(resolved)
        return cached[2]
    title = path.stem
    artist = album = album_artist = date = track_number = disc_number = ''
    duration = 0.0
    codec = path.suffix.lstrip('.').upper()
    bitrate = 0

    if MutagenFile is not None:
        try:
            audio = MutagenFile(path, easy=True)
            if audio is not None:
                tags = getattr(audio, 'tags', None)
                title = _first(tags, 'title') or title
                artist = _first(tags, 'artist')
                album = _first(tags, 'album')
                album_artist = _first(tags, 'albumartist', 'album artist')
                date = _first(tags, 'date', 'year')
                track_number = _track_number(_first(tags, 'tracknumber'))
                disc_number = _track_number(_first(tags, 'discnumber'))
                info = getattr(audio, 'info', None)
                if info is not None:
                    duration = float(getattr(info, 'length', 0.0) or 0.0)
                    raw_bitrate = getattr(info, 'bitrate', 0) or 0
                    bitrate = int(raw_bitrate / 1000) if raw_bitrate else 0
                    codec = type(audio).__name__.replace('Info', '') or codec
        except Exception:
            pass

    track = LibraryTrack(
        path=resolved, title=title, artist=artist, album=album,
        album_artist=album_artist, date=date, track_number=track_number,
        disc_number=disc_number, duration=duration, codec=codec,
        bitrate=bitrate, file_size=stat.st_size, modified=stat.st_mtime,
    )
    _TRACK_CACHE[resolved] = (stat.st_mtime_ns, stat.st_size, track)
    _TRACK_CACHE.move_to_end(resolved)
    while len(_TRACK_CACHE) > _TRACK_CACHE_LIMIT:
        _TRACK_CACHE.popitem(last=False)
    return track


def scan_folders(folders: Iterable[str | Path]):
    tracks = []
    seen = set()
    for folder in folders:
        root = Path(folder).expanduser()
        if not root.is_dir():
            continue
        try:
            iterator = root.rglob('*')
            for path in iterator:
                if not path.is_file() or path.suffix.lower() not in SUPPORTED_EXTENSIONS:
                    continue
                try:
                    resolved = str(path.resolve())
                except OSError:
                    continue
                key = os.path.normcase(resolved)
                if key in seen:
                    continue
                seen.add(key)
                try:
                    tracks.append(read_track(path))
                except (OSError, ValueError):
                    continue
        except OSError:
            continue
    tracks.sort(key=lambda t: ((t.album_artist or t.artist).casefold(), t.album.casefold(), _track_sort_key(t.track_number), t.display_title.casefold()))
    return tracks


def _track_sort_key(value):
    try:
        return int(value)
    except (TypeError, ValueError):
        return 10**9
