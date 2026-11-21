"""Import timed lyric paragraphs and export Apple Music-style lyric TTML."""

from collections import defaultdict
from dataclasses import dataclass
import math
from pathlib import Path
import re
import xml.etree.ElementTree as ET

from lyrics.models import LyricLine, LyricSegment


_TT = "http://www.w3.org/ns/ttml"
_TTM = _TT + "#metadata"
_TTS = _TT + "#styling"
_ITUNES = "http://music.apple.com/lyric-ttml-internal"
_ITUNES_NAMESPACES = (_ITUNES, "http://itunes.apple.com/lyric-ttml-extensions")
_SPACE = "{http://www.w3.org/XML/1998/namespace}space"
_ROLE = f"{{{_TTM}}}role"
_RUBY = f"{{{_TTS}}}ruby"
_CLOCK = re.compile(r"(?:(\d+):)?(\d+):(\d{2}(?:\.\d+)?)")
_OFFSET = re.compile(r"(\d+(?:\.\d+)?)(ms|s|m|h)?")
_WHITESPACE = re.compile(r"[ \t\r\n]+")
_MAX_FILE_BYTES = 8 * 1024 * 1024


class _LyricXmlTree(ET.TreeBuilder):
    def __init__(self):
        super().__init__()
        self.depth = 0

    def start(self, tag, attrs):
        self.depth += 1
        if self.depth > 64:
            raise ValueError("The TTML document is nested too deeply.")
        return super().start(tag, attrs)

    def end(self, tag):
        self.depth -= 1
        return super().end(tag)

    def doctype(self, name, pubid, system):
        raise ValueError("TTML document type declarations are unsupported.")


def _tag(element):
    return element.tag.rsplit("}", 1)[-1]


def _itunes_attribute(node, name):
    return next((node.get(f"{{{ns}}}{name}") for ns in _ITUNES_NAMESPACES
                 if f"{{{ns}}}{name}" in node.attrib), None)


def _time(value, absolute):
    clock = _CLOCK.fullmatch(value)
    if clock:
        hours, minutes, seconds = clock.groups()
        result = int(hours or 0) * 3600 + int(minutes) * 60 + float(seconds)
    else:
        offset = _OFFSET.fullmatch(value)
        if not offset or (offset[2] is None and not absolute):
            raise ValueError(f"Unsupported TTML time expression: {value}")
        result = float(offset[1]) * {None: 1, "s": 1, "ms": 0.001, "m": 60, "h": 3600}[offset[2]]
    if not math.isfinite(result):
        raise ValueError("TTML timestamps must be finite.")
    return result


@dataclass
class _Interval:
    start: float = 0.0
    end: float | None = None


def _interval(node, parent, absolute):
    if node.get("timeContainer") == "seq":
        raise ValueError("Sequential TTML timing containers are unsupported.")
    origin = 0.0 if absolute else parent.start
    begin = node.get("begin")
    end = node.get("end")
    duration = node.get("dur")
    start = round(origin + _time(begin, absolute), 9) if begin is not None else parent.start
    bounds = [parent.end] if parent.end is not None else []
    if end is not None:
        bounds.append(round(origin + _time(end, absolute), 9))
    if duration is not None:
        bounds.append(round(start + _time(duration, absolute), 9))
    return _Interval(start, max(start, min(bounds)) if bounds else None)


def _own_timing(node):
    return any(name in node.attrib for name in ("begin", "end", "dur"))


def _visible_text(node):
    """Read plain auxiliary text, excluding any nested auxiliary layers."""
    result = [node.text or ""]
    for child in node:
        if _tag(child) == "br":
            result.append("\n")
        elif not child.get(_ROLE):
            result.append(_visible_text(child))
        result.append(child.tail or "")
    return "".join(result)


def _ruby_parts(node, parent, absolute):
    base = "".join(_visible_text(child) for child in node.iter() if child.get(_RUBY) == "base")
    reading = []
    for child in node.iter():
        if child.get(_RUBY) == "text":
            value = _visible_text(child).strip()
            if value:
                timing = _interval(child, parent, absolute) if _own_timing(child) else None
                reading.append((value, timing))
    return base, reading


def _mora_cues(reading, start, end):
    if not reading or any(timing is None or timing.end is None for _, timing in reading):
        return ()
    cues = []
    offset = 0
    previous_end = start
    for value, timing in reading:
        if timing.start < previous_end or timing.end <= timing.start:
            return ()
        if timing.start > previous_end:
            if offset:
                cues.append((offset, round(previous_end - start, 9)))
            cues.append((offset, round(timing.start - start, 9)))
        elif offset:
            cues.append((offset, round(timing.start - start, 9)))
        previous_end = timing.end
        offset += len(value)
    return tuple(cues) if end == previous_end else ()


