"""Furigana painting, decorations and animated karaoke highlight effects."""

from PySide6.QtCore import Qt, QPointF, QRectF
from PySide6.QtGui import QFontMetrics, QColor, QPainter, QPen
from lyrics.karaoke_effects import eased_progress, sweep_effect_pen, text_color_pen


class FuriganaPaintingMixin:
    def _paint_decoration_spans(self, painter, segment, normal, highlight, progress, timing_span, sliding, opacity=None):
        spans = segment.get("_decorations", ())
        if not spans:
            return
        reveal = None
        if progress > 0:
            value = min(1.0, max(0.0, progress))
            easing = getattr(self, "karaoke_sweep_easing", "linear")
            if easing == "ease_in":
                value *= value
            elif easing == "ease_out":
                value = 1 - (1 - value) ** 2
            elif easing == "ease_in_out":
                value = 2 * value * value if value < 0.5 else 1 - (-2 * value + 2) ** 2 / 2
            left, right = timing_span or (segment["x"], segment["x"] + segment["width"])
            reveal = left + (right - left) * value
            if progress >= 1:
                reveal = max(reveal, max(span["right"] for span in spans))
        elif sliding:
            reveal = self.width() * max(0.0, min(1.0, self.highlight_progress))
        for span in spans:
            reading = span["band"] == "reading"
            if reading and (not (self.show_ruby or self.show_romaji)
                            or not self.reading_render_enabled or self.reading_opacity <= 0):
                continue
            left, right, y = span["left"], span["right"], span["y"]
            painter.save()
            painter.setOpacity((self.display_opacity if opacity is None else opacity) * (self.reading_opacity if reading else 1.0))
            for color, start, end in (
                (normal, max(left, reveal) if reveal is not None else left, right),
                (highlight, left, min(right, reveal) if reveal is not None else left),
            ):
                if end <= start:
                    continue
                painter.save()
                thickness = span["thickness"]
                painter.setClipRect(QRectF(start, y - thickness, end - start, thickness * 2 + 1), Qt.ClipOperation.IntersectClip)
                painter.setPen(QPen(color, thickness, Qt.PenStyle.SolidLine, Qt.PenCapStyle.FlatCap))
                painter.drawLine(QPointF(left, y), QPointF(right, y))
                painter.restore()
            painter.restore()

    def _blended_highlight_color(self):
        if self.highlight_animation != "fade":
            return self.highlight_color

        progress = max(0.0, min(1.0, self.highlight_progress))
        a = self.lyrics_color
        b = self.highlight_color
        return QColor(
            round(a.red() + (b.red() - a.red()) * progress),
            round(a.green() + (b.green() - a.green()) * progress),
            round(a.blue() + (b.blue() - a.blue()) * progress),
        )

    def _highlight_scale(self):
        # Karaoke timing controls the per-segment fill, while highlight
        # animation controls the lyric widget itself. They can coexist.
        if self.highlight_animation == "none":
            return 1.0

        progress = max(0.0, min(1.0, self.highlight_progress))

        if self.highlight_animation == "scale":
            # Every inactive row is exactly the same size: 88%.
            return 0.88 + 0.12 * progress

        if self.highlight_animation == "pop":
            import math
            # Same 88% resting scale for every inactive row. On entry,
            # rise to 100% with a controlled overshoot, then settle.
            base = 0.88 + 0.12 * progress
            overshoot = 0.10 * math.sin(progress * math.pi)
            return base + overshoot

        return 1.0

    def _highlight_slide_offset(self):
        if self.highlight_animation != "slide":
            return 0.0
        progress = max(0.0, min(1.0, self.highlight_progress))

        # Slide is enter-only. Current lyrics travel from their assigned
        # off-screen side to their final position.
        if not self.is_current_line:
            return 0.0

        direction = self._resolved_slide_side()
        distance = (1.0 - progress) * self.width()
        return -distance if direction == "left" else distance

    def _karaoke_text_pen(self, segment, progress, normal, timing_spans,
                          vertical_piece_sweep, metrics, *, reading=False):
        if progress <= 0.0:
            return text_color_pen(normal)
        vertical = segment.get("reading_vertical") if reading else segment.get("_vertical")
        if segment.get("_vertical"):
            start, end = vertical_piece_sweep.get(id(segment), (0.0, 1.0))
            progress = (min(1.0, progress) - start) / max(1e-9, end - start)
        progress = eased_progress(progress, self.karaoke_sweep_easing)
        if vertical:
            if reading:
                left = float(segment.get("reading_y", 0)) - metrics.ascent()
                extent = len(segment.get("reading") or "") * segment.get("reading_step", metrics.height())
            else:
                left = float(segment["base_y"]) - metrics.ascent()
                extent = float(segment.get("height", metrics.height()))
        else:
            group = segment.get("timing_group", segment.get("karaoke_group"))
            span = timing_spans.get((group, segment.get("base_y")))
            if span is not None:
                left, right = span
                extent = right - left
            else:
                left = float(segment.get("karaoke_x", segment["x"]))
                extent = float(segment.get("karaoke_width", segment["width"]))
        return sweep_effect_pen(
            self.karaoke_sweep_style, normal, self.highlight_color,
            left, extent, progress, font_height=metrics.height(), vertical=bool(vertical),
            softness=self.karaoke_softness, glow_intensity=self.karaoke_glow_intensity,
            glow_radius=self.karaoke_glow_radius, shimmer_width=self.karaoke_shimmer_width,
        )

    def paintEvent(self, event):
        # In Slide mode, upcoming lyrics do not exist visually until their
        # timestamp/current-line turn arrives.
        if self.highlight_animation == "slide" and self.slide_future:
            return

        painter = QPainter(self)
        painter.setRenderHint(
            QPainter.RenderHint.TextAntialiasing
        )

        if not self.lyric:
            painter.end()
            return

        prepared = self._prepare_segments()
        if not prepared:
            painter.end()
            return

        # Used by the vertical karaoke brush geometry below. Keep these
        # metrics in paintEvent so the highlight uses the exact same base
        # font measurements as the normal glyph rendering.
        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        painter.save()
        entrance = self.line_entrance_progress if self.karaoke_line_entrance else 1.0
        line_opacity = self.display_opacity * (0.2 + 0.8 * entrance)
        if self.focus_transition:
            line_opacity *= 0.7 + 0.3 * self.focus_progress
        painter.setOpacity(line_opacity)
        if entrance < 1.0:
            painter.translate(0, base_metrics.height() * 0.16 * (1 - entrance))

        highlight_scale = self._highlight_scale()
        slide_offset = self._highlight_slide_offset()
        if slide_offset:
            painter.translate(slide_offset, 0)
        if highlight_scale != 1.0:
            # Scale around the alignment edge instead of always around the
            # widget center. This keeps left/right aligned lyrics attached to
            # their own edge and prevents the huge empty gap during Scale/Pop.
            if self.alignment == "left":
                anchor_x = 0.0
            elif self.alignment == "right":
                anchor_x = float(self.width())
            else:
                anchor_x = self.width() / 2.0

            # Keep vertical scaling centered so ruby/base text expand evenly.
            anchor_y = self.height() / 2.0
            painter.translate(anchor_x, anchor_y)
            painter.scale(highlight_scale, highlight_scale)
            painter.translate(-anchor_x, -anchor_y)

        romaji_x_scale = getattr(self, "_all_romaji_x_scale", 1.0)
        quote_bar = self._quote_bar_rect(prepared)
        if not quote_bar.isEmpty():
            color = QColor(self.lyrics_color)
            color.setAlphaF(color.alphaF() * 0.45)
            painter.fillRect(quote_bar, color)
        if romaji_x_scale < 1.0 and self.direction != "vertical":
            indent = self._quote_indent()
            painter.translate(indent, 0)
            painter.scale(romaji_x_scale, 1.0)
            painter.translate(-indent, 0)

        # Qt clips pixels, but shaping and drawing offscreen text still costs
        # CPU time. Map the viewport through animation transforms before
        # culling, with space for glyph overhangs and the karaoke glow.
        visible_rect = painter.worldTransform().inverted()[0].mapRect(
            QRectF(self.rect())
        )
        paint_margin = base_metrics.height() + reading_metrics.height() + 8

        # Build visual karaoke geometry per timed group. Horizontal lyrics
        # sweep across X; vertical lyrics sweep DOWN the visual piece sequence.
        # A timed source word may be split into multiple display characters or
        # wrapped columns, so vertical karaoke cannot be represented by one
        # global QRectF spanning the whole group.
        timing_spans = {}
        vertical_group_pieces = {}
        vertical_piece_sweep = {}

        for piece in prepared:
            group = piece.get("timing_group", piece.get("karaoke_group"))
            if group is None:
                continue

            if piece.get("_vertical"):
                vertical_group_pieces.setdefault(group, []).append(piece)
                continue
            group = (group, piece.get("base_y"))

            # Rebuild from the actual visual piece geometry on this paint pass.
            # Never reuse karaoke_x/karaoke_width here: those values may belong
            # to a previous layout pass and can make one timed chunk appear as
            # disconnected token-local sweeps.
            left = float(piece.get("x", 0.0))
            width_value = float(piece.get("width", 0.0))
            right = left + max(0.0, width_value)
            if group not in timing_spans:
                timing_spans[group] = [left, right]
            else:
                timing_spans[group][0] = min(timing_spans[group][0], left)
                timing_spans[group][1] = max(timing_spans[group][1], right)

        # Resolve karaoke progress ONCE per stable timing group. Generated ruby
        # may create many visual pieces, but those pieces must never get their
        # own independent timing calculation.
        group_progress = {}
        group_lift = {}
        for piece in prepared:
            group = piece.get("timing_group", piece.get("karaoke_group"))
            if group is None or group in group_progress:
                continue
            group_progress[group] = self._segment_progress(piece, prepared)
            group_lift[group] = self._word_lift_offset(piece)

        # Give every vertical visual piece a stable portion of its group's
        # karaoke sweep. Prepared order is also reading order: top-to-bottom,
        # then the next wrapped column.
        for group, pieces in vertical_group_pieces.items():
            total = sum(max(1.0, float(piece.get("height", 1.0))) for piece in pieces)
            cursor = 0.0
            for piece in pieces:
                span = max(1.0, float(piece.get("height", 1.0)))
                vertical_piece_sweep[id(piece)] = (cursor / total, (cursor + span) / total)
                cursor += span

        for segment in prepared:
            width = segment["width"]
            base_x = (
                segment["x"]
                + (width - segment["base_width"]) / 2
            )
            reading_width = segment["_display_reading_width"]
            reading_x = segment.get(
                "reading_x", segment["x"] + (width - reading_width) / 2
            )
            left = min(base_x, reading_x)
            right = max(base_x + segment["base_width"], reading_x + reading_width)
            for decoration in segment.get("_decorations", ()):
                left = min(left, decoration["left"])
                right = max(right, decoration["right"])
            if (right < visible_rect.left() - paint_margin
                    or left > visible_rect.right() + paint_margin):
                continue

            timing_group = segment.get("timing_group", segment.get("karaoke_group"))
            lift = group_lift[timing_group] if timing_group in group_lift else self._word_lift_offset(segment)
            painter.save()
            painter.translate(0, lift)
            painter.setFont(self._paint_font(self.base_font, segment))

            # Each glyph is painted once with a brush containing both colors.
            normal_pen = self.lyrics_color
            highlight_pen = self.highlight_color
            timing_group = segment.get(
                "timing_group", segment.get("karaoke_group")
            )
            if timing_group in group_progress:
                progress = group_progress[timing_group]
            else:
                progress = self._segment_progress(segment, prepared)

            normal_highlight = (
                self.is_current_line
                and self.highlight_current
                and self.karaoke_position is None
            )

            # Fade is special: the old line is no longer "current", but it
            # still needs to remain visually blended while its out animation
            # runs from highlight color back to the normal lyric color.
            fading = (
                self.highlight_animation == "fade"
                and self.highlight_current
                and self.karaoke_position is None
                and self.highlight_progress > 0.0
            )

            if normal_highlight or fading:
                if self.highlight_animation == "fade":
                    normal_pen = self._blended_highlight_color()
                elif self.highlight_animation in {"none", "pop", "scale"}:
                    normal_pen = self.highlight_color

            painter.setPen(self._karaoke_text_pen(
                segment, progress, normal_pen, timing_spans,
                vertical_piece_sweep, base_metrics,
            ))
            if segment.get("_vertical"):
                painter.drawText(
                    int(segment.get("base_x", segment["x"])),
                    int(segment["base_y"]),
                    segment["text"],
                )
            else:
                painter.drawText(
                    int(base_x),
                    int(segment["base_y"]),
                    segment["text"],
                )

            # Slide sweeps the current-line highlight across the lyric.
            if (
                normal_highlight
                and self.highlight_animation == "slide"
            ):
                reveal_right = (
                    self.width() * max(
                        0.0,
                        min(1.0, self.highlight_progress),
                    )
                )
                left = segment["x"]
                reveal_width = max(
                    0.0,
                    min(segment["width"], reveal_right - left),
                )
                if reveal_width > 0:
                    painter.save()
                    painter.setClipRect(
                        QRectF(
                            left,
                            0,
                            reveal_width,
                            self.height(),
                        )
                    )
                    painter.setPen(highlight_pen)
                    painter.drawText(
                        int(base_x),
                        int(segment["base_y"]),
                        segment["text"],
                    )
                    painter.restore()

            # Be defensive here: renderer input can come from multiple
            # lyric sources/plugins, and a malformed segment should not abort
            # the entire QPainter pass after some text has already been drawn.
            reading = segment.get("reading")
            display_reading = segment["_display_reading"]

            if (
                (self.show_ruby or self.show_romaji)
                and self.reading_render_enabled
                and self.reading_opacity > 0.0
                and display_reading
                and (not segment.get("_vertical") or self.show_ruby)
            ):
                painter.save()
                painter.setOpacity(line_opacity * self.reading_opacity)
                reading_x = (
                    segment["x"]
                    + (
                        width
                        - segment["_display_reading_width"]
                    )
                    / 2
                )

                painter.setFont(self._paint_font(self.reading_font, segment))

                painter.setPen(self._karaoke_text_pen(
                    segment, progress, normal_pen, timing_spans,
                    vertical_piece_sweep, reading_metrics, reading=True,
                ))
                if segment.get("reading_vertical"):
                    reading_x = segment.get("reading_x", reading_x)
                    reading_y = segment.get("reading_y", self.TOP_MARGIN + reading_metrics.ascent() if False else self.TOP_MARGIN)
                    cursor_y = reading_y
                    for reading_char in reading:
                        painter.drawText(int(reading_x), int(cursor_y), reading_char)
                        cursor_y += segment.get("reading_step", reading_metrics.height())
                else:
                    if self.show_romaji:
                        romaji_y = segment.get("romaji_y")
                        if romaji_y is None:
                            romaji_y = (
                                self.TOP_MARGIN
                                + base_metrics.height()
                                + self.ruby_padding
                                + base_metrics.descent()
                                + reading_metrics.ascent()
                            )
                        draw_y = romaji_y
                    else:
                        draw_y = segment["reading_y"]
                    painter.drawText(
                        int(reading_x),
                        int(draw_y),
                        display_reading,
                    )

                # Slide highlighting must cover ruby too, not only base text.
                if (
                    not segment.get("_vertical")
                    and normal_highlight
                    and self.highlight_animation == "slide"
                ):
                    reveal_right = (
                        self.width()
                        * max(0.0, min(1.0, self.highlight_progress))
                    )
                    left = segment["x"]
                    reveal_width = max(
                        0.0,
                        min(width, reveal_right - left),
                    )
                    if reveal_width > 0:
                        painter.save()
                        painter.setClipRect(
                            QRectF(
                                left,
                                0,
                                reveal_width,
                                self.height(),
                            )
                        )
                        painter.setPen(highlight_pen)
                        if self.show_romaji:
                            romaji_y = segment.get("romaji_y")
                            if romaji_y is None:
                                romaji_y = (
                                    self.TOP_MARGIN
                                    + base_metrics.height()
                                    + self.ruby_padding
                                    + base_metrics.descent()
                                    + reading_metrics.ascent()
                                )
                            draw_y = romaji_y
                        else:
                            draw_y = segment["reading_y"]
                        painter.drawText(
                            int(reading_x),
                            int(draw_y),
                            display_reading,
                        )
                        painter.restore()

                painter.restore()

            self._paint_decoration_spans(
                painter, segment, normal_pen, highlight_pen, progress,
                timing_spans.get((timing_group, segment.get("base_y"))),
                normal_highlight and self.highlight_animation == "slide",
                opacity=line_opacity,
            )
            painter.restore()

        painter.restore()
        painter.end()
