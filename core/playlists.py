"""Named local playlists and portable M3U files, preserving order and repeats."""

from dataclasses import dataclass
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit
from urllib.request import url2pathname

from core.file_io import atomic_write_text
from core.music_library import SUPPORTED_EXTENSIONS
from core.settings import load_all_settings, save_all_settings


def path_key(path):
    return os.path.normcase(str(Path(path).expanduser().resolve()))


def load_saved_playlists():
    stored = load_all_settings().get("saved_playlists", {})
    if not isinstance(stored, dict):
        return {}
    result = {}
    for name, paths in stored.items():
        if not isinstance(name, str) or not name.strip() or not isinstance(paths, list):
            continue
        if any(key.casefold() == name.strip().casefold() for key in result):
            continue
        result[name.strip()] = [path for path in paths if isinstance(path, str) and path.strip()]
    return result


def _playlist_name(name):
    name = str(name).strip()
    if not name:
        raise ValueError("Enter a playlist name.")
    return name


def _matching_name(playlists, name):
    return next((key for key in playlists if key.casefold() == name.casefold()), None)


def save_playlist(name, paths, *, overwrite=False):
    name = _playlist_name(name)
    playlists = load_saved_playlists()
    existing = _matching_name(playlists, name)
    if existing is not None and not overwrite:
        raise FileExistsError(f'A playlist named "{existing}" already exists.')
    if existing is not None:
        del playlists[existing]
    playlists[name] = [str(Path(path).expanduser().resolve()) for path in paths]
    payload = load_all_settings()
    payload["saved_playlists"] = playlists
    save_all_settings(payload)
    return name


def rename_playlist(old_name, new_name):
    new_name = _playlist_name(new_name)
    playlists = load_saved_playlists()
    if old_name not in playlists:
        raise ValueError("That playlist no longer exists.")
    existing = _matching_name(playlists, new_name)
    if existing is not None and existing != old_name:
        raise FileExistsError(f'A playlist named "{existing}" already exists.')
    playlists[new_name] = playlists.pop(old_name)
    payload = load_all_settings()
    payload["saved_playlists"] = playlists
    save_all_settings(payload)
    return new_name


def delete_playlist(name):
    playlists = load_saved_playlists()
    playlists.pop(name, None)
    payload = load_all_settings()
    payload["saved_playlists"] = playlists
    save_all_settings(payload)


@dataclass
class PlaylistImport:
    paths: list[Path]
    unavailable: list[str]


def available_tracks(paths):
    result = PlaylistImport([], [])
    for value in paths:
        try:
            path = Path(value).expanduser().resolve()
            if path.suffix.lower() in SUPPORTED_EXTENSIONS and path.is_file():
                result.paths.append(path)
                continue
        except (OSError, ValueError):
            pass
        result.unavailable.append(str(value))
    return result


def read_m3u(filename):
    filename = Path(filename).resolve()
    raw = filename.read_bytes()
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        if filename.suffix.lower() == ".m3u8":
            raise
        text = raw.decode("cp1252")
    result = PlaylistImport([], [])
    for line in text.splitlines():
        line = line.strip()
        if not line or line.startswith("#"):
            continue
        try:
            if line.lower().startswith("file:"):
                url = urlsplit(line)
                # url2pathname decodes escaped characters itself.
                local = url2pathname(url.path)
                if url.netloc and url.netloc.lower() != "localhost":
                    local = "//" + unquote(url.netloc) + local
                path = Path(local)
            elif "://" in line:
                result.unavailable.append(line)
                continue
            else:
                path = Path(line)
            if not path.is_absolute():
                path = filename.parent / path
            imported = available_tracks([path])
            result.paths.extend(imported.paths)
            if imported.unavailable:
                result.unavailable.append(line)
        except (OSError, ValueError):
            result.unavailable.append(line)
    return result


def write_m3u(filename, paths):
    filename = Path(filename).resolve()
    lines = ["#EXTM3U"]
    for value in paths:
        path = Path(value).expanduser().resolve()
        try:
            entry = os.path.relpath(path, filename.parent)
        except ValueError:  # Different Windows drives cannot be relative.
            entry = str(path)
        if "\n" in entry or "\r" in entry:
            raise ValueError("M3U cannot store filenames containing line breaks.")
        if entry.startswith("#"):
            entry = "." + os.sep + entry
        lines.append(entry)
    atomic_write_text(filename, "\n".join(lines) + "\n")