class _Paragraph:
    def __init__(self, timing, absolute):
        self.timing = timing
        self.absolute = absolute
        self.segments = []
        self.translations = []
        self.row = 0
        self.scope = None
        self.karaoke = False

    def append(self, text, timing=None, *, preserve=False, reading=()):
        if not text:
            return
        if preserve and "\n" in text:
            for index, row in enumerate(text.split("\n")):
                if index:
                    self.break_row()
                self.append(row, timing, preserve=True)
            return
        previous = self.segments[-1] if self.segments else None
        if not preserve:
            # Indentation between inline XML elements is source formatting.
            if not text.strip() and "\n" in text:
                return
            text = _WHITESPACE.sub(" ", text)
            if previous is None or previous.text.endswith((" ", "\n")):
                text = text.lstrip(" ")
            if not text:
                return
        self.karaoke |= timing is not None
        if (previous and previous.text != "\n" and
                (self.scope is timing or (timing is None and text.isspace()))):
            segment = previous
        else:
            start = timing.start if timing is not None else self.timing.start
            if timing is None and previous:
                start = previous.end if previous.end is not None else previous.start
            segment = LyricSegment(start, "", instant=timing is None or timing.start == timing.end,
                                   end=timing.end if timing is not None else None,
                                   visual_line=self.row)
            self.segments.append(segment)
            self.scope = timing
        index = len(segment.text)
        segment.text += text
        if reading:
            annotation = "".join(value for value, _ in reading)
            segment.ruby_spans.append((index, len(segment.text), annotation))
            if index == 0:
                segment.mora_cues = _mora_cues(reading, segment.start, segment.end)

    def break_row(self):
        start = self.timing.start
        if self.segments:
            previous = self.segments[-1]
            start = previous.end if previous.end is not None else previous.start
        self.segments.append(LyricSegment(start, "\n", visual_line=self.row))
        self.row += 1
        self.scope = None

    def add_translation(self, text, preserve):
        for value in text.split("\n"):
            if not value.strip():
                continue
            if self.segments and self.segments[-1].text != "\n":
                self.break_row()
            self.translations.append(self.row)
            self.append(value if preserve else value.strip(), preserve=preserve)

    def read_content(self, node, timing, preserve):
        preserve = node.get(_SPACE, "preserve" if preserve else "default") == "preserve"
        self.append(node.text, timing, preserve=preserve)
        for child in node:
            role = child.get(_ROLE)
            if role == "x-translation":
                self.add_translation(_visible_text(child), preserve)
            elif role not in {"x-roman", "x-bg"}:
                if _tag(child) == "br":
                    self.break_row()
                else:
                    parent = timing if timing is not None else self.timing
                    child_timing = _interval(child, parent, self.absolute) if _own_timing(child) else timing
                    if child.get(_RUBY) == "container":
                        base, reading = _ruby_parts(child, child_timing or parent, self.absolute)
                        if child_timing is None and reading and all(interval is not None for _, interval in reading):
                            child_timing = _Interval(reading[0][1].start, reading[-1][1].end)
                        self.append(base, child_timing, preserve=preserve, reading=reading)
                    else:
                        self.read_content(child, child_timing, preserve)
            self.append(child.tail, timing, preserve=preserve)

    def finish(self):
        for segment in self.segments:
            if not self.karaoke:
                segment.instant = False
            if len(segment.ruby_spans) == 1 and segment.ruby_spans[0][:2] == (0, len(segment.text)):
                segment.ruby = segment.ruby_spans[0][2]
                segment.ruby_source = "manual"
            else:
                segment.mora_cues = ()
        return LyricLine(self.timing.start, "".join(segment.text for segment in self.segments),
                         self.segments, self.timing.end, self.karaoke,
                         translation_lines=self.translations)


def _head_translations(root):
    translations = defaultdict(list)
    for node in root.iter():
        if _tag(node) == "translation":
            for text in node:
                key = text.get("for")
                if _tag(text) == "text" and key:
                    translations[key].append(_visible_text(text).strip())
    return translations


def _read_paragraphs(node, parent, absolute, preserve, translations):
    timing = _interval(node, parent, absolute)
    preserve = node.get(_SPACE, "preserve" if preserve else "default") == "preserve"
    if _tag(node) == "p":
        paragraph = _Paragraph(timing, absolute)
        paragraph.read_content(node, None, preserve)
        for text in translations.get(_itunes_attribute(node, "key"), ()):
            paragraph.add_translation(text, preserve=False)
        line = paragraph.finish()
        if line.text.strip():
            yield line
        return
    for child in node:
        if _tag(child) in {"body", "div", "p"}:
            yield from _read_paragraphs(child, timing, absolute, preserve, translations)


def parse_ttml(contents):
    """Read TTML media timing and Apple Music-style absolute lyric timing."""
    try:
        root = ET.fromstring(contents, parser=ET.XMLParser(target=_LyricXmlTree()))
    except (ET.ParseError, LookupError) as error:
        raise ValueError(f"Invalid TTML XML: {error}") from error
    if root.tag not in {"tt", f"{{{_TT}}}tt"}:
        raise ValueError("The XML file does not contain a TTML document.")
    time_base = root.get(f"{{{_TT}#parameter}}timeBase", "media")
    if time_base != "media":
        raise ValueError("Only TTML media time is supported.")
    absolute = any(_itunes_attribute(node, "timing") or _itunes_attribute(node, "key")
                   for node in root.iter())
    return list(_read_paragraphs(root, _Interval(), absolute, False, _head_translations(root)))


