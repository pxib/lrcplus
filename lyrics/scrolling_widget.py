from bisect import bisect_right
import math

from PySide6.QtCore import Qt, QPropertyAnimation, QEasingCurve, QTimer, QEvent
from PySide6.QtGui import QFont, QFontMetrics
from PySide6.QtWidgets import QVBoxLayout, QWidget, QScrollArea
from lyrics.furigana import get_cached_furigana_segments
from lyrics.widgets import FuriganaWidget

class ScrollingLyricsWidget(QWidget):
    """Scrollable lyrics with widgets allocated only around the viewport.

    ``rows`` contains the mounted widgets, each with a stable ``_lyric_index``.
    Text and timing for the complete document live separately in ``_lyrics``.
    """

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
        self._lyrics = []
        self._mounted_rows = {}
        self._extents = []
        self._starts = [0]
        self._geometry_signature = None
        self._syncing_rows = False
        self._placeholder_texts = {}
        self._row_height_refresh_pending = False
        self._row_height_timer = QTimer(self)
        self._row_height_timer.setSingleShot(True)
        self._row_height_timer.timeout.connect(self._run_scheduled_row_height_refresh)
        self.current_line = -1
        self.scroll_animation = None

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(False)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.container = QWidget()

        self.scroll_area.setWidget(self.container)
        self.scroll_area.viewport().installEventFilter(self)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.scroll_area)

        self.scroll_area.verticalScrollBar().valueChanged.connect(self._sync_visible_rows)
        self.scroll_area.horizontalScrollBar().valueChanged.connect(self._sync_visible_rows)

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


    @property
    def lyric_count(self):
        return len(self._lyrics)

    def _rebuild_scroll_layout(self):
        self._geometry_signature = None
        self._refresh_row_heights()

    def eventFilter(self, watched, event):
        if event.type() == QEvent.Type.Resize:
            self._schedule_row_height_refresh()
        return super().eventFilter(watched, event)

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self._schedule_row_height_refresh()

    def _scroll_axis(self):
        return self.scroll_area.horizontalScrollBar() if self._is_vertical_layout() else self.scroll_area.verticalScrollBar()

    def _viewport_extent(self):
        viewport = self.scroll_area.viewport()
        return viewport.width() if self._is_vertical_layout() else viewport.height()

    def _schedule_row_height_refresh(self):
        if self._row_height_refresh_pending:
            return
        self._row_height_refresh_pending = True
        self._row_height_timer.start(0)

    def _run_scheduled_row_height_refresh(self):
        self._row_height_refresh_pending = False
        self._refresh_row_heights()


    def _display_index(self, index):
        if self._is_vertical_layout() and self.alignment == "right":
            return self.lyric_count - 1 - index
        return index

    def _line_start(self, index):
        return self._starts[self._display_index(index)]

    def _line_center(self, index):
        return self._line_start(index) + self._extents[index] / 2

    def _rebuild_positions(self):
        vertical = self._is_vertical_layout()
        self._starts = [self.padding if vertical else 90]
        spacing = self.padding if vertical else 0
        indices = range(self.lyric_count)
        if vertical and self.alignment == "right":
            indices = reversed(indices)
        for index in indices:
            self._starts.append(self._starts[-1] + self._extents[index] + spacing)
        tail = 0 if vertical else 90
        total = self._starts[-1] + tail
        viewport = self.scroll_area.viewport()
        if vertical:
            self.container.resize(max(viewport.width(), total), max(1, viewport.height()))
        else:
            self.container.resize(max(1, viewport.width()), max(viewport.height(), total))

    def _reset_estimates(self):
        vertical = self._is_vertical_layout()
        signature = (vertical, self.alignment, self.font_family, self.font_size,
                     self.ruby_font_size, self.show_ruby, self.show_romaji,
                     getattr(self, "all_romaji", False), self.ruby_position,
                     self.ruby_padding, self.padding, self.vertical_padding,
                     self.scroll_area.viewport().height() if vertical else 0)
        if signature == self._geometry_signature:
            return
        self._geometry_signature = signature
        font = QFont(self.font_family)
        font.setPointSize(self.font_size)
        base_height = QFontMetrics(font).height()
        font.setPointSize(self.ruby_font_size)
        reading_height = QFontMetrics(font).height() if self.show_ruby or self.show_romaji else 0
        self._extents = []
        for lyric in self._lyrics:
            if vertical:
                text = str(lyric.text if hasattr(lyric, "text") else lyric[1])
                drawable = max(1, self.scroll_area.viewport().height()
                               - FuriganaWidget.TOP_MARGIN - FuriganaWidget.BOTTOM_MARGIN
                               - self.vertical_padding * 2)
                columns = max(1, math.ceil(len(text) * (base_height + FuriganaWidget.PADDING) / drawable))
                extent = FuriganaWidget.PADDING + columns * (base_height + FuriganaWidget.PADDING
                         + (reading_height + self.ruby_padding if reading_height else 0))
            else:
                extent = (FuriganaWidget.TOP_MARGIN + base_height + FuriganaWidget.BOTTOM_MARGIN
                          + self.padding + (reading_height + self.ruby_padding if reading_height else 0))
            self._extents.append(max(1, extent))

    def _measure_row(self, row):
        if self._is_vertical_layout():
            row.setFixedHeight(max(1, self.scroll_area.viewport().height()))
            flags = row._vertical_column_ruby_flags()
            base = QFontMetrics(row.base_font).height()
            ruby = row._vertical_ruby_lane_width() if row.show_ruby else 0
            width = FuriganaWidget.PADDING + sum(
                base + FuriganaWidget.PADDING + (ruby + row.ruby_padding if has_ruby and ruby else 0)
                for has_ruby in flags)
            row.setFixedWidth(max(1, int(width)))
            return row.width()
        row.setMinimumWidth(1)
        row.setMaximumWidth(16777215)
        row.setFixedHeight(max(1, row.minimumSizeHint().height() + self.padding))
        return row.height()

    def _refresh_row_heights(self):
        anchor = None
        signature = self._geometry_signature
        if self._lyrics and signature is not None and signature[:2] == (self._is_vertical_layout(), self.alignment):
            display = max(0, min(self.lyric_count - 1, bisect_right(self._starts, self._scroll_axis().value()) - 1))
            anchor = self._display_index(display)
            offset = self._scroll_axis().value() - self._line_start(anchor)
        self._reset_estimates()
        self._syncing_rows = True
        try:
            self._rebuild_positions()
            if anchor is not None:
                self._scroll_axis().setValue(max(0, int(self._line_start(anchor) + offset)))
        finally:
            self._syncing_rows = False
        self._sync_visible_rows()

    def refresh_layout_now(self):
        """Refresh viewport measurements after showing a hidden lyrics page."""
        self._refresh_row_heights()
        self.update()

    def _sync_visible_rows(self, _value=None):
        if self._syncing_rows or not self._lyrics:
            return
        self._syncing_rows = True
        try:
            # Measured ruby/column widths can differ from the lightweight
            # estimates. Preserve the visible source line and its pixel offset
            # while replacing those estimates, including right-to-left columns.
            for _pass in range(3):
                scrollbar = self._scroll_axis()
                value = scrollbar.value()
                display_anchor = max(0, min(self.lyric_count - 1, bisect_right(self._starts, value) - 1))
                anchor = self._display_index(display_anchor)
                offset = value - self._line_start(anchor)
                extent = max(1, self._viewport_extent())
                overscan = max(180, extent // 2)
                first = max(0, bisect_right(self._starts, value - overscan) - 1)
                last = min(self.lyric_count, bisect_right(self._starts, value + extent + overscan))
                wanted = {self._display_index(index) for index in range(first, last)}
                for index in self._mounted_rows.keys() - wanted:
                    row = self._mounted_rows.pop(index)
                    self._discard_row(row)
                for index in sorted(wanted - self._mounted_rows.keys()):
                    self._mounted_rows[index] = self._create_row(self._lyrics[index], index)
                self.rows = [self._mounted_rows[index] for index in sorted(self._mounted_rows)]
                changed = False
                for row in self.rows:
                    index = row._lyric_index
                    measured = self._measure_row(row)
                    if measured != self._extents[index]:
                        self._extents[index] = measured
                        changed = True
                if changed:
                    self._rebuild_positions()
                    scrollbar.setValue(max(0, int(self._line_start(anchor) + offset)))
                for row in self.rows:
                    start = self._line_start(row._lyric_index)
                    if self._is_vertical_layout():
                        row.move(start, 0)
                    else:
                        row.setGeometry(0, start, self.container.width(), row.height())
                    row.show()
                if not changed:
                    break
            self._update_reading_render_window(self.current_line, initial=True)
        finally:
            self._syncing_rows = False

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
        self._refresh_row_heights()
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
            return
        self.direction = direction
        if self._is_vertical_layout() and self.alignment == "center":
            self.alignment = "left"
        for row in self.rows:
            row.set_direction(direction)
            row.set_alignment(self.alignment)
        self._rebuild_scroll_layout()
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
        for row in self.rows:
            row.set_alignment(alignment)
        if self._is_vertical_layout() and changed:
            self._rebuild_scroll_layout()
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
        if 0 <= index < self.lyric_count:
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
                and 0 <= index < self.lyric_count
            ):
                extent = max(1.0, float(self._viewport_extent()))
                viewport_center = target + extent / 2.0
                row_center = float(self._line_center(index))
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
        """Return the source index nearest the viewport center, without widgets."""
        if not self._lyrics:
            return -1
        center = self._scroll_axis().value() + self._viewport_extent() / 2
        display = max(0, min(self.lyric_count - 1, bisect_right(self._starts, center) - 1))
        candidates = range(max(0, display - 1), min(self.lyric_count, display + 2))
        return min((self._display_index(i) for i in candidates),
                   key=lambda index: abs(self._line_center(index) - center))

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
        end = min(self.lyric_count, index + self.limited_reading_range + 1)
        for row in self.rows:
            row_index = getattr(row, "_lyric_index", 0)
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
        self._row_height_timer.stop()
        self._row_height_refresh_pending = False
        if self.scroll_animation is not None:
            self.scroll_animation.stop()
            self.scroll_animation.deleteLater()
            self.scroll_animation = None
        for row in self.rows:
            self._discard_row(row)
        self.rows = []
        self._mounted_rows.clear()
        self._lyrics = []
        self._extents = []
        self._starts = [0]
        self._geometry_signature = None
        self._placeholder_texts.clear()
        self._karaoke_row_index = None
        self._last_karaoke_position = None
        self._karaoke_completed_before = -1
        self._continuous_last_position_ms = None
        self._continuous_nudge_index = -1
        self._continuous_nudge_offset = 0.0
        self._continuous_nudge_strength = 0.0
        self.container.resize(self.scroll_area.viewport().size())
        self._scroll_axis().setValue(0)

    @staticmethod
    def _discard_row(row):
        # Pending providers may finish after their viewport row has gone away.
        # Cancel queued work; running work can still populate the shared cache.
        future = getattr(row, "_furigana_future", None)
        row._reading_disposed = True
        if future is not None:
            future.cancel()
        row.hide()
        row.setParent(None)
        row.deleteLater()

    def set_lyrics(self, lyrics):
        self.clear()
        self._lyrics = list(lyrics)
        self._refresh_row_heights()
        if self._lyrics:
            self.set_current_line(0, animate=False)

    def _create_row(self, lyric, index):
        row = FuriganaWidget(self.manual_overrides)
        row.set_direction(self.direction)
        row.set_vertical_padding(self.vertical_padding)
        row.set_alignment(self.alignment)
        # Centered Slide mode may enter from either side. Pick once per
        # row and keep it stable for the life of that lyric row.
        row.set_slide_side(
            "left" if (index % 2 == 0) else "right"
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
        row.set_all_romaji_visible(getattr(self, "all_romaji", False))
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

        row._lyric_index = index
        row.setParent(self.container)
        self._apply_row_state(row, index)
        if index in self._placeholder_texts:
            self._apply_placeholder(row, self._placeholder_texts[index])
        self._restore_row_karaoke(row)
        return row

    def _apply_row_state(self, row, index):
        current = self.current_line
        distance = abs(index - current)
        opacity = (1.0 if distance == 0 else 0.65 if distance == 1 else 0.48 if distance == 2 else 0.32)
        row.set_active(index == current)
        row.set_current_line(index == current)
        row.set_slide_future(index > current)
        row.set_opacity(opacity if current >= 0 else 0.32)

    def _restore_row_karaoke(self, row):
        position = getattr(self, "_last_karaoke_position", None)
        if position is not None and row._lyric_index < getattr(self, "_karaoke_completed_before", -1):
            position = self._row_finish_position(row, position)
        row.set_karaoke_position(position)

    @staticmethod
    def _row_finish_position(row, position):
        finish = position
        for segment in row.segments:
            for key in ("end", "line_end", "start"):
                value = segment.get(key)
                if value is not None:
                    try:
                        finish = max(finish, float(value))
                    except (TypeError, ValueError):
                        pass
        return finish + 1.0

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
        """Synchronize mounted rows and remember playback state for later mounts."""
        previous_position = getattr(self, "_last_karaoke_position", None)
        previous_index = getattr(self, "_karaoke_row_index", None)
        if position is None:
            self._karaoke_completed_before = -1
        else:
            position = float(position)
            discontinuity = (previous_position is not None and
                             (position < previous_position - 0.050 or position > previous_position + 0.500))
            if discontinuity:
                self._karaoke_completed_before = -1
            elif previous_index is not None and 0 <= previous_index < self.current_line:
                self._karaoke_completed_before = self.current_line
        self._last_karaoke_position = position
        self._karaoke_row_index = self.current_line if self.current_line >= 0 else None
        for row in self.rows:
            self._restore_row_karaoke(row)

    def refresh_furigana_for_texts(self, texts=None):
        wanted = None if texts is None else {str(text).lstrip() for text in texts}

        for row in self.rows:
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


    @staticmethod
    def _apply_placeholder(row, text):
        if getattr(row, "_lyrics_plus_placeholder_source_empty", False) and row.lyric != text:
            row.lyric = text
            row.segments = [{"text": text, "reading": None}]
            row.loading_text = None
            row.update()

    def set_placeholder_text(self, index, text):
        """Store countdown text even when its empty timestamp is off screen."""
        if not 0 <= index < self.lyric_count:
            return
        lyric = self._lyrics[index]
        source_text = lyric.text if hasattr(lyric, "text") else lyric[1]
        if str(source_text).strip():
            return
        self._placeholder_texts[index] = str(text)
        row = self._mounted_rows.get(index)
        if row is not None:
            self._apply_placeholder(row, str(text))

    def clear_placeholder_text(self, index):
        """Restore the static placeholder after the active countdown."""
        self.set_placeholder_text(index, str(getattr(self, "_empty_timestamp_static_placeholder", "♪")))


    def set_current_line(self, index, animate=True):
        if not 0 <= index < self.lyric_count:
            index = -1
        if index == self.current_line and animate:
            return
        self.current_line = index
        for row in self.rows:
            self._apply_row_state(row, row._lyric_index)
        self._update_reading_render_window(index)
        if index >= 0 and self.scrolling_mode == "automatic":
            self._scroll_to_line(index, animate)

    def _scroll_to_line(self, index, animate=True):
        if not (0 <= index < self.lyric_count):
            return

        if self.scroll_behavior == "off":
            return
        scrollbar = self._scroll_axis()
        target = self._scroll_target(index)
        if self.scroll_animation is not None:
            self.scroll_animation.stop()
            self.scroll_animation.deleteLater()
            self.scroll_animation = None
        if not animate:
            # Mounting the destination replaces estimated sizes with real
            # measurements. Recalculate the target after that geometry pass.
            for _ in range(3):
                scrollbar.setValue(target)
                self._sync_visible_rows()
                target = self._scroll_target(index)
            return

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
        animation.finished.connect(lambda: self._clear_animation(animation, index))
        self.scroll_animation = animation
        animation.start()

    def _scroll_target(self, index):
        start = self._line_start(index)
        center = self._line_center(index) - self._viewport_extent() / 2
        target = start - 20 if self.scroll_behavior == "ensure_visible" else center
        return int(max(0, min(target, self._scroll_axis().maximum())))

    def _clear_animation(self, animation, index):
        if self.scroll_animation is animation:
            self.scroll_animation = None
            if index == self.current_line and 0 <= index < self.lyric_count:
                self._scroll_to_line(index, animate=False)
        animation.deleteLater()

