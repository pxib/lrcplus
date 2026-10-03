"""
Built-in Lyrics Display plugin.

Owns LRC parsing, furigana generation, and lyric display widgets.
"""
import re
from concurrent.futures import ThreadPoolExecutor

from lyrics.furigana import build_furigana_segments

from PySide6.QtCore import (
    Qt,
    QRectF,
    QPropertyAnimation,
    QEasingCurve,
    QTimer,
    QObject,
    Signal,
)
from PySide6.QtGui import (
    QFont,
    QFontMetrics,
    QColor,
    QPainter,
)
from PySide6.QtWidgets import (
    QVBoxLayout,
    QWidget,
    QScrollArea,
)

_FURIGANA_EXECUTOR = ThreadPoolExecutor(
    max_workers=2,
    thread_name_prefix="LyricsPlus-Yomi",
)
_FURIGANA_SHUTTING_DOWN = False

def load_lrc(path):
    """Compatibility service backed by the canonical occurrence-aware parser."""
    from lyrics.parser import load_lrc as _load_lrc
    return _load_lrc(path)


class _FuriganaBridge(QObject):
    ready = Signal(str, object)


class FuriganaWidget(QWidget):
    BASE_FONT_SIZE = 32
    READING_FONT_SIZE = 18

    PADDING = 6
    READING_BASE_GAP = 0

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
        self.direction = "center"
        self.show_ruby = True
        self.ruby_padding = 2

        self.base_font = QFont()
        self.base_font.setPointSize(self.BASE_FONT_SIZE)

        self.reading_font = QFont()
        self.reading_font.setPointSize(self.READING_FONT_SIZE)

        self._furigana_bridge = _FuriganaBridge(self)
        self._furigana_bridge.ready.connect(self.furigana_ready)
        self._furigana_future = None
        self.setMinimumHeight(self.RUBY_BAND_HEIGHT + self.BASE_HEIGHT)

    def set_ruby_visible(self, visible):
        visible = bool(visible)
        if self.show_ruby == visible:
            return
        self.show_ruby = visible
        self.update()

    def set_ruby_padding(self, padding):
        padding = max(0, min(18, int(padding)))
        if self.ruby_padding == padding:
            return
        self.ruby_padding = padding
        self.update()

    def set_direction(self, direction):
        direction = direction if direction in {"left", "center", "right"} else "center"
        if self.direction == direction:
            return
        self.direction = direction
        self.update()

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

    def set_resolved_segments(self, text, segments):
        """Display readings already resolved by the lyric model.

        Automatic Korean/Japanese/etc. readings are generated upstream and may
        have a different segmentation from a fresh renderer-side parse.  Use
        those exact segments when available.
        """
        self.lyric = str(text or "")
        resolved = []
        for segment in segments or []:
            if isinstance(segment, dict):
                piece = str(segment.get("text", ""))
                reading = segment.get("reading")
            else:
                piece = str(getattr(segment, "text", ""))
                reading = getattr(segment, "ruby", None)
            if piece:
                resolved.append({"text": piece, "reading": reading or None})

        if resolved and "".join(item["text"] for item in resolved) == self.lyric:
            self.loading_text = self.lyric
            self.segments = self.apply_manual_overrides(resolved)
            self.update()
            return

        self.set_lyric(self.lyric)

    def set_lyric(self, text):
        self.lyric = text

        # Janome tokenization is local and fast, so processing it in the GUI
        # thread avoids the lifecycle hazards of one QThread per lyric line.
        self.segments = [{
            "text": text,
            "reading": None,
        }]

        if not text:
            self.loading_text = None
            self.update()
            return

        if text == getattr(self, "loading_text", None):
            self.update()
            return

        self.loading_text = text

        # Yomi is network-backed; parse in a worker so loading a scrolling
        # lyric list never blocks the Qt event loop.
        def parse():
            try:
                return build_furigana_segments(
                    text,
                    should_continue=lambda: not _FURIGANA_SHUTTING_DOWN,
                )
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
        print(f"[Furigana] Ready: {len(segments)} segments for {text!r}")

        # Ignore an old request if the lyric has already changed.
        if text != self.lyric:
            return

        self.segments = self.apply_manual_overrides(segments)
        self.update()

    def _prepare_segments(self):
        if not self.lyric:
            return []

        base_metrics = QFontMetrics(self.base_font)
        reading_metrics = QFontMetrics(self.reading_font)

        prepared = []
        total_width = 0

        for index, segment in enumerate(self.segments):
            base_width = base_metrics.horizontalAdvance(segment["text"])
            reading_width = 0
            reading = segment.get("reading")

            if reading:
                reading_width = reading_metrics.horizontalAdvance(reading)

            width = max(base_width, reading_width)
            prepared.append({
                **segment,
                "index": index,
                "base_width": base_width,
                "reading_width": reading_width,
                "width": width,
            })
            total_width += width
            if segment.get("reading"):
                total_width += self.PADDING

        if self.direction == "left":
            x = 0
        elif self.direction == "right":
            x = self.width() - total_width
        else:
            x = (self.width() - total_width) / 2

        x = max(0, x)

        base_y = (
            self.RUBY_BAND_HEIGHT
            + self.BASE_HEIGHT
            - max(2, (self.BASE_HEIGHT - base_metrics.height()) // 2)
        )
        reading_y = (
            base_y
            - base_metrics.height()
            - self.READING_BASE_GAP
            + self.RUBY_ZERO_OFFSET
            - self.ruby_padding
        )

        for segment in prepared:
            segment["x"] = x
            segment["base_y"] = base_y
            segment["reading_y"] = reading_y
            x += segment["width"]
            if segment.get("reading"):
                x += self.PADDING

        return prepared

    def _edit_reading_for_segment(self, segment):
        lyric_text = self.lyric
        segment_index = segment["index"]
        current = segment.get("reading") or ""

        new_reading, ok = QInputDialog.getText(
            self,
            "Edit Furigana",
            f"Reading for: {segment['text']}",
            text=current,
        )
        if not ok:
            return

        self.manual_overrides.setdefault(lyric_text, {})[segment_index] = new_reading.strip()

        if self.lyric == lyric_text:
            self.segments = self.apply_manual_overrides(self.segments)
            self.update()

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

    def paintEvent(self, event):
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

        painter.save()
        painter.setOpacity(self.display_opacity)

        for segment in prepared:
            width = segment["width"]
            base_x = (
                segment["x"]
                + (width - segment["base_width"]) / 2
            )

            painter.setFont(self.base_font)

            painter.drawText(
                int(base_x),
                int(segment["base_y"]),
                segment["text"],
            )

            reading = segment["reading"]

            if self.show_ruby and reading:
                reading_x = (
                    segment["x"]
                    + (
                        width
                        - segment["reading_width"]
                    )
                    / 2
                )

                painter.setFont(self.reading_font)

                painter.drawText(
                    int(reading_x),
                    int(segment["reading_y"]),
                    reading,
                )

                # Long reading connector.
                if (
                    segment["reading_width"]
                    > segment["base_width"] * 1.25
                ):
                    connector_x = (
                        segment["x"] + width / 2
                    )

                    painter.drawLine(
                        int(connector_x),
                        int(segment["reading_y"] + 4),
                        int(connector_x),
                        int(
                            segment["base_y"]
                            - QFontMetrics(self.base_font).height()
                            + 2
                        ),
                    )

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
        self.direction = direction if direction in {"left", "center", "right"} else "center"
        self.padding = max(0, int(padding))
        self.ruby_padding = 2
        self.show_ruby = True
        self.rows = []
        self.current_line = -1
        self.scroll_animation = None

        self.scroll_area = QScrollArea()
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setHorizontalScrollBarPolicy(
            Qt.ScrollBarPolicy.ScrollBarAlwaysOff
        )

        self.container = QWidget()
        self.layout = QVBoxLayout(self.container)
        self.layout.setContentsMargins(0, 90, 0, 90)
        self.layout.setSpacing(0)

        self.scroll_area.setWidget(self.container)

        outer_layout = QVBoxLayout(self)
        outer_layout.setContentsMargins(0, 0, 0, 0)
        outer_layout.addWidget(self.scroll_area)

    def set_padding(self, padding):
        padding = max(0, min(24, int(padding)))
        if self.padding == padding:
            return

        self.padding = padding
        row_height = self.RUBY_BAND_HEIGHT + self.BASE_HEIGHT + self.padding

        for row in self.rows:
            row.setFixedHeight(row_height)

        self.updateGeometry()
        self.update()

    def set_ruby_visible(self, visible):
        self.show_ruby = bool(visible)
        for row in self.rows:
            row.set_ruby_visible(self.show_ruby)
        self.update()

    def set_ruby_padding(self, padding):
        self.ruby_padding = max(0, min(18, int(padding)))
        for row in self.rows:
            row.set_ruby_padding(self.ruby_padding)
        self.update()

    def set_direction(self, direction):
        direction = direction if direction in {"left", "center", "right"} else "center"
        if self.direction == direction:
            return
        self.direction = direction
        for row in self.rows:
            row.set_direction(direction)
        self.update()

    def clear(self):
        self.current_line = -1
        if self.scroll_animation is not None:
            self.scroll_animation.stop()
            self.scroll_animation = None

        for row in self.rows:
            row.setParent(None)
            row.deleteLater()

        self.rows.clear()

    def set_lyrics(self, lyrics):
        self.clear()

        for _, text in lyrics:
            row = FuriganaWidget(self.manual_overrides)
            row.set_direction(self.direction)
            row.set_ruby_visible(self.show_ruby)
            row.set_ruby_padding(self.ruby_padding)
            row.setFixedHeight(
                self.RUBY_BAND_HEIGHT + self.BASE_HEIGHT + self.padding
            )
            row.set_active(False)
            row.set_opacity(0.32)
            segments = getattr(line, "segments", None)
            if segments:
                row.set_resolved_segments(text, segments)
            else:
                row.set_lyric(text)
            self.layout.addWidget(row)
            self.rows.append(row)

        if self.rows:
            self.rows[0].set_active(True)
            self.rows[0].set_opacity(1.0)

        QTimer.singleShot(0, lambda: self._scroll_to_line(0, False))

    def set_current_line(self, index, animate=True):
        if not self.rows:
            self.current_line = -1
            return

        if index < 0 or index >= len(self.rows):
            for row in self.rows:
                row.set_active(False)
            self.current_line = -1
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
            row.set_opacity(opacity)

        self.current_line = index
        QTimer.singleShot(0, lambda: self._scroll_to_line(index, animate))

    def _scroll_to_line(self, index, animate=True):
        if not (0 <= index < len(self.rows)):
            return

        row = self.rows[index]
        scrollbar = self.scroll_area.verticalScrollBar()
        viewport_height = self.scroll_area.viewport().height()
        # Center the lyric band itself. Ruby lives in its own reserved band
        # above it, so the current line no longer needs a large scroll offset.
        target = int(
            row.geometry().center().y()
            - viewport_height / 2
        )
        target = max(0, min(target, scrollbar.maximum()))

        if not animate:
            scrollbar.setValue(target)
            return

        if self.scroll_animation is not None:
            self.scroll_animation.stop()

        animation = QPropertyAnimation(scrollbar, b"value", self)
        animation.setDuration(self.SCROLL_ANIMATION_MS)
        animation.setStartValue(scrollbar.value())
        animation.setEndValue(target)
        animation.setEasingCurve(QEasingCurve.Type.OutCubic)
        animation.finished.connect(lambda: self._clear_animation(animation))
        self.scroll_animation = animation
        animation.start()

    def _clear_animation(self, animation):
        if self.scroll_animation is animation:
            self.scroll_animation = None

class LyricsDisplayPlugin:
    name = "Lyrics Display"
    version = "1.0.0"
    description = "Built-in LRC parsing and furigana lyric display."

    def on_load(self, context):
        context.register_service("lyrics.display", self)
        context.register_service("lyrics.load_lrc", load_lrc)
        context.log(f"Loaded {self.name} {self.version}")

    def on_unload(self, context):
        global _FURIGANA_SHUTTING_DOWN

        _FURIGANA_SHUTTING_DOWN = True

        # Cancel queued lyric requests immediately. A request already inside
        # requests.post() cannot be forcibly killed, but it will finish at its
        # timeout and no additional queued lyrics will start afterwards.
        _FURIGANA_EXECUTOR.shutdown(
            wait=False,
            cancel_futures=True,
        )

        context.log(f"Unloaded {self.name}")


PLUGIN_CLASS = LyricsDisplayPlugin
