"""Basic ASS/SSA subtitle karaoke importer for LyricsPlus."""
from pathlib import Path
import re

from .models import LyricLine, LyricSegment

_KARAOKE_TAG = re.compile(r"\\(?:k|K|kf|ko)(\d+)")
_OVERRIDE = re.compile(r"\{([^}]*)\}")

def parse_ass_timestamp(value: str) -> float:
    value = value.strip()
    hours, minutes, seconds = value.split(":")
    return int(hours) * 3600 + int(minutes) * 60 + float(seconds)

def _strip_override_tags(text: str) -> str:
    return _OVERRIDE.sub("", text).replace(r"\N", "\n").replace(r"\n", "\n").replace(r"\h", " ")

def parse_ass(path_or_text):
    """Return LyricLine objects from ASS/SSA Dialogue events.

    Supports \\k, \\K, \\kf and \\ko durations (centiseconds). Other ASS
    styling is stripped while visible text is retained.
    """
    if isinstance(path_or_text, (str, Path)):
        candidate = Path(path_or_text)
        if candidate.exists():
            source = candidate.read_text(encoding="utf-8-sig", errors="replace")
        else:
            source = str(path_or_text)
    else:
        source = str(path_or_text)

    lines = []
    in_events = False
    for raw in source.splitlines():
        stripped = raw.strip()
        if stripped.lower() == "[events]":
            in_events = True
            continue
        if stripped.startswith("[") and stripped.endswith("]"):
            in_events = False
            continue
        if not in_events or not stripped.lower().startswith("dialogue:"):
            continue

        payload = stripped.split(":", 1)[1].lstrip()
        fields = payload.split(",", 9)
        if len(fields) < 10:
            continue
        try:
            start = parse_ass_timestamp(fields[1])
        except Exception:
            continue
        text = fields[9]

        # Split on override blocks, carrying a karaoke duration onto the
        # immediately following visible text.
        pending_duration = None
        cursor = 0
        segments = []
        current_time = start
        saw_karaoke = False
        for match in _OVERRIDE.finditer(text):
            visible = _strip_override_tags(text[cursor:match.start()])
            if visible:
                seg_start = current_time if pending_duration is not None else start
                segments.append(LyricSegment(seg_start, visible))
                if pending_duration is not None:
                    current_time = seg_start + pending_duration / 100.0
                    pending_duration = None
            tags = match.group(1)
            karaoke = _KARAOKE_TAG.search(tags)
            if karaoke:
                pending_duration = int(karaoke.group(1))
                saw_karaoke = True
            cursor = match.end()

        visible = _strip_override_tags(text[cursor:])
        if visible:
            seg_start = current_time if pending_duration is not None else (
                current_time if saw_karaoke else start
            )
            segments.append(LyricSegment(seg_start, visible))
            if pending_duration is not None:
                current_time = seg_start + pending_duration / 100.0

        if not segments:
            plain = _strip_override_tags(text)
            segments = [LyricSegment(start, plain)]

        lyric_text = "".join(segment.text for segment in segments)
        line = LyricLine(start=start, text=lyric_text, segments=segments)
        line.has_karaoke = saw_karaoke and len(segments) > 0
        lines.append(line)

    return lines
