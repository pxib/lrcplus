"""Parse the editor's source view without silently discarding invalid lines."""

from dataclasses import dataclass
import math
from pathlib import Path
import re
import tempfile

from lyrics.loader import get_lyrics_generation_mode, load_lyrics
from lyrics.lrcx_parser import get_lrcx_offset
from lyrics.timing import parse_timestamp


@dataclass
class EditorSource:
    lines: list
    offset_ms: float
    generation_mode: str | None
    attachment: str | None
    metadata: list[str]


def extract_metadata(text):
    """Keep portable metadata that the structured model does not otherwise store."""
    reserved = {"format", "version", "g", "offset", "attach"}
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r"\[([A-Za-z][A-Za-z0-9_]*):.*\]", line.strip())
        if match and match.group(1).casefold() not in reserved:
            result.append(line.strip())
    return result


def parse_editor_source(text, suffix):
    if suffix not in {".lrc", ".lrcx", ".txt"}:
        raise ValueError("Unsupported source format.")
    if suffix != ".txt":
        for number, physical_line in enumerate(text.splitlines(), 1):
            line = physical_line.strip()
            if not line:
                continue
            if re.fullmatch(r"\[[A-Za-z][A-Za-z0-9_]*:.*\]", line):
                if line.lower().startswith("[offset:"):
                    try:
                        offset = float(line[8:-1])
                        if not math.isfinite(offset):
                            raise ValueError
                    except ValueError:
                        raise ValueError(f"Source line {number}: invalid lyric offset.") from None
                continue
            timestamps = re.match(r"(?:\[\d+:\d+(?:\.\d+)?\])+", line)
            if timestamps is None:
                raise ValueError(f"Source line {number}: expected a timestamp such as [00:12.500].")
            if suffix == ".lrcx" and timestamps.group().count("[") > 1:
                raise ValueError(f"Source line {number}: use a separate LRCX line for each timestamp.")
            for stamp in re.findall(r"\[(\d+:\d+(?:\.\d+)?)\]|<(\d+:\d+(?:\.\d+)?)>", line):
                try:
                    parse_timestamp(stamp[0] or stamp[1])
                except ValueError as error:
                    raise ValueError(f"Source line {number}: {error}") from error
    with tempfile.TemporaryDirectory(prefix="lyrics-source-") as directory:
        path = Path(directory) / ("source" + suffix)
        path.write_text(text, encoding="utf-8")
        lines = load_lyrics(path)
        mode = get_lyrics_generation_mode(path)
        offset = get_lrcx_offset(path) if suffix != ".txt" else 0.0
    attachment = re.search(r"(?mi)^\[attach:(.*?)\]\s*$", text) if suffix != ".txt" else None
    attachment_path = (attachment.group(1).strip() or None) if attachment else None
    return EditorSource(lines, offset, mode, attachment_path,
                        extract_metadata(text) if suffix != ".txt" else [])
