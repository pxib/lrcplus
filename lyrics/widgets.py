import unicodedata
import re
import math
from concurrent.futures import ThreadPoolExecutor

from PySide6.QtCore import Qt, QRectF, QPropertyAnimation, QEasingCurve, QTimer, QSize, QVariantAnimation, QObject, Signal
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

_FURIGANA_EXECUTOR = ThreadPoolExecutor(max_workers=2, thread_name_prefix="LyricsPlus-Yomi")




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
        self.base_font.setPointSize(self.BASE_FONT_SIZE)

        self.reading_font = QFont()
        self.reading_font.setPointSize(self.READING_FONT_SIZE)

        self._furigana_bridge = _FuriganaBridge(self)
        self._furigana_bridge.ready.connect(self.furigana_ready)
        self._furigana_future = None
        self._karaoke_furigana_pending = None

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
        if end is None:
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

    def set_segments(self, text, segments, line_end=None):
        """Display timed segments while generating readings from the full lyric.

        Karaoke boundaries are timing metadata only.  Automatic reading
        generation always sees the complete unsplit lyric, then each generated
        token is mapped back to the timing range that owns its character range.
        Explicit LRCX ruby remains authoritative.
        """
        text = str(text).lstrip()
        self.lyric = text
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

        # IMPORTANT: set_lyrics() constructs one FuriganaWidget for every
        # lyric line. Calling build_furigana_segments() here therefore meant
        # opening a song synchronously romanized the *entire* song on the Qt
        # thread. PyCantonese is particularly expensive and made first load
        # freeze. Only consume an already-generated cached result here; the
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
                            f"[Yomi] Karaoke furigana processing failed for {text!r}: {error!r}"
                        )
                        return [{"text": text, "reading": None}]

                future = _FURIGANA_EXECUTOR.submit(parse_karaoke)
                self._furigana_future = future

                def finished_karaoke(done):
                    try:
                        result = done.result()
                    except Exception as error:
                        print(
                            f"[Yomi] Karaoke worker failed for {text!r}: {error!r}"
                        )
                        result = [{"text": text, "reading": None}]
                    self._furigana_bridge.ready.emit(
                        text,
                        {"karaoke": original_segments, "segments": result, "line_end": line_end},
                    )

                future.add_done_callback(finished_karaoke)
                return

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
                owner = owner_for(a)
                if owner is None:
                    continue

                piece_text = text[a:b]
                reading = None
                source = "generated"

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
                elif a == token_start and b == token_end:
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

    def set_lyric(self, text, generate=True):
        # Preserve whitespace in the source/model, but ignore optional
        # leading whitespace after an LRC timestamp when rendering.
        text = str(text).lstrip()
        self.lyric = text

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
            try:
                result = done.result()
            except Exception as error:
                print(f"[Yomi] Worker failed for {text!r}: {error!r}")
                result = [{"text": text, "reading": None}]
            self._furigana_bridge.ready.emit(text, result)

        future.add_done_callback(finished)

    def furigana_ready(self, text, segments):
        # Karaoke requests return a small envelope so the cached full-line
        # reading can be remapped onto the original timing chunks.  Ordinary
        # lyric rendering still passes the segment list directly.
        if isinstance(segments, dict) and "segments" in segments:
            karaoke_segments = segments.get("karaoke") or []
            generated_segments = segments.get("segments") or []
            line_end = segments.get("line_end")
            print(
                f"[Furigana] Ready: {len(generated_segments)} segments for {text!r} (karaoke)"
            )

            if text != self.lyric:
                if self._karaoke_furigana_pending == text:
                    self._karaoke_furigana_pending = None
                return

            self._karaoke_furigana_pending = None
            self.loading_text = None
            self.set_segments(text, karaoke_segments, line_end=line_end)
            return

        print(f"[Furigana] Ready: {len(segments)} segments for {text!r}")

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

        for source in self.segments:
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
        if self._has_visible_ruby():
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
            self.setMinimumHeight(total)
        self.updateGeometry()

        # A scrolling lyric row may have a fixed height in its parent.  When a
        # reading arrives asynchronously (including romaji), make the parent
        # recalculate row geometry instead of waiting for an unrelated resize.
        parent = self.parentWidget()
        while parent is not None:
            refresh = getattr(parent, "_refresh_row_heights", None)
            if callable(refresh):
                QTimer.singleShot(0, refresh)
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
        return QSize(max(0, self.minimumWidth()), total)

    def minimumSizeHint(self):
        return self.sizeHint()

    def _wrap_prepared_segments(self, prepared, base_metrics, reading_metrics):
        if not self.wrap_text or not prepared:
            return prepared

        available = max(1, self.width() - 20)
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

        rows = [[]]
        row_widths = [0.0]
        truncated = False

        def add_piece(piece):
            nonlocal truncated
            if truncated:
                return
            width = piece["width"]
            gap = piece.get("gap_before", 0)

            # Start a new visual row when the next piece would overflow.
            if rows[-1] and row_widths[-1] + gap + width > available:
                if len(rows) >= max_lines:
                    truncated = True
                    return
                rows.append([])
                row_widths.append(0.0)
                gap = 0
                piece["gap_before"] = 0

            # A single segment can itself be wider than the panel. Split it
            # into visible character chunks so Japanese text can wrap naturally.
            if not rows[-1] and width > available and len(piece["text"]) > 1:
                text_value = piece["text"]
                chunk = ""
                for char in text_value:
                    candidate = chunk + char
                    if chunk and base_metrics.horizontalAdvance(candidate) > available:
                        clone = dict(piece)
                        clone["text"] = chunk
                        clone["base_width"] = base_metrics.horizontalAdvance(chunk)
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
                    clone["base_width"] = base_metrics.horizontalAdvance(chunk)
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
                x = 0.0
            elif self.alignment == "right":
                x = max(0.0, self.width() - total_width)
            else:
                x = max(0.0, (self.width() - total_width) / 2.0)

            y_offset = row_index * line_height
            if ruby_height:
                reading_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + reading_metrics.ascent()
                )
                base_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + ruby_height
                    + ruby_gap
                    + base_metrics.ascent()
                )
            else:
                reading_y = None
                base_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + base_metrics.ascent()
                )

            if self.show_romaji and not ruby_height:
                romaji_y = (
                    self.TOP_MARGIN
                    + y_offset
                    + base_metrics.height()
                    + self.ruby_padding
                    + reading_metrics.ascent()
                )
            else:
                romaji_y = None

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
        source = reading if reading and any("\u3400" <= ch <= "\u9fff" for ch in text) else text

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

    def _prepare_segments(self):
        if not self.lyric:
            return []

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        prepared = []
        total_width = 0

        previous_text = ""
        pending_sokuon = False
        for index, segment in enumerate(self.segments):
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
            base_width = base_metrics.horizontalAdvance(text)
            reading_width = 0
            reading = segment.get("reading")
            ruby_visible = bool((self.show_ruby or self.show_romaji) and reading)

            if ruby_visible:
                reading_width = reading_metrics.horizontalAdvance(reading)

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
            total_width += gap_before + width
            if ruby_visible:
                total_width += self.PADDING
            previous_text = text
            pending_sokuon = segment_sokuon

        # All-Romaji is much wider than Japanese. Fit only overflowing lines by
        # compressing their X axis; the configured font size and vertical rhythm
        # stay unchanged.
        if self.all_romaji and self.direction != "vertical" and total_width > 0:
            available_width = max(1.0, float(self.width() - 12))
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
                text_value = str(source.get("text", ""))
                if not text_value:
                    continue

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
                        piece["base_width"] = base_metrics.horizontalAdvance(char)
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
                cursor_x = float(self.PADDING)
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
            x = 0
        elif self.alignment == "right":
            x = self.width() - total_width
        else:
            x = (self.width() - total_width) / 2

        x = max(0, x)

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
            left = float(segment["x"])
            right = left + float(segment["width"])
            if group not in groups:
                groups[group] = [left, right]
            else:
                groups[group][0] = min(groups[group][0], left)
                groups[group][1] = max(groups[group][1], right)

        for segment in prepared:
            group = segment.get("timing_group", segment.get("karaoke_group"))
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
        if romaji_x_scale < 1.0 and self.direction != "vertical":
            painter.scale(romaji_x_scale, 1.0)

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

            painter.setFont(self.base_font)

            # v0.2: draw normal text first, then clip the karaoke color across
            # the segment according to its progress between timed starts.
            normal_pen = self.lyrics_color
            highlight_pen = self.highlight_color
            timing_group = segment.get(
                "timing_group", segment.get("karaoke_group")
            )
            progress = group_progress.get(
                timing_group,
                self._segment_progress(segment, prepared),
            )

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
                    span = timing_spans.get(timing_group)
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
            display_reading = self._reading_text_for_display(reading)

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
                        - QFontMetrics(self.reading_font).horizontalAdvance(display_reading)
                    )
                    / 2
                )

                painter.setFont(self.reading_font)

                painter.setPen(normal_pen)
                if segment.get("reading_vertical"):
                    reading_x = segment.get("reading_x", reading_x)
                    reading_y = segment.get("reading_y", self.TOP_MARGIN + reading_metrics.ascent() if False else self.TOP_MARGIN)
                    cursor_y = reading_y
                    for reading_char in reading:
                        painter.drawText(int(reading_x), int(cursor_y), reading_char)
                        cursor_y += segment.get("reading_step", QFontMetrics(self.reading_font).height())
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
                        ruby_span = timing_spans.get(ruby_timing_group)
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

        painter.restore()
        painter.end()


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
            previous_row.set_karaoke_position(finish_at)

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
        changed = False

        for row_index, row in enumerate(self.rows):
            if (
                self.limited_reading_rendering
                and self.current_line >= 0
                and abs(row_index - self.current_line) > self.limited_reading_range
            ):
                continue
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
                    changed = True

        # Rows are initially created with plain-text geometry while furigana
        # is generated in the background. Once ruby arrives, the widget's
        # minimum height grows, but its fixed layout height would otherwise
        # remain at the old plain-text value. Recalculate the scrolling layout
        # after each batch so padding stays correct during generation too.
        if changed:
            self._refresh_row_heights()

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

