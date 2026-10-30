import unicodedata
import re
import math
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QPointF, QRectF, QPropertyAnimation, QEasingCurve, QTimer, QSize, QVariantAnimation, QObject, Signal
from PySide6.QtGui import QFont, QFontMetrics, QColor, QPainter, QLinearGradient, QBrush, QPen
from PySide6.QtWidgets import (
    QVBoxLayout,
    QHBoxLayout,
    QWidget,
    QScrollArea,
    QInputDialog,
)
from lyrics.furigana import (
    build_furigana_segments,
    get_cached_furigana_segments,
    split_reading_by_text_ranges,
    kana_to_romaji,
)
from lyrics.karaoke_layout import annotate_next_timed_starts
from lyrics.formatting import style_at

_FURIGANA_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LRCPlus-Yomi")




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

class _FuriganaBridge(QObject):
    ready = Signal(str, object)

class FuriganaWidget(QWidget):
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

        # Optional multi-line layout. Disabled by default so the main lyrics
        # display keeps its existing behavior; the editor preview enables it.
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

        self.base_font = QFont()
        self.style_spans = []
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
        Anything beyond the final allowed line is represented by an ellipsis.
        """
        self.wrap_text = bool(enabled)
        self.wrap_max_lines = max(0, int(max_lines or 0))
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
        self.karaoke_sweep_style = (
            style if style in {"classic", "soft", "glow", "shimmer"} else "classic"
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
        self.update()

    def set_karaoke_sweep_style(self, style):
        style = str(style or "classic").lower()
        if style not in {"classic", "soft", "glow", "shimmer"}:
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

        self.karaoke_position = position
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
            updated.append(item)

        return updated

    def set_segments(self, text, segments, line_end=None, style_spans=None, is_blockquote=False):
        """Display lyric segments while generating readings from the full lyric.

        Karaoke boundaries are timing metadata only.  Automatic reading
        generation always sees the complete unsplit lyric, then each generated
        token is mapped back to the timing range that owns its character range.
        Explicit LRCX ruby remains authoritative.
        """
        source = str(text)
        text = source.lstrip()
        skipped = len(source) - len(text)
        self.style_spans = [(max(0, a - skipped), b - skipped, *styles)
                            for a, b, *styles in (style_spans or []) if b > skipped]
        self.lyric = text
        self.is_blockquote = bool(is_blockquote)
        self.loading_text = text
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
        whole_tokens = get_cached_furigana_segments(text) if text else []
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
                self.loading_text = text
                self._karaoke_furigana_pending = text
                original_segments = list(source_segments)

                def parse_karaoke():
                    try:
                        return build_furigana_segments(text)
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
                        text,
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
                })

        if not prepared and text:
            prepared = [{"text": text, "reading": None}]

        self.segments = self.apply_manual_overrides(prepared)
        self._update_content_geometry()
        self.update()

    def set_lyric(self, text, generate=True, style_spans=None, is_blockquote=False):
        # Preserve whitespace in the source/model, but ignore optional
        # leading whitespace after an LRC timestamp when rendering.
        source = str(text)
        text = source.lstrip()
        skipped = len(source) - len(text)
        self.style_spans = [(max(0, a - skipped), b - skipped, *styles)
                            for a, b, *styles in (style_spans or []) if b > skipped]
        self.lyric = text
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

        cached = get_cached_furigana_segments(text)
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

        if text == getattr(self, "loading_text", None):
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
        if text == getattr(self, "loading_text", None):
            self.update()
            return

        self.loading_text = text

        def parse():
            try:
                return build_furigana_segments(text)
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
            self._emit_readings_ready(text, result)

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

            if text != self.lyric:
                if self._karaoke_furigana_pending == text:
                    self._karaoke_furigana_pending = None
                return

            self._karaoke_furigana_pending = None
            self.loading_text = None
            self.set_segments(text, karaoke_segments, line_end=line_end,
                              style_spans=self.style_spans, is_blockquote=self.is_blockquote)
            return

        print(f"[Furigana] Ready: {len(segments)} reading tokens for {text!r}")

        # Ignore an old request if the lyric has already changed.
        if text != self.lyric:
            return

        self.loading_text = None
        self.segments = self.apply_manual_overrides(segments)
        self._update_content_geometry()
        self.update()

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
            self.setMinimumHeight(total * (self.lyric.count("\n") + 1))
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
        return QSize(max(0, self.minimumWidth()), total * (self.lyric.count("\n") + 1))

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
        if max_lines <= 0:
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
        truncated = False

        def add_piece(piece):
            nonlocal truncated
            if truncated:
                return
            if piece.get("_line_break"):
                rows.append([])
                row_widths.append(0.0)
                return
            width = piece["width"]
            gap = piece.get("gap_before", 0)
            piece_metrics = (QFontMetrics(self._styled_font(self.base_font, piece))
                             if piece.get("bold") or piece.get("italic") else base_metrics)

            # Start a new visual row when the next piece would overflow.
            if self.wrap_text and rows[-1] and row_widths[-1] + gap + width > available:
                if len(rows) >= max_lines:
                    truncated = True
                    return
                rows.append([])
                row_widths.append(0.0)
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
            row_widths[-1] += gap + width
            if (self.show_ruby or self.show_romaji) and piece.get("reading"):
                row_widths[-1] += self.PADDING

        for segment in prepared:
            add_piece(dict(segment))

        if truncated and rows and rows[-1]:
            ellipsis_width = base_metrics.horizontalAdvance("…")
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
                })
                row_widths[-1] += ellipsis_width

        wrapped = []
        for row_index, row in enumerate(rows):
            if not row:
                continue
            total_width = row_widths[row_index]
            if self.alignment == "left":
                x = float(indent)
            elif self.alignment == "right":
                x = max(float(indent), self.width() - total_width)
            else:
                x = max(float(indent), indent + (self.width() - indent - total_width) / 2.0)

            y_offset = row_index * line_height
            reading_height = ruby_height or (reading_metrics.height() if self._has_visible_romaji() else 0)
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
                    + ruby_gap
                    + base_metrics.ascent()
                )
            elif reading_height:
                base_y = self.TOP_MARGIN + y_offset + base_metrics.ascent()
                reading_y = (
                    self.TOP_MARGIN + y_offset + base_height + ruby_gap
                    + base_metrics.descent() + reading_metrics.ascent()
                )
            else:
                reading_y = None
                base_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + base_metrics.ascent()
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

        return wrapped

    def _visual_source_segments(self):
        """Split structural newlines for layout while retaining reading/timing ownership."""
        offset = 0
        for segment in self.segments:
            text = str(segment.get("text", ""))
            if "\n" not in text and not self.style_spans:
                yield segment
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
                piece["reading"] = part_reading
                piece["bold"], piece["italic"], piece["strike"], piece["underline"] = style_at(self.style_spans, offset + start)
                if piece["text"] == "\n":
                    piece.update(text="", reading=None, _line_break=True)
                yield piece
            offset += len(text)

    def _styled_font(self, font, segment):
        if not any(segment.get(style) for style in ("bold", "italic", "strike", "underline")):
            return font
        styled = QFont(font)
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

    def _paint_decoration_spans(self, painter, segment, normal, highlight, progress, timing_span, sliding):
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
            painter.setOpacity(self.display_opacity * (self.reading_opacity if reading else 1.0))
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
        # digraphs such as ``ち`` -> ``chi`` become ``cchi``.
        if leading_sokuon and converted:
            first = converted[0]
            if first.isalpha() and first.lower() not in "aeiou":
                converted = first + converted

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
            list(self.style_spans),
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
            default_reading_metrics = QFontMetrics(self.reading_font)
            for piece in prepared:
                reading_metrics = (QFontMetrics(self._styled_font(self.reading_font, piece))
                                   if piece.get("bold") or piece.get("italic") else default_reading_metrics)
                reading = self._reading_text_for_display(piece.get("reading"))
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
        if not self.lyric:
            return []

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        prepared = []
        total_width = 0

        previous_text = ""
        pending_sokuon = False
        row_width = 0
        for index, segment in enumerate(self._visual_source_segments()):
            if segment.get("_line_break"):
                prepared.append(dict(segment, width=0, base_width=0, reading_width=0, gap_before=0))
                total_width = max(total_width, row_width)
                row_width = 0
                previous_text = ""
                pending_sokuon = False
                continue
            segment_base_metrics = base_metrics
            segment_reading_metrics = reading_metrics
            if segment.get("bold") or segment.get("italic"):
                segment_base_metrics = QFontMetrics(self._styled_font(self.base_font, segment))
                segment_reading_metrics = QFontMetrics(self._styled_font(self.reading_font, segment))
            if self.all_romaji:
                text, segment_sokuon = self._display_text(segment, pending_sokuon)
            else:
                text, segment_sokuon = self._display_text(segment)
            if (
                self.all_romaji
                and segment.get("_romaji_word_start", False)
                and previous_text
                and not pending_sokuon
                and not previous_text[-1].isspace()
                and self._romaji_needs_word_space(text)
            ):
                text = " " + text
            base_width = segment_base_metrics.horizontalAdvance(text)
            reading_width = 0
            reading = segment.get("reading")
            ruby_visible = bool((self.show_ruby or self.show_romaji) and reading)

            if ruby_visible:
                reading_width = segment_reading_metrics.horizontalAdvance(reading)

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
            pending_sokuon = segment_sokuon
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
                                  if source.get("bold") or source.get("italic") else base_metrics)

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

        # Used by the vertical karaoke clip geometry below. Keep these
        # metrics in paintEvent so the highlight uses the exact same base
        # font measurements as the normal glyph rendering.
        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        painter.save()
        painter.setOpacity(self.display_opacity)

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
        for piece in prepared:
            group = piece.get("timing_group", piece.get("karaoke_group"))
            if group is None or group in group_progress:
                continue
            group_progress[group] = self._segment_progress(piece, prepared)

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

            painter.setFont(self._paint_font(self.base_font, segment))

            # v0.2: draw normal text first, then clip the karaoke color across
            # the segment according to its progress between timed starts.
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

            # Classic karaoke uses a hard edge. Draw only the not-yet-sung
            # portion in the normal color; the highlighted pass below draws
            # the sung portion. Drawing the whole glyph twice compounds its
            # antialiased edge coverage and makes it look brighter/thicker.
            normal_sweep_clip = None
            if progress > 0.0:
                if segment.get("_vertical"):
                    start, end = vertical_piece_sweep.get(
                        id(segment), (0.0, 1.0)
                    )
                    span = max(1e-9, end - start)
                    local_progress = max(
                        0.0,
                        min(
                            1.0,
                            (min(1.0, progress) - start) / span,
                        ),
                    )
                    top = float(segment["base_y"]) - base_metrics.ascent()
                    fill_height = max(
                        0.0,
                        float(
                            segment.get("height", base_metrics.height())
                        ) * local_progress,
                    )
                    normal_sweep_clip = QRectF(
                        0,
                        top + fill_height,
                        self.width(),
                        max(0.0, self.height() - top - fill_height),
                    )
                elif getattr(self, "karaoke_sweep_style", "classic") == "classic":
                    visual_group = (timing_group, segment.get("base_y"))
                    if visual_group in timing_spans:
                        karaoke_left, karaoke_right = timing_spans[visual_group]
                        karaoke_width = max(0.0, karaoke_right - karaoke_left)
                    else:
                        karaoke_left = segment.get("karaoke_x", base_x)
                        karaoke_width = segment.get(
                            "karaoke_width", segment["width"]
                        )
                    progress_value = min(1.0, max(0.0, progress))
                    easing = getattr(self, "karaoke_sweep_easing", "linear")
                    if easing == "ease_in":
                        progress_value *= progress_value
                    elif easing == "ease_out":
                        progress_value = 1.0 - (1.0 - progress_value) ** 2
                    elif easing == "ease_in_out":
                        if progress_value < 0.5:
                            progress_value = 2.0 * progress_value * progress_value
                        else:
                            progress_value = (
                                1.0
                                - ((-2.0 * progress_value + 2.0) ** 2) / 2.0
                            )
                    sweep_end = karaoke_left + karaoke_width * progress_value
                    normal_sweep_clip = QRectF(
                        sweep_end,
                        0,
                        max(0.0, self.width() - sweep_end),
                        self.height(),
                    )

            if normal_sweep_clip is not None:
                painter.save()
                painter.setClipRect(normal_sweep_clip)
            painter.setPen(normal_pen)
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
            if normal_sweep_clip is not None:
                painter.restore()

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

            if progress > 0.0:
                if segment.get("_vertical"):
                    start, end = vertical_piece_sweep.get(id(segment), (0.0, 1.0))
                    span = max(1e-9, end - start)
                    local_progress = max(0.0, min(1.0, (min(1.0, progress) - start) / span))
                    if local_progress > 0.0:
                        top = float(segment["base_y"]) - base_metrics.ascent()
                        fill_height = max(0.0, float(segment.get("height", base_metrics.height())) * local_progress)
                        painter.save()
                        painter.setClipRect(QRectF(
                            float(segment.get("base_x", segment["x"])),
                            top,
                            max(1.0, float(segment.get("width", base_metrics.height()))),
                            fill_height,
                        ))
                        painter.setPen(highlight_pen)
                        painter.drawText(
                            int(segment.get("base_x", segment["x"])),
                            int(segment["base_y"]),
                            segment["text"],
                        )
                        painter.restore()
                else:
                    timing_group = segment.get(
                        "timing_group", segment.get("karaoke_group")
                    )
                    span = timing_spans.get((timing_group, segment.get("base_y")))
                    if span is not None:
                        karaoke_left = span[0]
                        karaoke_width = max(0.0, span[1] - span[0])
                    else:
                        karaoke_left = segment.get("karaoke_x", base_x)
                        karaoke_width = segment.get(
                            "karaoke_width", segment["width"]
                        )

                    progress_value = min(1.0, max(0.0, progress))
                    style = getattr(self, "karaoke_sweep_style", "classic")
                    easing = getattr(self, "karaoke_sweep_easing", "linear")
                    if easing == "ease_in":
                        progress_value = progress_value * progress_value
                    elif easing == "ease_out":
                        progress_value = 1.0 - (1.0 - progress_value) ** 2
                    elif easing == "ease_in_out":
                        if progress_value < 0.5:
                            progress_value = 2.0 * progress_value * progress_value
                        else:
                            progress_value = (
                                1.0
                                - ((-2.0 * progress_value + 2.0) ** 2) / 2.0
                            )

                    # Every sweep style must use this same final, eased edge.
                    sweep_end = (
                        karaoke_left + karaoke_width * progress_value
                    )

                    if style == "classic":
                        painter.save()
                        painter.setClipRect(QRectF(
                            karaoke_left,
                            0,
                            karaoke_width * progress_value,
                            self.height(),
                        ))
                        painter.setPen(highlight_pen)
                        painter.drawText(
                            int(base_x),
                            int(segment["base_y"]),
                            segment["text"],
                        )
                        painter.restore()

                    elif style == "soft":
                        # Keep the normal text intact, then paint the whole
                        # segment with a narrow color blend around the sweep edge.
                        softness = max(
                            1,
                            min(40, int(getattr(self, "karaoke_softness", 8))),
                        )
                        feather = min(
                            48.0,
                            max(2.0, karaoke_width * (softness / 100.0)),
                        )
                        gradient = QLinearGradient(
                            sweep_end - feather,
                            0,
                            sweep_end + feather,
                            0,
                        )
                        gradient.setColorAt(0.0, highlight_pen)
                        gradient.setColorAt(0.5, highlight_pen)
                        gradient.setColorAt(1.0, self.lyrics_color)
                        painter.save()
                        painter.setClipRect(
                            QRectF(karaoke_left, 0, karaoke_width, self.height())
                        )
                        painter.setPen(QPen(QBrush(gradient), 1))
                        painter.drawText(
                            int(base_x),
                            int(segment["base_y"]),
                            segment["text"],
                        )
                        painter.restore()

                    elif style == "shimmer":
                        # A narrow bright band travels with the sweep edge,
                        # while the completed portion keeps the highlight color.
                        shimmer_width = max(
                            1,
                            min(
                                40,
                                int(
                                    getattr(
                                        self,
                                        "karaoke_shimmer_width",
                                        10,
                                    )
                                ),
                            ),
                        )
                        painter.save()
                        painter.setClipRect(QRectF(
                            karaoke_left,
                            0,
                            karaoke_width * progress_value,
                            self.height(),
                        ))
                        painter.setPen(highlight_pen)
                        painter.drawText(
                            int(base_x),
                            int(segment["base_y"]),
                            segment["text"],
                        )
                        painter.restore()

                        # Treat the Properties value as a visible width
                        # percentage of the current lyric sweep, rather than
                        # an almost-unnoticeable fixed pixel width.
                        band = min(
                            max(2.0, karaoke_width * (shimmer_width / 100.0)),
                            max(2.0, karaoke_width),
                        )
                        shimmer = QLinearGradient(
                            sweep_end - band,
                            0,
                            sweep_end + band,
                            0,
                        )
                        edge_color = QColor(highlight_pen)
                        edge_color.setAlpha(40)
                        bright = QColor(highlight_pen)
                        bright = bright.lighter(180)
                        shimmer.setColorAt(0.0, edge_color)
                        shimmer.setColorAt(0.35, highlight_pen)
                        shimmer.setColorAt(0.5, bright)
                        shimmer.setColorAt(0.65, highlight_pen)
                        shimmer.setColorAt(1.0, edge_color)
                        painter.save()
                        band_left = max(
                            karaoke_left,
                            sweep_end - band,
                        )
                        band_right = min(
                            karaoke_left + karaoke_width,
                            sweep_end + band,
                        )
                        painter.setClipRect(QRectF(
                            band_left,
                            0,
                            max(0.0, band_right - band_left),
                            self.height(),
                        ))
                        painter.setPen(QPen(QBrush(shimmer), 1))
                        painter.drawText(
                            int(base_x),
                            int(segment["base_y"]),
                            segment["text"],
                        )
                        painter.restore()

                    else:  # glow
                        glow_radius = max(
                            1,
                            min(8, int(getattr(self, "karaoke_glow_radius", 1))),
                        )
                        feather = min(
                            40.0,
                            max(4.0, karaoke_width * (glow_radius * 0.03)),
                        )
                        # A small translucent edge glow, followed by the normal
                        # hard sweep. This keeps Glow readable rather than
                        # turning the entire lyric into a bloom effect.
                        glow_color = QColor(highlight_pen)
                        glow_color.setAlpha(
                            max(
                                0,
                                min(
                                    255,
                                    int(
                                        getattr(
                                            self,
                                            "karaoke_glow_intensity",
                                            70,
                                        )
                                    ),
                                ),
                            )
                        )
                        painter.save()
                        painter.setClipRect(QRectF(
                            karaoke_left,
                            0,
                            karaoke_width * progress_value + feather,
                            self.height(),
                        ))
                        painter.setPen(glow_color)
                        glow_radius = max(
                            1,
                            min(8, int(getattr(self, "karaoke_glow_radius", 1))),
                        )
                        for dx, dy in (
                            (-glow_radius, 0),
                            (glow_radius, 0),
                            (0, -glow_radius),
                            (0, glow_radius),
                        ):
                            painter.drawText(
                                int(base_x + dx),
                                int(segment["base_y"] + dy),
                                segment["text"],
                            )
                        painter.restore()

                        painter.save()
                        painter.setClipRect(QRectF(
                            karaoke_left,
                            0,
                            karaoke_width * progress_value,
                            self.height(),
                        ))
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
                painter.setOpacity(self.display_opacity * self.reading_opacity)
                reading_x = (
                    segment["x"]
                    + (
                        width
                        - segment["_display_reading_width"]
                    )
                    / 2
                )

                painter.setFont(self._paint_font(self.reading_font, segment))

                reading_normal_sweep_clip = (
                    normal_sweep_clip
                    if (
                        progress > 0.0
                        and not segment.get("reading_vertical")
                        and getattr(
                            self, "karaoke_sweep_style", "classic"
                        ) == "classic"
                    )
                    else None
                )
                if reading_normal_sweep_clip is not None:
                    painter.save()
                    painter.setClipRect(reading_normal_sweep_clip)
                painter.setPen(normal_pen)
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
                if reading_normal_sweep_clip is not None:
                    painter.restore()

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

                if progress > 0.0:
                    if segment.get("reading_vertical"):
                        # Vertical ruby must use the SAME local piece progress
                        # as its base glyph.  Using the whole group's progress
                        # here made every ruby piece sweep simultaneously while
                        # the base text correctly advanced piece-by-piece.
                        if segment.get("_vertical"):
                            piece_start, piece_end = vertical_piece_sweep.get(
                                id(segment), (0.0, 1.0)
                            )
                            piece_span = max(1e-9, piece_end - piece_start)
                            ruby_progress = max(
                                0.0,
                                min(
                                    1.0,
                                    (
                                        min(1.0, progress) - piece_start
                                    ) / piece_span,
                                ),
                            )
                        else:
                            ruby_progress = min(1.0, progress)

                        easing = getattr(
                            self,
                            "karaoke_sweep_easing",
                            "linear",
                        )
                        if easing == "ease_in":
                            ruby_progress = ruby_progress * ruby_progress
                        elif easing == "ease_out":
                            ruby_progress = (
                                1.0 - (1.0 - ruby_progress) ** 2
                            )
                        elif easing == "ease_in_out":
                            if ruby_progress < 0.5:
                                ruby_progress = (
                                    2.0 * ruby_progress * ruby_progress
                                )
                            else:
                                ruby_progress = (
                                    1.0
                                    - (
                                        (-2.0 * ruby_progress + 2.0) ** 2
                                    ) / 2.0
                                )

                        if ruby_progress > 0.0:
                            reading_step = float(
                                segment.get(
                                    "reading_step",
                                    QFontMetrics(self.reading_font).height(),
                                )
                            )
                            reading_top = (
                                float(segment.get("reading_y", 0.0))
                                - reading_metrics.ascent()
                            )
                            reading_height = max(
                                1.0, len(reading) * reading_step
                            )
                            painter.save()
                            painter.setClipRect(QRectF(
                                float(reading_x),
                                reading_top,
                                max(
                                    1.0,
                                    float(
                                        segment.get(
                                            "reading_width",
                                            reading_metrics.height(),
                                        )
                                    ),
                                ),
                                reading_height * ruby_progress,
                            ))
                            painter.setPen(highlight_pen)
                            cursor_y = float(
                                segment.get(
                                    "reading_y",
                                    reading_top + reading_metrics.ascent(),
                                )
                            )
                            for reading_char in reading:
                                painter.drawText(
                                    int(reading_x),
                                    int(cursor_y),
                                    reading_char,
                                )
                                cursor_y += reading_step
                            painter.restore()
                    elif not segment.get("_vertical"):
                        ruby_timing_group = segment.get(
                            "timing_group", segment.get("karaoke_group")
                        )
                        ruby_span = timing_spans.get((ruby_timing_group, segment.get("base_y")))
                        if ruby_span is not None:
                            ruby_left = ruby_span[0]
                            ruby_width = max(0.0, ruby_span[1] - ruby_span[0])
                        else:
                            ruby_left = float(segment.get("x", reading_x))
                            ruby_width = max(
                                float(segment.get("reading_width", 0.0)),
                                float(segment.get("width", 0.0)),
                            )
                        ruby_progress = min(1.0, max(0.0, progress))
                        easing = getattr(
                            self,
                            "karaoke_sweep_easing",
                            "linear",
                        )
                        if easing == "ease_in":
                            ruby_progress = ruby_progress * ruby_progress
                        elif easing == "ease_out":
                            ruby_progress = (
                                1.0 - (1.0 - ruby_progress) ** 2
                            )
                        elif easing == "ease_in_out":
                            if ruby_progress < 0.5:
                                ruby_progress = (
                                    2.0 * ruby_progress * ruby_progress
                                )
                            else:
                                ruby_progress = (
                                    1.0
                                    - (
                                        (-2.0 * ruby_progress + 2.0) ** 2
                                    ) / 2.0
                                )

                        style = getattr(
                            self,
                            "karaoke_sweep_style",
                            "classic",
                        )
                        sweep_end = ruby_left + ruby_width * ruby_progress

                        if style == "soft":
                            softness = max(
                                1,
                                min(
                                    40,
                                    int(
                                        getattr(
                                            self,
                                            "karaoke_softness",
                                            8,
                                        )
                                    ),
                                ),
                            )
                            feather = min(
                                48.0,
                                max(
                                    2.0,
                                    ruby_width * (softness / 100.0),
                                ),
                            )
                            gradient = QLinearGradient(
                                sweep_end - feather,
                                0,
                                sweep_end + feather,
                                0,
                            )
                            gradient.setColorAt(0.0, highlight_pen)
                            gradient.setColorAt(0.5, highlight_pen)
                            gradient.setColorAt(
                                1.0,
                                self.lyrics_color,
                            )
                            painter.save()
                            painter.setClipRect(
                                QRectF(
                                    ruby_left,
                                    0,
                                    ruby_width,
                                    self.height(),
                                )
                            )
                            painter.setPen(
                                QPen(QBrush(gradient), 1)
                            )
                            painter.drawText(
                                int(reading_x),
                                int(segment["reading_y"]),
                                reading,
                            )
                            painter.restore()

                        elif style == "shimmer":
                            painter.save()
                            painter.setClipRect(
                                QRectF(
                                    ruby_left,
                                    0,
                                    ruby_width * ruby_progress,
                                    self.height(),
                                )
                            )
                            painter.setPen(highlight_pen)
                            painter.drawText(
                                int(reading_x),
                                int(segment["reading_y"]),
                                reading,
                            )
                            painter.restore()

                            shimmer_width = max(
                                1,
                                min(
                                    40,
                                    int(
                                        getattr(
                                            self,
                                            "karaoke_shimmer_width",
                                            10,
                                        )
                                    ),
                                ),
                            )
                            band = min(
                                max(
                                    2.0,
                                    ruby_width * (shimmer_width / 100.0),
                                ),
                                max(2.0, ruby_width),
                            )
                            shimmer = QLinearGradient(
                                sweep_end - band,
                                0,
                                sweep_end + band,
                                0,
                            )
                            edge_color = QColor(highlight_pen)
                            edge_color.setAlpha(40)
                            bright = QColor(highlight_pen).lighter(180)
                            shimmer.setColorAt(0.0, edge_color)
                            shimmer.setColorAt(0.35, highlight_pen)
                            shimmer.setColorAt(0.5, bright)
                            shimmer.setColorAt(0.65, highlight_pen)
                            shimmer.setColorAt(1.0, edge_color)
                            painter.save()
                            band_left = max(
                                ruby_left,
                                sweep_end - band,
                            )
                            band_right = min(
                                ruby_left + ruby_width,
                                sweep_end + band,
                            )
                            painter.setClipRect(
                                QRectF(
                                    band_left,
                                    0,
                                    max(0.0, band_right - band_left),
                                    self.height(),
                                )
                            )
                            painter.setPen(
                                QPen(QBrush(shimmer), 1)
                            )
                            painter.drawText(
                                int(reading_x),
                                int(segment["reading_y"]),
                                reading,
                            )
                            painter.restore()

                        elif style == "glow":
                            glow_radius = max(
                                1,
                                min(
                                    8,
                                    int(
                                        getattr(
                                            self,
                                            "karaoke_glow_radius",
                                            1,
                                        )
                                    ),
                                ),
                            )
                            feather = min(
                                40.0,
                                max(
                                    4.0,
                                    ruby_width
                                    * (glow_radius * 0.03),
                                ),
                            )
                            glow_color = QColor(highlight_pen)
                            glow_color.setAlpha(
                                max(
                                    0,
                                    min(
                                        255,
                                        int(
                                            getattr(
                                                self,
                                                "karaoke_glow_intensity",
                                                70,
                                            )
                                        ),
                                    ),
                                )
                            )
                            painter.save()
                            painter.setClipRect(
                                QRectF(
                                    ruby_left,
                                    0,
                                    ruby_width * ruby_progress
                                    + feather,
                                    self.height(),
                                )
                            )
                            painter.setPen(glow_color)
                            for dx, dy in (
                                (-glow_radius, 0),
                                (glow_radius, 0),
                                (0, -glow_radius),
                                (0, glow_radius),
                            ):
                                painter.drawText(
                                    int(reading_x + dx),
                                    int(segment["reading_y"] + dy),
                                    reading,
                                )
                            painter.restore()

                            painter.save()
                            painter.setClipRect(
                                QRectF(
                                    ruby_left,
                                    0,
                                    ruby_width * ruby_progress,
                                    self.height(),
                                )
                            )
                            painter.setPen(highlight_pen)
                            painter.drawText(
                                int(reading_x),
                                int(segment["reading_y"]),
                                reading,
                            )
                            painter.restore()

                        else:  # classic
                            painter.save()
                            painter.setClipRect(
                                QRectF(
                                    ruby_left,
                                    0,
                                    ruby_width * ruby_progress,
                                    self.height(),
                                )
                            )
                            painter.setPen(highlight_pen)
                            painter.drawText(
                                int(reading_x),
                                int(segment["reading_y"]),
                                reading,
                            )
                            painter.restore()

                painter.restore()

            self._paint_decoration_spans(
                painter, segment, normal_pen, highlight_pen, progress,
                timing_spans.get((timing_group, segment.get("base_y"))),
                normal_highlight and self.highlight_animation == "slide",
            )

        painter.restore()
        painter.end()

