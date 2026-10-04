from PySide6.QtCore import Qt, QPoint, QTimer
from PySide6.QtGui import QFontMetrics, QPainter, QColor
from PySide6.QtGui import QFont
from PySide6.QtWidgets import (
    QWidget, QVBoxLayout, QHBoxLayout, QPushButton, QFormLayout,
    QSpinBox, QDoubleSpinBox, QCheckBox, QLineEdit, QColorDialog,
    QGroupBox
)
from lyrics.widgets import FuriganaWidget


class BaseLyricRenderer:
    """Draw the main lyric at one fixed baseline, independent of ruby."""

    FIXED_BASE_Y = 75

    def paint(self, painter, owner, prepared):
        painter.setFont(owner.base_font)
        painter.setPen(QColor(owner.text_color))
        for segment in prepared:
            width = segment["width"]
            base_x = (
                segment["x"]
                + (width - segment["base_width"]) / 2
            )
            painter.drawText(
                int(base_x),
                self.FIXED_BASE_Y,
                segment["text"],
            )


class RubyRenderer:
    """Draw ruby independently above the fixed base baseline."""

    RUBY_GAP = 12

    def paint(self, painter, owner, prepared):
        if not owner.show_ruby:
            return

        painter.setFont(owner.reading_font)
        painter.setPen(QColor(owner.text_color))
        base_metrics = QFontMetrics(owner.base_font)
        reading_metrics = QFontMetrics(owner.reading_font)

        fixed_base_y = owner._base_renderer.FIXED_BASE_Y
        reading_y = (
            fixed_base_y
            - base_metrics.height()
            - self.RUBY_GAP
            + reading_metrics.ascent()
        )

        for segment in prepared:
            reading = segment.get("reading")
            if not reading:
                continue

            width = segment["width"]
            reading_x = (
                segment["x"]
                + (width - segment["reading_width"]) / 2
            )
            painter.drawText(
                int(reading_x),
                int(reading_y),
                reading,
            )


class SplitRenderFuriganaWidget(FuriganaWidget):
    """
    Floating-only renderer.

    The main lyric and ruby are painted by two independent render passes from
    the same prepared segment geometry. The base pass therefore never needs to
    know whether a segment has furigana.
    """

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)

        self._base_renderer = BaseLyricRenderer()
        self._ruby_renderer = RubyRenderer()
        # Renderers receive this widget as their owner.
        self.text_color = "#FFFFFF"

        self._pan_offset = 0.0
        self._pan_start = 0.0
        self._pan_end = 0.0
        self._pan_direction = 1
        self._pan_active = False
        self._pan_timer = QTimer(self)
        self._pan_timer.setInterval(16)
        self._pan_timer.timeout.connect(self._advance_pan)

    def set_lyric(self, lyric):
        super().set_lyric(lyric)
        self._stop_pan()
        QTimer.singleShot(0, self._configure_pan)

    def _raw_segments(self):
        return super()._prepare_segments()

    def _configure_pan(self):
        prepared = self._raw_segments()
        if not prepared or self.width() <= 0:
            return

        left = min(float(s.get("x", 0)) for s in prepared)
        right = max(
            float(s.get("x", 0)) + float(s.get("width", 0))
            for s in prepared
        )
        viewport = float(self.width())

        if left >= 0 and right <= viewport:
            self._pan_offset = 0.0
            self.update()
            return

        self._pan_start = -left if left < 0 else 0.0
        self._pan_end = viewport - right if right > viewport else self._pan_start
        self._pan_offset = self._pan_start
        self._pan_direction = 1
        self._pan_active = True
        self.update()
        QTimer.singleShot(700, self._start_pan)

    def _start_pan(self):
        if self._pan_active and self._pan_start != self._pan_end:
            self._pan_timer.start()

    def _advance_pan(self):
        step = 1.25
        if self._pan_direction > 0:
            self._pan_offset -= step
            if self._pan_offset <= self._pan_end:
                self._pan_offset = self._pan_end
                self._pan_direction = -1
                self._pan_timer.stop()
                QTimer.singleShot(900, self._resume_pan)
        else:
            self._pan_offset += step
            if self._pan_offset >= self._pan_start:
                self._pan_offset = self._pan_start
                self._pan_direction = 1
                self._pan_timer.stop()
                QTimer.singleShot(900, self._resume_pan)
        self.update()

    def _resume_pan(self):
        if self._pan_active:
            self._pan_timer.start()

    def _stop_pan(self):
        self._pan_active = False
        self._pan_timer.stop()
        self._pan_offset = 0.0

    def _prepare_segments(self):
        # Use the main renderer's geometry unchanged. It already reserves a
        # constant ruby band, so base_y does not depend on whether ruby exists.
        prepared = super()._prepare_segments()
        if self._pan_offset:
            for segment in prepared:
                if not segment.get("_vertical"):
                    segment["x"] = (
                        float(segment.get("x", 0))
                        + self._pan_offset
                    )
        return prepared

    def paintEvent(self, event):
        if not self.lyric:
            return

        prepared = self._prepare_segments()
        if not prepared:
            return

        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
            painter.save()
            try:
                painter.setOpacity(self.display_opacity)

                # Independent passes, shared positions.
                self._base_renderer.paint(painter, self, prepared)
                self._ruby_renderer.paint(painter, self, prepared)
            finally:
                painter.restore()
        finally:
            if painter.isActive():
                painter.end()


