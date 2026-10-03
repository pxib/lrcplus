from pathlib import Path

from lyrics.lrcx_parser import load_lrcx, get_lrcx_generation_mode
from lyrics.models import LyricLine
from lyrics.parser import load_lrc, get_lrc_generation_mode


def load_lyrics(path):
    """Load .lrc or .lrcx and return normalized LyricLine objects."""
    path = Path(path)

    if path.suffix.lower() == ".lrcx":
        return load_lrcx(path)

    if path.suffix.lower() == ".lrc":
        return [LyricLine(start=timestamp, text=text) for timestamp, text in load_lrc(path)]

    if path.suffix.lower() == ".txt":
        with open(path, "r", encoding="utf-8-sig") as file:
            return [LyricLine(start=float(index), text=line.rstrip("\r\n"))
                    for index, line in enumerate(file) if line.rstrip("\r\n")]

    raise ValueError(f"Unsupported lyrics format: {path.suffix}")



def get_lyrics_generation_mode(path):
    """Return persisted generated-reading mode for an LRC/LRCX document."""
    path = Path(path)
    suffix = path.suffix.lower()
    if suffix == ".lrcx":
        return get_lrcx_generation_mode(path)
    if suffix == ".lrc":
        return get_lrc_generation_mode(path)
    return None