def load_ttml(path):
    with Path(path).open("rb") as file:
        contents = file.read(_MAX_FILE_BYTES + 1)
    if len(contents) > _MAX_FILE_BYTES:
        raise ValueError("The TTML file exceeds 8 MiB.")
    return parse_ttml(contents)


def _clock(seconds):
    if not math.isfinite(seconds):
        raise ValueError("TTML timestamps must be finite.")
    total = round(max(0, seconds) * 1000)
    minutes, millis = divmod(total, 60_000)
    return f"{minutes // 60:02d}:{minutes % 60:02d}:{millis // 1000:02d}.{millis % 1000:03d}"


def _time_attributes(start, end, offset):
    return {"begin": _clock(start + offset), "end": _clock(max(start, end) + offset)}


def _append_text(node, text):
    if len(node):
        node[-1].tail = (node[-1].tail or "") + text
    else:
        node.text = (node.text or "") + text


def _write_ruby(node, base, reading, segment, full, end, offset):
    ruby = ET.SubElement(node, "span", {"tts:ruby": "container"})
    ET.SubElement(ruby, "span", {"tts:ruby": "base"}).text = base
    container = ET.SubElement(ruby, "span", {"tts:ruby": "textContainer"})
    cues = dict(segment.mora_cues) if full else {}
    if not cues:
        ET.SubElement(container, "span", {"tts:ruby": "text"}).text = reading
        return
    ends = {}
    for index, timing in segment.mora_cues:
        ends.setdefault(index, timing)
    edges = sorted({0, len(reading), *(index for index in cues if 0 < index < len(reading))})
    for start, stop in zip(edges, edges[1:]):
        start_time = segment.start + cues.get(start, 0)
        end_time = segment.start + ends[stop] if stop in ends else end
        attrs = _time_attributes(start_time, end_time, offset)
        ET.SubElement(container, "span", {"tts:ruby": "text", **attrs}).text = reading[start:stop]


def _write_slice(node, segment, start, stop, end, offset):
    spans = segment.ruby_spans
    if not spans and segment.ruby and segment.ruby_source != "generated":
        spans = [(0, len(segment.text), segment.ruby)]
    cursor = start
    for left, right, reading in spans:
        if cursor <= left < right <= stop and reading:
            _append_text(node, segment.text[cursor:left])
            _write_ruby(node, segment.text[left:right], reading, segment,
                        left == 0 and right == len(segment.text), end, offset)
            cursor = right
    _append_text(node, segment.text[cursor:stop])


def _write_paragraph(div, line, line_end, index, offset):
    paragraph = ET.SubElement(div, "p", {
        "itunes:key": f"L{index}", **_time_attributes(line.start, line_end, offset),
    })
    paragraph.tail = "\n"
    segments = line.segments
    if not segments or "".join(segment.text for segment in segments) != line.text:
        segments = [LyricSegment(line.start, line.text)]
    row = 0
    row_node = paragraph
    next_start = line_end
    ends = [line_end] * len(segments)
    for i in range(len(segments) - 1, -1, -1):
        segment = segments[i]
        ends[i] = segment.end if segment.end is not None else next_start
        if segment.text.strip() and segment.visual_line not in line.translation_lines:
            next_start = segment.start

    for segment, end in zip(segments, ends):
        start = 0
        for match in re.finditer(r"\n|$", segment.text):
            stop = match.start()
            if stop > start:
                node = row_node
                if line.has_karaoke and row_node is paragraph:
                    if segment.instant:
                        end = segment.start
                    node = ET.SubElement(paragraph, "span", _time_attributes(segment.start, end, offset))
                _write_slice(node, segment, start, stop, end, offset)
            if not match.group():
                break
            row += 1
            ET.SubElement(paragraph, "br")
            if row in line.translation_lines:
                row_node = ET.SubElement(paragraph, "span", {"ttm:role": "x-translation"})
            else:
                row_node = paragraph
            start = match.end()


def serialize_ttml(lines, *, offset_ms=0.0):
    """Export a separate exchange copy, with the document offset applied."""
    lines = [line for line in lines if line.text.strip()]
    if not lines:
        raise ValueError("Add lyrics before exporting a TTML file.")
    root = ET.Element("tt", {
        "xmlns": _TT, "xmlns:ttm": _TTM, "xmlns:tts": _TTS,
        "xmlns:itunes": _ITUNES, "xml:space": "preserve", "xml:lang": "und",
        "itunes:timing": "Word" if any(line.has_karaoke for line in lines) else "Line",
    })
    div = ET.SubElement(ET.SubElement(root, "body"), "div")
    div.text = "\n"
    starts = sorted({line.start for line in lines})
    next_starts = dict(zip(starts, starts[1:]))
    for index, line in enumerate(lines, start=1):
        end = line.end
        if end is None:
            next_start = next_starts.get(line.start, line.start + 2.0)
            end = max(next_start, *(segment.end or segment.start for segment in line.segments), line.start)
        _write_paragraph(div, line, end, index, offset_ms / 1000)
    return ET.tostring(root, encoding="unicode", xml_declaration=True) + "\n"
