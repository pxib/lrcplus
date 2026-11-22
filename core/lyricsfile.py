"""Read LRCLIB's Lyricsfile 1.0 YAML into the existing LRCX lyric model."""

import re

import yaml

from core.remote_lyric_formats import timed_payload
from lyrics.loader import PLAYER_LYRIC_LINE_LIMIT
from lyrics.models import LyricLine, LyricSegment


_TAG_PREFIX = "tag:yaml.org,2002:"
_NODE_TAGS = {_TAG_PREFIX + name for name in ("str", "int", "bool", "null", "map", "seq")}


class _LyricsfileLoader(yaml.SafeLoader):
    # Lyricsfile uses YAML 1.2 scalars. YAML 1.1 would read bare words such
    # as Yes and On as booleans and bare dates as timestamps.
    yaml_implicit_resolvers = {}
    _depth = 0
    _nodes = 0

    def compose_node(self, parent, index):
        self._nodes += 1
        if self._depth >= 24 or self._nodes > 65_536 or self.check_event(yaml.AliasEvent):
            raise yaml.YAMLError("Lyricsfile exceeds YAML structure limits")
        self._depth += 1
        try:
            node = super().compose_node(parent, index)
            if node.tag not in _NODE_TAGS:
                raise yaml.YAMLError("Unsupported Lyricsfile YAML type")
            return node
        finally:
            self._depth -= 1

    def construct_mapping(self, node, deep=False):
        if not isinstance(node, yaml.MappingNode):
            raise yaml.YAMLError("Expected a Lyricsfile YAML mapping")
        mapping = {}
        for key_node, value_node in node.value:
            key = self.construct_object(key_node)
            if not isinstance(key, str) or key in mapping:
                raise yaml.YAMLError("Invalid or duplicate Lyricsfile YAML key")
            mapping[key] = self.construct_object(value_node)
        return mapping

    def construct_yaml_int(self, node):
        value = self.construct_scalar(node)
        unsigned = value.lstrip("+-")
        base = {"0x": 16, "0o": 8}.get(unsigned[:2], 10)
        return int(value, base)

    def construct_yaml_str(self, node):
        value = self.construct_scalar(node)
        # YAML escapes can decode to lone surrogates that cannot be saved as UTF-8.
        value.encode("utf-8")
        return value


_LyricsfileLoader.add_constructor(_TAG_PREFIX + "int", _LyricsfileLoader.construct_yaml_int)
_LyricsfileLoader.add_constructor(_TAG_PREFIX + "str", _LyricsfileLoader.construct_yaml_str)
for tag, pattern, initial in (
    ("bool", r"^(?:true|True|TRUE|false|False|FALSE)$", "tTfF"),
    ("null", r"^(?:null|Null|NULL|~|)$", "nN~"),
    ("int", r"^[-+]?(?:[0-9]+|0o[0-7]+|0x[0-9a-fA-F]+)$", "-+0123456789"),
    # Floats are a YAML core type, but a Lyricsfile may only contain integers.
    ("float", r"^[-+]?(?:(?:[0-9]+(?:\.[0-9]*)?|\.[0-9]+)(?:[eE][-+]?[0-9]+)?|\.(?:inf|Inf|INF|nan|NaN|NAN))$",
     "-+0123456789."),
):
    _LyricsfileLoader.add_implicit_resolver(_TAG_PREFIX + tag, re.compile(pattern), [*initial, ""])


def _timing(row):
    start = row.get("start_ms")
    end = row.get("end_ms")
    if type(start) is not int or not 0 <= start < 10**15:
        return None
    if end is not None and (type(end) is not int or not start <= end < 10**15):
        return None
    return start / 1000, end / 1000 if end is not None else None


def _words(rows, line_start, line_end):
    if not isinstance(rows, list):
        return []
    segments = []
    previous_start = line_start
    for row in rows:
        if not isinstance(row, dict) or not isinstance(text := row.get("text"), str) or not text:
            return []
        if (timing := _timing(row)) is None:
            return []
        start, end = timing
        word_boundary = end if end is not None else start
        if start < previous_start or (line_end is not None and word_boundary > line_end):
            return []
        segments.append(LyricSegment(start, text, end=end, instant=start == end))
        previous_start = start
    return segments


def _lines(rows):
    if not isinstance(rows, list) or len(rows) > PLAYER_LYRIC_LINE_LIMIT:
        return []
    lines = []
    for row in rows:
        if not isinstance(row, dict) or not isinstance(text := row.get("text"), str):
            return []
        if (timing := _timing(row)) is None:
            return []
        start, end = timing
        segments = _words(row.get("words"), start, end)
        word_text = "".join(segment.text for segment in segments)
        if word_text.strip():
            text = word_text
        else:
            segments = []
        lines.append(LyricLine(start, text, segments, end, has_karaoke=bool(segments)))
    return sorted(lines, key=lambda line: line.start) if any(line.text.strip() for line in lines) else []


def apply_lyricsfile(record):
    source = record.get("lyricsfile")
    if not isinstance(source, str) or not 0 < len(source) <= 512_000:
        return record
    try:
        document = yaml.load(source, Loader=_LyricsfileLoader)
    except (yaml.YAMLError, ValueError):
        return record
    if not isinstance(document, dict) or document.get("version") != "1.0":
        return record
    metadata = document.get("metadata")
    if not isinstance(metadata, dict) or metadata.get("instrumental") is True:
        return record
    lines = _lines(document.get("lines"))
    payload = timed_payload(lines)
    plain = document.get("plain")
    if isinstance(plain, str) and plain.strip():
        payload["plainLyrics"] = plain
    else:
        plain = record.get("plainLyrics")
        # Live radio displays untimed lyrics since the stream has no track clock.
        if lines and (not isinstance(plain, str) or not plain.strip()):
            payload["plainLyrics"] = "\n".join(line.text for line in lines)
    return {**record, **payload}
