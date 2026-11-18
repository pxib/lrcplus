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


@dataclass(frozen=True)
class SourceIssue:
    line_number: int
    message: str
    column: int = 0
    length: int = 1


def effective_source_suffix(text, suffix):
    if suffix == ".lrc" and (
        re.search(r"(?mi)^\[format:LRCX\]", text)
        or re.search(r"<\d+:\d+(?:\.\d+)?>|\{[^{}|]+\|[^{}]+\}", text)
    ):
        return ".lrcx"
    return suffix


def validate_editor_source(text, suffix):
    """Collect source errors without parsing files or mutating the document."""
    if suffix not in {".lrc", ".lrcx", ".txt"}:
        raise ValueError("Unsupported source format.")
    if suffix == ".txt":
        return []
    issues = []
    for number, physical_line in enumerate(text.splitlines(), 1):
        line = physical_line.strip()
        if not line:
            continue
        indent = len(physical_line) - len(physical_line.lstrip())

        def report(message, column=0, length=None):
            issues.append(SourceIssue(number, message, indent + column,
                                      len(line) if length is None else length))

        if re.fullmatch(r"\[[A-Za-z][A-Za-z0-9_]*:.*\]", line):
            if line.lower().startswith("[offset:"):
                try:
                    if not math.isfinite(float(line[8:-1])):
                        raise ValueError
                except ValueError:
                    report("invalid lyric offset.")
            continue
        timestamps = re.match(r"(?:\[\d+:\d+(?:\.\d+)?\])+", line)
        if timestamps is None:
            report("expected a timestamp such as [00:12.500].")
            continue
        if suffix == ".lrcx" and timestamps.group().count("[") > 1:
            report("use a separate LRCX line for each timestamp.", length=timestamps.end())
        for stamp in re.finditer(r"\[(\d+:\d+(?:\.\d+)?)\]|<(\d+:\d+(?:\.\d+)?)>", line):
            try:
                parse_timestamp(stamp.group(1) or stamp.group(2))
            except ValueError as error:
                report(str(error), stamp.start(), len(stamp.group()))
        if suffix == ".lrcx":
            for token in re.finditer(r"<\d+:[^<>]*(?:>|(?=<)|$)", line):
                if not re.fullmatch(r"<\d+:\d+(?:\.\d+)?>", token.group()):
                    report("invalid karaoke timestamp; use <00:12.500>.",
                           token.start(), len(token.group()))
            for ruby in re.finditer(r"\{[^{}\n]*\|[^{}\n]*(?:\}|(?=\{)|$)", line):
                if not ruby.group().endswith("}"):
                    report("unclosed reading; use {text|reading}.", ruby.start(), len(ruby.group()))
    return issues


def extract_metadata(text):
    """Keep portable metadata that the structured model does not otherwise store."""
    reserved = {"format", "version", "g", "offset", "attach", "reading_groups"}
    result = []
    for line in text.splitlines():
        match = re.fullmatch(r"\[([A-Za-z][A-Za-z0-9_]*):.*\]", line.strip())
        if match and match.group(1).casefold() not in reserved:
            result.append(line.strip())
    return result


def parse_editor_source(text, suffix):
    issues = validate_editor_source(text, suffix)
    if issues:
        first = issues[0]
        raise ValueError(f"Source line {first.line_number}: {first.message}")
    with tempfile.TemporaryDirectory(prefix="lyrics-source-") as directory:
        path = Path(directory) / ("source" + suffix)
        path.write_text(text, encoding="utf-8")
        lines = load_lyrics(path, infer_ends=False)
        mode = get_lyrics_generation_mode(path)
        offset = get_lrcx_offset(path) if suffix != ".txt" else 0.0
    attachment = re.search(r"(?mi)^\[attach:(.*?)\]\s*$", text) if suffix != ".txt" else None
    attachment_path = (attachment.group(1).strip() or None) if attachment else None
    return EditorSource(lines, offset, mode, attachment_path,
                        extract_metadata(text) if suffix != ".txt" else [])
