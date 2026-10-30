"""Pure karaoke render-model preparation helpers.

This module intentionally has no Qt dependency so timestamp/newline handling can
be regression-tested independently of the GUI.
"""

from lyrics.furigana import split_reading_by_text_ranges


def annotate_next_timed_starts(pieces):
    """Cache the first later-starting piece outside each piece's timing group.

    Keep source-order lookup semantics, even for unsorted timestamps. Walking
    starts backwards lets us retain only the earliest two source indices with
    different groups, instead of scanning the whole line for every glyph.
    """
    timed = []
    for index, piece in enumerate(pieces):
        piece["_next_timed_start"] = None
        if piece.get("start") is not None:
            timed.append((float(piece["start"]), index))
    timed.sort(reverse=True)
    candidates = []
    cursor = 0
    while cursor < len(timed):
        end = cursor + 1
        start = timed[cursor][0]
        while end < len(timed) and timed[end][0] == start:
            end += 1
        batch = []
        for _, index in timed[cursor:end]:
            piece = pieces[index]
            group = piece.get("timing_group", piece.get("karaoke_group"))
            for _, candidate_group, candidate_start in candidates:
                if candidate_group != group:
                    piece["_next_timed_start"] = candidate_start
                    break
            batch.append((index, group, start))
        earliest = []
        for candidate in sorted(candidates + batch, key=lambda item: item[0]):
            if not earliest or candidate[1] != earliest[0][1]:
                earliest.append(candidate)
                if len(earliest) == 2:
                    break
        candidates = earliest
        cursor = end


def build_karaoke_render_pieces(text, normalized, whole_tokens, line_end=None):
    """Map furigana annotations onto authoritative timed lyric chunks.

    ``normalized`` is built from LRCX parser segments and therefore defines
    what must be rendered. Furigana tokens are optional annotations. Structural
    newlines are removed from the tokenizer coordinate space and never affect
    timing ownership. Every non-empty normalized chunk is guaranteed to produce
    drawable text even if a tokenizer drops a separator or returns no token.
    """
    text = str(text or "")
    compact_text = text.replace("\n", "")

    canonical_for_compact = []
    for canonical_index, char in enumerate(text):
        if char != "\n":
            canonical_for_compact.append(canonical_index)
    canonical_for_compact.append(len(text))

    token_spans = []
    search_cursor = 0
    for raw_token in whole_tokens or []:
        token = dict(raw_token)
        token_text = str(token.get("text", ""))
        if not token_text:
            continue
        start = compact_text.find(token_text, search_cursor)
        if start < 0:
            # A provider may normalize punctuation or otherwise return a token
            # that is not byte-identical to the lyric. Never let that suppress
            # the underlying lyric chunk; simply keep the token as annotationless.
            continue
        end = start + len(token_text)
        token_spans.append({
            "start": start,
            "end": end,
            "text": token_text,
            "reading": token.get("reading") or None,
        })
        search_cursor = end

    explicit_ranges = []
    for item in normalized:
        for a, b, ruby in item["ruby_spans"]:
            explicit_ranges.append(
                (
                    item["compact_start"] + int(a),
                    item["compact_start"] + int(b),
                    ruby,
                    item["reading_source"] or "manual",
                )
            )
        if item["reading"] and not item["ruby_spans"]:
            explicit_ranges.append(
                (
                    item["compact_start"],
                    item["compact_end"],
                    item["reading"],
                    item["reading_source"] or "manual",
                )
            )

    prepared = []
    for item in normalized:
        item_start = item["compact_start"]
        item_end = item["compact_end"]
        if item_start >= item_end:
            continue

        boundaries = {item_start, item_end}
        for a, b, _, _ in explicit_ranges:
            if item_start < a < item_end:
                boundaries.add(a)
            if item_start < b < item_end:
                boundaries.add(b)
        for token in token_spans:
            a, b = token["start"], token["end"]
            if item_start < a < item_end:
                boundaries.add(a)
            if item_start < b < item_end:
                boundaries.add(b)

        ordered = sorted(boundaries)
        for a, b in zip(ordered, ordered[1:]):
            if a >= b:
                continue
            piece_text = compact_text[a:b]
            if not piece_text:
                continue

            token = next(
                (
                    candidate
                    for candidate in token_spans
                    if candidate["start"] <= a and b <= candidate["end"]
                ),
                None,
            )

            reading = None
            source = None
            exact_explicit = next(
                (
                    (ruby, ruby_source)
                    for x, y, ruby, ruby_source in explicit_ranges
                    if x == a and y == b
                ),
                None,
            )
            if exact_explicit is not None:
                reading, source = exact_explicit
            elif token is not None and token.get("reading"):
                token_start = token["start"]
                token_end = token["end"]
                token_reading = token["reading"]
                if a == token_start and b == token_end:
                    reading = token_reading
                    source = "generated"
                else:
                    reading = split_reading_by_text_ranges(
                        token["text"],
                        token_reading,
                        [(a - token_start, b - token_start)],
                    )[0]
                    source = "generated" if reading else None

            prepared.append({
                "text": piece_text,
                "reading": reading,
                "reading_source": source,
                "start": item["start"],
                "end": item["end"],
                "instant": item["instant"],
                "line_end": line_end,
                "karaoke_group": item["karaoke_group"],
                "timing_group": item["timing_group"],
                "lyric_group": item["lyric_group"],
                "visual_line": item["visual_line"],
                "start_offset": canonical_for_compact[a],
                "end_offset": canonical_for_compact[b],
            })

    return prepared

