"""VTT/SRT captions converted to line-timed lyrics without external libraries."""
from dataclasses import dataclass, replace
import html
from pathlib import Path
import re


@dataclass(frozen=True)
class Cue:
    start: int
    end: int
    text: str


TIME = r"(?:\d+:)?\d{2}:\d{2}[.,]\d{3}"
TIMING = re.compile(rf"^({TIME})\s*-->\s*({TIME})(?:\s.*)?$")
MAX_CONVERTED_LINES = 5000


def caption_style_warning(text):
    if (re.search(r"(?im)^\s*\[(?:Script Info|V4\+? Styles)\]\s*$", text)
            or re.search(r"\{\\(?:an\d|pos\(|move\(|fad\(|k[fo]?\d|[1-4]?c&H)", text, re.I)):
        return ("ASS/SSA-style subtitles detected. Converting them to synced lyrics removes styling, "
                "positioning and karaoke effects. Review the generated lyrics.")
    if re.search(r"(?:<c\.color[0-9a-f]{6}\b|::cue\(c\.color[0-9a-f]{6}\))", text, re.I):
        return ("Styled captions detected, possibly converted from ASS/SSA. Repeated outline/color redraws "
                "will be merged and visual effects removed. Review the generated lyrics.")
    return ""


def timestamp_ms(value):
    parts = value.replace(",", ".").split(":")
    seconds, millis = parts[-1].split(".")
    minutes = int(parts[-2])
    if int(seconds) >= 60 or (len(parts) == 3 and minutes >= 60):
        raise ValueError("Invalid subtitle timestamp.")
    hours = int(parts[0]) if len(parts) == 3 else 0
    return ((hours * 60 + minutes) * 60 + int(seconds)) * 1000 + int(millis)


def clean_text(value):
    value = re.sub(r"<br\s*/?>", "\n", value, flags=re.I)
    value = re.sub(r"<[^>]*>", "", value)
    value = html.unescape(value).replace("\u200b", "")
    return "\n".join(" ".join(line.split()) for line in value.splitlines() if line.strip())


def parse_captions(text):
    cues = []
    text = text.lstrip("\ufeff").replace("\r\n", "\n").replace("\r", "\n")
    for block in re.split(r"\n[ \t]*\n", text):
        lines = block.strip().splitlines()
        if not lines or re.match(r"^(NOTE(?:\s|$)|STYLE$|REGION$|WEBVTT(?:\s|$))", lines[0]):
            continue
        # Some styled exports place successive cues without blank separators.
        # Stop each payload at its next timing line rather than incorporating
        # every later cue (and its timestamp) into the first lyric.
        boundaries = [(index, match) for index, line in enumerate(lines)
                      if (match := TIMING.fullmatch(line.strip()))]
        for position, (index, match) in enumerate(boundaries):
            try:
                start, end = map(timestamp_ms, match.groups())
            except ValueError:
                continue
            stop = boundaries[position + 1][0] if position + 1 < len(boundaries) else len(lines)
            value = clean_text("\n".join(lines[index + 1:stop]))
            if end > start and value:
                cues.append(Cue(start, end, value))
    if not cues:
        raise ValueError("No readable timed cues were found in the subtitles.")
    return sorted(cues, key=lambda cue: cue.start)


def coalesce_caption_redraws(cues):
    """Discard outline layers and unchanged frame updates, including uploaded CC.

    Preserve distinct lines at the same timestamp and repetitions after a gap.
    Only the text's continuous visible interval is combined; source files are
    left intact. Color/position markup has already been removed.
    """
    grouped = {}
    for cue in cues:
        text = cue.text
        lines = text.splitlines()
        # Vertical CJK captions commonly contain one character per physical
        # line. Keep the phrase together, including repeated characters.
        cjk = r"[\u3040-\u30ff\u3400-\u9fff\uf900-\ufaff\uac00-\ud7af]"
        if (len(lines) >= 3 and all(len(line) == 1 for line in lines)
                and any(re.fullmatch(cjk, line) for line in lines)
                and all(re.fullmatch(cjk, line) or not line.isalnum() for line in lines)):
            text = "".join(lines)
        grouped.setdefault((cue.start, cue.end), {}).setdefault(text, None)
    result = []
    last_by_text = {}
    for (start, end), texts in sorted(grouped.items()):
        text = "\n".join(texts)
        previous_index = last_by_text.get(text)
        if previous_index is not None and start <= result[previous_index].end + 1:
            previous = result[previous_index]
            result[previous_index] = replace(previous, end=max(previous.end, end))
        else:
            last_by_text[text] = len(result)
            result.append(Cue(start, end, text))
    return result


def clean_rolling_captions(cues):
    result = []
    for cue in cues:
        if result and cue.start < result[-1].end:
            previous = result[-1]
            if cue.text == previous.text:
                result[-1] = replace(previous, end=max(previous.end, cue.end))
                continue
            if "\n" not in previous.text and cue.text.startswith(previous.text + " "):
                result[-1] = Cue(previous.start, max(previous.end, cue.end), cue.text)
                continue
            old, new = previous.text.splitlines(), cue.text.splitlines()
            for count in range(min(len(old), len(new)), 0, -1):
                if old[-count:] == new[:count]:
                    remaining = "\n".join(new[count:])
                    if not remaining:
                        result[-1] = replace(previous, end=max(previous.end, cue.end))
                        break
                    cue = replace(cue, text=remaining)
                    break
            else:
                result.append(cue)
                continue
            if not remaining:
                continue
        result.append(cue)
    return result


def lrc_stamp(milliseconds):
    minutes, remainder = divmod(milliseconds, 60_000)
    seconds, millis = divmod(remainder, 1000)
    return f"[{minutes:02d}:{seconds:02d}.{millis:03d}]"


def captions_to_lrc(text, *, automatic=False):
    cues = coalesce_caption_redraws(parse_captions(text))
    if automatic:
        cues = clean_rolling_captions(cues)
    lines = []
    for index, cue in enumerate(cues):
        for line in cue.text.splitlines():
            # Literal timestamp-like text must not become an extra LRC tag.
            line = re.sub(r"\[(\d+:\d+(?:\.\d+)?)\]", r"［\1］", line)
            lines.append(lrc_stamp(cue.start) + line)
        if index + 1 == len(cues) or cue.end < cues[index + 1].start:
            lines.append(lrc_stamp(cue.end))
    if len(lines) > MAX_CONVERTED_LINES:
        raise ValueError(f"Caption conversion produced {len(lines):,} lyric rows; the limit is {MAX_CONVERTED_LINES:,}. "
                         "Keep the original subtitles and review them before importing lyrics.")
    return "\n".join(lines) + "\n"


def convert_caption_file(source, target, *, automatic=False):
    raw = Path(source).read_bytes()
    if raw.startswith((b"\xff\xfe", b"\xfe\xff")):
        text = raw.decode("utf-16")
    else:
        try:
            text = raw.decode("utf-8-sig")
        except UnicodeDecodeError:
            text = raw.decode("cp1252")
    Path(target).write_text(captions_to_lrc(text, automatic=automatic), encoding="utf-8")
