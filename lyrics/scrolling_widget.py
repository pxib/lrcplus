from PySide6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, QTimer, QSize, QVariantAnimation, QObject, Signal
from PySide6.QtGui import QFont, QFontMetrics, QColor, QPainter, QLinearGradient, QBrush, QPen
from PySide6.QtWidgets import QVBoxLayout, QHBoxLayout, QWidget, QScrollArea, QInputDialog
from lyrics.furigana import build_furigana_segments, get_cached_furigana_segments, split_reading_by_text_ranges, kana_to_romaji
from lyrics.widgets import FuriganaWidget

class ScrollingLyricsWidget(QWidget):
    """Scrollable multi-line lyric display with the current line centered."""

    SCROLL_ANIMATION_MS = 300
    BASE_HEIGHT = 54
    RUBY_BAND_HEIGHT = 28
    RUBY_GAP = 2
    LINE_GAP = 8
    ROW_HEIGHT = RUBY_BAND_HEIGHT + BASE_HEIGHT + LINE_GAP

    def __init__(
        self,
        manual_overrides=None,
        direction="center",
        padding=8,
        parent=None,
    ):
        super().__init__(parent)

        self.manual_overrides = (
            manual_overrides if manual_overrides is not None else {}
        )
        self.direction = direction if direction in {"horizontal", "vertical", "auto"} else "horizontal"
        self.vertical_padding = 0
        self.alignment = "center"
        self.padding = max(0, int(padding))
        self.vertical_padding = 0
        self.ruby_padding = 2
        self.show_ruby = True
        self.show_romaji = False
        self.ruby_position = "above"
        self.font_family = ""
        self.font_size = 32
        self.ruby_font_size = 18
        self.lyrics_color = "#ffffff"
        self.highlight_color = "#ffaa00"
        self.highlight_current = True
        self.highlight_animation = "none"
        self.highlight_animation_duration = 300
        self.highlight_animation_out_duration = 300
        self.highlight_animation_easing = "out_cubic"
        self.scroll_animation_ms = self.SCROLL_ANIMATION_MS
        self.scroll_animation_easing = "out_cubic"
        self.scroll_behavior = "center"
        self.scrolling_mode = "automatic"
        self.continuous_scroll_speed = 35.0
        self.continuous_scroll_duration_based = True
        self.continuous_scroll_nudge_current = True
        # Cooperative continuous nudge state. Unlike the old implementation,
        # this never creates a second scrollbar animation.
        self._continuous_nudge_index = -1
        # Persistent offset layered on top of normal continuous motion.
        # This is adjusted smoothly instead of directly moving the scrollbar.
        self._continuous_nudge_offset = 0.0
        # 0..1 smoothed boost strength for easing acceleration/deceleration.
        self._continuous_nudge_strength = 0.0
        self.limited_reading_rendering = False
        self.limited_reading_range = 10
        self.fade_in_readings = True
        self._continuous_last_position_ms = None
        self.rows = []
        self._row_height_refresh_pending = False
        self.current_line = -1
        self.scroll_animation = None

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.container = QWidget()

        # Build the correct layout for the initial direction. Previously we
        # always started with QVBoxLayout and relied on set_direction() to
        # rebuild it. If the widget was constructed already in vertical mode,
        # set_direction("vertical") returned early and the first lyric load
        # stayed in the wrong layout until the user toggled direction.
        if self._is_vertical_layout():
            self.layout = QHBoxLayout(self.container)
            self.layout.setContentsMargins(self.padding, 0, self.padding, 0)
            self.layout.setSpacing(self.padding)
        else:
            self.layout = QVBoxLayout(self.container)
            self.layout.setContentsMargins(0, 90, 0, 90)
            self.layout.setSpacing(0)

        self.scroll_area.setWidget(self.container)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.scroll_area)

        # In continuous/plain-text mode there is no timestamp-driven current
        # line. Keep limited reading rendering tied to the lyric nearest the
        # viewport center instead of falling back to rendering every reading.
        self.scroll_area.verticalScrollBar().valueChanged.connect(
            self._refresh_limited_reading_from_viewport
        )
        self.scroll_area.horizontalScrollBar().valueChanged.connect(
            self._refresh_limited_reading_from_viewport
        )

    def _is_vertical_layout(self):
        return self.direction == "vertical"

    def _rebuild_scroll_layout(self):
        # IMPORTANT: do not use deleteLater() here. A direction toggle can
        # rebuild twice before the event loop destroys the old layout, leaving
        # QWidget temporarily attached to the stale QBoxLayout. Detach and
        # destroy the old layout synchronously before installing the new one.
        old_layout = self.container.layout()
        if old_layout is not None:
            while old_layout.count():
                item = old_layout.takeAt(0)
                widget = item.widget()
                if widget is not None:
                    widget.setParent(None)

            holder = QWidget()
            holder.setLayout(old_layout)
            old_layout = None
            holder.deleteLater()

        if self._is_vertical_layout():
            self.layout = QHBoxLayout()
            self.layout.setContentsMargins(self.padding, 0, self.padding, 0)
            self.layout.setSpacing(self.padding)
        else:
            self.layout = QVBoxLayout()
            self.layout.setContentsMargins(0, 90, 0, 90)
            self.layout.setSpacing(0)

        self.container.setLayout(self.layout)
        self.scroll_area.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)
        self.scroll_area.setVerticalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

        layout_rows = list(self.rows)
        if self._is_vertical_layout() and self.alignment == "right":
            layout_rows.reverse()
        for row in layout_rows:
            self.layout.addWidget(row)

        # Reparenting/layout changes are not final until Qt processes events.
        # Refresh geometry after the new layout owns every row.
        self.container.adjustSize()
        QTimer.singleShot(0, self._refresh_row_heights)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        if self._is_vertical_layout() and self.rows:
            self._refresh_row_heights()

    def _scroll_axis(self):
        return self.scroll_area.horizontalScrollBar() if self._is_vertical_layout() else self.scroll_area.verticalScrollBar()

    def _viewport_extent(self):
        viewport = self.scroll_area.viewport()
        return viewport.width() if self._is_vertical_layout() else viewport.height()

    def _row_axis_center(self, row):
        return row.geometry().center().x() if self._is_vertical_layout() else row.geometry().center().y()

    def _row_axis_start(self, row):
        return row.geometry().left() if self._is_vertical_layout() else row.geometry().top()

    def _row_axis_end(self, row):
        return row.geometry().right() if self._is_vertical_layout() else row.geometry().bottom()

    def _schedule_row_height_refresh(self):
        if self._row_height_refresh_pending:
            return
        self._row_height_refresh_pending = True
        QTimer.singleShot(0, self._run_scheduled_row_height_refresh)

    def _run_scheduled_row_height_refresh(self):
        self._row_height_refresh_pending = False
        self._refresh_row_heights()

    def _refresh_row_heights(self):
        if self._is_vertical_layout():
            viewport_height = max(1, self.scroll_area.viewport().height())

            # Use the actual font metrics. Previously this used rough font-size
            # multipliers while the painter used font heights, so the parent
            # could allocate a narrower widget than the painter required.
            base_font = QFont()
            if self.font_family:
                base_font.setFamily(self.font_family)
            base_font.setPointSize(max(1, int(self.font_size)))
            base_metrics = QFontMetrics(base_font)

            ruby_font = QFont()
            if self.font_family:
                ruby_font.setFamily(self.font_family)
            ruby_font.setPointSize(max(1, int(self.ruby_font_size)))
            ruby_metrics = QFontMetrics(ruby_font)

            char_step = max(1, base_metrics.height() + FuriganaWidget.PADDING)
            drawable = max(
                1,
                viewport_height
                - FuriganaWidget.TOP_MARGIN
                - FuriganaWidget.BOTTOM_MARGIN
                - (self.vertical_padding * 2),
            )
            chars_per_column = max(1, drawable // char_step)

            for row in self.rows:
                row.set_vertical_padding(self.vertical_padding)
                # The row height is part of the actual wrapping calculation.
                # Set it before asking which columns exist so the parent and the
                # renderer use the same drawable vertical span.
                row.setFixedHeight(viewport_height)
                flags = row._vertical_column_ruby_flags(chars_per_column)
                required_width = float(FuriganaWidget.PADDING)
                # Use the same real glyph width as the painter. The old code
                # used ruby font height here, which can be narrower than the
                # actual vertical ruby glyph and leave the first ruby lane
                # clipped at the row edge.
                ruby_lane = row._vertical_ruby_lane_width() if row.show_ruby else 0
                for has_ruby in flags:
                    lane = ruby_lane if has_ruby else 0
                    required_width += (
                        lane
                        + (row.ruby_padding if lane else 0)
                        + base_metrics.height()
                        + FuriganaWidget.PADDING
                    )
                row.setFixedWidth(max(1, int(required_width)))
        else:
            for row in self.rows:
                height = row.minimumSizeHint().height() + self.padding
                row.setMinimumWidth(1)
                row.setMaximumWidth(16777215)
                row.setFixedHeight(max(1, height))

        self.container.adjustSize()
        self.updateGeometry()

    def refresh_layout_now(self):
        """Force a geometry pass after this widget becomes visible.

        Vertical rows may be created while the stacked page is hidden, where
        the viewport can temporarily report a 0/1-pixel height.  A second pass
        after the page is shown prevents the first-open empty/clipped state.
        """
        if self._is_vertical_layout() and self.rows:
            self._refresh_row_heights()
        self.container.adjustSize()
        self.updateGeometry()
        self.update()

    def set_vertical_padding(self, padding):
        padding = max(0, min(200, int(padding)))
        if self.vertical_padding == padding:
            return
        self.vertical_padding = padding
        for row in self.rows:
            row.set_vertical_padding(padding)
        self._refresh_row_heights()
        self.update()

    def set_padding(self, padding):
        padding = max(0, min(24, int(padding)))
        if self.padding == padding:
            return

        self.padding = padding
        if self._is_vertical_layout():
            # Vertical lyrics scroll horizontally, so keep the outer padding
            # and the spacing between lyric columns in sync with this setting.
            self.layout.setContentsMargins(padding, 0, padding, 0)
            self.layout.setSpacing(padding)
        self._refresh_row_heights()
        self.container.adjustSize()
        self.updateGeometry()
        self.update()

    def set_romaji_visible(self, visible):
        self.show_romaji = bool(visible)
        if self.show_romaji:
            self.show_ruby = False
            self.all_romaji = False
        for row in self.rows:
            row.set_romaji_visible(self.show_romaji)
        self._update_reading_render_window(self.current_line, initial=True)
        self._refresh_row_heights()
        self.update()

    def set_all_romaji_visible(self, visible):
        self.all_romaji = bool(visible)
        if self.all_romaji:
            self.show_ruby = False
            self.show_romaji = False
        for row in self.rows:
            row.set_all_romaji_visible(self.all_romaji)
        self._update_reading_render_window(self.current_line, initial=True)
        self._refresh_row_heights()
        self.update()

    def set_ruby_visible(self, visible):
        self.show_ruby = bool(visible)
        if self.show_ruby:
            self.show_romaji = False
        for row in self.rows:
            row.set_ruby_visible(self.show_ruby)
        self._update_reading_render_window(self.current_line, initial=True)
        self._refresh_row_heights()
        self.update()

    def set_ruby_position(self, position):
        position = str(position or "above").lower()
        if position not in {"above", "below"}:
            position = "above"
        self.ruby_position = position
        for row in self.rows:
            row.set_ruby_position(position)
        self._refresh_row_heights()
        self.update()

    def set_ruby_padding(self, padding):
        self.ruby_padding = max(0, min(18, int(padding)))
        for row in self.rows:
            row.set_ruby_padding(self.ruby_padding)
        self._refresh_row_heights()
        self.update()

    def set_direction(self, direction):
        direction = (
            direction
            if direction in {"horizontal", "vertical", "auto"}
            else "horizontal"
        )
        if self.direction == direction:
            # The initial layout can be constructed before rows exist. Make
            # sure a same-direction call still repairs a stale layout type.
            expected = QHBoxLayout if direction == "vertical" else QVBoxLayout
            if not isinstance(self.layout, expected):
                self._rebuild_scroll_layout()
            return
        self.direction = direction
        if self._is_vertical_layout() and self.alignment == "center":
            self.alignment = "left"
        self._rebuild_scroll_layout()
        for row in self.rows:
            row.set_direction(direction)
            row.set_alignment(self.alignment)
        self._refresh_row_heights()
        self.update()

    def set_alignment(self, alignment):
        alignment = (
            alignment
            if alignment in {"left", "center", "right"}
            else "center"
        )
        if self._is_vertical_layout() and alignment == "center":
            alignment = "left"
        changed = self.alignment != alignment
        self.alignment = alignment
        if self._is_vertical_layout() and changed:
            self._rebuild_scroll_layout()
        for row in self.rows:
            row.set_alignment(alignment)
        self.update()

    def set_font(self, family="", size=32, ruby_size=18):
        self.font_family = str(family or "")
        try:
            self.font_size = max(10, min(96, int(size)))
        except (TypeError, ValueError):
            self.font_size = 32
        try:
            self.ruby_font_size = max(6, min(72, int(ruby_size)))
        except (TypeError, ValueError):
            self.ruby_font_size = 18
        for row in self.rows:
            row.set_font(
                self.font_family,
                self.font_size,
                self.ruby_font_size,
            )
        self._refresh_row_heights()
        self.update()

    def set_colors(self, lyrics_color="#ffffff", highlight_color="#ffaa00"):
        self.lyrics_color = str(lyrics_color)
        self.highlight_color = str(highlight_color)
        for row in self.rows:
            row.set_colors(self.lyrics_color, self.highlight_color)
        self.update()

    def set_highlight_current(self, enabled):
        self.highlight_current = bool(enabled)
        for row in self.rows:
            row.set_highlight_current(enabled)
        self.update()

    def set_highlight_animation(
        self,
        animation,
        duration=300,
        easing="out_cubic",
        out_duration=300,
    ):
        self.highlight_animation = str(animation or "none")
        self.highlight_animation_easing = str(
            easing or "out_cubic"
        ).lower()
        try:
            self.highlight_animation_duration = max(
                0,
                min(2000, int(duration)),
            )
        except (TypeError, ValueError):
            self.highlight_animation_duration = 300
        try:
            self.highlight_animation_out_duration = max(
                0,
                min(2000, int(out_duration)),
            )
        except (TypeError, ValueError):
            self.highlight_animation_out_duration = (
                self.highlight_animation_duration
            )

        for row in self.rows:
            row.set_highlight_animation(
                self.highlight_animation,
                self.highlight_animation_duration,
                self.highlight_animation_easing,
                self.highlight_animation_out_duration,
            )
        self.update()

    def set_scroll_animation_speed(self, milliseconds, easing="out_cubic"):
        try:
            milliseconds = max(0, min(1000, int(milliseconds)))
        except (TypeError, ValueError):
            milliseconds = self.SCROLL_ANIMATION_MS
        self.scroll_animation_ms = milliseconds
        self.scroll_animation_easing = str(easing or "out_cubic").lower()

    def set_scroll_behavior(self, behavior):
        self.scroll_behavior = (
            behavior
            if behavior in {"center", "ensure_visible", "off"}
            else "center"
        )

    def set_scrolling_mode(self, mode):
        mode = str(mode or "automatic").lower()
        self.scrolling_mode = (
            mode if mode in {"automatic", "continuous"}
            else "automatic"
        )
        self._continuous_last_position_ms = None

    def set_continuous_scroll_speed(self, pixels_per_second):
        try:
            pixels_per_second = float(pixels_per_second)
        except (TypeError, ValueError):
            pixels_per_second = 35.0
        self.continuous_scroll_speed = max(1.0, min(500.0, pixels_per_second))


    def set_continuous_scroll_options(self, duration_based=True, nudge_current=True):
        self.continuous_scroll_duration_based = bool(duration_based)
        self.continuous_scroll_nudge_current = bool(nudge_current)

    def nudge_current_line_into_view(self, index):
        """Request a cooperative speed boost toward the current lyric.

        Continuous scrolling remains the sole owner of the scrollbar. When the
        current lyric falls below the viewport, update_continuous_scroll()
        temporarily advances faster until that lyric is near the viewport
        center. No competing QPropertyAnimation is created.
        """
        if not self.continuous_scroll_nudge_current:
            self._continuous_nudge_index = -1
            return
        if 0 <= index < len(self.rows):
            self._continuous_nudge_index = index


    def update_continuous_scroll(self, position_ms):
        """Continuous scrolling with a smoothly eased temporary speed boost."""
        if self.scrolling_mode != "continuous":
            self._continuous_last_position_ms = None
            self._continuous_nudge_index = -1
            self._continuous_nudge_offset = 0.0
            self._continuous_nudge_strength = 0.0
            return

        try:
            position_ms = float(position_ms)
        except (TypeError, ValueError):
            return

        scrollbar = self._scroll_axis()
        maximum = scrollbar.maximum()

        # A backward playback jump is a seek, not normal motion. Discard all
        # forward-only nudge state so old catch-up distance cannot survive into
        # the earlier point in the song.
        backward_seek = (
            self._continuous_last_position_ms is not None
            and position_ms < self._continuous_last_position_ms
        )
        if backward_seek:
            self._continuous_nudge_offset = 0.0
            self._continuous_nudge_strength = 0.0

        elapsed_ms = 0.0
        if (
            self._continuous_last_position_ms is not None
            and not backward_seek
        ):
            elapsed_ms = max(
                0.0,
                position_ms - self._continuous_last_position_ms,
            )
        dt = elapsed_ms / 1000.0

        duration = 0.0
        parent = self.parent()
        while parent is not None:
            player = getattr(parent, "media_player", None)
            if player is not None and hasattr(player, "duration"):
                duration = float(player.duration() or 0)
                break
            parent = parent.parent()

        if maximum > 0:
            # A seek must be allowed to establish a new position immediately.
            # Duration-based scrolling has an exact playback-position mapping.
            if self.continuous_scroll_duration_based and duration > 0:
                ratio = max(0.0, min(1.0, position_ms / duration))
                base_target = float(maximum) * ratio
                if backward_seek:
                    scrollbar.setValue(int(round(base_target)))
            else:
                base_target = float(scrollbar.value())
                if self._continuous_last_position_ms is not None and not backward_seek:
                    base_target += (
                        dt * self.continuous_scroll_speed
                    )
                elif backward_seek:
                    # Without a duration mapping, reset the forward-only state
                    # at the seek point and let normal continuous motion resume
                    # from the newly established scrollbar position.
                    self._continuous_nudge_offset = 0.0
                    self._continuous_nudge_strength = 0.0

            if backward_seek:
                target = base_target
            else:
                target = max(
                    float(scrollbar.value()),
                    base_target + self._continuous_nudge_offset,
                )

            desired_strength = 0.0
            index = self._continuous_nudge_index
            if (
                not backward_seek
                and self.continuous_scroll_nudge_current
                and 0 <= index < len(self.rows)
            ):
                extent = max(1.0, float(self._viewport_extent()))
                viewport_center = target + extent / 2.0
                row_center = float(self._row_axis_center(self.rows[index]))
                distance = row_center - viewport_center
                dead_zone = max(8.0, extent * 0.08)

                if distance > dead_zone:
                    desired_strength = min(
                        1.0,
                        (distance - dead_zone) / extent,
                    )

            if dt > 0:
                response = (
                    0.16
                    if desired_strength > self._continuous_nudge_strength
                    else 0.32
                )
                alpha = 1.0 - math.exp(-dt / response)
                self._continuous_nudge_strength += (
                    desired_strength - self._continuous_nudge_strength
                ) * alpha

                if self._continuous_nudge_strength > 0.001:
                    extra_speed = (
                        max(1.0, self.continuous_scroll_speed)
                        * 4.0
                        * self._continuous_nudge_strength
                    )
                    self._continuous_nudge_offset += dt * extra_speed
                    target = max(
                        target,
                        base_target + self._continuous_nudge_offset,
                    )

                if (
                    desired_strength == 0.0
                    and self._continuous_nudge_strength < 0.002
                ):
                    self._continuous_nudge_strength = 0.0
                    self._continuous_nudge_index = -1

            scrollbar.setValue(
                min(maximum, max(0, int(round(target))))
            )

        self._continuous_last_position_ms = position_ms


    def set_limited_reading_rendering(self, enabled, nearby_range=10, fade_in=True):
        self.limited_reading_rendering = bool(enabled)
        try:
            nearby_range = int(nearby_range)
        except (TypeError, ValueError):
            nearby_range = 10
        self.limited_reading_range = max(5, min(15, nearby_range))
        self.fade_in_readings = bool(fade_in)
        self._update_reading_render_window(self.current_line, initial=True)

    def _refresh_limited_reading_from_viewport(self, _value=None):
        """Refresh the limited-reading window around the visible lyric.

        Continuous/TXT lyrics intentionally have ``current_line == -1``.
        Previously that state was treated as "render every reading", which
        made the Limited reading rendering setting appear to do nothing.
        """
        if (
            not self.limited_reading_rendering
            or not self.show_ruby
            or not self.rows
            or self.current_line >= 0
        ):
            return

        anchor = self._visible_reading_anchor()
        if anchor >= 0:
            self._update_reading_render_window(anchor)

    def _visible_reading_anchor(self):
        """Return the row nearest the center of the current viewport."""
        if not self.rows:
            return -1

        if self._is_vertical_layout():
            scrollbar = self.scroll_area.horizontalScrollBar()
            viewport_center = scrollbar.value() + self.scroll_area.viewport().width() / 2.0
            centers = [
                row.geometry().x() + row.geometry().width() / 2.0
                for row in self.rows
            ]
        else:
            scrollbar = self.scroll_area.verticalScrollBar()
            viewport_center = scrollbar.value() + self.scroll_area.viewport().height() / 2.0
            centers = [
                row.geometry().y() + row.geometry().height() / 2.0
                for row in self.rows
            ]

        return min(
            range(len(self.rows)),
            key=lambda row_index: abs(centers[row_index] - viewport_center),
        )

    def _update_reading_render_window(self, index, initial=False):
        if not self.rows:
            return

        if not (self.show_ruby or self.show_romaji):
            for row in self.rows:
                row.set_reading_rendered(False)
            return

        if not self.limited_reading_rendering:
            for row in self.rows:
                row.set_reading_rendered(True, fade=False)
            return

        # ``index < 0`` occurs for continuous/plain-text lyrics. Anchor the
        # render window to what is actually on screen rather than disabling
        # the optimization for the entire lyric.
        if index < 0:
            index = self._visible_reading_anchor()
            if index < 0:
                return

        start = max(0, index - self.limited_reading_range)
        end = min(len(self.rows), index + self.limited_reading_range + 1)
        for row_index, row in enumerate(self.rows):
            visible = start <= row_index < end
            changed = visible != row._reading_render_target_enabled
            row.set_reading_rendered(
                visible,
                fade=(
                    changed
                    and self.fade_in_readings
                    and not initial
                ),
            )

    def clear(self):
        self.current_line = -1
        if self.scroll_animation is not None:
            self.scroll_animation.stop()
            self.scroll_animation = None

        # Remove widgets from the active layout immediately as well as from
        # the Python list. Otherwise stale layout items can survive until the
        # event loop and poison the next direction rebuild.
        while self.layout.count():
            item = self.layout.takeAt(0)
            widget = item.widget()
            if widget is not None:
                widget.setParent(None)
                widget.deleteLater()

        self.rows.clear()

    def set_lyrics(self, lyrics):
        self.clear()
        self._continuous_last_position_ms = None

        for lyric in lyrics:
            row = FuriganaWidget(self.manual_overrides)
            row.set_direction(self.direction)
            row.set_vertical_padding(self.vertical_padding)
            row.set_alignment(self.alignment)
            # Centered Slide mode may enter from either side. Pick once per
            # row and keep it stable for the life of that lyric row.
            row.set_slide_side(
                "left" if (len(self.rows) % 2 == 0) else "right"
            )
            row.set_font(
                self.font_family,
                self.font_size,
                self.ruby_font_size,
            )
            row.set_colors(self.lyrics_color, self.highlight_color)
            row.set_highlight_current(self.highlight_current)
            row.set_highlight_animation(
                self.highlight_animation,
                self.highlight_animation_duration,
                self.highlight_animation_easing,
                self.highlight_animation_out_duration,
            )
            row.set_karaoke_properties(
                getattr(self, "karaoke_sweep_style", "classic"),
                getattr(self, "karaoke_sweep_easing", "linear"),
                getattr(self, "karaoke_softness", 8),
                getattr(self, "karaoke_glow_intensity", 70),
                getattr(self, "karaoke_glow_radius", 1),
                getattr(self, "karaoke_shimmer_width", 10),
                getattr(self, "karaoke_sweep_delay_ms", 100),
                getattr(self, "karaoke_sweep_delay_percent", 10),
            )
            row.set_current_line(False)
            row.set_ruby_visible(self.show_ruby)
            row.set_romaji_visible(self.show_romaji)
            row.set_ruby_position(self.ruby_position)
            row.set_ruby_padding(self.ruby_padding)
            row.set_active(False)
            row.set_opacity(0.32)

            # LyricLine objects carry explicit LRCX ruby segments.
            if hasattr(lyric, "segments"):
                if lyric.segments:
                    row.set_segments(
                        lyric.text,
                        lyric.segments,
                        getattr(lyric, "end", None),
                    )
                else:
                    source_text = str(lyric.text)
                    display_text = (
                        getattr(
                            self,
                            "_empty_timestamp_static_placeholder",
                            "",
                        )
                        if not source_text.strip()
                        else source_text
                    )
                    row.set_lyric(display_text, generate=False)
                    if not source_text.strip():
                        row._lyrics_plus_placeholder_source_empty = True
            else:
                _, text = lyric
                source_text = str(text)
                display_text = (
                    getattr(
                        self,
                        "_empty_timestamp_static_placeholder",
                        "",
                    )
                    if not source_text.strip()
                    else source_text
                )
                row.set_lyric(display_text, generate=False)
                if not source_text.strip():
                    row._lyrics_plus_placeholder_source_empty = True

            # Horizontal mode uses a normal stacked row height. Vertical mode
            # is fundamentally different: each lyric occupies the viewport
            # height and its width is computed from the number of columns it
            # needs. Do not apply the old horizontal fixed-height rule here.
            if not self._is_vertical_layout():
                row.setFixedHeight(
                    row.minimumSizeHint().height() + self.padding
                )
            self.rows.append(row)
            if self._is_vertical_layout() and self.alignment == "right":
                self.layout.insertWidget(0, row)
            else:
                self.layout.addWidget(row)

        # This must happen after every row has been added. Previously the
        # vertical dimensions were only refreshed when a later setting or
        # resize event occurred, leaving the freshly loaded lyric list with
        # the old horizontal geometry.
        if self._is_vertical_layout() and self.rows:
            self._refresh_row_heights()
            # The viewport can still be 0/1 pixels during the first event
            # loop pass. Refresh once more after Qt has laid out the scroll
            # area so the initial left/right direction has real geometry.
            QTimer.singleShot(0, self.refresh_layout_now)
            # One more queued pass catches the case where the lyrics page is
            # switched into a QStackedWidget during the same event turn.
            QTimer.singleShot(1, self.refresh_layout_now)

        if self.rows:
            self.rows[0].set_active(True)
            self.rows[0].set_opacity(1.0)
            self._update_reading_render_window(0, initial=True)

        QTimer.singleShot(0, lambda: self._scroll_to_line(0, False))

    def set_karaoke_properties(
        self,
        style="classic",
        easing="linear",
        softness=8,
        glow_intensity=70,
        glow_radius=1,
        shimmer_width=10,
        sweep_delay_ms=100,
        sweep_delay_percent=10,
    ):
        self.karaoke_sweep_style = style
        self.karaoke_sweep_easing = easing
        self.karaoke_softness = softness
        self.karaoke_glow_intensity = glow_intensity
        self.karaoke_glow_radius = glow_radius
        self.karaoke_shimmer_width = shimmer_width
        self.karaoke_sweep_delay_ms = sweep_delay_ms
        self.karaoke_sweep_delay_percent = sweep_delay_percent
        for row in self.rows:
            row.set_karaoke_properties(
                style,
                easing,
                softness,
                glow_intensity,
                glow_radius,
                shimmer_width,
                sweep_delay_ms,
                sweep_delay_percent,
            )
        self.update()

    def set_karaoke_sweep_style(self, style):
        """Set the karaoke sweep style for every scrolling lyric row."""
        style = str(style or "classic").lower()
        if style not in {"classic", "soft", "glow", "shimmer"}:
            style = "classic"

        self.karaoke_sweep_style = style
        for row in self.rows:
            row.set_karaoke_sweep_style(style)

        self.update()

    def set_karaoke_position(self, position):
        """Update karaoke while keeping past rows correct across seeks."""
        if not self.rows:
            self._karaoke_row_index = None
            self._last_karaoke_position = None
            return

        # Stopping/unloading karaoke resets every row.
        if position is None:
            for row in self.rows:
                if row.karaoke_position is not None:
                    row.set_karaoke_position(None)
            self._karaoke_row_index = None
            self._last_karaoke_position = None
            return

        position = float(position)
        last_position = getattr(self, "_last_karaoke_position", None)

        # A backward seek, or a large forward jump, invalidates the frozen-row
        # optimization. Re-sync every timed row once so past/current/future
        # highlights immediately match the new playback position.
        discontinuity = (
            last_position is not None
            and (
                position < last_position - 0.050
                or position > last_position + 0.500
            )
        )

        if discontinuity:
            for row in self.rows:
                segments = getattr(row, "segments", ()) or ()
                has_timing = any(
                    isinstance(segment, dict)
                    and (
                        segment.get("karaoke_group") is not None
                        or segment.get("start") is not None
                    )
                    for segment in segments
                )
                if has_timing:
                    row.set_karaoke_position(position)

        current = getattr(self, "current_line", -1)
        if not (0 <= current < len(self.rows)):
            current = None

        previous = getattr(self, "_karaoke_row_index", None)

        if (
            not discontinuity
            and previous is not None
            and previous != current
            and 0 <= previous < len(self.rows)
        ):
            # Finish the previous line rather than freezing it at the new
            # line's timestamp. Adjacent/overlapping line timing can otherwise
            # leave the last chunk only partially highlighted.
            previous_row = self.rows[previous]
            segments = getattr(previous_row, "segments", ()) or ()
            finish_at = position
            for segment in segments:
                if not isinstance(segment, dict):
                    continue
                for key in ("end", "line_end", "start"):
                    value = segment.get(key)
                    if value is not None:
                        try:
                            finish_at = max(finish_at, float(value))
                        except (TypeError, ValueError):
                            pass
            # Some files place the next lyric before the last segment's start
            # (for example, a trailing segment at 55.800 after a line at
            # 55.450). In that case the segment has no usable interval and
            # falls back to a one-second sweep. Move past that fallback too,
            # so the completed row is fully filled when playback advances.
            previous_row.set_karaoke_position(finish_at + 1.0)

        if current is not None:
            row = self.rows[current]
            segments = getattr(row, "segments", ()) or ()
            has_timing = any(
                isinstance(segment, dict)
                and (
                    segment.get("karaoke_group") is not None
                    or segment.get("start") is not None
                )
                for segment in segments
            )
            if has_timing:
                row.set_karaoke_position(position)

        self._karaoke_row_index = current
        self._last_karaoke_position = position

    def refresh_furigana_for_texts(self, texts=None):
        wanted = None if texts is None else {str(text).lstrip() for text in texts}

        for row_index, row in enumerate(self.rows):
            if wanted is None or row.lyric in wanted:
                # Timed LRCX segments contain karaoke metadata. Replacing them
                # with a plain full-line furigana cache destroys that metadata
                # and makes highlighting stop working.
                has_timing_metadata = any(
                    segment.get("karaoke_group") is not None
                    or segment.get("start") is not None
                    for segment in row.segments
                    if isinstance(segment, dict)
                )
                if has_timing_metadata:
                    continue

                cached = get_cached_furigana_segments(row.lyric)
                if cached is not None:
                    row.segments = row.apply_manual_overrides(cached)
                    row._update_content_geometry()
                    row.update()

    def set_placeholder_text(self, index, text):
        """Update the display text for an empty timestamp row."""
        if not (0 <= index < len(self.rows)):
            return

        row = self.rows[index]
        if not getattr(row, "_lyrics_plus_placeholder_source_empty", False):
            return

        text = str(text)
        if row.lyric == text:
            return

        row.lyric = text
        row.segments = [{"text": text, "reading": None}]
        row.loading_text = None
        row.update()

    def clear_placeholder_text(self, index):
        """Restore the static placeholder after the active countdown."""
        if not (0 <= index < len(self.rows)):
            return

        row = self.rows[index]
        if not getattr(row, "_lyrics_plus_placeholder_source_empty", False):
            return

        static_text = str(
            getattr(
                self,
                "_empty_timestamp_static_placeholder",
                "♪",
            )
        )
        if row.lyric != static_text:
            row.lyric = static_text
            row.segments = [{"text": static_text, "reading": None}]
            row.loading_text = None
            row.update()

    def set_current_line(self, index, animate=True):
        if not self.rows:
            self.current_line = -1
            return

        if index < 0 or index >= len(self.rows):
            # No current lyric means no row may retain the visual emphasis
            # created during initial layout. TXT uses this state permanently
            # because its synthetic line indices are not timestamps.
            for row in self.rows:
                row.set_active(False)
                row.set_current_line(False)
                row.set_slide_future(True)
                row.set_opacity(0.32)
            self.current_line = -1
            self._update_reading_render_window(-1, initial=True)
            return

        if index == self.current_line and animate:
            return

        for row_index, row in enumerate(self.rows):
            distance = abs(row_index - index)
            if distance == 0:
                opacity = 1.0
            elif distance == 1:
                opacity = 0.65
            elif distance == 2:
                opacity = 0.48
            else:
                opacity = 0.32

            row.set_active(row_index == index)
            row.set_current_line(row_index == index)
            row.set_slide_future(row_index > index)
            row.set_opacity(opacity)

        self.current_line = index
        self._update_reading_render_window(index)
        if self.scrolling_mode == "automatic":
            QTimer.singleShot(0, lambda: self._scroll_to_line(index, animate))

    def _scroll_to_line(self, index, animate=True):
        if not (0 <= index < len(self.rows)):
            return

        row = self.rows[index]
        scrollbar = self._scroll_axis()
        extent = self._viewport_extent()
        if self.scroll_behavior == "off":
            return

        if self.scroll_behavior in {"ensure_visible", "focus_current"}:
            target = (
                self._row_axis_center(row) - extent / 2
                if self.scroll_behavior == "focus_current"
                else self._row_axis_start(row) - 20
            )
        else:
            target = self._row_axis_center(row) - extent / 2

        target = int(max(0, min(target, scrollbar.maximum())))
        if not animate:
            scrollbar.setValue(target)
            return

        if self.scroll_animation is not None:
            self.scroll_animation.stop()

        animation = QPropertyAnimation(scrollbar, b"value", self)
        animation.setDuration(self.scroll_animation_ms)
        animation.setStartValue(scrollbar.value())
        animation.setEndValue(target)
        easing_map = {
            "linear": QEasingCurve.Type.Linear,
            "in_out_quad": QEasingCurve.Type.InOutQuad,
            "out_quart": QEasingCurve.Type.OutQuart,
            "out_expo": QEasingCurve.Type.OutExpo,
            "out_cubic": QEasingCurve.Type.OutCubic,
        }
        animation.setEasingCurve(easing_map.get(self.scroll_animation_easing, QEasingCurve.Type.OutCubic))
        animation.finished.connect(lambda: self._clear_animation(animation))
        self.scroll_animation = animation
        animation.start()

    def _clear_animation(self, animation):
        if self.scroll_animation is animation:
            self.scroll_animation = None

