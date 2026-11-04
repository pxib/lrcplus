"""Detached lyric documents for translation plugins; timing stays host-owned."""
from pathlib import Path

from lyrics.editor_source import extract_metadata
from lyrics.loader import get_lyrics_generation_mode, load_lyrics
from lyrics.lrcx_parser import get_lrcx_offset, parse_lrcx
from lyrics.models import LyricSegment
from lyrics.writer import serialize_lrcx


def snapshot_document(audio_path, lyrics_path):
    if not audio_path or not lyrics_path:
        raise ValueError("Open a song with timed lyrics first.")
    path = Path(lyrics_path).resolve()
    if path.suffix.lower() not in {".lrc", ".lrcx"}:
        raise ValueError("Translation requires timed LRC or LRCX lyrics.")
    lines = load_lyrics(path, infer_ends=False)
    if not lines:
        raise ValueError("The lyrics file contains no timed lyrics.")
    source = path.read_text(encoding="utf-8-sig")
    offset = get_lrcx_offset(path)
    mode = get_lyrics_generation_mode(path)
    return document_from_lines(lines, audio_path, path, offset, mode, extract_metadata(source))


def document_from_lines(lines, audio_path, lyrics_path, offset=0, mode=None, metadata=()):
    """Capture in-memory editor lyrics, including edits not yet saved."""
    return {
        "audio_path": str(Path(audio_path).resolve()) if audio_path else "",
        "lyrics_path": str(Path(lyrics_path).resolve()),
        "lrcx": serialize_lrcx(lines, offset_ms=offset, generation_mode=mode),
        "metadata": list(metadata), "offset_ms": offset, "generation_mode": mode,
        "lines": [{"index": index, "start_ms": round(line.start * 1000),
                   "text": "\n".join(text for row, text in enumerate(line.text.split("\n"))
                                     if row not in line.translation_lines),
                   "has_translation": bool(line.translation_lines)}
                  for index, line in enumerate(lines)],
    }


def render_translations(document, translations):
    """Translations use source row indices, including duplicate timestamps."""
    lines = parse_lrcx(document["lrcx"], infer_ends=False)
    for index, text in translations.items():
        if type(index) is not int or not 0 <= index < len(lines):
            raise ValueError("Translation refers to an unknown lyric row.")
        if not isinstance(text, str) or not text.strip():
            raise ValueError("Translation text cannot be empty.")
        line = lines[index]
        if line.translation_lines:
            raise ValueError("This lyric row already has a translation.")
        text = text.strip().replace("\r\n", "\n").replace("\r", "\n")
        first_row = line.text.count("\n") + 1
        line.translation_lines = list(range(first_row, first_row + text.count("\n") + 1))
        line.text += "\n" + text
        if line.has_karaoke:
            line.segments.extend([
                LyricSegment(line.start, "\n", visual_line=first_row - 1),
                LyricSegment(line.start, text, instant=True, end=line.end, visual_line=first_row),
            ])
        else:
            line.segments.append(LyricSegment(line.start, "\n" + text))
    content = serialize_lrcx(lines, offset_ms=document.get("offset_ms", 0),
                             generation_mode=document.get("generation_mode"))
    metadata = document.get("metadata", [])
    return "\n".join(metadata) + "\n" + content if metadata else content