class FloatingLyricsBackground(QWidget):
    """Transparent-capable rounded background painted independently of stylesheets."""

    def __init__(self, parent=None):
        super().__init__(parent)
        self.opacity = 0.35
        self.radius = 18
        self.setAttribute(Qt.WidgetAttribute.WA_TransparentForMouseEvents, True)

    def set_appearance(self, opacity, radius):
        self.opacity = max(0.0, min(1.0, float(opacity)))
        self.radius = max(0, int(radius))
        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        try:
            painter.setRenderHint(QPainter.RenderHint.Antialiasing)
            alpha = int(self.opacity * 255)
            painter.setPen(Qt.PenStyle.NoPen)
            painter.setBrush(QColor(20, 20, 20, alpha))
            rect = self.rect().adjusted(0, 0, -1, -1)
            painter.drawRoundedRect(rect, self.radius, self.radius)
        finally:
            painter.end()


class FloatingLyricsWindow(QWidget):
    def __init__(self, settings):
        super().__init__(None)
        self.settings = settings
        self._drag_offset = None
        self.visibility_changed = None

        # Required for rgba() window backgrounds to actually reveal whatever
        # is behind this top-level floating window.
        self.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground, True)

        self.text_color = "#FFFFFF"
        self.setWindowTitle("Floating Lyrics")

        self.resize(720, 150)

        # Paint the actual translucent surface ourselves. Qt stylesheets on a
        # translucent top-level window do not reliably provide this.
        self._background = FloatingLyricsBackground(self)
        self._background.lower()

        self._build_ui()
        self.apply_settings(settings, initial=True)

    def resizeEvent(self, event):
        if hasattr(self, "_background"):
            self._background.setGeometry(self.rect())
        super().resizeEvent(event)

    def showEvent(self, event):
        super().showEvent(event)
        if self.visibility_changed is not None:
            self.visibility_changed(True)

    def hideEvent(self, event):
        super().hideEvent(event)
        if self.visibility_changed is not None:
            self.visibility_changed(False)

    def _build_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(18, 10, 12, 14)

        top = QHBoxLayout()
        top.addStretch()
        close = QPushButton("×")
        close.setFixedSize(30, 28)
        close.clicked.connect(self.hide)
        top.addWidget(close)
        self.close_button = close
        layout.addLayout(top)

        self.lyric_widget = SplitRenderFuriganaWidget()
        self.lyric_widget.setMinimumHeight(110)
        self.lyric_widget.setMaximumHeight(110)
        self.lyric_widget.set_alignment("center")
        self.lyric_widget.set_ruby_visible(True)
        layout.addWidget(self.lyric_widget, 1)

    def _window_flags(self, settings):
        flags = Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint
        if settings.get("always_on_top", True):
            flags |= Qt.WindowType.WindowStaysOnTopHint
        if settings.get("click_through", False):
            flags |= Qt.WindowType.WindowTransparentForInput
        return flags

    def apply_settings(self, settings, initial=False):
        was_visible = self.isVisible()
        old_pos = self.pos()

        self.settings = dict(settings)
        self.text_color = self.settings.get("text_color", "#FFFFFF")
        self.lyric_widget.text_color = self.text_color

        base_font = QFont(self.lyric_widget.base_font)
        base_font.setPointSize(int(self.settings.get("base_font_size", base_font.pointSize())))
        self.lyric_widget.base_font = base_font

        ruby_font = QFont(self.lyric_widget.reading_font)
        ruby_font.setPointSize(int(self.settings.get("ruby_font_size", ruby_font.pointSize())))
        self.lyric_widget.reading_font = ruby_font

        self.lyric_widget.display_opacity = float(self.settings.get("text_opacity", 1.0))
        width = int(self.settings.get("window_width", 720))
        self.resize(width, self.height())

        bg_opacity = float(self.settings.get("background_opacity", 0.35))
        radius = int(self.settings.get("corner_radius", 18))
        # The background is painted directly so 0.0–1.0 opacity maps
        # predictably to the visible floating-window surface.
        self._background.set_appearance(bg_opacity, radius)
        self.setStyleSheet(
            "FloatingLyricsWindow { background: transparent; }"
        )
        self.close_button.setVisible(not self.settings.get("click_through", False))

        self.setWindowFlags(self._window_flags(self.settings))
        if was_visible:
            self.move(old_pos)
            self.show()
            self.raise_()
        self.lyric_widget.update()

    def export_settings(self):
        return dict(self.settings)

    def set_text(self, text):
        text = str(text or "")
        if getattr(self, "_current_text", None) == text:
            return
        self._current_text = text
        self.lyric_widget.set_lyric(text)

    def set_furigana_enabled(self, enabled):
        self.lyric_widget.set_ruby_visible(enabled)

    def mousePressEvent(self, event):
        if self.settings.get("lock_position", False):
            event.ignore()
            return
        if event.button() == Qt.MouseButton.LeftButton:
            self._drag_offset = event.globalPosition().toPoint() - self.frameGeometry().topLeft()
            event.accept()
            return
        super().mousePressEvent(event)

    def mouseMoveEvent(self, event):
        if self.settings.get("lock_position", False):
            event.ignore()
            return
        if self._drag_offset is not None and event.buttons() & Qt.MouseButton.LeftButton:
            self.move(event.globalPosition().toPoint() - self._drag_offset)
            event.accept()
            return
        super().mouseMoveEvent(event)

    def mouseReleaseEvent(self, event):
        self._drag_offset = None
        super().mouseReleaseEvent(event)


