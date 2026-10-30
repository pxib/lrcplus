"""Inline lyric styles for LRCX, independent of timing and ruby."""

import re


def parse_quote_prefix(source):
    """Recognize one quote level at the start of a timed lyric."""
    source = str(source)
    quoted = source.startswith("> ")
    return (source[2:] if quoted else source), quoted


def parse_emphasis(source, boundaries=()):
    """Return text, (start, end, bold, italic, strike, underline) runs and offsets.

    Paired *, **, ~~ and <u>...</u> markers can be combined and nested.
    Unmatched markers remain literal; backslashes escape marker characters.
    The offset map lets ruby and karaoke retain their original ownership.
    """
    source = str(source)
    escaped = set()
    removed = set()
    cursor = 0
    while cursor + 1 < len(source):
        if source[cursor] == "\\" and source[cursor + 1] in "*~<>\\":
            removed.add(cursor)
            escaped.add(cursor + 1)
            cursor += 2
        else:
            cursor += 1

    stack = []
    strike_stack = []
    underline_stack = []
    pairs = []
    cuts = sorted({0, len(source), *boundaries})
    matches = (match for start, end in zip(cuts, cuts[1:])
               for match in re.compile(r"\*+|~~|</?u>").finditer(source, start, end))
    for match in matches:
        token = match.group()
        if token.startswith("<"):
            if match.start() in escaped:
                continue
            if token == "<u>":
                underline_stack.append((match.start(), match.end()))
            elif underline_stack:
                opening, inner_start = underline_stack.pop()
                pairs.append((inner_start, match.start(), 3))
                removed.update(range(opening, inner_start))
                removed.update(range(match.start(), match.end()))
            continue
        if token == "~~":
            if any(position in escaped for position in range(match.start(), match.end())):
                continue
            can_open = match.end() < len(source) and not source[match.end()].isspace()
            can_close = match.start() > 0 and not source[match.start() - 1].isspace()
            if can_close and strike_stack:
                opening = strike_stack.pop()
                pairs.append((opening + 2, match.start(), 2))
                removed.update(range(opening, opening + 2))
                removed.update(range(match.start(), match.end()))
            elif can_open:
                strike_stack.append(match.start())
            continue
        positions = [index for index in range(match.start(), match.end()) if index not in escaped]
        if not positions:
            continue
        start, end = positions[0], positions[-1] + 1
        can_open = end < len(source) and not source[end].isspace()
        can_close = start > 0 and not source[start - 1].isspace()
        remaining = len(positions)
        position = start
        while remaining:
            size = stack[-1][0] if remaining >= 3 and stack else min(2, remaining)
            if can_close and stack and stack[-1][0] == size:
                size, opening = stack.pop()
                pairs.append((opening + size, position, 0 if size == 2 else 1))
                removed.update(range(opening, opening + size))
                removed.update(range(position, position + size))
            elif can_open:
                size = min(2, remaining)
                stack.append((size, position))
            else:
                break
            position += size
            remaining -= size

    changes = [[0] * (len(source) + 1) for _ in range(4)]
    for start, end, style in pairs:
        counts = changes[style]
        counts[start] += 1
        counts[end] -= 1
    text = []
    states = []
    offsets = []
    active = [0] * 4
    for index, char in enumerate(source):
        offsets.append(len(text))
        for style in range(4):
            active[style] += changes[style][index]
        if index not in removed:
            text.append(char)
            states.append(tuple(value > 0 for value in active))
    offsets.append(len(text))
    spans = []
    for index, state in enumerate(states):
        if not any(state):
            continue
        if spans and spans[-1][1] == index and spans[-1][2:] == state:
            spans[-1] = (spans[-1][0], index + 1, *state)
        else:
            spans.append((index, index + 1, *state))
    return "".join(text), spans, offsets


def style_at(spans, position):
    flags = [False] * 4
    for start, end, *styles in spans:
        if start <= position < end:
            for index, enabled in enumerate(styles[:4]):
                flags[index] |= bool(enabled)
    return tuple(flags)


def _transition(before, after):
    markers = (("**", "**"), ("*", "*"), ("~~", "~~"), ("<u>", "</u>"))
    old = [marker for marker, enabled in zip(markers, before) if enabled]
    new = [marker for marker, enabled in zip(markers, after) if enabled]
    common = 0
    while common < min(len(old), len(new)) and old[common] == new[common]:
        common += 1
    return "".join(marker[1] for marker in reversed(old[common:])), "".join(marker[0] for marker in new[common:])


def format_emphasis(text, spans, offset=0, ruby_spans=()):
    """Serialize a slice of visible text without closing styles at timed cuts."""
    opens = {}
    closes = {}
    cursor = 0
    for start, end, reading in sorted(ruby_spans):
        start, end = max(cursor, int(start)), min(len(text), int(end))
        if start < end:
            opens[start] = "{"
            closes[end - 1] = "|" + reading + "}"
            cursor = end
    result = []
    for index, char in enumerate(text):
        position = offset + index
        current = style_at(spans, position)
        result.append(_transition(style_at(spans, position - 1), current)[1])
        result.append(opens.get(index, ""))
        result.append("\\" + char if char in "*~<>\\" else char)
        result.append(closes.get(index, ""))
        result.append(_transition(current, style_at(spans, position + 1))[0])
    return "".join(result)
