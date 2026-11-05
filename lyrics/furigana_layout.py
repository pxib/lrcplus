"""Furigana geometry, wrapping, display text and prepared-segment caching."""

import unicodedata
import re
from PySide6.QtCore import QRectF, QSize
from PySide6.QtGui import QFont, QFontMetrics
from lyrics.furigana import split_reading_by_text_ranges, kana_to_romaji
from lyrics.karaoke_layout import annotate_next_timed_starts
from lyrics.formatting import style_at
from lyrics.romaji import all_romaji_texts, romanized_readings


def is_kana(char):
    """Return True if a single character is Japanese kana."""
    return bool(char) and (
        "\u3040" <= char <= "\u309f"  # Hiragana
        or "\u30a0" <= char <= "\u30ff"  # Katakana
        or "\uff66" <= char <= "\uff9f"  # Half-width katakana
    )

def _normalize_all_romaji_punctuation(text):
    """Normalize Japanese/full-width punctuation for All Romaji display."""
    text = unicodedata.normalize("NFKC", text)
    replacements = {"、": ",", "。": ".", "？": "?", "！": "!", "：": ":", "；": ";",
                    "（": "(", "）": ")", "「": '"', "」": '"', "『": '"', "』": '"', "〜": "~"}
    for old, new in replacements.items():
        text = text.replace(old, new)

    text = re.sub(r"(?<=\S)([,.?!:;])(?=\S)", r"\1 ", text)

    text = re.sub(r"\(\s+", "(", text)
    text = re.sub(r"\s+\)", ")", text)
    text = re.sub(r"(?<=\S)\)(?=\S)", ") ", text)

    text = re.sub(r'"\s+', '"', text)
    text = re.sub(r'\s+"', '"', text)

    return re.sub(r"[ \t]+", " ", text).strip()
def _normalize_all_romaji_blocks(text):
    """Treat paired Japanese punctuation as whitespace-delimited blocks."""
    pairs = {
        "（": ")",
        "「": '"',
        "『": '"',
    }
    closers = {
        "）": "(",
        "」": '"',
        "』": '"',
    }

    # First normalize paired delimiters while preserving their contents.
    for opener, closer in [("（", "）"), ("「", "」"), ("『", "』")]:
        # Match a block without consuming unrelated text.
        pattern = re.compile(
            re.escape(opener) + r"(.*?)" + re.escape(closer),
            re.DOTALL,
        )

        def repl(match, op=opener, cl=closer):
            inner = match.group(1).strip()
            left = "(" if op == "（" else '"'
            right = ")" if cl == "）" else '"'
            return f"{left}{inner}{right}"

        text = pattern.sub(repl, text)

    # Surround completed blocks with exactly one whitespace boundary.
    # Do not add whitespace inside the delimiters.
    text = re.sub(r'(?<=\S)(\([^()\n]*\)|"[^"\n]*")(?=\S)', r' \1 ', text)
    text = re.sub(r'(^|\s)(\([^()\n]*\)|"[^"\n]*")(?=\S)', r'\1\2 ', text)

    # Normalize punctuation spacing outside blocks.
    text = re.sub(r"(?<=\S)([,.?!:;])(?=\S)", r"\1 ", text)

    return re.sub(r"[ \t]+", " ", text).strip()