class FloatingLyricsSettingsWidget(QWidget):
    """Embedded Settings > Plugins widget for Floating Lyrics."""

    def __init__(self, settings, parent=None):
        super().__init__(parent)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)

        appearance = QGroupBox("Appearance")
        form = QFormLayout(appearance)

        self.base_size = QSpinBox()
        self.base_size.setRange(8, 96)
        self.base_size.setValue(int(settings["base_font_size"]))
        form.addRow("Lyric font size", self.base_size)

        self.ruby_size = QSpinBox()
        self.ruby_size.setRange(6, 72)
        self.ruby_size.setValue(int(settings["ruby_font_size"]))
        form.addRow("Furigana font size", self.ruby_size)

        self.text_opacity = QDoubleSpinBox()
        self.text_opacity.setRange(0.10, 1.00)
        self.text_opacity.setSingleStep(0.05)
        self.text_opacity.setDecimals(2)
        self.text_opacity.setValue(float(settings["text_opacity"]))
        form.addRow("Text opacity", self.text_opacity)

        self.bg_opacity = QDoubleSpinBox()
        self.bg_opacity.setRange(0.00, 1.00)
        self.bg_opacity.setSingleStep(0.05)
        self.bg_opacity.setDecimals(2)
        self.bg_opacity.setValue(float(settings["background_opacity"]))
        form.addRow("Background opacity", self.bg_opacity)

        self.corner_radius = QSpinBox()
        self.corner_radius.setRange(0, 60)
        self.corner_radius.setValue(int(settings["corner_radius"]))
        form.addRow("Corner radius", self.corner_radius)

        self.window_width = QSpinBox()
        self.window_width.setRange(300, 2000)
        self.window_width.setSingleStep(20)
        self.window_width.setValue(int(settings["window_width"]))
        form.addRow("Window width", self.window_width)

        color_row = QHBoxLayout()
        self.color = QLineEdit(settings["text_color"])
        self.color_pick = QPushButton("Pick…")
        self.color_pick.clicked.connect(self.pick_color)
        color_row.addWidget(self.color, 1)
        color_row.addWidget(self.color_pick)
        form.addRow("Text color", color_row)

        root.addWidget(appearance)

        behavior = QGroupBox("Window Behavior")
        behavior_layout = QVBoxLayout(behavior)

        self.always_on_top = QCheckBox("Always on top")
        self.always_on_top.setChecked(bool(settings["always_on_top"]))
        behavior_layout.addWidget(self.always_on_top)

        self.lock_position = QCheckBox("Lock window position")
        self.lock_position.setChecked(bool(settings["lock_position"]))
        behavior_layout.addWidget(self.lock_position)

        self.click_through = QCheckBox("Click-through window")
        self.click_through.setChecked(bool(settings["click_through"]))
        behavior_layout.addWidget(self.click_through)

        self.remember_position = QCheckBox("Remember window position")
        self.remember_position.setChecked(bool(settings["remember_position"]))
        behavior_layout.addWidget(self.remember_position)

        self.hide_on_pause = QCheckBox("Hide when playback pauses")
        self.hide_on_pause.setChecked(bool(settings["hide_on_pause"]))
        behavior_layout.addWidget(self.hide_on_pause)

        root.addWidget(behavior)

    def pick_color(self):
        color = QColorDialog.getColor(
            QColor(self.color.text()),
            self,
            "Choose lyric color",
        )
        if color.isValid():
            self.color.setText(color.name())

    def values(self):
        color = QColor(self.color.text())
        return {
            "base_font_size": self.base_size.value(),
            "ruby_font_size": self.ruby_size.value(),
            "text_opacity": self.text_opacity.value(),
            "background_opacity": self.bg_opacity.value(),
            "corner_radius": self.corner_radius.value(),
            "window_width": self.window_width.value(),
            "text_color": color.name() if color.isValid() else "#FFFFFF",
            "always_on_top": self.always_on_top.isChecked(),
            "lock_position": self.lock_position.isChecked(),
            "click_through": self.click_through.isChecked(),
            "remember_position": self.remember_position.isChecked(),
            "hide_on_pause": self.hide_on_pause.isChecked(),
        }


