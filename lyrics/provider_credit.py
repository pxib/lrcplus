"""Keep source links with imported lyrics without adding timed rows."""

from html import escape
import re


_SOURCE_URL = {
    "lrc.red": r"https://lrc\.red/s/[A-Z0-9]{12}",
    "Unison": r"https://unison\.betterlyrics\.org/song/[A-Za-z0-9_-]{11}",
    "AMLL TTML DB": r"https://github\.com/amll-dev/amll-ttml-db",
    "Better Lyrics": r"https://betterlyrics\.org/",
    "LiriQo": r"https://github\.com/AlFarrizi-Studio/Liriqo",
    "VocaDB": r"https://vocadb\.net/S/\d+/lyrics(?:\?lyricsId=\d+)?",
    "Lyrica": r"https://github\.com/Wilooper/Lyrica",
    "Fetch-Lyrics": r"https://(?:www\.)?(?:utaten\.com|uta-net\.com)/[^\s\[\]<>]+",
}
_SUBMITTER_URL = {
    "Unison": r"https://unison\.betterlyrics\.org/curator/[A-Za-z0-9_%~.-]+",
    "AMLL TTML DB": r"https://github\.com/[A-Za-z0-9-]+",
}


def _safe_label(value):
    return " ".join(value.split()).replace("[", "(").replace("]", ")")


def _read_source(line):
    label, separator, url = line.rpartition(" ")
    if not separator or not label.startswith("Lyrics from "):
        return None
    source = label.removeprefix("Lyrics from ").split(" · ", 1)[0]
    pattern = _SOURCE_URL.get(source)
    return source if pattern and re.fullmatch(pattern, url) else None


def _has_submitter(line, source):
    label, _, url = line.rpartition(" ")
    pattern = _SUBMITTER_URL.get(source)
    return pattern and label.startswith("Submitted by ") and re.fullmatch(pattern, url)


def provider_credit(record):
    source = record.get("source")
    if source not in _SOURCE_URL:
        return ()
    detail = _safe_label(record.get("providerDetail") or "")
    label = f"Lyrics from {source} · {detail}" if detail else f"Lyrics from {source}"
    line = f"{label} {record.get('sourceUrl', '')}"
    if _read_source(line) != source:
        return ()
    lines = [line]
    if record.get("submitterName") and record.get("submitterUrl"):
        submitter = f"Submitted by {_safe_label(record['submitterName'])} {record['submitterUrl']}"
        if _has_submitter(submitter, source):
            lines.append(submitter)
    return tuple(lines)


def credit_markup(lines):
    links = []
    for line in lines:
        label, url = line.rsplit(" ", 1)
        links.append(f'<a href="{escape(url)}">{escape(label)}</a>')
    return " · ".join(links)


def add_provider_credit(content, lines, *, synced):
    if not lines:
        return content
    if synced:
        return "".join(f"[re:{line}]\n" for line in lines) + content
    return content.rstrip() + "\n\n" + "\n".join(lines)


def read_provider_credit(path):
    plain = path.suffix.lower() == ".txt"
    with path.open(encoding="utf-8-sig") as file:
        if plain:
            candidates = file.read().rstrip().splitlines()[-2:]
        else:
            candidates = []
            for raw in file:
                line = raw.strip()
                if line and not (line.startswith("[") and line[1:2].isalpha()):
                    break
                if line.startswith("[re:") and line.endswith("]"):
                    candidates.append(line[4:-1])

    for index, line in enumerate(candidates):
        source = _read_source(line)
        if not source:
            continue
        submitter = candidates[index + 1:index + 2]
        if submitter and _has_submitter(submitter[0], source):
            if not plain or index + 2 == len(candidates):
                return line, submitter[0]
        if not plain or index + 1 == len(candidates):
            return (line,)
    return ()
