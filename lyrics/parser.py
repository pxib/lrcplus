import re

GENERATION_MODE_RE = re.compile(
    r"^\[g:([a-z0-9][a-z0-9_.-]*)\]$",
    re.IGNORECASE,
)


def normalize_generation_mode(mode):
    mode = str(mode or "").strip().lower()
    aliases = {"jyut": "jyutping"}
    return aliases.get(mode, mode) or None


def get_lrc_generation_mode(path):
    """Return the document-level generated-reading mode stored as [g:mode]."""
    try:
        with open(path, "r", encoding="utf-8-sig") as file:
            for raw_line in file:
                match = GENERATION_MODE_RE.match(raw_line.strip())
                if match:
                    return normalize_generation_mode(match.group(1))
    except OSError:
        pass
    return None


def load_lrc(path):
    """Load an LRC file with occurrence-aware multi-timestamp expansion.

    Consecutive physical lines with the same timestamp sequence stay together
    at each timestamp occurrence. For example:

        [00:01.59][03:35.36]Lyric
        [00:01.59][03:35.36](reading)

    becomes:

        00:01.59 Lyric
        00:01.59 (reading)
        03:35.36 Lyric
        03:35.36 (reading)
    """
    timestamp_pattern = re.compile(r"\[(\d+):(\d+(?:\.\d+)?)\]")
    physical_lines = []

    with open(path, "r", encoding="utf-8-sig") as file:
        for raw_line in file:
            line = raw_line.rstrip("\r\n")
            matches = list(timestamp_pattern.finditer(line))
            if not matches:
                continue

            timestamps = tuple(
                int(match.group(1)) * 60 + float(match.group(2))
                for match in matches
            )
            lyric_text = line[matches[-1].end():].lstrip()
            physical_lines.append((timestamps, lyric_text))

    lyrics = []
    index = 0
    while index < len(physical_lines):
        timestamps = physical_lines[index][0]
        group = [physical_lines[index]]
        index += 1

        while (
            index < len(physical_lines)
            and physical_lines[index][0] == timestamps
        ):
            group.append(physical_lines[index])
            index += 1

        for timestamp in timestamps:
            for _timestamps, lyric_text in group:
                lyrics.append((timestamp, lyric_text))

    # Multi-timestamp lines are expanded occurrence-by-occurrence above.
    # The source order therefore cannot necessarily be chronological (for
    # example, [00:01.59][03:35.36] appears before a later physical line at
    # [00:08.91]). Sort the expanded timeline once here so every consumer
    # receives the same chronological order. Python's sort is stable, which
    # preserves the physical-line order for entries sharing a timestamp.
    lyrics.sort(key=lambda lyric: lyric[0])
    return lyrics