class FuriganaLayoutMixin:
    def _has_visible_ruby(self):
        return self.show_ruby and any(
            bool(segment.get("reading"))
            for segment in self.segments
        )

    def _has_visible_romaji(self):
        return (
            self.show_romaji
            and self.direction != "vertical"
            and any(bool(segment.get("reading")) for segment in self.segments)
        )

    def _reading_text_for_display(self, reading):
        reading = str(reading or "")
        if self.show_romaji:
            return kana_to_romaji(reading)
        return reading

    def _vertical_column_ruby_flags(self, chars_per_column=None):
        """Return ruby presence using the same wrapping rules as vertical layout.

        The parent widget needs this information before it can size each row.
        The old implementation estimated columns from raw character counts, but
        ``_prepare_segments`` can split a source segment differently when a ruby
        slice is too tall for the corresponding base chunk. That made the parent
        allocate the wrong width for some lyric lines, clipping or shifting their
        first/last columns.

        Keep this calculation in lock-step with the vertical wrapping pass. The
        optional ``chars_per_column`` argument is retained for compatibility with
        older callers, but is intentionally ignored.
        """
        if not self.segments:
            return [False]

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        vertical_pad = max(0, int(getattr(self, "vertical_padding", 0)))
        top_edge = self.TOP_MARGIN + vertical_pad
        bottom_edge = self.height() - self.BOTTOM_MARGIN - vertical_pad
        if bottom_edge <= top_edge:
            return [False]

        base_step = max(1, base_metrics.height() + self.PADDING)
        ruby_step = max(
            1,
            reading_metrics.height(),
        )

        def reading_bounds(total_base, total_reading, begin, finish):
            if total_reading <= 0 or total_base <= 0:
                return 0, 0
            r0 = round(total_reading * begin / total_base)
            r1 = round(total_reading * finish / total_base)
            r0 = max(0, min(total_reading, r0))
            r1 = max(r0, min(total_reading, r1))
            return r0, r1

        flags = []
        y = float(top_edge)
        column = 0

        def ensure_column(index):
            while len(flags) <= index:
                flags.append(False)

        for source in self._visual_source_segments():
            if source.get("_line_break"):
                ensure_column(column)
                column += 1
                y = float(top_edge)
                continue
            text_value = str(source.get("text", ""))
            if not text_value:
                continue

            total_base = len(text_value)
            reading_value = (
                str(source.get("reading") or "")
                if self.show_ruby
                else ""
            )
            total_reading = len(reading_value)
            base_pos = 0

            while base_pos < total_base:
                remaining = max(0.0, bottom_edge - y)
                slots = int((remaining + 1e-6) // base_step)

                if slots <= 0:
                    if y > top_edge:
                        column += 1
                        y = float(top_edge)
                        continue
                    slots = 1

                best_end = min(total_base, base_pos + max(1, slots))

                if self.show_ruby and total_reading > 0:
                    while best_end > base_pos + 1:
                        r0_test, r1_test = reading_bounds(
                            total_base, total_reading, base_pos, best_end
                        )
                        ruby_chars_test = max(0, r1_test - r0_test)
                        ruby_height_test = ruby_chars_test * ruby_step
                        chunk_span_test = (best_end - base_pos) * base_step
                        if ruby_height_test <= chunk_span_test + 1e-6:
                            break
                        best_end -= 1

                r0, r1 = reading_bounds(
                    total_base, total_reading, base_pos, best_end
                )
                chunk_reading = reading_value[r0:r1]
                chunk_base_span = (best_end - base_pos) * base_step
                chunk_ruby_span = (
                    max(0, r1 - r0) * ruby_step
                    if self.show_ruby and chunk_reading
                    else 0
                )
                chunk_span = max(chunk_base_span, chunk_ruby_span)

                # If a long ruby reading cannot fit in the remaining vertical
                # space, move the ENTIRE visual chunk to the next column. This
                # prevents ruby from bleeding into the next chunk and visually
                # merging with its reading.
                if (
                    chunk_span > remaining + 1e-6
                    and y > top_edge
                ):
                    column += 1
                    y = float(top_edge)
                    remaining = max(0.0, bottom_edge - y)

                    slots = int((remaining + 1e-6) // base_step)
                    best_end = min(total_base, base_pos + max(1, slots))

                    if (self.show_ruby or self.show_romaji) and total_reading > 0:
                        while best_end > base_pos + 1:
                            r0_test, r1_test = reading_bounds(
                                total_base, total_reading, base_pos, best_end
                            )
                            ruby_chars_test = max(0, r1_test - r0_test)
                            ruby_height_test = ruby_chars_test * ruby_step
                            chunk_span_test = (best_end - base_pos) * base_step
                            if ruby_height_test <= chunk_span_test + 1e-6:
                                break
                            best_end -= 1

                    r0, r1 = reading_bounds(
                        total_base, total_reading, base_pos, best_end
                    )
                    chunk_reading = reading_value[r0:r1]
                    chunk_base_span = (best_end - base_pos) * base_step
                    chunk_ruby_span = (
                        max(0, r1 - r0) * ruby_step
                        if (self.show_ruby or self.show_romaji) and chunk_reading
                        else 0
                    )
                    chunk_span = max(chunk_base_span, chunk_ruby_span)

                ensure_column(column)
                if chunk_reading:
                    # Exactly matches ``_vertical_has_ruby`` in the real
                    # renderer: ruby belongs to the first base glyph of each
                    # wrapped visual chunk.
                    flags[column] = True

                base_pos = best_end
                y = y + chunk_span

                if base_pos < total_base and y >= bottom_edge - 1e-6:
                    column += 1
                    y = float(top_edge)

            if y >= bottom_edge - 1e-6:
                column += 1
                y = float(top_edge)

        return flags or [False]

    def _vertical_ruby_lane_width(self):
        """Return the real horizontal space needed by vertical ruby glyphs.

        Vertical ruby is painted one glyph at a time with ``drawText``.
        Using ``QFontMetrics.height()`` as its *width* can under-size the lane
        for some fonts/scales, which is especially visible in the first column:
        its ruby can end up at x < 0 and be clipped completely. Measure the
        actual glyph advances instead and keep a small floor from the font
        metrics.
        """
        metrics = QFontMetrics(self.reading_font)
        lane = metrics.horizontalAdvance("あ")
        for segment in self.segments:
            if not self.show_ruby:
                break
            reading = str(segment.get("reading") or "")
            for char in reading:
                lane = max(lane, metrics.horizontalAdvance(char))
        return max(1, int(lane))

    def _content_metrics(self):
        base_height = QFontMetrics(self.base_font).height()
        ruby_height = 0
        romaji_height = 0
        if self._has_visible_ruby() or (
            self.reserve_ruby_space
            and self.show_ruby
            and self.ruby_position == "above"
        ):
            ruby_height = QFontMetrics(self.reading_font).height()
        elif self._has_visible_romaji():
            romaji_height = QFontMetrics(self.reading_font).height()

        reading_height = max(ruby_height, romaji_height)
        ruby_gap = self.ruby_padding if reading_height else 0
        if reading_height and self.ruby_position == "below":
            total = self.TOP_MARGIN + base_height + ruby_gap + reading_height + self.BOTTOM_MARGIN
        else:
            total = self.TOP_MARGIN + reading_height + ruby_gap + base_height + self.BOTTOM_MARGIN
        return base_height, ruby_height, ruby_gap, max(1, total)

    def _update_content_geometry(self):
        _base, _ruby, _gap, total = self._content_metrics()
        # Vertical text is laid out inside the available widget height and
        # wraps into columns, so it must not force the normal ruby-band height.
        if self.direction == "vertical":
            self.setMinimumHeight(1)
        else:
            height = (self.sizeHint().height() if self.translation_lines
                      else total * (self.lyric.count("\n") + 1))
            self.setMinimumHeight(height)
        self.updateGeometry()

        # A scrolling lyric row may have a fixed height in its parent.  When a
        # reading arrives asynchronously (including romaji), make the parent
        # recalculate row geometry instead of waiting for an unrelated resize.
        parent = self.parentWidget()
        while parent is not None:
            schedule_refresh = getattr(
                parent,
                "_schedule_row_height_refresh",
                None,
            )
            if callable(schedule_refresh):
                schedule_refresh()
                break
            parent = parent.parentWidget()

    def sizeHint(self):
        _base, _ruby, _gap, total = self._content_metrics()
        if self.direction == "vertical":
            base_metrics = QFontMetrics(self.base_font)
            reading_metrics = QFontMetrics(self.reading_font)
            ruby_lane = self._vertical_ruby_lane_width() if self._has_visible_ruby() else 0
            width = (
                self.PADDING
                + ruby_lane
                + (self.ruby_padding if ruby_lane else 0)
                + base_metrics.height()
                + self.PADDING
            )
            return QSize(max(1, width), max(1, total))
        rows = self.lyric.count("\n") + 1
        if self.translation_lines:
            self._prepare_segments()
            return QSize(max(0, self.minimumWidth()), self._horizontal_layout_height)
        if self.wrap_text:
            prepared = self._prepare_segments()
            visual_rows = {
                round(float(piece["base_y"]), 3)
                for piece in prepared
                if piece.get("base_y") is not None
            }
            rows = max(rows, len(visual_rows))
        return QSize(max(0, self.minimumWidth()), total * rows)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _wrap_prepared_segments(self, prepared, base_metrics, reading_metrics):
        explicit_rows = 1 + sum(bool(piece.get("_line_break")) for piece in prepared)
        if not prepared or (not self.wrap_text and explicit_rows == 1):
            return prepared

        indent = self._quote_indent()
        available = max(1, self.width() - 20 - indent)
        base_height, ruby_height, ruby_gap, line_height = self._content_metrics()
        max_lines = self.wrap_max_lines
        if max_lines < 0:
            max_lines = max(
                1,
                sum(len(str(piece.get("text", ""))) for piece in prepared)
                + explicit_rows,
            )
        elif max_lines == 0:
            # Use the real drawable height. A small tolerance prevents
            # ellipsizing a line that is still visibly inside the bottom edge.
            drawable_height = max(
                1, self.height() - self.TOP_MARGIN - self.BOTTOM_MARGIN
            )
            bottom_tolerance = max(2, int(line_height * 0.18))
            max_lines = max(
                1,
                int(
                    (drawable_height + bottom_tolerance)
                    // max(1, line_height)
                ),
            )

        # Explicit breaks are document structure, even when automatic wrapping
        # is disabled or the panel's configured wrap limit is only one row.
        max_lines = max(max_lines, explicit_rows)
        rows = [[]]
        row_widths = [0.0]
        translation_rows = [False]
        truncated = False

        def add_piece(piece):
            nonlocal truncated
            if truncated:
                return
            if piece.get("_line_break"):
                rows.append([])
                row_widths.append(0.0)
                translation_rows.append(bool(piece.get("_next_translation")))
                return
            width = piece["width"]
            gap = piece.get("gap_before", 0)
            piece_metrics = (QFontMetrics(self._styled_font(self.base_font, piece))
                             if piece.get("bold") or piece.get("italic") or piece.get("translation") else base_metrics)

            # Start a new visual row when the next piece would overflow.
            if self.wrap_text and rows[-1] and row_widths[-1] + gap + width > available:
                if len(rows) >= max_lines:
                    truncated = True
                    return
                rows.append([])
                row_widths.append(0.0)
                translation_rows.append(bool(piece.get("translation")))
                gap = 0
                piece["gap_before"] = 0

            # A single segment can itself be wider than the panel. Split it
            # at spaces first, then into visible character chunks so Japanese
            # text and unusually long words can still fit naturally.
            if self.wrap_text and not rows[-1] and width > available and len(piece["text"]) > 1:
                text_value = piece["text"]
                word_pieces = re.findall(r"\S+\s*|\s+", text_value)
                if len(word_pieces) > 1:
                    for word_index, word in enumerate(word_pieces):
                        clone = dict(piece)
                        clone["text"] = word
                        clone["base_width"] = piece_metrics.horizontalAdvance(word)
                        clone["reading"] = None
                        clone["reading_width"] = 0
                        clone["width"] = clone["base_width"]
                        clone["gap_before"] = (
                            piece.get("gap_before", 0) if word_index == 0 else 0
                        )
                        clone["_visual_split"] = True
                        add_piece(clone)
                    return

                chunk = ""
                for char in text_value:
                    candidate = chunk + char
                    if chunk and piece_metrics.horizontalAdvance(candidate) > available:
                        clone = dict(piece)
                        clone["text"] = chunk
                        clone["base_width"] = piece_metrics.horizontalAdvance(chunk)
                        clone["reading"] = None
                        clone["reading_width"] = 0
                        clone["width"] = clone["base_width"]
                        clone["gap_before"] = 0
                        clone["_visual_split"] = True
                        add_piece(clone)
                        chunk = char
                    else:
                        chunk = candidate
                if chunk:
                    clone = dict(piece)
                    clone["text"] = chunk
                    clone["base_width"] = piece_metrics.horizontalAdvance(chunk)
                    clone["reading"] = None
                    clone["reading_width"] = 0
                    clone["width"] = clone["base_width"]
                    clone["gap_before"] = 0
                    clone["_visual_split"] = True
                    add_piece(clone)
                return

            rows[-1].append(piece)
            translation_rows[-1] = bool(piece.get("translation"))
            row_widths[-1] += gap + width
            if (self.show_ruby or self.show_romaji) and piece.get("reading"):
                row_widths[-1] += self.PADDING

        for segment in prepared:
            add_piece(dict(segment))

        if truncated and rows and rows[-1]:
            ellipsis_metrics = (QFontMetrics(self._styled_font(self.base_font, {"translation": True}))
                                if translation_rows[-1] else base_metrics)
            ellipsis_width = ellipsis_metrics.horizontalAdvance("…")
            while rows[-1] and row_widths[-1] + ellipsis_width > available:
                removed = rows[-1].pop()
                row_widths[-1] -= removed["width"] + removed.get("gap_before", 0)
                if (self.show_ruby or self.show_romaji) and removed.get("reading"):
                    row_widths[-1] -= self.PADDING

            if rows[-1] or available >= ellipsis_width:
                rows[-1].append({
                    "text": "…",
                    "reading": None,
                    "base_width": ellipsis_width,
                    "reading_width": 0,
                    "width": ellipsis_width,
                    "gap_before": 0,
                    "start": None,
                    "karaoke_group": None,
                    "_ellipsis": True,
                    "translation": translation_rows[-1],
                })
                row_widths[-1] += ellipsis_width

        wrapped = []
        y_offset = 0
        for row_index, row in enumerate(rows):
            translation = translation_rows[row_index]
            row_base_metrics = (QFontMetrics(self._styled_font(self.base_font, {"translation": True}))
                                if translation else base_metrics)
            row_base_height = row_base_metrics.height() if translation else base_height
            reading_height = ruby_height or (reading_metrics.height() if self._has_visible_romaji() else 0)
            if translation and not any(piece.get("reading") for piece in row):
                reading_height = 0
            row_ruby_gap = ruby_gap if reading_height else 0
            row_height = (self.TOP_MARGIN + row_base_height + reading_height
                          + row_ruby_gap + self.BOTTOM_MARGIN) if translation else line_height
            if not row:
                y_offset += row_height
                continue
            total_width = row_widths[row_index]
            if self.alignment == "left":
                x = float(indent)
            elif self.alignment == "right":
                x = max(float(indent), self.width() - total_width)
            else:
                x = max(float(indent), indent + (self.width() - indent - total_width) / 2.0)

            if reading_height and self.ruby_position == "above":
                reading_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + reading_metrics.ascent()
                )
                base_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + reading_height
                    + row_ruby_gap
                    + row_base_metrics.ascent()
                )
            elif reading_height:
                base_y = self.TOP_MARGIN + y_offset + row_base_metrics.ascent()
                reading_y = (
                    self.TOP_MARGIN + y_offset + row_base_height + row_ruby_gap
                    + row_base_metrics.descent() + reading_metrics.ascent()
                )
            else:
                reading_y = None
                base_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + row_base_metrics.ascent()
                )

            romaji_y = reading_y if self.show_romaji else None

            for piece_index, segment in enumerate(row):
                x += segment.get("gap_before", 0)
                segment["x"] = x
                segment["base_y"] = base_y
                segment["reading_y"] = reading_y
                segment["romaji_y"] = romaji_y

                # Wrapped pieces get independent visual karaoke spans. Their
                # original start time remains unchanged, so they animate
                # together without a cross-row clipping rectangle.
                if segment.get("start") is not None:
                    original_group = segment.get("karaoke_group")
                    segment["karaoke_group"] = (
                        "visual", original_group, row_index, piece_index
                    )
                    # timing_group intentionally survives wrapping unchanged.
                    # Visual splitting must never turn one timed word into
                    # several independently sweeping karaoke chunks.
                segment["karaoke_x"] = x
                segment["karaoke_width"] = segment["width"]

                x += segment["width"]
                if (self.show_ruby or self.show_romaji) and segment.get("reading"):
                    x += self.PADDING
                wrapped.append(segment)

            y_offset += row_height

        self._horizontal_layout_height = max(1, y_offset)
        return wrapped

    def _visual_source_segments(self):
        """Split structural newlines for layout while retaining reading/timing ownership."""
        offset = 0
        row = 0
        for segment in self.segments:
            text = str(segment.get("text", ""))
            reading_group = segment.get("_reading_group", (offset, offset + len(text)))
            if "\n" not in text and not self.style_spans:
                yield {**segment, "_reading_group": reading_group, "translation": row in self.translation_lines}
                offset += len(text)
                continue
            boundaries = {0, len(text)}
            for match in re.finditer("\n", text):
                boundaries.update((match.start(), match.end()))
            for start, end, *_styles in self.style_spans:
                for boundary in (start - offset, end - offset):
                    if 0 < boundary < len(text):
                        boundaries.add(boundary)
            ordered = sorted(boundaries)
            ranges = list(zip(ordered, ordered[1:]))
            reading = segment.get("reading")
            readings = (
                split_reading_by_text_ranges(text, reading, ranges)
                if reading else [None] * len(ranges)
            )
            for (start, end), part_reading in zip(ranges, readings):
                piece = dict(segment)
                piece["text"] = text[start:end]
                piece["translation"] = row in self.translation_lines
                piece["reading"] = part_reading
                piece["_reading_group"] = reading_group
                piece["_romaji_word_start"] = bool(segment.get("_romaji_word_start", False)) and start == 0
                piece["bold"], piece["italic"], piece["strike"], piece["underline"] = style_at(self.style_spans, offset + start)
                if piece["text"] == "\n":
                    piece.update(text="", reading=None, _line_break=True)
                    row += 1
                    piece["_next_translation"] = row in self.translation_lines
                yield piece
            offset += len(text)

    def _styled_font(self, font, segment):
        if not segment.get("translation") and not any(segment.get(style) for style in ("bold", "italic", "strike", "underline")):
            return font
        styled = QFont(font)
        if segment.get("translation"):
            if font.pointSizeF() > 0:
                styled.setPointSizeF(max(1.0, font.pointSizeF() * 0.75))
            else:
                styled.setPixelSize(max(1, round(font.pixelSize() * 0.75)))
        styled.setBold(font.bold() or bool(segment.get("bold")))
        styled.setItalic(font.italic() or bool(segment.get("italic")))
        styled.setStrikeOut(font.strikeOut() or bool(segment.get("strike")))
        styled.setUnderline(font.underline() or bool(segment.get("underline")))
        return styled

    def _paint_font(self, font, segment):
        styled = self._styled_font(font, segment)
        if not segment.get("_vertical") and (styled.strikeOut() or styled.underline()):
            # Horizontal rules are painted across the whole styled run below;
            # native font decorations stop at each separately drawn token.
            styled = QFont(styled)
            styled.setStrikeOut(False)
            styled.setUnderline(False)
        return styled

    def _prepare_decoration_spans(self, prepared):
        """Connect decorated glyphs across layout padding and source spaces."""
        for band, font in (("base", self.base_font), ("reading", self.reading_font)):
            metrics = QFontMetrics(font)
            for style in ("underline", "strike"):
                previous = None
                previous_row = None
                for piece in prepared:
                    row = piece.get("base_y")
                    if piece.get("_vertical") or not piece.get(style):
                        previous = None
                        continue
                    if row != previous_row:
                        previous = None
                    previous_row = row
                    if band == "reading":
                        baseline = piece.get("romaji_y") if self.show_romaji else piece.get("reading_y")
                        width = piece["_display_reading_width"]
                        if not piece["_display_reading"] or baseline is None:
                            continue
                        left = piece["x"] + (piece["width"] - width) / 2
                    else:
                        baseline = piece["base_y"]
                        width = piece["base_width"]
                        left = piece["x"] + (piece["width"] - width) / 2
                    if previous is not None:
                        previous["right"] = max(previous["right"], left)
                    span = {
                        "band": band, "left": left, "right": left + width,
                        "y": int(baseline) + (metrics.underlinePos() if style == "underline" else -metrics.strikeOutPos()),
                        "thickness": max(1, metrics.lineWidth()),
                    }
                    piece.setdefault("_decorations", []).append(span)
                    previous = span

    def _display_text(self, segment, leading_sokuon=False):
        """Return the base lyric text in the selected display mode.

        ``っ`` can be its own timed lyric segment.  In that case converting
        each segment independently would drop it and turn ``な`` + ``っ`` +
        ``て`` into ``na`` + ```` + ``te`` (``nate``).  ``leading_sokuon``
        carries that pronunciation marker across the segment boundary.
        """
        text = str(segment.get("text", ""))
        if not self.all_romaji:
            return text, False

        # Prefer the parser's reading for kanji; kana/Latin/punctuation can
        # be converted directly. This is display-only and leaves the source
        # lyric and timing data untouched.
        reading = str(segment.get("reading") or "")
        source = reading if reading and any(
            "\u3400" <= ch <= "\u9fff" or ch == "々"
            for ch in text
        ) else text

        # A small-tsu at the end of this segment belongs to the next segment.
        source_for_conversion = source
        trailing_sokuon = source_for_conversion.rstrip().endswith(("っ", "ッ"))
        if trailing_sokuon:
            stripped = source_for_conversion.rstrip()
            source_for_conversion = stripped[:-1] + source_for_conversion[len(stripped):]

        # Context-sensitive Japanese particles.  Only rewrite a segment
        # whose *entire surface* is the particle; never replace these kana
        # globally, so はし remains ``hashi`` and へや remains ``heya``.
        particle_readings = {"は": "わ", "へ": "え", "を": "お"}
        particle_surface = text.strip()
        if particle_surface in particle_readings:
            leading = source_for_conversion[:len(source_for_conversion) - len(source_for_conversion.lstrip())]
            trailing = source_for_conversion[len(source_for_conversion.rstrip()):]
            source_for_conversion = (
                leading + particle_readings[particle_surface] + trailing
            )

        converted = kana_to_romaji(source_for_conversion)

        # If the previous timed segment ended in ``っ``, double the first
        # consonant of this segment's next mora.  Do this after conversion so
        # Hepburn uses t before ch: ``っ`` + ``ち`` becomes ``tchi``.
        if leading_sokuon and converted:
            first = converted[0]
            if first.isalpha() and first.lower() not in "aeiou":
                converted = ("t" if converted.startswith("ch") else first) + converted

        converted = _normalize_all_romaji_punctuation(converted)
        return converted, trailing_sokuon

    @staticmethod
    def _romaji_needs_word_space(text):
        if not text or not text.strip():
            return False
        first = text.lstrip()[0]
        if first in "\"'\u2018\u2019\u201c\u201d.,!?;:)]}\u3001\u3002\uff0c\uff01\uff1f":
            return False
        return True

    def _quote_indent(self):
        return max(14, QFontMetrics(self.base_font).height() // 2) if self.is_blockquote else 0

    def _quote_bar_rect(self, prepared):
        if not self.is_blockquote or not prepared:
            return QRectF()
        base = QFontMetrics(self.base_font)
        reading = QFontMetrics(self.reading_font)
        left = min(piece["x"] for piece in prepared)
        top = min(piece["base_y"] - base.ascent() for piece in prepared)
        bottom = max(piece["base_y"] + base.descent() for piece in prepared)
        if self.show_ruby or self.show_romaji:
            for piece in prepared:
                baseline = piece.get("romaji_y") if self.show_romaji else piece.get("reading_y")
                if baseline is None or not piece.get("_display_reading"):
                    continue
                left = min(left, piece.get("reading_x", piece["x"]))
                top = min(top, baseline - reading.ascent())
                reading_bottom = baseline + reading.descent()
                if piece.get("reading_vertical"):
                    reading_bottom += (len(piece["_display_reading"]) - 1) * piece.get("reading_step", reading.height())
                bottom = max(bottom, reading_bottom)
        thickness = max(2, base.height() // 16)
        x = max(2.0, left - self._quote_indent() + thickness)
        return QRectF(x, top, thickness, max(1, bottom - top))

    def _prepare_segments(self):
        # Playback, fades and highlight animations only change paint state.
        # Keep one layout per mounted row, rebuilding when content or geometry
        # changes. Snapshot dictionaries because plugins can edit them in place.
        key = (
            self.lyric, [dict(segment) for segment in self.segments],
            list(self.style_spans), tuple(self.translation_lines),
            self.is_blockquote,
            self.base_font.key(), self.reading_font.key(),
            self.width(), self.height(), self.direction, self.alignment,
            self.show_ruby, self.show_romaji, self.all_romaji,
            self.ruby_position, self.ruby_padding, self.reserve_ruby_space,
            self.wrap_text, self.wrap_max_lines,
            getattr(self, "vertical_padding", 0),
            self.PADDING, self.TOP_MARGIN, self.BOTTOM_MARGIN,
        )
        if key != self._prepared_layout_key:
            prepared = self._build_prepared_segments()
            annotate_next_timed_starts(prepared)
            starts, ends = [], []
            for piece in prepared:
                start = piece.get("start")
                if start is None:
                    continue
                start = float(start)
                end = piece.get("end")
                if end is None or float(end) <= start:
                    end = piece.get("_next_timed_start")
                if end is None:
                    end = piece.get("line_end", self.karaoke_line_end)
                starts.append(start)
                ends.append(float(end) if end is not None and float(end) > start else start + 1.0)
            self._karaoke_static_bounds = (
                self.segments, self.karaoke_line_end,
                min(starts) if starts else None, max(ends) if ends else None,
                key[1],
            )
            default_reading_metrics = QFontMetrics(self.reading_font)
            reading_texts = romanized_readings(prepared) if self.show_romaji else []
            for index, piece in enumerate(prepared):
                reading_metrics = (QFontMetrics(self._styled_font(self.reading_font, piece))
                                   if piece.get("bold") or piece.get("italic") or piece.get("translation") else default_reading_metrics)
                reading = reading_texts[index] if self.show_romaji else self._reading_text_for_display(piece.get("reading"))
                piece["_display_reading"] = reading
                piece["_display_reading_width"] = (
                    reading_metrics.horizontalAdvance(reading) if reading else 0
                )
            if any(piece.get("underline") or piece.get("strike") for piece in prepared):
                self._prepare_decoration_spans(prepared)
            self._prepared_layout = prepared
            self._prepared_layout_key = key
        # Floating lyrics adjust these positions for panning. Give callers
        # their own dictionaries so offsets cannot accumulate in the cache.
        return [dict(piece) for piece in self._prepared_layout]

    def _build_prepared_segments(self):
        self._horizontal_layout_height = self._content_metrics()[3] * (self.lyric.count("\n") + 1)
        if not self.lyric:
            return []

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        prepared = []
        total_width = 0

        previous_text = ""
        visual_segments = list(self._visual_source_segments())
        romaji_texts = all_romaji_texts(visual_segments) if self.all_romaji else []
        reading_texts = romanized_readings(visual_segments) if self.show_romaji else []
        row_width = 0
        for index, segment in enumerate(visual_segments):
            if segment.get("_line_break"):
                prepared.append(dict(segment, width=0, base_width=0, reading_width=0, gap_before=0))
                total_width = max(total_width, row_width)
                row_width = 0
                previous_text = ""
                continue
            segment_base_metrics = base_metrics
            segment_reading_metrics = reading_metrics
            if segment.get("bold") or segment.get("italic") or segment.get("translation"):
                segment_base_metrics = QFontMetrics(self._styled_font(self.base_font, segment))
                segment_reading_metrics = QFontMetrics(self._styled_font(self.reading_font, segment))
            if self.all_romaji:
                text = segment["text"] if segment.get("translation") else romaji_texts[index]
            else:
                text, _ = self._display_text(segment)
            base_width = segment_base_metrics.horizontalAdvance(text)
            reading_width = 0
            reading = segment.get("reading")
            ruby_visible = bool((self.show_ruby or self.show_romaji) and reading)

            if ruby_visible:
                displayed_reading = reading_texts[index] if self.show_romaji else reading
                reading_width = segment_reading_metrics.horizontalAdvance(displayed_reading)

            width = max(base_width, reading_width)

            # When a ruby-bearing kanji follows visible kana (e.g. し + 変),
            # give the ruby group a little breathing room. This is display-only:
            # turning furigana off removes the gap completely.
            gap_before = 0
            if (
                ruby_visible
                and previous_text
                and is_kana(previous_text[-1])
            ):
                gap_before = self.PADDING

            prepared.append({
                **segment,
                # Store the selected display text in the prepared visual
                # segment. paintEvent already draws this field, so All romaji
                # changes what is actually rendered without touching the
                # underlying lyric/timing data.
                "text": text,
                "index": index,
                "base_width": base_width,
                "reading_width": reading_width,
                "width": width,
                "gap_before": gap_before,
            })
            row_width += gap_before + width
            if ruby_visible:
                row_width += self.PADDING
            previous_text = text
        total_width = max(total_width, row_width)

        # All-Romaji is much wider than Japanese. Fit only overflowing lines by
        # compressing their X axis; the configured font size and vertical rhythm
        # stay unchanged.
        if self.all_romaji and self.direction != "vertical" and total_width > 0:
            available_width = max(1.0, float(self.width() - 12 - self._quote_indent()))
            self._all_romaji_x_scale = min(1.0, available_width / total_width)
        else:
            self._all_romaji_x_scale = 1.0

        # Vertical mode: wrap source segments as *visual chunks* while keeping
        # their ruby attached.  The old character-by-character pass attached the
        # whole reading only to source char 0, so a segment that crossed a column
        # boundary could lose text/ruby or paint the reading off the bottom edge.
        if self.direction == "vertical":
            vertical_pad = max(0, int(getattr(self, "vertical_padding", 0)))
            top_edge = self.TOP_MARGIN + vertical_pad
            bottom_edge = self.height() - self.BOTTOM_MARGIN - vertical_pad
            available_height = max(1, bottom_edge - top_edge)

            base_step = max(1, base_metrics.height() + self.PADDING)
            ruby_step = max(
                1,
                reading_metrics.height() + max(0, self.ruby_padding // 2),
            )
            base_column_width = max(1, base_metrics.height())
            ruby_lane_width = max(1, self._vertical_ruby_lane_width())

            logical = []
            y = float(top_edge)
            column = 0

            def reading_bounds(total_base, total_reading, begin, finish):
                """Map a visual base chunk onto its proportional ruby slice.

                This is only used when one already-mapped source segment must
                cross a vertical column.  The original source/timing metadata is
                preserved; we merely split its visible ruby string so no chunk
                silently loses the reading.
                """
                if total_reading <= 0 or total_base <= 0:
                    return 0, 0
                r0 = round(total_reading * begin / total_base)
                r1 = round(total_reading * finish / total_base)
                r0 = max(0, min(total_reading, r0))
                r1 = max(r0, min(total_reading, r1))
                return r0, r1

            for source in prepared:
                if source.get("_line_break"):
                    column += 1
                    y = float(top_edge)
                    continue
                text_value = str(source.get("text", ""))
                if not text_value:
                    continue
                source_metrics = (QFontMetrics(self._styled_font(self.base_font, source))
                                  if source.get("bold") or source.get("italic") or source.get("translation") else base_metrics)

                chars = list(text_value)
                reading_value = (
                    str(source.get("reading") or "")
                    if self.show_ruby else ""
                )
                total_base = len(chars)
                total_reading = len(reading_value)
                base_pos = 0

                while base_pos < total_base:
                    # IMPORTANT: ruby must never change where the BASE lyric
                    # wraps. Furigana lives in the adjacent ruby lane, so using
                    # its character count/height as vertical capacity makes the
                    # base text disappear or wrap differently when show_ruby is
                    # toggled. Determine the column break from base characters
                    # only, then split the already-existing reading to match the
                    # resulting visual base chunk.
                    remaining = max(0.0, bottom_edge - y)
                    slots = int((remaining + 1e-6) // base_step)

                    if slots <= 0:
                        if y > top_edge:
                            column += 1
                            y = float(top_edge)
                            continue
                        slots = 1

                    best_end = min(total_base, base_pos + max(1, slots))

                    # Ruby is allowed to influence wrapping when a reading
                    # slice is taller than the base chunk that owns it. Keep
                    # each ruby slice entirely inside the vertical span of its
                    # corresponding base chunk. That way the next chunk can
                    # start immediately after this one without any ruby/ruby
                    # collision or post-layout pushing.
                    if (self.show_ruby or self.show_romaji) and total_reading > 0:
                        while best_end > base_pos + 1:
                            r0_test, r1_test = reading_bounds(
                                total_base, total_reading, base_pos, best_end
                            )
                            ruby_chars_test = max(0, r1_test - r0_test)
                            ruby_height_test = ruby_chars_test * ruby_step
                            chunk_span_test = (best_end - base_pos) * base_step
                            if ruby_height_test <= chunk_span_test + 1e-6:
                                break
                            best_end -= 1

                    r0, r1 = reading_bounds(
                        total_base, total_reading, base_pos, best_end
                    )
                    chunk_reading = reading_value[r0:r1]
                    chunk_base_span = (best_end - base_pos) * base_step
                    chunk_ruby_span = (
                        max(0, r1 - r0) * ruby_step
                        if (self.show_ruby or self.show_romaji) and chunk_reading
                        else 0
                    )
                    chunk_span = max(chunk_base_span, chunk_ruby_span)

                    # A ruby reading may be taller than the base chunk that
                    # owns it. Do not let that extra height overlap the next
                    # chunk: start the entire chunk in the next column when
                    # there is already content above it.
                    if chunk_span > remaining + 1e-6 and y > top_edge:
                        column += 1
                        y = float(top_edge)
                        continue

                    chunk_start_y = y

                    for local_index, char_index in enumerate(
                        range(base_pos, best_end)
                    ):
                        char = chars[char_index]
                        piece = dict(source)
                        piece["text"] = char
                        piece["_vertical"] = True
                        piece["_vertical_column"] = column
                        piece["_vertical_index"] = char_index
                        piece["_vertical_chunk_start"] = base_pos
                        piece["_vertical_chunk_end"] = best_end
                        piece["_vertical_has_ruby"] = bool(
                            local_index == 0 and chunk_reading
                        )
                        piece["base_y"] = (
                            chunk_start_y
                            + local_index * base_step
                            + base_metrics.ascent()
                        )
                        piece["width"] = base_column_width
                        piece["height"] = base_step
                        piece["base_width"] = source_metrics.horizontalAdvance(char)
                        piece["karaoke_width"] = base_column_width

                        if local_index == 0 and chunk_reading:
                            piece["reading"] = chunk_reading
                            piece["reading_vertical"] = True
                            piece["reading_width"] = ruby_lane_width
                            piece["reading_y"] = (
                                chunk_start_y + reading_metrics.ascent()
                            )
                            piece["reading_step"] = ruby_step
                        else:
                            piece["reading"] = None
                            piece["reading_width"] = 0
                            piece["reading_y"] = None

                        logical.append(piece)

                    base_pos = best_end
                    # Reserve whichever vertical span is larger: the base
                    # glyphs or their ruby. This keeps a long reading from
                    # extending into the next visual chunk.
                    y = chunk_start_y + chunk_span

                    if base_pos < total_base and y >= bottom_edge - 1e-6:
                        column += 1
                        y = float(top_edge)

                # Keep independent source segments from beginning below the
                # drawable edge after a ruby-heavy predecessor.
                if y >= bottom_edge - 1e-6:
                    column += 1
                    y = float(top_edge)

            # Decide ruby lanes from the ACTUAL wrapped pieces, not from source
            # character positions.  A ruby chunk that moved into the next column
            # therefore always brings its lane with it.
            column_has_ruby = {}
            for piece in logical:
                col = piece["_vertical_column"]
                column_has_ruby[col] = (
                    column_has_ruby.get(col, False)
                    or bool(piece.get("_vertical_has_ruby"))
                )

            max_column = max(column_has_ruby, default=0)
            column_x = {}

            if self.alignment == "right":
                cursor_x = float(self.width() - self.PADDING)
                for col in range(max_column + 1):
                    has_ruby = column_has_ruby.get(col, False)
                    lane = ruby_lane_width + self.ruby_padding if has_ruby else 0
                    cursor_x -= base_column_width
                    column_x[col] = cursor_x
                    cursor_x -= lane + self.PADDING
            else:
                cursor_x = float(self.PADDING + self._quote_indent())
                for col in range(max_column + 1):
                    has_ruby = column_has_ruby.get(col, False)
                    lane = ruby_lane_width + self.ruby_padding if has_ruby else 0
                    column_x[col] = cursor_x + lane
                    cursor_x += lane + base_column_width + self.PADDING

            # The ruby lane belongs to the same visual column as its base
            # glyph. Keep that relationship, but also guarantee the lane stays
            # inside the row even with unusually wide fonts or a stale parent
            # width from an earlier font setting.
            min_ruby_x = float(self.PADDING)
            if logical:
                min_needed_x = min(
                    (
                        column_x[piece["_vertical_column"]]
                        - ruby_lane_width
                        - self.ruby_padding
                    )
                    for piece in logical
                    if piece.get("reading_vertical")
                ) if any(piece.get("reading_vertical") for piece in logical) else min_ruby_x
                if min_needed_x < min_ruby_x:
                    shift = min_ruby_x - min_needed_x
                    for col in column_x:
                        column_x[col] += shift

            for piece in logical:
                x = column_x[piece["_vertical_column"]]
                piece["x"] = x
                piece["base_x"] = x
                piece["karaoke_x"] = x
                if piece.get("reading_vertical"):
                    piece["reading_x"] = x - ruby_lane_width - self.ruby_padding

            # Ruby positions are now guaranteed by the wrap pass to fit inside
            # the base span of their visual chunk. Do not run a second collision
            # resolver here: moving ruby after wrapping can desynchronize it from
            # its base chunk and create the very overlaps this layout is meant to
            # prevent. Keep the natural position established above.
            for piece in logical:
                if piece.get("reading_vertical") and piece.get("reading"):
                    piece["_ruby_top"] = (
                        float(piece["reading_y"]) - reading_metrics.ascent()
                    )
                    piece["_ruby_height"] = max(
                        1.0,
                        len(str(piece.get("reading") or ""))
                        * float(piece.get("reading_step", ruby_step)),
                    )

            return logical

        if self.alignment == "left":
            x = self._quote_indent()
        elif self.alignment == "right":
            x = self.width() - total_width
        else:
            x = self._quote_indent() + (self.width() - self._quote_indent() - total_width) / 2

        x = max(self._quote_indent(), x)

        base_height, ruby_height, ruby_gap, _total_height = self._content_metrics()
        romaji_height = QFontMetrics(self.reading_font).height() if self._has_visible_romaji() else 0

        # Put the auxiliary reading band either above or below the base lyric.
        reading_height = ruby_height if ruby_height else romaji_height
        if reading_height and self.ruby_position == "above":
            reading_y = self.TOP_MARGIN + reading_metrics.ascent()
            base_y = (
                self.TOP_MARGIN + reading_height + ruby_gap
                + base_metrics.ascent()
            )
        elif reading_height and self.ruby_position == "below":
            base_y = self.TOP_MARGIN + base_metrics.ascent()
            reading_y = (
                self.TOP_MARGIN + base_height + ruby_gap
                + base_metrics.descent() + reading_metrics.ascent()
            )
        else:
            reading_y = None
            base_y = self.TOP_MARGIN + base_metrics.ascent()
        romaji_y = reading_y if self.show_romaji else None

        for segment in prepared:
            x += segment.get("gap_before", 0)
            segment["x"] = x
            segment["base_y"] = base_y
            segment["reading_y"] = reading_y
            x += segment["width"]
            # Keep the horizontal layout identical to total_width above.
            # Hidden ruby must not leave invisible spacing behind.
            if (self.show_ruby or self.show_romaji) and segment.get("reading"):
                x += self.PADDING

        prepared = self._wrap_prepared_segments(
            prepared, base_metrics, reading_metrics
        )

        # The wrapping helper assigns per-row baselines when wrapping is
        # enabled, but it intentionally returns early for non-wrapped lyrics.
        # Ensure the romaji baseline is populated in both paths.
        if self.show_romaji:
            for segment in prepared:
                segment["romaji_y"] = segment.get("reading_y")

        # Generated readings can split ONE timed karaoke chunk into several
        # display/ruby pieces.  Karaoke geometry must be rebuilt from the
        # stable timing identity, not from those visual pieces (or from the
        # temporary per-row karaoke_group created by wrapping).  Otherwise a
        # single editor row such as ``さっきも見たおじさんが`` can appear to
        # sweep token-by-token.
        if self.direction == "vertical":
            return prepared

        groups = {}
        for segment in prepared:
            group = segment.get("timing_group", segment.get("karaoke_group"))
            if group is None:
                continue
            group = (group, segment.get("base_y"))
            left = float(segment["x"])
            right = left + float(segment["width"])
            if group not in groups:
                groups[group] = [left, right]
            else:
                groups[group][0] = min(groups[group][0], left)
                groups[group][1] = max(groups[group][1], right)

        for segment in prepared:
            group = segment.get("timing_group", segment.get("karaoke_group"))
            group = (group, segment.get("base_y"))
            if group in groups:
                left, right = groups[group]
                # Store the canonical span on every visual/ruby piece.  The
                # paint path may then use either the prepared span or rebuild
                # it without ever falling back to token-local geometry.
                segment["karaoke_x"] = left
                segment["karaoke_width"] = right - left

        return prepared
