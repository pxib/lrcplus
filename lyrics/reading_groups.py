"""Reading word boundaries, independent of karaoke timing and tokenizers."""

from difflib import SequenceMatcher


def valid_reading_groups(text, groups):
    result = []
    for group in groups:
        if not isinstance(group, (tuple, list)) or len(group) != 2:
            continue
        a, b = group
        if type(a) is not int or type(b) is not int or not 0 <= a < b <= len(text):
            continue
        if any(char.isspace() for char in text[a:b]):
            continue
        result.append((a, b))
    ordered = []
    for a, b in sorted(set(result)):
        if not ordered or a >= ordered[-1][1]:
            ordered.append((a, b))
    return ordered


def remap_reading_groups(old, new, groups, change=None):
    if old == new:
        return list(groups)
    opcodes = SequenceMatcher(None, old, new, autojunk=False).get_opcodes()
    if change is not None:
        a, b, c, d = change
        if (0 <= a <= b <= len(old) and 0 <= c <= d <= len(new)
                and old[:a] == new[:c] and old[b:] == new[d:]):
            opcodes = [("equal", 0, a, 0, c), ("replace", a, b, c, d),
                       ("equal", b, len(old), d, len(new))]
    positions = {a + i: c + i for kind, a, b, c, d in opcodes if kind == "equal"
                 for i in range(b - a)}
    result = []
    for a, b in valid_reading_groups(old, groups):
        mapped = [positions.get(i) for i in range(a, b)]
        if None not in mapped and mapped == list(range(mapped[0], mapped[0] + len(mapped))):
            result.append((mapped[0], mapped[-1] + 1))
    return valid_reading_groups(new, result)


def group_reading_tokens(text, tokens, groups):
    """Overlay authored words on cached provider tokens without a new request."""
    groups = valid_reading_groups(text, groups)
    if not groups or "".join(token["text"] for token in tokens) != text:
        return tokens
    from lyrics.furigana import split_reading_by_text_ranges

    cuts = {boundary for group in groups for boundary in group}
    pieces = []
    for token in tokens:
        start, end = token["start"], token["end"]
        boundaries = sorted({start, end, *(p for p in cuts if start < p < end)})
        for a, b in zip(boundaries, boundaries[1:]):
            part = dict(token, text=text[a:b], start=a, end=b)
            if (a, b) != (start, end):
                x, y = token.get("ruby_start", start), token.get("ruby_end", end)
                first, last = max(a, x), min(b, y)
                reading = token.get("reading")
                part["reading"] = (split_reading_by_text_ranges(
                    text[x:y], reading, [(first - x, last - x)],
                )[0] if reading and first < last else None)
                part.pop("ruby_start", None)
                part.pop("ruby_end", None)
                if first < last and part["reading"]:
                    part.update(ruby_start=first, ruby_end=last)
            pieces.append(part)
    result = []
    index = 0
    for a, b in groups:
        while index < len(pieces) and pieces[index]["end"] <= a:
            result.append(pieces[index])
            index += 1
        members = []
        while index < len(pieces) and pieces[index]["start"] < b:
            members.append(pieces[index])
            index += 1
        reading = "".join(part.get("reading") or "" for part in members) or None
        annotated = [(part.get("ruby_start", part["start"]), part.get("ruby_end", part["end"]))
                     for part in members if part.get("reading")]
        first = min(x for x, _ in annotated) if annotated else a
        last = max(y for _, y in annotated) if annotated else b
        result.append({"text": text[a:b], "reading": reading, "source": "manual" if reading else None,
                       "start": a, "end": b, "ruby_start": first, "ruby_end": last})
    result.extend(pieces[index:])
    return result
