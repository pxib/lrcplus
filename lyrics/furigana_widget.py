"""Furigana widget state, reading workers and animation controls."""

import math
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import QRectF, QEasingCurve, QVariantAnimation, QObject, Signal
from PySide6.QtGui import QFont, QFontMetrics, QColor
from PySide6.QtWidgets import QWidget, QGraphicsBlurEffect
from lyrics.furigana import (
    build_furigana_segments,
    get_cached_furigana_segments,
    split_reading_by_text_ranges,
)
from core.settings import KARAOKE_SWEEP_STYLES
from lyrics.karaoke_effects import word_lift_amount
from lyrics.line_breaks import reading_source
from lyrics.furigana_layout import (
    FuriganaLayoutMixin,
    is_kana,
    _normalize_all_romaji_punctuation,
    _normalize_all_romaji_blocks,
)
from lyrics.furigana_painting import FuriganaPaintingMixin

_FURIGANA_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LRCPlus-Yomi")


class _LineFocusBlurEffect(QGraphicsBlurEffect):
    def boundingRectFor(self, source_rect):
        # Stay inside this lyric's paint area. Expanded effect bounds make a
        # repaint of the active line also invalidate its static neighbors.
        return source_rect


class _FuriganaBridge(QObject):
    ready = Signal(str, object)