class FloatingLyricsPlugin:
    name = "Floating Lyrics"
    version = "0.5.2"
    description = "A detachable customizable current lyric window."

    DEFAULTS = {
        "base_font_size": 28,
        "ruby_font_size": 14,
        "text_opacity": 1.0,
        "background_opacity": 0.35,
        "corner_radius": 18,
        "window_width": 720,
        "text_color": "#FFFFFF",
        "always_on_top": True,
        "lock_position": False,
        "click_through": False,
        "remember_position": True,
        "hide_on_pause": False,
        "enabled": True,
        "window_x": None,
        "window_y": None,
    }

    def on_load(self, context):
        self.context = context
        self.window = None
        self.settings = self._load_settings()
        self.floating = FloatingLyricsWindow(self.settings)
        self.floating.visibility_changed = self._set_menu_checked

        context.api.on("app_ready", self.on_app_ready)
        context.api.on("position_changed", self.on_position_changed)
        context.api.on("track_changed", self.on_track_changed)
        context.api.on("playback_state_changed", self.on_playback_state_changed)

        context.api.add_menu_action(
            "Plugins",
            "Floating Lyrics",
            self.toggle_window,
            checkable=True,
        )
        context.api.register_settings_widget(
            "Floating Lyrics",
            self.create_settings_widget,
            apply=self.apply_settings_widget,
            description="Customize the floating lyric window's appearance and behavior.",
            order=10,
        )
        context.api.log("Loaded. Settings are available in Settings > Plugins.")

    def _load_settings(self):
        settings = dict(self.DEFAULTS)
        for key, default in self.DEFAULTS.items():
            settings[key] = self.context.api.get_setting(key, default)
        return settings

    def _save_settings(self):
        for key, value in self.settings.items():
            self.context.api.set_setting(key, value)

    def _remember_position(self):
        if not self.settings.get("remember_position", True):
            return
        self.settings["window_x"] = self.floating.x()
        self.settings["window_y"] = self.floating.y()
        self.context.api.set_setting("window_x", self.settings["window_x"])
        self.context.api.set_setting("window_y", self.settings["window_y"])

    def on_app_ready(self, window):
        self.window = window
        QTimer.singleShot(0, self._show_after_main_window)

    def _show_after_main_window(self):
        if self.window is None:
            return
        if not self.settings.get("enabled", True):
            self._set_menu_checked(False)
            return

        x = self.settings.get("window_x")
        y = self.settings.get("window_y")
        if self.settings.get("remember_position", True) and x is not None and y is not None:
            self.floating.move(int(x), int(y))
        else:
            geometry = self.window.frameGeometry()
            if geometry.width() > 0 and geometry.height() > 0:
                center = geometry.center()
                self.floating.move(center - QPoint(
                    self.floating.width() // 2,
                    self.floating.height() // 2,
                ))
            else:
                self.floating.move(100, 100)

        self.floating.show()
        self.floating.raise_()
        self.refresh()

    def _set_menu_checked(self, checked):
        self.context.api.set_menu_action_checked("Floating Lyrics", checked)

    def toggle_window(self, checked=False):
        self.settings["enabled"] = bool(checked)
        self._save_settings()
        if checked:
            self.floating.show()
            self.floating.raise_()
            self.refresh()
        else:
            self._remember_position()
            self.floating.hide()

    def create_settings_widget(self, parent):
        return FloatingLyricsSettingsWidget(self.settings, parent)

    def apply_settings_widget(self, widget):
        old_click_through = self.settings.get("click_through", False)
        self.settings.update(widget.values())
        self._save_settings()

        was_visible = self.floating.isVisible()
        old_pos = self.floating.pos()
        self.floating.apply_settings(self.settings)

        # apply_settings() recreates window flags, so restore visibility and
        # position if the floating window was already open.
        if was_visible:
            self.floating.move(old_pos)
            self.floating.show()
            self.floating.raise_()

        self.context.api.log("Floating Lyrics settings updated via Settings > Plugins.")


    def on_track_changed(self, path):
        self.refresh()

    def on_position_changed(self, position_ms):
        self.refresh()

    def on_playback_state_changed(self, *args, **kwargs):
        state = kwargs.get("state")
        if state is None and args:
            state = args[0]
        state = str(state or "").lower()

        paused = "paused" in state or state.endswith(".pause")
        if not self.settings.get("hide_on_pause", False):
            return

        if paused:
            self._remember_position()
            self.floating.hide()
        elif "playing" in state or "play" == state:
            if not self.settings.get("enabled", True):
                return
            self.floating.show()
            self.floating.raise_()
            self.refresh()

    def refresh(self):
        if self.window is None:
            return

        self.floating.set_furigana_enabled(getattr(self.window, "show_ruby", True))

        index = getattr(self.window, "current_line", -1)
        lyrics = getattr(self.window, "lyrics", []) or []
        if 0 <= index < len(lyrics):
            entry = lyrics[index]
            if isinstance(entry, (tuple, list)) and len(entry) > 1:
                self.floating.set_text(entry[1])
            elif isinstance(entry, dict):
                self.floating.set_text(entry.get("text", ""))
            else:
                self.floating.set_text(str(entry))
        else:
            self.floating.set_text("No lyrics")

    def on_unload(self, context):
        self._remember_position()
        self.floating.close()


PLUGIN_CLASS = FloatingLyricsPlugin