class FuriganaWidget(FuriganaLayoutMixin, FuriganaPaintingMixin, QWidget):
    BASE_FONT_SIZE = 32
    READING_FONT_SIZE = 18

    PADDING = 6
    READING_BASE_GAP = 0
    TOP_MARGIN = 4
    BOTTOM_MARGIN = 4

    # Positive offset moves ruby closer to the base lyric. Padding then
    # increases the distance upward from this tighter zero position.
    RUBY_ZERO_OFFSET = 15

    # Vertical "band" geometry: ruby has its own reserved strip above the
    # lyric baseline, so line spacing no longer has to be inflated just to
    # keep furigana from clipping.
    RUBY_BAND_HEIGHT = 28
    BASE_HEIGHT = 54

    def __init__(self, manual_overrides=None):
        super().__init__()

        self.lyric = ""
        self.segments = []
        self.manual_overrides = manual_overrides if manual_overrides is not None else {}
        self.active = True
        self.display_opacity = 1.0
        self.direction = "horizontal"
        self.alignment = "center"
        self.show_ruby = True
        self.show_romaji = False
        self.all_romaji = False
        self.ruby_position = "above"
        self.ruby_padding = 2
        self.reserve_ruby_space = False

        # Optional multi-line layout used by editor previews and scrolling lyrics.
        self.wrap_text = False
        self.wrap_max_lines = 0  # 0 = derive from available widget height

        self.base_font_family = ""
        self.base_font_size = self.BASE_FONT_SIZE
        self.ruby_font_size = self.READING_FONT_SIZE
        self.lyrics_color = QColor("#ffffff")
        self.highlight_color = QColor("#ffaa00")
        self.highlight_current = True
        self.highlight_animation = "none"
        self.highlight_animation_duration = 300
        self.highlight_animation_out_duration = 300
        self.highlight_animation_easing = "out_cubic"
        # Non-current lyrics must start in their resting state, not at 100%.
        self.highlight_progress = 0.0
        self._was_current_line = False
        # Slide-specific state. Future lyrics are not painted at all until
        # they become current, and each centered lyric gets a stable side.
        self.slide_future = False
        self.slide_side = None
        self.is_current_line = False
        self._highlight_animation = QVariantAnimation(self)
        self._highlight_animation.setStartValue(0.0)
        self._highlight_animation.setEndValue(1.0)
        self._highlight_animation.setDuration(
            self.highlight_animation_duration
        )
        self._highlight_animation.setEasingCurve(
            QEasingCurve.Type.OutCubic
        )
        self._highlight_animation.valueChanged.connect(
            self._on_highlight_animation_value
        )

        # Karaoke state. None means normal lyric rendering.
        self.karaoke_position = None
        self.karaoke_line_end = None
        self.karaoke_sweep_style = "classic"
        self.karaoke_sweep_easing = "linear"
        self.karaoke_softness = 8
        self.karaoke_glow_intensity = 70
        self.karaoke_glow_radius = 1
        self.karaoke_shimmer_width = 10
        self.karaoke_sweep_delay_ms = 100
        self.karaoke_sweep_delay_percent = 10
        self.karaoke_line_entrance = False
        self.karaoke_word_lift = False
        self.focus_transition = False
        self.focus_progress = 1.0
        self._focus_effect = None
        self._focus_animation = QVariantAnimation(self)
        self._focus_animation.setDuration(240)
        self._focus_animation.setEasingCurve(QEasingCurve.Type.InOutQuad)
        self._focus_animation.valueChanged.connect(self._on_focus_value)
        self.line_entrance_progress = 1.0
        self._line_entrance_animation = QVariantAnimation(self)
        self._line_entrance_animation.setStartValue(0.0)
        self._line_entrance_animation.setEndValue(1.0)
        self._line_entrance_animation.setDuration(260)
        self._line_entrance_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        self._line_entrance_animation.valueChanged.connect(self._on_line_entrance_value)

        self.base_font = QFont()
        self.style_spans = []
        self.translation_lines = []
        self.is_blockquote = False
        self.base_font.setPointSize(self.BASE_FONT_SIZE)

        self.reading_font = QFont()
        self.reading_font.setPointSize(self.READING_FONT_SIZE)

        self._furigana_bridge = _FuriganaBridge(self)
        self._furigana_bridge.ready.connect(self.furigana_ready)
        self._furigana_future = None
        self._karaoke_furigana_pending = None
        self._prepared_layout_key = None
        self._prepared_layout = []
        self._karaoke_static_bounds = None

        # Reading rendering can be limited independently from the base lyric.
        # This keeps long scrolling lyrics responsive without hiding the lyrics
        # themselves. Geometry is preserved so rows never jump in size.
        self.reading_render_enabled = True
        self.reading_opacity = 1.0
        self._reading_fade_animation = QVariantAnimation(self)
        self._reading_render_target_enabled = True
        self._reading_fade_animation.valueChanged.connect(
            self._on_reading_fade_value
        )
        self._reading_fade_animation.finished.connect(
            self._on_reading_fade_finished
        )
        self._update_content_geometry()

    def _on_reading_fade_value(self, value):
        self.reading_opacity = max(0.0, min(1.0, float(value)))
        self.update()

    def _on_reading_fade_finished(self):
        if not self._reading_render_target_enabled:
            self.reading_render_enabled = False
            self.reading_opacity = 0.0
            self.update()

    def set_reading_rendered(self, enabled, fade=False):
        """Enable or suppress ruby/pinyin painting, optionally fading both ways."""
        enabled = bool(enabled)

        if self._reading_render_target_enabled == enabled:
            return

        self._reading_render_target_enabled = enabled
        self._reading_fade_animation.stop()

        if enabled:
            self.reading_render_enabled = True
            if fade and self.show_ruby:
                self._reading_fade_animation.setStartValue(self.reading_opacity)
                self._reading_fade_animation.setEndValue(1.0)
                self._reading_fade_animation.setDuration(300)
                self._reading_fade_animation.setEasingCurve(QEasingCurve.Type.OutCubic)
                self._reading_fade_animation.start()
            else:
                self.reading_opacity = 1.0
                self.update()
            return

        # Keep rendering during the fade-out. Only disable after the animation
        # finishes, so the opacity values can actually be painted.
        if fade and (self.show_ruby or self.show_romaji) and self.reading_render_enabled:
            self._reading_fade_animation.setStartValue(self.reading_opacity)
            self._reading_fade_animation.setEndValue(0.0)
            self._reading_fade_animation.setDuration(300)
            self._reading_fade_animation.setEasingCurve(QEasingCurve.Type.InCubic)
            self._reading_fade_animation.start()
        else:
            self.reading_render_enabled = False
            self.reading_opacity = 0.0
            self.update()

    def set_romaji_visible(self, visible):
        visible = bool(visible)
        changed = (
            self.show_romaji != visible
            or (visible and self.show_ruby)
            or (visible and self.all_romaji)
        )
        self.show_romaji = visible
        if visible:
            self.show_ruby = False
            self.all_romaji = False
        if changed:
            self._update_content_geometry()
            self.update()

    def set_all_romaji_visible(self, visible):
        visible = bool(visible)
        changed = (
            self.all_romaji != visible
            or (visible and self.show_ruby)
            or (visible and self.show_romaji)
        )
        self.all_romaji = visible
        if visible:
            self.show_ruby = False
            self.show_romaji = False
        if changed:
            self._update_content_geometry()
            self.update()

    def set_ruby_visible(self, visible):
        visible = bool(visible)
        changed = self.show_ruby != visible or (visible and self.show_romaji)
        self.show_ruby = visible
        if visible:
            self.show_romaji = False
        if changed:
            self._update_content_geometry()
            self.update()

    def set_ruby_position(self, position):
        position = str(position or "above").lower()
        if position not in {"above", "below"}:
            position = "above"
        if self.ruby_position == position:
            return
        self.ruby_position = position
        self._update_content_geometry()
        self.update()

    def set_ruby_padding(self, padding):
        padding = max(0, min(18, int(padding)))
        if self.ruby_padding == padding:
            return
        self.ruby_padding = padding
        self._update_content_geometry()
        self.update()

    def set_reserve_ruby_space(self, reserve):
        """Keep the above-ruby band even when this lyric has no reading."""
        reserve = bool(reserve)
        if self.reserve_ruby_space == reserve:
            return
        self.reserve_ruby_space = reserve
        self._update_content_geometry()
        self.update()

    def set_vertical_padding(self, padding):
        self.vertical_padding = max(0, min(200, int(padding)))
        self.updateGeometry()
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
        self._update_content_geometry()
        self.update()

    def set_alignment(self, alignment):
        alignment = (
            alignment
            if alignment in {"left", "center", "right"}
            else "center"
        )
        if self.alignment == alignment:
            return
        self.alignment = alignment
        # Existing layout code historically used direction as alignment.
        # Keep the visual alignment centralized here for compatibility.
        self.update()

    def set_text_wrapping(self, enabled, max_lines=0):
        """Enable width-aware multi-line rendering for constrained previews.

        max_lines=0 derives a sensible cap from the widget's current height.
        max_lines<0 allows all wrapped lines.
        Anything beyond the final allowed line is represented by an ellipsis.
        """
        self.wrap_text = bool(enabled)
        self.wrap_max_lines = int(max_lines or 0)
        self.update()

    def set_font(self, family="", size=32, ruby_size=None):
        family = str(family or "").strip()
        try:
            size = max(10, min(96, int(size)))
        except (TypeError, ValueError):
            size = self.BASE_FONT_SIZE
        if ruby_size is None:
            ruby_size = round(size * 0.56)
        try:
            ruby_size = max(6, min(72, int(ruby_size)))
        except (TypeError, ValueError):
            ruby_size = self.READING_FONT_SIZE

        self.base_font_family = family
        self.base_font_size = size
        self.ruby_font_size = ruby_size
        self.base_font = QFont()
        if family:
            self.base_font.setFamily(family)
        self.base_font.setPointSize(size)

        self.reading_font = QFont()
        if family:
            self.reading_font.setFamily(family)
        self.reading_font.setPointSize(ruby_size)
        self._update_focus_effect()
        self._update_motion_margin()
        self._update_content_geometry()
        self.update()

    def set_colors(self, lyrics_color="#ffffff", highlight_color="#ffaa00"):
        normal = QColor(str(lyrics_color))
        highlight = QColor(str(highlight_color))
        if not normal.isValid():
            normal = QColor("#ffffff")
        if not highlight.isValid():
            highlight = QColor("#ffaa00")
        self.lyrics_color = normal
        self.highlight_color = highlight
        self.update()

    def set_highlight_current(self, enabled):
        self.highlight_current = bool(enabled)
        self.update()

    def _easing_curve(self, name):
        curves = {
            "linear": QEasingCurve.Type.Linear,
            "in_quad": QEasingCurve.Type.InQuad,
            "out_quad": QEasingCurve.Type.OutQuad,
            "in_out_quad": QEasingCurve.Type.InOutQuad,
            "in_cubic": QEasingCurve.Type.InCubic,
            "out_cubic": QEasingCurve.Type.OutCubic,
            "in_out_cubic": QEasingCurve.Type.InOutCubic,
            "out_back": QEasingCurve.Type.OutBack,
            "out_bounce": QEasingCurve.Type.OutBounce,
        }
        return curves.get(name, QEasingCurve.Type.OutCubic)

    def set_highlight_animation(
        self,
        animation,
        duration=None,
        easing=None,
        out_duration=None,
    ):
        animation = str(animation or "none").lower()
        if animation not in {"none", "fade", "pop", "scale", "slide"}:
            animation = "none"
        self.highlight_animation = animation

        if duration is not None:
            try:
                self.highlight_animation_duration = max(
                    0, min(2000, int(duration))
                )
            except (TypeError, ValueError):
                self.highlight_animation_duration = 300

        if out_duration is not None:
            try:
                self.highlight_animation_out_duration = max(
                    0, min(2000, int(out_duration))
                )
            except (TypeError, ValueError):
                self.highlight_animation_out_duration = (
                    self.highlight_animation_duration
                )

        if easing is not None:
            easing = str(easing).lower()
            if easing in {
                "linear", "in_quad", "out_quad", "in_out_quad",
                "in_cubic", "out_cubic", "in_out_cubic",
                "out_back", "out_bounce",
            }:
                self.highlight_animation_easing = easing

        self._highlight_animation.setEasingCurve(
            self._easing_curve(self.highlight_animation_easing)
        )
        if not self.is_current_line:
            self.highlight_progress = 0.0
        self.update()

    def _on_highlight_animation_value(self, value):
        self.highlight_progress = float(value)
        self.update()

    def _start_highlight_animation(self, entering):
        # Slide is intentionally enter-only. The old line resets immediately
        # instead of visibly flying away.
        if not entering and self.highlight_animation == "slide":
            self._highlight_animation.stop()
            self.highlight_progress = 0.0
            self.update()
            return

        if self.highlight_animation == "none":
            self._highlight_animation.stop()
            self.highlight_progress = 1.0 if entering else 0.0
            self.update()
            return

        duration = self.highlight_animation_duration
        if not entering and self.highlight_animation == "fade":
            duration = self.highlight_animation_out_duration

        if duration <= 0:
            self._highlight_animation.stop()
            self.highlight_progress = 1.0 if entering else 0.0
            self.update()
            return

        self._highlight_animation.stop()
        start_value = 0.0 if entering else 1.0
        end_value = 1.0 if entering else 0.0
        self.highlight_progress = start_value
        self._highlight_animation.setStartValue(start_value)
        self._highlight_animation.setEndValue(end_value)
        self._highlight_animation.setDuration(duration)
        self._highlight_animation.setEasingCurve(
            self._easing_curve(self.highlight_animation_easing)
        )
        self._highlight_animation.start()

    def set_slide_future(self, future):
        future = bool(future)
        if self.slide_future == future:
            return
        self.slide_future = future
        self.update()

    def set_slide_side(self, side):
        side = side if side in {"left", "right"} else None
        self.slide_side = side
        self.update()

    def _resolved_slide_side(self):
        if self.alignment == "left":
            return "left"
        if self.alignment == "right":
            return "right"
        if self.slide_side in {"left", "right"}:
            return self.slide_side
        return "left"

    def set_current_line(self, current):
        current = bool(current)
        if self.is_current_line == current:
            return

        self._was_current_line = self.is_current_line
        self.is_current_line = current
        self._start_highlight_animation(current)
        if current:
            self.start_line_entrance()
        else:
            self._line_entrance_animation.stop()
            self.line_entrance_progress = 1.0
            self._transition_focus(False)

    def set_focus_transition(self, enabled):
        enabled = bool(enabled)
        if self.focus_transition == enabled:
            return
        self.focus_transition = enabled
        self._transition_focus(self.is_current_line, animate=False)

    def _on_focus_value(self, value):
        self.focus_progress = float(value)
        self._update_focus_effect()
        self.update()

    def _update_focus_effect(self):
        if not self.focus_transition:
            if self._focus_effect is not None:
                self._focus_effect.setEnabled(False)
            return
        if self._focus_effect is None:
            self._focus_effect = _LineFocusBlurEffect(self)
            self._focus_effect.setBlurHints(QGraphicsBlurEffect.BlurHint.AnimationHint)
            self.setGraphicsEffect(self._focus_effect)
        radius = QFontMetrics(self.base_font).height() * 0.045 * (1 - self.focus_progress)
        self._focus_effect.setBlurRadius(radius)
        self._focus_effect.setEnabled(radius > 0.01)

    def _transition_focus(self, current, restart=False, animate=True):
        self._focus_animation.stop()
        target = 1.0 if current or not self.focus_transition else 0.0
        if not self.focus_transition or not self.isVisible() or not animate:
            self._on_focus_value(target)
            return
        if restart and current:
            self._on_focus_value(0.0)
        if self.focus_progress == target:
            return
        self._focus_animation.setStartValue(self.focus_progress)
        self._focus_animation.setEndValue(target)
        self._focus_animation.start()

    def _on_line_entrance_value(self, value):
        self.line_entrance_progress = float(value)
        self.update()

    def start_line_entrance(self):
        self._line_entrance_animation.stop()
        if self.is_current_line:
            self._transition_focus(True, restart=True)
        if self.karaoke_line_entrance and self.lyric and self.isVisible():
            self.line_entrance_progress = 0.0
            self._line_entrance_animation.start()
        else:
            self.line_entrance_progress = 1.0
        self.update()

    def showEvent(self, event):
        super().showEvent(event)
        self._transition_focus(self.is_current_line, animate=False)
        if self.is_current_line:
            self.start_line_entrance()

    def hideEvent(self, event):
        self._line_entrance_animation.stop()
        self.line_entrance_progress = 1.0
        self._transition_focus(self.is_current_line, animate=False)
        super().hideEvent(event)

    def _word_lift_offset(self, segment):
        if not self.karaoke_word_lift or self.karaoke_position is None or segment.get("instant"):
            return 0.0
        start = segment.get("start")
        if start is None:
            return 0.0
        start = float(start)
        end = segment.get("end")
        if end is None or float(end) <= start:
            end = segment.get("_next_timed_start")
        if end is None:
            end = segment.get("line_end", self.karaoke_line_end)
        if end is None:
            end = start + 1.0
        return -QFontMetrics(self.base_font).height() * 0.1 * word_lift_amount(
            self.karaoke_position, start, float(end)
        )

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
        line_entrance=False,
        word_lift=False,
    ):
        self.karaoke_sweep_style = (
            style if style in KARAOKE_SWEEP_STYLES else "classic"
        )
        self.karaoke_sweep_easing = (
            easing
            if easing in {"linear", "ease_in", "ease_out", "ease_in_out"}
            else "linear"
        )
        self.karaoke_softness = max(1, min(40, int(softness)))
        self.karaoke_glow_intensity = max(0, min(255, int(glow_intensity)))
        self.karaoke_glow_radius = max(1, min(8, int(glow_radius)))
        self.karaoke_shimmer_width = max(
            1, min(40, int(shimmer_width))
        )
        self.karaoke_sweep_delay_ms = max(
            0, min(500, int(sweep_delay_ms))
        )
        self.karaoke_sweep_delay_percent = max(
            0, min(40, int(sweep_delay_percent))
        )
        self.karaoke_line_entrance = bool(line_entrance)
        self.karaoke_word_lift = bool(word_lift)
        if not self.karaoke_line_entrance:
            self._line_entrance_animation.stop()
            self.line_entrance_progress = 1.0
        self._update_motion_margin()
        self.update()

    def _update_motion_margin(self):
        height = QFontMetrics(self.base_font).height()
        top = type(self).TOP_MARGIN + (math.ceil(height * 0.1) if self.karaoke_word_lift else 0)
        bottom = type(self).BOTTOM_MARGIN + (math.ceil(height * 0.16) if self.karaoke_line_entrance else 0)
        if self.TOP_MARGIN != top or self.BOTTOM_MARGIN != bottom:
            self.TOP_MARGIN = top
            self.BOTTOM_MARGIN = bottom
            self._update_content_geometry()

    def set_karaoke_sweep_style(self, style):
        style = str(style or "classic").lower()
        if style not in KARAOKE_SWEEP_STYLES:
            style = "classic"
        if self.karaoke_sweep_style == style:
            return
        self.karaoke_sweep_style = style
        self.update()

    def set_karaoke_position(self, position):
        """Update the current playback position for timed LRCX segments."""
        if position is None:
            if self.karaoke_position is not None:
                self.karaoke_position = None
                self.update()
            return

        position = float(position)
        if self.karaoke_position == position:
            return

        previous = self.karaoke_position
        self.karaoke_position = position
        bounds = self._karaoke_static_bounds
        if previous is not None and bounds is not None:
            source, line_end, start, end, snapshot = bounds
            if source is self.segments and line_end == self.karaoke_line_end:
                if start is None or (previous < start and position < start) or (previous >= end and position >= end):
                    # The clock still advances, but static neighbors keep their
                    # cached glyph/blur image instead of repainting at sweep FPS.
                    if snapshot == self.segments:
                        return
        self.update()

    def _segment_progress(self, segment, segments):
        """Return fill progress for one whole timed LRCX segment."""
        if self.karaoke_position is None:
            return 0.0

        start = segment.get("start")
        if start is None:
            return 0.0

        start = float(start)

        # Explicit instant-fill tag.
        if segment.get("instant", False):
            return 1.0 if self.karaoke_position >= start else 0.0

        # Keep timing identity separate from visual/furigana pieces. A single
        # karaoke chunk such as "沈む" may be displayed as several furigana
        # pieces, but it must still behave as ONE timed karaoke word.
        group = segment.get("timing_group", segment.get("karaoke_group"))

        # An explicit end is authoritative. This is what preserves a real
        # pause between two karaoke chunks: the previous chunk can finish
        # before the next chunk begins.
        explicit_end = segment.get("end")
        has_explicit_end = (
            explicit_end is not None
            and float(explicit_end) > start
        )
        end = (
            float(explicit_end)
            if has_explicit_end
            else None
        )

        # Legacy / ordinary LRCX segments have no explicit end, so they still
        # sweep until the next timed karaoke group begins.
        if end is None and "_next_timed_start" in segment:
            end = segment["_next_timed_start"]
        elif end is None:
            for later in segments:
                later_group = later.get(
                    "timing_group", later.get("karaoke_group")
                )
                if later_group == group:
                    continue
                later_start = later.get("start")
                if later_start is not None and float(later_start) > start:
                    end = float(later_start)
                    break

        # Final segment: sweep until the lyric line ends. The parser supplies
        # the next line's timestamp as the natural line end.
        if end is None:
            line_end = segment.get("line_end", self.karaoke_line_end)
            if line_end is not None and float(line_end) > start:
                end = float(line_end)

        # Truly unknown end: use a small automatic sweep instead of snapping.
        if end is None:
            end = start + 1.0

        # Generated/user timestamps usually use the next timestamp as the
        # effective end of this chunk. Use a proportional hold so short chunks
        # and long chunks keep the same visual timing relationship instead of
        # every chunk losing the same fixed number of milliseconds.
        sweep_start = start
        if has_explicit_end:
            duration = max(0.0, end - start)
            delay_percent = max(
                0.0,
                min(
                    40.0,
                    float(
                        getattr(
                            self,
                            "karaoke_sweep_delay_percent",
                            10,
                        )
                    ),
                ),
            )
            delay = duration * (delay_percent / 100.0)
            sweep_start = start + delay

        if self.karaoke_position <= sweep_start:
            return 0.0
        if self.karaoke_position >= end:
            return 1.0

        sweep_duration = max(1e-9, end - sweep_start)
        return (
            self.karaoke_position - sweep_start
        ) / sweep_duration

    def set_opacity(self, opacity):
        opacity = max(0.0, min(1.0, float(opacity)))
        if getattr(self, "display_opacity", 1.0) == opacity:
            return
        self.display_opacity = opacity
        self.update()

    def set_active(self, active):
        active = bool(active)
        if self.active == active:
            return
        self.active = active
        self.update()

    def apply_manual_overrides(self, segments):
        override_map = self.manual_overrides.get(self.lyric, {})
        if not override_map:
            return segments

        updated = []
        for index, segment in enumerate(segments):
            item = dict(segment)
            reading = override_map.get(index)
            if reading is not None:
                item["reading"] = reading or None
                item["ruby_source"] = "manual"
            updated.append(item)

        return updated

    def set_segments(self, text, segments, line_end=None, style_spans=None, is_blockquote=False, translation_lines=None):
        """Display lyric segments while generating readings from the full lyric.

        Karaoke boundaries are timing metadata only.  Automatic reading
        generation always sees the complete unsplit lyric, then each generated
        token is mapped back to the timing range that owns its character range.
        Explicit LRCX ruby remains authoritative.
        """
        source = str(text)
        text = source.lstrip(" \t\r") if translation_lines else source.lstrip()
        skipped = len(source) - len(text)
        self.style_spans = [(max(0, a - skipped), b - skipped, *styles)
                            for a, b, *styles in (style_spans or []) if b > skipped]
        self.lyric = text
        skipped_rows = source[:skipped].count("\n")
        self.translation_lines = [row - skipped_rows for row in (translation_lines or []) if row >= skipped_rows]
        reading_key = reading_source(text, self.translation_lines)
        self.is_blockquote = bool(is_blockquote)
        self.loading_text = reading_key
        self.karaoke_line_end = line_end

        raw_segments = []
        offset = 0

        # Plain LRC lines may intentionally have no karaoke segments at all.
        # The preview still needs a timing owner so automatic reading tokens can
        # be mapped onto the whole line.  Without this synthetic single segment,
        # `owner_for()` returns None for every generated token and the preview
        # falls back to bare text, which makes ruby disappear only in Karaoke
        # Preview.
        source_segments = list(segments or [])
        if not source_segments and text:
            source_segments = [{
                "text": text,
                "start": None,
                "end": line_end,
                "instant": False,
            }]

        for karaoke_group, segment in enumerate(source_segments):
            if isinstance(segment, dict):
                segment_text = str(segment.get("text", ""))
                explicit_reading = segment.get("reading")
                segment_start_value = segment.get("start")
                segment_end_value = segment.get("end")
                instant = bool(segment.get("instant", False))
                ruby_source = (
                    segment.get("ruby_source") or segment.get("source")
                )
            else:
                segment_text = str(segment.text)
                explicit_reading = segment.ruby
                segment_start_value = segment.start
                segment_end_value = getattr(segment, "end", None)
                instant = bool(getattr(segment, "instant", False))
                ruby_source = getattr(segment, "ruby_source", None)

            if not segment_text:
                continue

            ruby_spans = (
                list(segment.get("ruby_spans", []) or [])
                if isinstance(segment, dict)
                else list(getattr(segment, "ruby_spans", []) or [])
            )
            # Render inline ruby spans as separate display pieces while keeping
            # the original karaoke_group/start/end. This is display-only: the
            # LyricSegment itself remains one karaoke chunk.
            if ruby_spans:
                cursor = 0
                for span_start, span_end, span_reading in sorted(ruby_spans):
                    span_start = max(cursor, int(span_start))
                    span_end = min(len(segment_text), int(span_end))
                    if cursor < span_start:
                        literal = segment_text[cursor:span_start]
                        raw_segments.append({"start_offset": offset + cursor, "end_offset": offset + span_start, "text": literal, "reading": None, "reading_source": None, "start": segment_start_value, "end": segment_end_value, "instant": instant, "karaoke_group": karaoke_group})
                    if span_start < span_end:
                        base = segment_text[span_start:span_end]
                        raw_segments.append({"start_offset": offset + span_start, "end_offset": offset + span_end, "text": base, "reading": span_reading or None, "reading_source": "manual" if span_reading else None, "start": segment_start_value, "end": segment_end_value, "instant": instant, "karaoke_group": karaoke_group})
                    cursor = max(cursor, span_end)
                if cursor < len(segment_text):
                    literal = segment_text[cursor:]
                    raw_segments.append({"start_offset": offset + cursor, "end_offset": offset + len(segment_text), "text": literal, "reading": None, "reading_source": None, "start": segment_start_value, "end": segment_end_value, "instant": instant, "karaoke_group": karaoke_group})
            else:
                raw_segments.append({
                    "start_offset": offset,
                    "end_offset": offset + len(segment_text),
                    "text": segment_text,
                    "reading": explicit_reading or None,
                    "reading_source": (ruby_source or "manual" if explicit_reading else None),
                    "start": segment_start_value,
                    "end": segment_end_value,
                    "instant": instant,
                    "karaoke_group": karaoke_group,
                })
            offset += len(segment_text)

        # Reading generation must stay off the Qt thread, including when
        # scrolling mounts new viewport rows. Consume cached results here; the
        # active lyric renderer generates missing readings lazily in its
        # background worker.
        whole_tokens = get_cached_furigana_segments(reading_key) if text else []
        if whole_tokens is None:
            whole_tokens = [{"text": text, "reading": None}] if text else []

            # Karaoke lines used to call the automatic furigana generator
            # directly here.  That kept the initial GUI responsive, but it
            # also meant the line had no reading at all until another code
            # path happened to populate the cache.  Start the same background
            # generation used by set_lyric(), then rebuild this timed line from
            # the cached whole-line result.
            if (
                text
                and self._karaoke_furigana_pending != text
            ):
                self.loading_text = reading_key
                self._karaoke_furigana_pending = text
                original_segments = list(source_segments)

                def parse_karaoke():
                    try:
                        return build_furigana_segments(reading_key)
                    except Exception as error:
                        print(
                            f"[Yomi] Lyric furigana processing failed for {text!r}: {error!r}"
                        )
                        return [{"text": text, "reading": None}]

                future = _FURIGANA_EXECUTOR.submit(parse_karaoke)
                self._furigana_future = future

                def finished_karaoke(done):
                    if done.cancelled() or getattr(self, "_reading_disposed", False):
                        return
                    try:
                        result = done.result()
                    except Exception as error:
                        print(
                            f"[Yomi] Lyric reading worker failed for {text!r}: {error!r}"
                        )
                        result = [{"text": text, "reading": None}]
                    self._emit_readings_ready(
                        reading_key,
                        {"karaoke": original_segments, "segments": result, "line_end": line_end},
                    )

                future.add_done_callback(finished_karaoke)
                # Render authored LRCX ruby immediately while the automatic
                # reading worker runs.  Returning here used to leave the
                # previous/generated view on screen until the cache was ready,
                # making valid manual readings appear to be rejected.
                whole_tokens = [{"text": text, "reading": None}]

        # Readings already resolved on the lyric model override a fresh
        # automatic parse.  This includes generated readings: generation and
        # rendering must share one result instead of producing two independent
        # segmentations.
        explicit_ranges = [
            (
                item["start_offset"],
                item["end_offset"],
                item["reading"],
                item["reading_source"],
            )
            for item in raw_segments
            if item["reading"]
        ]

        prepared = []
        rendered_explicit_ranges = set()
        token_offset = 0

        def owner_for(position):
            for item in raw_segments:
                if item["start_offset"] <= position < item["end_offset"]:
                    return item
            return raw_segments[-1] if raw_segments else None

        for token in whole_tokens:
            token = dict(token)
            token_text = str(token.get("text", ""))
            if not token_text:
                continue

            token_start = token_offset
            token_end = token_start + len(token_text)
            token_offset = token_end

            # Explicit ruby wins when the token exactly corresponds to an
            # explicit range.  Automatic tokens overlapping explicit ranges are
            # split at those boundaries below.
            boundaries = {token_start, token_end}
            for a, b, _, _ in explicit_ranges:
                if token_start < a < token_end:
                    boundaries.add(a)
                if token_start < b < token_end:
                    boundaries.add(b)
            for item in raw_segments:
                a = item["start_offset"]
                b = item["end_offset"]
                if token_start < a < token_end:
                    boundaries.add(a)
                if token_start < b < token_end:
                    boundaries.add(b)

            ordered = sorted(boundaries)
            pieces = list(zip(ordered, ordered[1:]))
            token_reading = token.get("reading") or None
            reading_slices = (
                split_reading_by_text_ranges(
                    token_text,
                    token_reading,
                    [(a - token_start, b - token_start) for a, b in pieces],
                )
                if token_reading and len(pieces) > 1
                else [token_reading for _ in pieces]
            )

            for piece_index, (a, b) in enumerate(pieces):
                if a >= b:
                    continue

                # A saved/manual ruby span describes its complete base-text
                # range. Automatic tokenization may divide that range into
                # several differently-sized tokens, so render the explicit
                # span once as authored instead of letting generated readings
                # replace it token by token.
                explicit_match = next(
                    (
                        (range_index, x, y, ruby, ruby_source)
                        for range_index, (x, y, ruby, ruby_source)
                        in enumerate(explicit_ranges)
                        if x <= a < y and b <= y
                    ),
                    None,
                )
                if explicit_match is not None:
                    range_index, x, y, ruby, ruby_source = explicit_match
                    if range_index in rendered_explicit_ranges:
                        continue
                    if a != x:
                        continue
                    owner = owner_for(a)
                    if owner is None:
                        continue
                    prepared.append({
                        "text": text[x:y],
                        "reading": ruby,
                        "start": owner["start"],
                        "end": owner["end"],
                        "instant": owner["instant"],
                        "line_end": line_end,
                        "karaoke_group": owner["karaoke_group"],
                        "timing_group": owner["karaoke_group"],
                        "ruby_source": ruby_source or "manual",
                        "_romaji_word_start": bool(token.get("_romaji_word_start", False)),
                        "_romaji_particle": bool(token.get("_romaji_particle", False)),
                        "_reading_group": (x, y),
                    })
                    rendered_explicit_ranges.add(range_index)
                    continue

                owner = owner_for(a)
                if owner is None:
                    continue

                piece_text = text[a:b]
                reading = None
                source = "generated"

                if a == token_start and b == token_end:
                    reading = token_reading
                elif token_reading:
                    # A karaoke boundary cut through a ruby token.  Keep the
                    # token's reading attached to the corresponding visual
                    # pieces instead of throwing it away.  This keeps furigana
                    # stable when users split a lyric into karaoke chunks.
                    reading = reading_slices[piece_index]

                prepared.append({
                    "text": piece_text,
                    "reading": reading,
                    "start": owner["start"],
                    "end": owner["end"],
                    "instant": owner["instant"],
                    "line_end": line_end,
                    "karaoke_group": owner["karaoke_group"],
                    "timing_group": owner["karaoke_group"],
                    "ruby_source": source if reading else None,
                    "_romaji_word_start": bool(token.get("_romaji_word_start", False))
                    if piece_index == 0 else False,
                    "_romaji_particle": bool(token.get("_romaji_particle", False)),
                    "_reading_group": (token_start, token_end),
                })

        if not prepared and raw_segments:
            for item in raw_segments:
                prepared.append({
                    "text": item["text"],
                    "reading": item["reading"],
                    "start": item["start"],
                    "end": item["end"],
                    "instant": item["instant"],
                    "line_end": line_end,
                    "karaoke_group": item["karaoke_group"],
                    "timing_group": item["karaoke_group"],
                    "ruby_source": item["reading_source"],
                    "_romaji_word_start": bool(item.get("_romaji_word_start", False)),
                    "_romaji_particle": bool(item.get("_romaji_particle", False)),
                    "_reading_group": (item["start_offset"], item["end_offset"]),
                })

        if not prepared and text:
            prepared = [{"text": text, "reading": None}]

        self.segments = self.apply_manual_overrides(prepared)
        self._update_content_geometry()
        self.update()

    def set_lyric(self, text, generate=True, style_spans=None, is_blockquote=False, translation_lines=None):
        # Preserve whitespace in the source/model, but ignore optional
        # leading whitespace after an LRC timestamp when rendering.
        source = str(text)
        text = source.lstrip(" \t\r") if translation_lines else source.lstrip()
        skipped = len(source) - len(text)
        self.style_spans = [(max(0, a - skipped), b - skipped, *styles)
                            for a, b, *styles in (style_spans or []) if b > skipped]
        self.lyric = text
        skipped_rows = source[:skipped].count("\n")
        self.translation_lines = [row - skipped_rows for row in (translation_lines or []) if row >= skipped_rows]
        reading_key = reading_source(text, self.translation_lines)
        self.is_blockquote = bool(is_blockquote)

        # Janome tokenization is local and fast, so processing it in the GUI
        # thread avoids the lifecycle hazards of one QThread per lyric line.
        self.segments = [{
            "text": text,
            "reading": None,
        }]

        if not text:
            self.loading_text = None
            self._update_content_geometry()
            self.update()
            return

        cached = get_cached_furigana_segments(reading_key)
        if cached is not None:
            self.loading_text = None
            self.segments = self.apply_manual_overrides(cached)
            self._update_content_geometry()
            self.update()
            return

        if not generate:
            self.loading_text = None
            self._update_content_geometry()
            self.update()
            return

        if reading_key == getattr(self, "loading_text", None):
            self.update()
            return

        # Yomi is network-backed. Keep the GUI responsive while the request is
        # in flight and ignore stale results when playback changes lines.
        # Do not overwrite ``loading_text`` before checking it: doing so makes
        # the first call look like a duplicate request and prevents the initial
        # background reading generation. This is especially visible with
        # All Romaji enabled at launch: toggling the setting later forces a
        # geometry/update pass after the cache is ready, making it appear as if
        # re-enabling Romaji fixed the problem.
        if reading_key == getattr(self, "loading_text", None):
            self.update()
            return

        self.loading_text = reading_key

        def parse():
            try:
                return build_furigana_segments(reading_key)
            except Exception as error:
                print(f"[Yomi] Furigana processing failed for {text!r}: {error!r}")
                return [{"text": text, "reading": None}]

        future = _FURIGANA_EXECUTOR.submit(parse)
        self._furigana_future = future

        def finished(done):
            if done.cancelled() or getattr(self, "_reading_disposed", False):
                return
            try:
                result = done.result()
            except Exception as error:
                print(f"[Yomi] Worker failed for {text!r}: {error!r}")
                result = [{"text": text, "reading": None}]
            self._emit_readings_ready(reading_key, result)

        future.add_done_callback(finished)

    def _emit_readings_ready(self, text, result):
        try:
            self._furigana_bridge.ready.emit(text, result)
        except RuntimeError:
            # The Qt row may be destroyed between the worker finishing and
            # emitting its queued result (viewport eviction or window close).
            pass

    def furigana_ready(self, text, segments):
        # Karaoke requests return a small envelope so the cached full-line
        # reading can be remapped onto the original timing chunks.  Ordinary
        # lyric rendering still passes the segment list directly.
        if isinstance(segments, dict) and "segments" in segments:
            karaoke_segments = segments.get("karaoke") or []
            generated_segments = segments.get("segments") or []
            line_end = segments.get("line_end")
            print(
                f"[Furigana] Ready: {len(generated_segments)} reading tokens for {text!r} (segment mapping)"
            )

            if text != reading_source(self.lyric, self.translation_lines):
                if self._karaoke_furigana_pending == text:
                    self._karaoke_furigana_pending = None
                return

            self._karaoke_furigana_pending = None
            self.loading_text = None
            self.set_segments(self.lyric, karaoke_segments, line_end=line_end,
                              style_spans=self.style_spans, is_blockquote=self.is_blockquote,
                              translation_lines=self.translation_lines)
            return

        print(f"[Furigana] Ready: {len(segments)} reading tokens for {text!r}")

        # Ignore an old request if the lyric has already changed.
        if text != reading_source(self.lyric, self.translation_lines):
            return

        self.loading_text = None
        self.segments = self.apply_manual_overrides(segments)
        self._update_content_geometry()
        self.update()


    def _edit_reading_for_segment(self, segment):
        # Furigana editing now lives in Lyrics Editor, where changes can be
        # reviewed and saved into LRCX instead of being hidden in the display.
        return


    def mousePressEvent(self, event):
        super().mousePressEvent(event)

        if not self.segments:
            return

        position = event.position()
        for segment in self._prepare_segments():
            rect = QRectF(
                segment["x"],
                0,
                segment["width"],
                self.height(),
            )
            if rect.contains(position):
                self._edit_reading_for_segment(segment)
                return

    def stop_workers(self):
        # Kept as a no-op for the main-window shutdown hook. Furigana is now
        # generated synchronously and no worker threads are created.
        return

    def closeEvent(self, event):
        self.stop_workers()
        event.accept()
