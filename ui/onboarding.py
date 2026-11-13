"""First-run setup and its data-driven conversations."""

import json
import base64
import logging
import random
import re
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEasingCurve, QEvent, QEventLoop, QPoint, QPointF, QPropertyAnimation, QRect, Qt, QTimer, QVariantAnimation, Signal
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QCheckBox, QComboBox, QDialog, QFrame, QHBoxLayout, QLabel, QLayout, QMenu, QMessageBox, QPushButton, QSizePolicy, QStyle, QStyleOptionToolButton, QToolButton, QVBoxLayout, QWidget,
)

from core import settings
from core.onboarding_events import onboarding_events
from core.onboarding_script import SCRIPT_PATH, load_script
from core.onboarding_state import apply_state, decode_payload, matches_condition
from ui.onboarding_sound import OnboardingSounds, sound_expression, voice_duration_ms


DIALOGUE_PATH = Path(__file__).resolve().parent.parent / "assets" / "onboarding" / "dialogue.json"
ROUTES_PATH = DIALOGUE_PATH.with_name("dialogueroute.json")
STEPS = (
    ("firststeplanguage", "Welcome", "Welcome to LRCPlus", ""),
    ("secondstepmusiclibraryfolders", "Music library", "Your music library", ""),
    ("thirdstepappearance", "Appearance", "Appearance", ""),
    ("readingpreferences", "Reading preferences", "Reading preferences", ""),
    ("fourthsteplyrics", "Lyrics", "Lyrics", ""),
    (None, "Ready", "Ready to listen", "Setup is complete. You can change your settings and manage your music library at any time."),
)
READING_PAGE = 3
LYRICS_PAGE = 4
FINISH_PAGE = len(STEPS) - 1
DIALOGUE_STEPS = tuple(step[0] for step in STEPS[:-1])
SCRIPT_COMMAND = re.compile(r"\\(?:iftext\(\s*[a-z_]+\s*,\s*[a-z0-9_=-]*\s*\)|idle\(\s*\d+(?:\.\d+)?\s*\)|pointat\(\s*lyrics_button(?:\s*,\s*[a-z_]+)?\s*\)|jump\(\s*[a-z_][a-z_0-9]*\s*\)|watchmenu\(\s*lyrics_menu\s*,\s*[a-z_][a-z_0-9]*\s*\)|clickmenu\(\s*lyrics_menu\s*,\s*search_lyrics\s*,\s*[a-z_][a-z_0-9]*\s*\)|clickcontrol\(\s*lyrics_button\s*,\s*[a-z_][a-z_0-9]*\s*\)|waitevent\(\s*(?:[a-z_]+|idle\(\d+(?:\.\d+)?\))(?:\s*,\s*(?:[a-z_]+|idle\(\d+(?:\.\d+)?\)))*\s*\)|if\(\s*[a-z_]+\s*,\s*[^(),]+\)|toggleoutsideclick(?:\(\s*(?:true|false)\s*\))?|button\([^(),]+,[^(),]+\)|click\(\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?\s*\)|pos\(\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?(?:\s*,\s*[a-z_]+)?\s*\)|wait(?:\(\s*\d+(?:\.\d+)?\s*\))?|curhide|res(?:\(\s*[a-z_]+\s*\))?|cr\d+|c\d+|nw\d+(?:\.\d+)?|n|e|c)", re.IGNORECASE)

EASINGS = {"linear": QEasingCurve.Type.Linear, "in_out_cubic": QEasingCurve.Type.InOutCubic,
           "out_cubic": QEasingCurve.Type.OutCubic}

SCRIPT_COMMAND = re.compile(r"\\(?:state|branch|choose|choices|sound|voice|random|react)\([a-z0-9_=-]+\)|\\terminalclose|\\sceneclose|\\soundstop|\\call\([a-z_][a-z_0-9]*\)|\\return|\\flow|" + SCRIPT_COMMAND.pattern, re.IGNORECASE)


def relative_coordinate(value, length):
    return round(float(value[:-1]) * length / 100) if value.endswith("%") else round(float(value))


def parse_dialogue_line(text):
    """Extract recognized commands wherever they occur in the line."""
    matches = list(SCRIPT_COMMAND.finditer(text))
    return SCRIPT_COMMAND.sub("", text), [item.group() for item in matches]


def dialogue_tokens(text):
    tokens = []
    position = 0
    for match in SCRIPT_COMMAND.finditer(text):
        if match.start() > position:
            tokens.append(("text", text[position:match.start()]))
        tokens.append(("command", match.group()))
        position = match.end()
    if position < len(text):
        tokens.append(("text", text[position:]))
    return tokens


def needs_onboarding():
    """Do not present first-run setup to users with an existing configuration."""
    if settings.SETTINGS_PATH.exists():
        return False
    import os
    return bool(os.environ.get("LRCPLUS_SETTINGS_PATH")) or not settings.LEGACY_SETTINGS_PATH.exists()


def load_dialogue_data(key, path=DIALOGUE_PATH):
    try:
        if SCRIPT_PATH.exists():
            return load_script().get(key, [])
        data = json.loads(path.read_text(encoding="utf-8"))
        entries = data.get(key, [])
        lines = []
        for entry in entries:
            speaker = entry.get("speaker", "")
            for name, value in entry.items():
                if name == "lines" and isinstance(value, list):
                    lines.extend((speaker, line) for line in value if isinstance(line, str))
                elif re.fullmatch(r"text(?:\d+)?", name) and isinstance(value, str):
                    lines.append((speaker, value))
        return lines
    except (OSError, ValueError, TypeError, AttributeError):
        logging.exception("Unable to load onboarding dialogue for %s", key)
        return []


def load_dialogue(key):
    return load_dialogue_data(key)


def load_route(key):
    return load_dialogue_data(key, ROUTES_PATH)


class DialogueText(QLabel):
    clicked = Signal()

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.MouseButton.LeftButton:
            self.clicked.emit()
        super().mouseReleaseEvent(event)


class OnboardingDialog(QDialog):
    def __init__(self, player, parent=None, *, companion=False):
        super().__init__(parent if parent is not None else player)
        self.player = player
        self.companion_mode = companion
        self.companion_chat = None
        self.developer_window = None
        self.terminal_window = None
        self._scene_speaker = "Setup Wizard"
        self._terminal_history = ""
        self._voice_duration_ms = None
        self._voice_scripted = False
        self.pacing = settings.get_onboarding_pacing()
        self.sounds = OnboardingSounds(self, enabled=self.pacing["sound_enabled"])
        self.variables = {}
        self._call_stack = []
        self._random_bags = {}
        self._reaction_routes = {}
        self._last_theme_reaction = None
        self._read_delay_ms = None
        self._pause_active = False
        self.outside_click_enabled = False
        self.watched_menu = None
        self.menu_recovery_route = None
        self.pending_menu_action = None
        self.pending_control = None
        self.disruption_pending = False
        self.pending_events = []
        self.waiting_events = set()
        self.last_event = None
        self.event_auto_advance = False
        self.events = onboarding_events(player)
        self.events.occurred.connect(self.receive_event)
        self.start_position = None
        self.track_window_moves = False
        self.script_moving_window = False
        self.user_move_timer = QTimer(self)
        self.user_move_timer.setSingleShot(True)
        self.user_move_timer.setInterval(200)
        self.user_move_timer.timeout.connect(self.finish_user_move)
        self.page = 0
        self.dialogue_lines = []
        self.dialogue_index = -1
        self.return_to_setup = False
        self.timer = QTimer(self)
        self.timer.setSingleShot(True)
        self.timer.timeout.connect(self.advance_dialogue)
        self.typing_timer = QTimer(self)
        self.typing_timer.setInterval(settings.ONBOARDING_TEXT_INTERVALS[self.pacing["text_speed"]])
        self.typing_timer.timeout.connect(self.type_character)
        self.full_text = ""
        self.tokens = []
        self.current_text = ""
        self.current_text_index = 0
        self.visible_text = ""
        self.script_commands = []
        self.checkpoints = {}
        self.step_dialogue_lines = []
        self.script_busy = False
        self._running_commands = False
        self._command_requested = False
        self._activity_revision = 0
        self._dialogue_closed = False
        self._confirming_exit = False
        self._confirmation_deferred = []
        self.script_timer = QTimer(self)
        self.script_timer.setSingleShot(True)
        self.script_timer.timeout.connect(self.finish_script_wait)
        self.idle_timer = QTimer(self)
        self.idle_timer.setSingleShot(True)
        self.idle_timer.timeout.connect(self.idle_finished)
        self.idle_interval_ms = 0
        self.idle_cursor_position = QCursor.pos()
        self.idle_cursor_timer = QTimer(self)
        self.idle_cursor_timer.setInterval(100)
        self.idle_cursor_timer.timeout.connect(self.poll_idle_cursor)
        self.move_animation = QPropertyAnimation(self, b"pos", self)
        self.move_animation.setDuration(600)
        self.move_animation.finished.connect(self.finish_wizard_motion)
        self.motion_settle_timer = QTimer(self)
        self.motion_settle_timer.setSingleShot(True)
        self.motion_settle_timer.setInterval(100)
        self.motion_settle_timer.timeout.connect(self.clear_script_motion)
        self.fake_cursor = QLabel(self, Qt.WindowType.Tool | Qt.WindowType.FramelessWindowHint | Qt.WindowType.WindowDoesNotAcceptFocus | Qt.WindowType.WindowTransparentForInput | Qt.WindowType.WindowStaysOnTopHint)
        self.fake_cursor.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        self.cursor_normal = QPixmap(str(DIALOGUE_PATH.parent / "cursor" / "normal.cur"))
        self.cursor_click = QPixmap(str(DIALOGUE_PATH.parent / "cursor" / "click.cur"))
        self.fake_cursor.setPixmap(self.cursor_normal)
        self.fake_cursor.resize(self.cursor_normal.size())
        self.cursor_animation = QPropertyAnimation(self.fake_cursor, b"pos", self)
        self.cursor_animation.setDuration(600)
        self.cursor_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.cursor_animation.finished.connect(self.perform_script_click)
        self.cursor_hide_timer = QTimer(self)
        self.cursor_hide_timer.setSingleShot(True)
        self.cursor_hide_timer.timeout.connect(self.finish_script_click)
        self.click_position = QPoint()
        self.space = QShortcut(QKeySequence(Qt.Key.Key_Space), self)
        self.space.activated.connect(self.advance_dialogue)
        self.space.setEnabled(False)
        self.setWindowTitle("Welcome to LRCPlus")
        self.setFixedWidth(620)
        self._content_widget = QWidget(self)
        layout = QVBoxLayout(self._content_widget)
        self._content_layout = layout
        # Let the animation pass through heights below the new content minimum.
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        layout.setAlignment(Qt.AlignmentFlag.AlignTop)
        self._height_target = None
        self.size_animation = QVariantAnimation(self)
        self.size_animation.setDuration(220)
        self.size_animation.setEasingCurve(QEasingCurve.Type.InOutCubic)
        self.size_animation.valueChanged.connect(self._set_animated_height)
        self.size_animation.finished.connect(self._finish_content_resize)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.title.setSizePolicy(QSizePolicy.Policy.Preferred, QSizePolicy.Policy.Fixed)
        header = QHBoxLayout()
        header.setSpacing(10)
        header.addWidget(self.title)
        self.expression_label = QLabel()
        self.expression_label.setAccessibleName("Wizard expression")
        self.expression_label.setTextFormat(Qt.TextFormat.PlainText)
        self.expression_label.setStyleSheet(
            "font-size: 12px; color: palette(text); border: 1px solid palette(mid); "
            "border-radius: 6px; padding: 2px 7px;"
        )
        expression_policy = self.expression_label.sizePolicy()
        expression_policy.setRetainSizeWhenHidden(True)
        self.expression_label.setSizePolicy(expression_policy)
        self.expression_label.hide()
        header.addWidget(self.expression_label, 0, Qt.AlignmentFlag.AlignVCenter)
        header.addStretch(1)
        self.description = DialogueText()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        self.description.setAlignment(Qt.AlignmentFlag.AlignLeft | Qt.AlignmentFlag.AlignTop)
        self.description.clicked.connect(self.advance_dialogue)
        layout.addLayout(header)
        text_row = QHBoxLayout()
        self._dialogue_row = text_row
        text_row.addWidget(self.description, 1)
        self.dialogue_indicator = QPushButton("∨")
        self.dialogue_indicator.setFlat(True)
        self.dialogue_indicator.setAccessibleName("Advance dialogue")
        indicator_size = self.dialogue_indicator.sizePolicy()
        indicator_size.setRetainSizeWhenHidden(True)
        self.dialogue_indicator.setSizePolicy(indicator_size)
        self.dialogue_indicator.clicked.connect(self.advance_dialogue)
        self.dialogue_indicator.hide()
        text_row.addWidget(self.dialogue_indicator)
        layout.addLayout(text_row)
        self.route_buttons = QHBoxLayout()
        layout.addLayout(self.route_buttons)
        self.language_combo = QComboBox()
        self.language_combo.addItem("English")
        self.language_combo.setAccessibleName("Language")
        self.language_combo.setEnabled(False)
        layout.addWidget(self.language_combo)
        self.theme_combo = QComboBox()
        self.theme_combo.addItem("Light", "light")
        self.theme_combo.addItem("Dark", "dark")
        self.theme_combo.setAccessibleName("Color mode")
        appearance = getattr(player, "appearance_settings", {})
        self.theme_combo.setCurrentIndex(max(0, self.theme_combo.findData(appearance.get("theme_mode", "light"))))
        self.theme_combo.currentIndexChanged.connect(self.change_theme)
        layout.addWidget(self.theme_combo)
        from ui.settings_dialog import _load_theme_color_presets
        self.preset_combo = QComboBox()
        self.preset_combo.setAccessibleName("Theme preset")
        self.preset_combo.addItem("Choose a theme preset", None)
        for name, color in (("Green", "#3a9879"), ("Blue", "#4477cc"), ("Purple", "#895cc4"), ("Rose", "#c45278"), ("Orange", "#bd691f")):
            self.preset_combo.addItem(name, color)
        for name, preset in _load_theme_color_presets():
            self.preset_combo.addItem(name, preset)
        for preset in appearance.get("custom_themes", []):
            self.preset_combo.addItem(preset["name"], preset)
        selected = self.preset_combo.findText(appearance.get("theme_preset_name") or "")
        if selected >= 0:
            self.preset_combo.setCurrentIndex(selected)
        self.preset_combo.activated.connect(self.choose_preset)
        layout.addWidget(self.preset_combo)
        self.folder_button = QPushButton("Add music folder…")
        self.folder_button.clicked.connect(self.add_folder)
        layout.addWidget(self.folder_button)
        self.folders = QLabel()
        self.folders.setWordWrap(True)
        layout.addWidget(self.folders)
        self.reading_preferences = None
        self.reading_container = QWidget()
        self.reading_layout = QVBoxLayout(self.reading_container)
        self.reading_layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.reading_container)
        self.ai_features_check = QCheckBox("AI features")
        self.ai_features_check.setToolTip("Enable optional AI chat and memories. Turn off to use the scripted setup wizard.")
        self.ai_features_check.setVisible(companion)
        self.ai_features_check.setChecked(settings.load_all_settings().get("companion_ai_enabled", True) is not False)
        self.ai_features_check.installEventFilter(self)
        if companion:
            from ui.onboarding_chat import CompanionChat
            self.companion_chat = CompanionChat(self)
            layout.addWidget(self.companion_chat)
        self.dialogue_separator = QFrame()
        self.dialogue_separator.setFrameShape(QFrame.Shape.HLine)
        self.dialogue_separator.setFrameShadow(QFrame.Shadow.Plain)
        self.dialogue_separator.setFixedHeight(1)
        self.dialogue_separator.setStyleSheet("color: palette(mid);")
        layout.addWidget(self.dialogue_separator)
        pacing_row = QHBoxLayout()
        speed_label = QLabel("Text speed")
        self.text_speed_combo = QComboBox()
        self.text_speed_combo.setAccessibleName("Text speed")
        speed_label.setBuddy(self.text_speed_combo)
        for name in settings.ONBOARDING_TEXT_INTERVALS:
            self.text_speed_combo.addItem(name.title(), name)
        self.text_speed_combo.setCurrentIndex(self.text_speed_combo.findData(self.pacing["text_speed"]))
        self.text_speed_combo.currentIndexChanged.connect(self.change_text_speed)
        pacing_row.addWidget(speed_label)
        pacing_row.addWidget(self.text_speed_combo)
        pacing_row.addStretch()
        pacing_row.addWidget(self.ai_features_check)
        self.sound_check = QCheckBox("Sound")
        self.sound_check.setToolTip("Play the guide's reaction sounds without changing music playback.")
        self.sound_check.setChecked(self.pacing["sound_enabled"])
        self.sound_check.toggled.connect(self.change_sound)
        self.sound_check.installEventFilter(self)
        pacing_row.addWidget(self.sound_check)
        self.auto_read_check = QCheckBox("Auto-read")
        self.auto_read_check.setToolTip("Advance dialogue automatically after reading. Choices and application events still wait for you.")
        self.auto_read_check.setChecked(self.pacing["auto_read"])
        self.auto_read_check.toggled.connect(self.change_auto_read)
        self.auto_read_check.installEventFilter(self)
        pacing_row.addWidget(self.auto_read_check)
        self.reduced_motion_check = QCheckBox("Reduce motion")
        self.reduced_motion_check.setToolTip("Move the guide into place without animation and hide the demonstration cursor.")
        self.reduced_motion_check.setChecked(self.pacing["reduced_motion"])
        self.reduced_motion_check.toggled.connect(self.change_reduced_motion)
        self.text_speed_combo.installEventFilter(self)
        self.reduced_motion_check.installEventFilter(self)
        pacing_row.addWidget(self.reduced_motion_check)
        layout.addLayout(pacing_row)
        controls = QHBoxLayout()
        self.restart_setup = QPushButton("Run setup again")
        self.restart_setup.setAutoDefault(False)
        self.restart_setup.setVisible(companion)
        self.restart_setup.clicked.connect(self.start_setup_again)
        controls.addWidget(self.restart_setup)
        self.skip = QPushButton("Skip setup")
        self.skip.clicked.connect(self.request_exit)
        self.back = QPushButton("Back")
        self.back.clicked.connect(self.previous_page)
        self.skip_reading = QPushButton("Do this later")
        self.skip_reading.setToolTip("Continue without saving previewed reading preferences.")
        self.skip_reading.clicked.connect(self.skip_reading_preferences)
        self.skip_reading.installEventFilter(self)
        self.next = QPushButton("Next")
        self.next.clicked.connect(self.next_page)
        self.debug_button = QPushButton("Debug")
        self.debug_button.clicked.connect(self.show_debug)
        self.debug_dialog = None
        for button in (self.debug_button, self.skip, self.skip_reading, self.back, self.next):
            controls.addWidget(button)
        layout.addLayout(controls)
        self._content_ready = True
        self._content_widget.installEventFilter(self)
        self.set_page(FINISH_PAGE if companion else 0)
        if companion:
            self.setWindowTitle("Ayami · LRCPlus")
            self.title.setText("Ayami")
            self.skip.hide()
            self.back.hide()
            self.debug_button.hide()
            self.next.setText("Close")
            self.outside_click_enabled = True
            self.select_route("companion_welcome")
        else:
            self.show_dialogue()
        self.ai_features_check.toggled.connect(self.change_ai_features)
        if companion and not self.ai_features_check.isChecked():
            self.change_ai_features(False, persist=False)
        self._fit_content()

    def change_ai_features(self, enabled, *, persist=True):
        if self.companion_chat is None:
            return
        chat = self.companion_chat
        chat.ai_enabled = enabled
        if not enabled:
            chat.cancel_memory_check()
            chat.stop_request()
        chat.setVisible(enabled)
        self.companion_mode = enabled
        self.restart_setup.setVisible(enabled)
        self.skip.setVisible(not enabled)
        self.back.setVisible(not enabled)
        self.debug_button.setVisible(not enabled)
        self.outside_click_enabled = enabled
        self.set_page(FINISH_PAGE if enabled else 0)
        if enabled:
            self.setWindowTitle("Ayami · LRCPlus")
            self.title.setText("Ayami")
            self.next.setText("Close")
            if chat.exchanges:
                chat.show_page(chat.current_page)
            else:
                self.select_route("companion_welcome")
        else:
            self.show_dialogue()
        if persist:
            payload = settings.load_all_settings()
            payload["companion_ai_enabled"] = enabled
            try:
                settings.save_all_settings(payload)
            except OSError:
                QMessageBox.warning(self, "AI preference not saved",
                                    "The mode changed for this window, but could not be saved for next time.")
        self._fit_content()

    def start_setup_again(self):
        if self.companion_chat is not None and self.ai_features_check.isChecked():
            self.ai_features_check.setChecked(False)
            return
        self.companion_mode = False
        if self.companion_chat is not None:
            self.companion_chat.close_chat()
            self.companion_chat.hide()
        self.restart_setup.hide()
        self.skip.show()
        self.back.show()
        self.debug_button.show()
        self.outside_click_enabled = False
        self.set_page(0)
        self.show_dialogue()

    def _fit_content(self):
        if self._dialogue_closed or self._confirming_exit:
            return
        layout = self._content_layout
        layout.activate()
        height = layout.totalHeightForWidth(self.width())
        if height < 0:
            height = layout.sizeHint().height()
        # Lay out content at its destination size, independent of animation frames.
        self._content_widget.setGeometry(0, 0, self.width(), height)
        if not self.isVisible() or self.pacing["reduced_motion"]:
            self.size_animation.stop()
            self._height_target = height
            self.setFixedHeight(height)
        elif self._height_target != height:
            self.size_animation.stop()
            self._height_target = height
            if self.height() != height:
                self.setMinimumHeight(min(self.height(), height))
                self.setMaximumHeight(max(self.height(), height))
                self.size_animation.setStartValue(self.height())
                self.size_animation.setEndValue(height)
                self.size_animation.start()
            else:
                self.setFixedHeight(height)

    def _set_animated_height(self, height):
        # Keep native size constraints steady throughout the animation.
        self.resize(self.width(), height)

    def _finish_content_resize(self):
        self.setFixedHeight(self._height_target)

    def _reserve_dialogue_height(self, text):
        """Reserve the whole line so typing does not resize the window per word."""
        self.description.setVisible(bool(text))
        indicator_policy = self.dialogue_indicator.sizePolicy()
        indicator_policy.setRetainSizeWhenHidden(bool(text))
        self.dialogue_indicator.setSizePolicy(indicator_policy)
        margins = self._content_layout.contentsMargins()
        width = (self.width() - margins.left() - margins.right()
                 - self.dialogue_indicator.sizeHint().width() - self._dialogue_row.spacing())
        metrics = self.description.fontMetrics()
        height = metrics.boundingRect(
            QRect(0, 0, max(1, width), 0),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs, text,
        ).height()
        self.description.setMinimumHeight(max(metrics.height(), height) if text else 0)

    def showEvent(self, event):
        super().showEvent(event)
        if self.start_position is None:
            self.start_position = self.player.mapFromGlobal(self.pos())
        QTimer.singleShot(0, self, self.enable_window_move_tracking)

    def enable_window_move_tracking(self):
        if self.isVisible():
            self.track_window_moves = True

    def moveEvent(self, event):
        super().moveEvent(event)
        if (getattr(self, "track_window_moves", False) and self.isVisible()
                and event.pos() != event.oldPos()
                and not self.script_moving_window
                and self.move_animation.state() != QPropertyAnimation.State.Running
                and self.user_drag_active()):
            self.record_user_activity()
            self.user_move_timer.start()

    @staticmethod
    def user_drag_active():
        # Native title-bar drags on Windows do not always reach Qt's mouse state.
        if sys.platform == "win32":
            try:
                import ctypes
                return bool(ctypes.windll.user32.GetAsyncKeyState(0x01) & 0x8000)
            except (AttributeError, OSError):
                pass
        return bool(QApplication.mouseButtons() & Qt.MouseButton.LeftButton)

    def finish_user_move(self):
        if self.user_drag_active():
            self.user_move_timer.start()
        elif self.isVisible() and not self.script_moving_window:
            self.events.occurred.emit("wizard_moved")

    def start_wizard_motion(self):
        self.user_move_timer.stop()
        self.motion_settle_timer.stop()
        self.script_moving_window = True
        if self.pacing["reduced_motion"]:
            self.move(self.move_animation.endValue())
            self.finish_wizard_motion()
        else:
            self.move_animation.start()

    def start_cursor_motion(self):
        if self.pacing["reduced_motion"]:
            self.fake_cursor.hide()
            self._defer_activity(self.perform_script_click)
        else:
            self.fake_cursor.show()
            self.cursor_animation.start()

    def change_text_speed(self):
        speed = self.text_speed_combo.currentData()
        self.pacing["text_speed"] = speed
        self.typing_timer.setInterval(settings.ONBOARDING_TEXT_INTERVALS[speed])
        if speed == "instant" and self.typing_timer.isActive():
            self.finish_typing()

    def change_auto_read(self, enabled):
        self.pacing["auto_read"] = enabled
        self.timer.stop()
        if (enabled and self._read_delay_ms is not None and self.dialogue_lines
                and not self.script_busy and not self.typing_timer.isActive()):
            self.timer.start(self._read_delay_ms)

    def change_sound(self, enabled):
        self.pacing["sound_enabled"] = enabled
        self.sounds.set_enabled(enabled)

    def show_expression(self, sound_name=None):
        expression = sound_expression(sound_name)
        self.expression_label.setText(expression)
        self.expression_label.setVisible(bool(expression))

    def ready_for_reading(self, delay_ms=None):
        if self.pending_events:
            self._defer_activity(self.consume_event)
        if not self.dialogue_lines or self.route_buttons.count():
            return
        self._read_delay_ms = delay_ms if delay_ms is not None else min(10000, max(1200, 700 + len(self.visible_text) * 45))
        self.script_busy = False
        self.dialogue_indicator.show()
        if self.pacing["auto_read"]:
            self.timer.start(self._read_delay_ms)

    def finish_script_wait(self):
        if self._pause_active:
            self._pause_active = False
            if self.dialogue_lines:
                self.dialogue_indicator.show()
        self.run_script_command()

    def change_reduced_motion(self, enabled):
        self.pacing["reduced_motion"] = enabled
        if not enabled:
            return
        self._fit_content()
        self.fake_cursor.hide()
        if self.move_animation.state() == QPropertyAnimation.State.Running:
            self.move_animation.stop()
            self.move(self.move_animation.endValue())
            self.finish_wizard_motion()
        if self.cursor_animation.state() == QPropertyAnimation.State.Running:
            self.cursor_animation.stop()
            self._defer_activity(self.perform_script_click)

    def finish_wizard_motion(self):
        self.motion_settle_timer.start()
        self.run_script_command()

    def clear_script_motion(self):
        self.script_moving_window = False

    def run(self):
        """Run a non-modal wizard while keeping startup sequencing intact."""
        app = QApplication.instance()
        loop = QEventLoop(self)
        self.finished.connect(loop.quit)
        app.installEventFilter(self)
        self.player._active_onboarding = self
        try:
            self.setWindowModality(Qt.WindowModality.NonModal)
            self.show()
            loop.exec()
        finally:
            app.removeEventFilter(self)
            if getattr(self.player, "_active_onboarding", None) is self:
                self.player._active_onboarding = None
        return self.result()

    def eventFilter(self, watched, event):
        if not getattr(self, "_content_ready", False) or getattr(self, "_dialogue_closed", False):
            return super().eventFilter(watched, event)
        if watched is self._content_widget and event.type() == QEvent.Type.LayoutRequest:
            self._fit_content()
        if (event.type() == QEvent.Type.ShortcutOverride
                and watched in (self.text_speed_combo, self.reduced_motion_check, self.auto_read_check, self.sound_check, self.skip_reading, self.ai_features_check)
                and event.key() == Qt.Key.Key_Space):
            event.accept()
            return True
        if event.spontaneous() and event.type() in (
            QEvent.Type.MouseMove, QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
            QEvent.Type.MouseButtonDblClick, QEvent.Type.KeyPress, QEvent.Type.KeyRelease,
            QEvent.Type.Wheel, QEvent.Type.TouchBegin, QEvent.Type.TouchUpdate,
        ):
            self.record_user_activity()
        if watched is self.watched_menu and event.type() == QEvent.Type.Hide:
            self.recover_from_disruption()
        if (not self.outside_click_enabled and event.spontaneous()
                and event.type() in (QEvent.Type.MouseButtonPress, QEvent.Type.MouseButtonRelease,
                                     QEvent.Type.MouseButtonDblClick, QEvent.Type.Wheel)
                and isinstance(watched, QWidget)):
            widget = watched
            while widget is not None and widget is not self and widget is not self.player:
                if widget.isWindow() and not isinstance(widget, QMenu):
                    return super().eventFilter(watched, event)
                widget = widget.parentWidget()
            if widget is self.player:
                return True
        return super().eventFilter(watched, event)

    def record_user_activity(self):
        if self.idle_timer.isActive():
            self.idle_timer.start(self.idle_interval_ms)

    def poll_idle_cursor(self):
        position = QCursor.pos()
        if position != self.idle_cursor_position:
            self.idle_cursor_position = position
            self.record_user_activity()

    def idle_finished(self):
        self.idle_cursor_timer.stop()
        if "wizard_idle" in self.waiting_events:
            self.receive_event("wizard_idle")
            return
        self.last_event = "wizard_idle"
        self.event_auto_advance = True
        self.run_script_command()

    def set_page(self, page):
        self._reaction_routes.clear()
        self._last_theme_reaction = None
        self.close_scene_windows()
        self._call_stack.clear()
        self._cancel_dialogue_activity()
        self.dialogue_lines = []
        self.space.setEnabled(False)
        self.dialogue_indicator.hide()
        self.next.setEnabled(True)
        self.page = page
        if page == READING_PAGE:
            self._load_reading_preferences()
        self.setWindowTitle(STEPS[page][2])
        self.title.setText("Setup Wizard")
        self.description.setText(STEPS[page][3])
        self._reserve_dialogue_height(STEPS[page][3])
        self._show_setup_controls()
        self.skip_reading.setVisible(page == READING_PAGE)
        self.folders.setText("\n".join(self.player.library_folders) or "No folders added yet.")
        self.back.setEnabled(page > 0)
        self.next.setText("Finish" if page == FINISH_PAGE else "Apply and Next" if page == READING_PAGE else "Next")

    def _show_setup_controls(self, *, dialogue=False):
        page = self.page
        if self.companion_mode:
            for widget in (self.language_combo, self.theme_combo, self.preset_combo,
                           self.folder_button, self.folders, self.reading_container):
                widget.hide()
            return
        self.language_combo.setVisible(page == 0)
        self.theme_combo.setVisible(page == 2 and not dialogue)
        self.preset_combo.setVisible(page == 2 and not dialogue)
        self.folder_button.setVisible(page == 1 and not dialogue)
        self.folders.setVisible(page == 1 and not dialogue)
        self.reading_container.setVisible(page == READING_PAGE and not dialogue)

    def _load_reading_preferences(self):
        values = settings.get_global_lyrics_settings()
        if self.reading_preferences is None:
            from ui.onboarding_reading import ReadingPreferences
            self.reading_preferences = ReadingPreferences(values)
            self.reading_layout.addWidget(self.reading_preferences)
        else:
            self.reading_preferences.set_settings(values)

    def apply_reading_preferences(self):
        changes = self.reading_preferences.changes() if self.reading_preferences is not None else {}
        if not changes:
            return
        values = settings.get_global_lyrics_settings()
        values.update(changes)
        # Reading modes are mutually exclusive, including after concurrent edits.
        if any(key in changes for key in ("show_ruby", "show_romaji", "all_romaji")):
            values.update({key: self.reading_preferences.values()[key]
                           for key in ("show_ruby", "show_romaji", "all_romaji")})
        values = settings.normalize_lyrics_settings(values)
        settings.save_global_lyrics_settings(values)
        self.player.global_lyrics_settings = values
        refresh = getattr(self.player, "refresh_lyrics_settings_for_current_file", None)
        if callable(refresh):
            refresh()

    def skip_reading_preferences(self):
        if self.page == READING_PAGE:
            self._advance_setup_page()

    def change_theme(self):
        mode = self.theme_combo.currentData()
        self.player.appearance_settings["theme_mode"] = mode
        settings.save_appearance_settings(self.player.appearance_settings)
        self.player._apply_main_window_style()
        self.player._apply_appearance_settings()
        parent = self.parentWidget()
        if parent is not self.player and hasattr(parent, "_set_theme_mode"):
            parent._set_theme_mode(mode)
            parent.light_theme_button.setChecked(mode == "light")
            parent.dark_theme_button.setChecked(mode == "dark")

    def choose_preset(self, index):
        from ui.settings_dialog import apply_theme_preset
        preset = self.preset_combo.itemData(index)
        if not preset:
            return
        parent = self.parentWidget()
        if parent is not self.player and hasattr(parent, "_choose_theme_preset"):
            parent_index = parent.theme_preset_combo.findText(self.preset_combo.itemText(index))
            if parent_index >= 0:
                parent.theme_preset_combo.setCurrentIndex(parent_index)
                parent._choose_theme_preset(parent_index)
                values = parent.get_appearance_settings()
            else:
                values = apply_theme_preset(self.player.appearance_settings, preset)
        else:
            values = apply_theme_preset(self.player.appearance_settings, preset)
        for key in ("theme_mode", "theme_color", "theme_preset_name", "theme_overrides"):
            self.player.appearance_settings[key] = values[key]
        settings.save_appearance_settings(self.player.appearance_settings)
        self.player._apply_main_window_style()
        self.player._apply_appearance_settings()
        self.theme_combo.blockSignals(True)
        self.theme_combo.setCurrentIndex(self.theme_combo.findData(values["theme_mode"]))
        self.theme_combo.blockSignals(False)
        event = "theme_" + re.sub(r"[^a-z0-9]+", "_", self.preset_combo.itemText(index).casefold()).strip("_")
        if self.page == 2:
            previous = self._last_theme_reaction
            self._last_theme_reaction = event
            # Rapid browsing should leave only the newest theme reaction queued.
            if event != previous:
                self.pending_events = [name for name in self.pending_events if not name.startswith("theme_")]
                if event in self._reaction_routes:
                    self.receive_event(event)

    def add_folder(self):
        self.player.add_music_folder()
        worker = self.player._library_scan_worker
        if worker is not None and worker.isRunning():
            self.folder_button.setEnabled(False)
            worker.finished.connect(self.scan_finished)
        self.folders.setText("\n".join(self.player.library_folders) or "No folders added yet.")

    def scan_finished(self):
        self.folder_button.setEnabled(True)

    def show_debug(self):
        if self.debug_dialog is None:
            self.debug_dialog = QDialog(self)
            self.debug_dialog.setWindowTitle("Dialogue debug")
            self.debug_dialog.setModal(False)
            debug_layout = QVBoxLayout(self.debug_dialog)
            self.debug_text = QLabel()
            self.debug_text.setTextInteractionFlags(Qt.TextInteractionFlag.TextSelectableByMouse)
            debug_layout.addWidget(self.debug_text)
            self.debug_freeze = QPushButton("Freeze coordinates (F8)")
            self.debug_freeze.setCheckable(True)
            self.debug_freeze.toggled.connect(self.set_debug_frozen)
            debug_layout.addWidget(self.debug_freeze)
            skip_line = QPushButton("Skip dialogue line / wait")
            skip_line.clicked.connect(self.debug_skip_line)
            debug_layout.addWidget(skip_line)
            step_row = QHBoxLayout()
            self.debug_step = QComboBox()
            self.debug_step.addItems([step[1] for step in STEPS])
            self.debug_step.setCurrentIndex(self.page)
            step_row.addWidget(self.debug_step)
            jump_step = QPushButton("Go to step")
            jump_step.clicked.connect(self.debug_jump_step)
            step_row.addWidget(jump_step)
            debug_layout.addLayout(step_row)
            self.debug_frozen_position = None
            self.debug_shortcut = QShortcut(QKeySequence("F8"), self.debug_dialog)
            self.debug_shortcut.setContext(Qt.ShortcutContext.ApplicationShortcut)
            self.debug_shortcut.activated.connect(self.debug_freeze.toggle)
            self.debug_timer = QTimer(self.debug_dialog)
            self.debug_timer.setInterval(100)
            self.debug_timer.timeout.connect(self.update_debug)
            self.debug_dialog.finished.connect(self.debug_timer.stop)
        self.update_debug()
        self.debug_timer.start()
        self.debug_dialog.show()
        self.debug_dialog.raise_()

    def set_debug_frozen(self, frozen):
        self.debug_frozen_position = self.player.mapFromGlobal(QCursor.pos()) if frozen else None
        self.debug_freeze.setText("Resume tracking (F8)" if frozen else "Freeze coordinates (F8)")
        self.update_debug()

    def debug_cancel_activity(self):
        self._cancel_dialogue_activity()

    def _cancel_dialogue_activity(self):
        """Invalidate deferred work and stop all activity owned by the old scene."""
        self.sounds.stop()
        self.show_expression()
        self._voice_duration_ms = None
        self._voice_scripted = False
        self._activity_revision += 1
        self._confirmation_deferred.clear()
        self._read_delay_ms = None
        self._pause_active = False
        self._command_requested = False
        self.disruption_pending = False
        self.user_move_timer.stop()
        self.timer.stop()
        self.typing_timer.stop()
        self.script_timer.stop()
        self.idle_timer.stop()
        self.idle_cursor_timer.stop()
        self.move_animation.stop()
        self.motion_settle_timer.stop()
        self.script_moving_window = False
        self.cursor_animation.stop()
        self.cursor_hide_timer.stop()
        self.fake_cursor.hide()
        self.watched_menu = None
        self.menu_recovery_route = None
        self.pending_menu_action = None
        self.pending_control = None
        self.script_commands = []
        self.tokens = []
        self.current_text = ""
        self.waiting_events.clear()
        self.event_auto_advance = False
        self.script_busy = False
        self.clear_route_buttons()

    def _defer_activity(self, callback):
        revision = self._activity_revision

        def run_if_current():
            if revision == self._activity_revision and not self._dialogue_closed:
                if self._confirming_exit:
                    self._confirmation_deferred.append((revision, callback))
                else:
                    callback()

        QTimer.singleShot(0, self, run_if_current)

    def debug_skip_line(self):
        if not self.dialogue_lines:
            return
        self.debug_cancel_activity()
        self.dialogue_indicator.show()
        self.space.setEnabled(True)
        self.advance_dialogue()
        self.update_debug()

    def debug_jump_step(self):
        self._call_stack.clear()
        page = self.debug_step.currentIndex()
        self.debug_cancel_activity()
        self.dialogue_lines = []
        self.space.setEnabled(False)
        self.dialogue_indicator.hide()
        self.next.setEnabled(True)
        self.set_page(page)
        if page < FINISH_PAGE:
            self.show_dialogue()
        self.update_debug()

    def update_debug(self):
        relative = self.player.mapFromGlobal(self.pos())
        cursor = self.debug_frozen_position if self.debug_frozen_position is not None else self.player.mapFromGlobal(QCursor.pos())
        x_percent = 100 * cursor.x() / max(1, self.player.width())
        y_percent = 100 * cursor.y() / max(1, self.player.height())
        wizard_x_percent = 100 * relative.x() / max(1, self.player.width())
        wizard_y_percent = 100 * relative.y() / max(1, self.player.height())
        state = ("typing" if self.typing_timer.isActive() else
                 "running commands" if self.script_busy else
                 "waiting for input" if self.dialogue_lines else "setup controls")
        text = (
            f"Wizard position relative to main window: ({relative.x()}, {relative.y()})\n"
            f"Move to wizard: $ move({relative.x()},{relative.y()})\n"
            f"Move to wizard (%): $ move({wizard_x_percent:.1f}%,{wizard_y_percent:.1f}%)\n"
            f"Mouse position relative to main window: ({cursor.x()}, {cursor.y()})\n"
            f"Move to mouse: $ move({cursor.x()},{cursor.y()})\n"
            f"Move to mouse (%): $ move({x_percent:.1f}%,{y_percent:.1f}%)\n"
            f"Click in pixels: \\click({cursor.x()},{cursor.y()})\n"
            f"Click in percent: \\click({x_percent:.1f}%,{y_percent:.1f}%)\n"
            f"Wizard screen position: ({self.x()}, {self.y()})\n"
            f"Step: {self.page + 1}  |  Dialogue line: {self.dialogue_index + 1}\n"
            f"State: {state}\n"
            f"Outside clicks: {'enabled' if self.outside_click_enabled else 'blocked'}\n"
            f"Waiting for events: {', '.join(sorted(self.waiting_events)) or 'none'}\n"
            f"Idle wait: {'active' if self.idle_timer.isActive() else 'none'}\n"
            f"Last event: {self.last_event or 'none'}\n"
            f"Variables: {json.dumps(self.variables, ensure_ascii=False)}\n"
            f"Call depth: {len(self._call_stack)}\n"
            f"Pending commands: {', '.join(self.script_commands) or 'none'}\n"
            f"Checkpoints: {', '.join(sorted(self.checkpoints)) or 'none'}"
        )
        if self.debug_text.text() != text:
            self.debug_text.setText(text)

    def show_dialogue(self):
        self._reaction_routes.clear()
        self._call_stack.clear()
        self._cancel_dialogue_activity()
        key = STEPS[self.page][0]
        if key is None:
            return
        lines = load_dialogue_data(key)
        if lines:
            self.clear_route_buttons()
            self.checkpoints = {}
            self.pending_events.clear()
            self.waiting_events.clear()
            self.last_event = None
            self.step_dialogue_lines = lines
            self.dialogue_lines = lines
            self.dialogue_index = -1
            self.return_to_setup = False
            self.space.setEnabled(True)
            self.dialogue_indicator.show()
            self.back.setEnabled(False)
            self.next.setEnabled(False)
            self._show_setup_controls(dialogue=True)
            self.advance_dialogue()

    def advance_dialogue(self):
        if self._confirming_exit:
            return
        if not self.dialogue_lines:
            return
        if self.typing_timer.isActive():
            self.finish_typing()
            return
        if self.script_busy:
            return
        self.timer.stop()
        self._read_delay_ms = None
        self.dialogue_indicator.show()
        self.show_expression()
        self._voice_scripted = False
        self._voice_duration_ms = None
        self.dialogue_index += 1
        if self.dialogue_index >= len(self.dialogue_lines):
            if self._call_stack:
                self.return_from_call()
                return
            self.close_scene_windows()
            self.dialogue_lines = []
            self.space.setEnabled(False)
            self.dialogue_indicator.hide()
            self.next.setEnabled(True)
            if self.return_to_setup:
                self.set_page(self.page)
            else:
                self.back.setEnabled(self.page > 0)
                self._show_setup_controls()
            return
        speaker, text = self.dialogue_lines[self.dialogue_index]
        if self._scene_speaker == "Terminal":
            self._terminal_history += self.visible_text.removeprefix("$ ") + "\n>>> "
        self._scene_speaker = speaker
        if speaker in ("Developer", "Terminal"):
            self.show_scene_window(speaker)
        else:
            self.title.setText("Ayami" if self.companion_mode and speaker == "Setup Wizard" else speaker)
        self.full_text, _ = parse_dialogue_line(text)
        if speaker == "Developer":
            self.fit_developer_window(self.full_text)
        elif speaker != "Terminal":
            self._reserve_dialogue_height(self.full_text)
        self.tokens = dialogue_tokens(text)
        self.script_commands = []
        self.visible_text = ""
        self.set_scene_text("")
        self.resume_line()

    def show_scene_window(self, speaker):
        if speaker == "Developer" and self.developer_window is None:
            window = QDialog(self, Qt.WindowType.Window)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            window.setWindowTitle("Developer")
            layout = QVBoxLayout(window)
            layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
            label = DialogueText()
            label.setWordWrap(True)
            label.clicked.connect(self.advance_dialogue)
            layout.addWidget(label)
            window.scene_text = label
            window.resize(380, 70)
            window.scene_resize = QPropertyAnimation(window, b"size", window)
            window.scene_resize.setDuration(220)
            window.scene_resize.setEasingCurve(QEasingCurve.Type.InOutCubic)
            self.developer_window = window
        if speaker == "Terminal" and self.terminal_window is None:
            window = QDialog(self, Qt.WindowType.Window)
            window.setAttribute(Qt.WidgetAttribute.WA_ShowWithoutActivating)
            window.setWindowTitle("Command Prompt")
            window.resize(680, 320)
            window.setStyleSheet("QDialog { background: #0c0c0c; } QLabel { color: #cccccc; font: 13px Consolas; background: #0c0c0c; }")
            layout = QVBoxLayout(window)
            label = DialogueText()
            label.setAlignment(Qt.AlignmentFlag.AlignTop | Qt.AlignmentFlag.AlignLeft)
            label.setTextFormat(Qt.TextFormat.PlainText)
            label.clicked.connect(self.advance_dialogue)
            layout.addWidget(label)
            window.scene_text = label
            self._terminal_history = "Microsoft Windows [Version 10.0.26200.9457]\n(c) Microsoft Corporation. All rights reserved.\n\nC:\\Windows\\System32> python\n>>> "
            self.terminal_window = window
        window = self.developer_window if speaker == "Developer" else self.terminal_window
        window.show()

    def fit_developer_window(self, text):
        window = self.developer_window
        label = window.scene_text
        margins = window.layout().contentsMargins()
        width = window.width()
        text_width = max(1, width - margins.left() - margins.right())
        # Measure the complete line before typing starts, avoiding per-word resizing.
        metrics = label.fontMetrics()
        bounds = metrics.boundingRect(0, 0, text_width, 10000,
                                      Qt.TextFlag.TextWordWrap, text or " ")
        height = max(50, bounds.height() + margins.top() + margins.bottom())
        animation = window.scene_resize
        animation.stop()
        target = window.size()
        target.setHeight(height)
        if self.pacing["reduced_motion"] or not window.isVisible():
            window.resize(target)
        elif target != window.size():
            animation.setStartValue(window.size())
            animation.setEndValue(target)
            animation.start()

    def set_scene_text(self, text):
        if self._scene_speaker == "Developer" and self.developer_window:
            self.developer_window.scene_text.setText(text)
        elif self._scene_speaker == "Terminal" and self.terminal_window:
            self.terminal_window.scene_text.setText(self._terminal_history + text.removeprefix("$ "))
        else:
            self.description.setText(text)

    def close_scene_windows(self):
        for name in ("developer_window", "terminal_window"):
            window = getattr(self, name, None)
            if window is not None:
                window.close()
                window.deleteLater()
                setattr(self, name, None)
        self._scene_speaker = "Setup Wizard"

    def resume_line(self):
        if self.pending_events:
            self._defer_activity(self.consume_event)
        self.script_busy = False
        if not self.tokens:
            if self.event_auto_advance:
                self.event_auto_advance = False
                self.advance_dialogue()
            else:
                self.ready_for_reading()
            return
        self.event_auto_advance = False
        kind, value = self.tokens.pop(0)
        if kind == "text":
            self.current_text = value
            self.current_text_index = 0
            if self._voice_scripted:
                self.visible_text += value
                self.current_text = ""
                self.set_scene_text(self.visible_text)
                self.run_script_command()
            elif self._voice_duration_ms and value:
                self.typing_timer.setInterval(max(1, round(self._voice_duration_ms / len(value))))
                self._voice_duration_ms = None
                self.typing_timer.start()
            elif self.pacing["text_speed"] == "instant":
                self.visible_text += value
                self.current_text = ""
                self.set_scene_text(self.visible_text)
                self.run_script_command()
            else:
                self.typing_timer.start()
            return
        self.script_commands = [value]
        while self.tokens and self.tokens[0][0] == "command":
            self.script_commands.append(self.tokens.pop(0)[1])
        self.script_busy = True
        self.run_script_command()

    def type_character(self):
        self.current_text_index += 1
        self.set_scene_text(self.visible_text + self.current_text[:self.current_text_index])
        if self.current_text_index >= len(self.current_text):
            self.finish_typing()

    def finish_typing(self):
        self.typing_timer.stop()
        self.typing_timer.setInterval(settings.ONBOARDING_TEXT_INTERVALS[self.pacing["text_speed"]])
        self.visible_text += self.current_text
        self.current_text = ""
        self.set_scene_text(self.visible_text)
        self.resume_line()

    def run_script_command(self):
        """Drain immediate commands without recursion, yielding to Qt in batches."""
        if self._dialogue_closed or self._confirming_exit:
            return
        self._command_requested = True
        if self._running_commands:
            return
        self._running_commands = True
        try:
            for _ in range(256):
                if not self._command_requested:
                    break
                self._command_requested = False
                self._execute_script_command()
            if self._command_requested:
                self.script_timer.start(0)
        finally:
            self._running_commands = False

    def _execute_script_command(self):
        if not self.script_commands:
            self.resume_line()
            return
        original_command = self.script_commands.pop(0)
        command = original_command.lower()
        if command.startswith(r"\react("):
            event, route = decode_payload(original_command[7:-1])
            if route == "none":
                self._reaction_routes.pop(event, None)
            else:
                self._reaction_routes[event] = route
            self.run_script_command()
            return
        if command.startswith(r"\random("):
            payload = decode_payload(original_command[8:-1])
            variants = payload["variants"]
            key = (payload["id"], original_command)
            bag = self._random_bags.setdefault(key, {"remaining": [], "last": None})
            remaining = bag["remaining"]
            if not remaining:
                remaining.extend(variants)
            candidates = remaining
            if len(remaining) == len(variants) and len(variants) > 1:
                candidates = [variant for variant in remaining if variant != bag["last"]] or remaining
            selected = random.choice(candidates)
            remaining.remove(selected)
            bag["last"] = selected
            self.splice_dialogue(selected)
            return
        if command.startswith(r"\sound("):
            name = decode_payload(original_command[7:-1])
            self.show_expression(name)
            self.sounds.play(name)
            self.run_script_command()
            return
        if command.startswith(r"\voice("):
            name = decode_payload(original_command[7:-1])
            if self.sounds.play(name):
                self._voice_scripted = any(kind == "command" and token.lower().startswith(r"\wait(")
                                           for kind, token in self.tokens)
                if not self._voice_scripted:
                    self._voice_duration_ms = voice_duration_ms(name, self.sounds.directory)
            self.run_script_command()
            return
        if command == r"\terminalclose":
            if self.terminal_window is not None:
                self.terminal_window.close()
                self.terminal_window.deleteLater()
                self.terminal_window = None
            self.run_script_command()
            return
        if command == r"\sceneclose":
            self.close_scene_windows()
            self.run_script_command()
            return
        if command == r"\soundstop":
            self.sounds.stop()
            self.show_expression()
            self.run_script_command()
            return
        if command.startswith(r"\call("):
            self.call_route(original_command[6:-1])
            return
        if command == r"\return":
            self.return_from_call()
            return
        if command.startswith(r"\choose("):
            arms = decode_payload(original_command[8:-1])
            for arm in arms:
                if arm["condition"] is None or matches_condition(self.variables, arm["condition"], self.last_event):
                    self.splice_dialogue(arm["lines"])
                    return
            self.run_script_command()
            return
        if command.startswith(r"\choices("):
            revision = self._activity_revision
            enabled_count = 0
            for option in decode_payload(original_command[9:-1]):
                available = (option.get("condition") is None or
                             matches_condition(self.variables, option["condition"], self.last_event))
                if option.get("mode") == "if" and not available:
                    continue
                button = QPushButton(option["text"])
                button.setEnabled(available)
                enabled_count += available
                button.clicked.connect(
                    lambda checked=False, lines=option["lines"], version=revision:
                    self.splice_dialogue(lines) if version == self._activity_revision else None)
                self.route_buttons.addWidget(button)
            if not enabled_count:
                self.clear_route_buttons()
                self.run_script_command()
                return
            self.script_busy = True
            self.dialogue_indicator.hide()
            return
        if command.startswith(r"\state("):
            try:
                apply_state(self.variables, decode_payload(original_command[7:-1]))
            except ValueError:
                logging.exception("Unable to apply onboarding variable command")
            self.run_script_command()
            return
        if command.startswith(r"\branch("):
            branch = decode_payload(original_command[8:-1])
            if matches_condition(self.variables, branch["condition"], self.last_event):
                if "jump" in branch:
                    if load_route(branch["jump"]):
                        self.select_route(branch["jump"])
                        return
                elif "text" in branch:
                    self.show_conditional_text(branch["text"])
                    return
                else:
                    self.script_commands.insert(0, branch["command"])
            self.run_script_command()
            return
        if command.startswith(r"\iftext("):
            event_name, encoded = (part.strip() for part in original_command[8:-1].split(",", 1))
            if event_name.lower() == self.last_event:
                text = base64.urlsafe_b64decode(encoded).decode("utf-8")
                self.show_conditional_text(text)
            else:
                self.run_script_command()
            return
        if command.startswith(r"\idle("):
            seconds = float(command[6:-1])
            self.idle_interval_ms = round(min(seconds, 3600) * 1000)
            self.idle_cursor_position = QCursor.pos()
            self.idle_timer.start(self.idle_interval_ms)
            self.idle_cursor_timer.start()
            return
        if command.startswith(r"\pointat("):
            parts = [part.strip() for part in original_command[9:-1].split(",")]
            control = getattr(self.player, parts[0], None)
            if not isinstance(control, QToolButton) or not control.isVisible():
                self.run_script_command()
                return
            rect = self.tool_button_menu_rect(control)
            target = control.mapToGlobal(rect.center())
            caret_index = self.full_text.find("^")
            prefix = self.full_text[:caret_index] if caret_index >= 0 else ""
            metrics = self.description.fontMetrics()
            offset = metrics.horizontalAdvance(prefix) + metrics.horizontalAdvance("^") // 2
            text_origin = self.description.mapToGlobal(self.description.contentsRect().topLeft())
            x = self.pos().x() + target.x() - text_origin.x() - offset
            y = control.mapToGlobal(rect.bottomLeft()).y() + 8
            self.move_animation.setEasingCurve(EASINGS.get(parts[1].lower() if len(parts) > 1 else "out_cubic", QEasingCurve.Type.OutCubic))
            self.move_animation.setStartValue(self.pos())
            self.move_animation.setEndValue(QPoint(x, y))
            self.start_wizard_motion()
            return
        if command.startswith(r"\jump("):
            route = original_command[6:-1].strip()
            if load_route(route):
                self.select_route(route)
            else:
                self.run_script_command()
            return
        if command.startswith(r"\watchmenu("):
            menu_name, route = (part.strip() for part in original_command[11:-1].split(",", 1))
            self.watched_menu = getattr(self.player, menu_name, None)
            self.menu_recovery_route = route
            if not isinstance(self.watched_menu, QMenu) or not self.watched_menu.isVisible():
                self.recover_from_disruption()
            else:
                self.run_script_command()
            return
        if command.startswith(r"\clickcontrol("):
            name, route = (part.strip() for part in original_command[14:-1].split(",", 1))
            control = getattr(self.player, name, None)
            self.menu_recovery_route = route
            if not isinstance(control, QToolButton) or not control.isVisible() or not control.isEnabled():
                self.recover_from_disruption()
                return
            point = self.tool_button_menu_rect(control).center()
            target = control.mapToGlobal(point)
            self.click_position = self.player.mapFromGlobal(target)
            self.pending_control = control
            self.fake_cursor.setPixmap(self.cursor_normal)
            if not self.fake_cursor.isVisible():
                self.fake_cursor.move(self.mapToGlobal(self.rect().center()))
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.start_cursor_motion()
            return
        if command.startswith(r"\clickmenu("):
            menu_name, action_name, route = (part.strip() for part in original_command[11:-1].split(","))
            menu = getattr(self.player, menu_name, None)
            self.watched_menu = menu
            self.menu_recovery_route = route
            action = next((item for item in menu.actions() if item.objectName() == action_name), None) if isinstance(menu, QMenu) else None
            if not menu or not menu.isVisible() or not action or not action.isEnabled() or not action.isVisible():
                self.recover_from_disruption()
                return
            self.pending_menu_action = action
            target = menu.mapToGlobal(menu.actionGeometry(action).center())
            self.click_position = self.player.mapFromGlobal(target)
            self.fake_cursor.setPixmap(self.cursor_normal)
            if not self.fake_cursor.isVisible():
                self.fake_cursor.move(self.mapToGlobal(self.rect().center()))
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.start_cursor_motion()
            return
        if command.startswith(r"\waitevent("):
            names = [name.strip().lower() for name in original_command[11:-1].split(",")]
            idle = next((name for name in names if name.startswith("idle(")), None)
            self.waiting_events = {name for name in names if not name.startswith("idle(")}
            if idle:
                self.waiting_events.add("wizard_idle")
                self.idle_interval_ms = round(min(float(idle[5:-1]), 3600) * 1000)
                self.idle_cursor_position = QCursor.pos()
                self.idle_timer.start(self.idle_interval_ms)
                self.idle_cursor_timer.start()
            self.consume_event()
            return
        if command.startswith(r"\if("):
            event_name, route = (part.strip() for part in original_command[4:-1].split(",", 1))
            if event_name.lower() == self.last_event and load_route(route):
                self.event_auto_advance = False
                self.select_route(route)
            else:
                self.run_script_command()
            return
        if command.startswith(r"\toggleoutsideclick"):
            value = re.search(r"\(\s*(true|false)\s*\)", command)
            self.outside_click_enabled = value.group(1) == "true" if value else not self.outside_click_enabled
            self.run_script_command()
            return
        if command.startswith(r"\button("):
            label, route = (part.strip() for part in original_command[8:-1].split(",", 1))
            button = QPushButton(label)
            button.clicked.connect(lambda checked=False, target=route: self.select_route(target))
            self.route_buttons.addWidget(button)
            if self.script_commands and self.script_commands[0].lower().startswith(r"\button("):
                self.run_script_command()
            else:
                self.script_busy = True
                self.dialogue_indicator.hide()
            return
        if re.fullmatch(r"\\cr\d+", command):
            checkpoint = self.checkpoints.get(command[3:])
            if checkpoint is None:
                marker = r"\c" + command[3:]
                for index, (_, text) in enumerate(self.step_dialogue_lines):
                    _, commands = parse_dialogue_line(text)
                    if any(item.lower() == marker for item in commands):
                        checkpoint = (self.step_dialogue_lines, index - 1)
                        break
                if checkpoint is None and SCRIPT_PATH.exists():
                    try:
                        labels = load_script()
                        for label, lines in labels.items():
                            for index, (_, text) in enumerate(lines):
                                if any(item.lower() == marker for item in parse_dialogue_line(text)[1]):
                                    steps = DIALOGUE_STEPS
                                    if label in steps and self.page != steps.index(label):
                                        self.set_page(steps.index(label))
                                    if label in steps:
                                        self.step_dialogue_lines = lines
                                    checkpoint = (lines, index - 1)
                                    break
                            if checkpoint is not None:
                                break
                    except (OSError, ValueError):
                        logging.exception("Unable to resolve checkpoint %s", command[3:])
            if checkpoint:
                self._call_stack.clear()
                self.dialogue_lines, self.dialogue_index = checkpoint[0], checkpoint[1]
                self.script_busy = False
                self.advance_dialogue()
            else:
                self.run_script_command()
            return
        if re.fullmatch(r"\\c\d+", command):
            self.checkpoints[command[2:]] = (self.dialogue_lines, self.dialogue_index - 1)
            self.run_script_command()
            return
        if command.startswith(r"\wait"):
            match = re.search(r"\(([^)]+)\)", command)
            seconds = float(match.group(1)) if match else 1.0
            self._pause_active = True
            self.dialogue_indicator.hide()
            self.script_timer.start(round(min(seconds, 3600) * 1000))
            return
        if command.startswith(r"\click("):
            parts = [part.strip() for part in command[7:-1].split(",")]
            self.click_position = QPoint(relative_coordinate(parts[0], self.player.width()), relative_coordinate(parts[1], self.player.height()))
            global_pos = self.player.mapToGlobal(self.click_position)
            if not self.player.rect().contains(self.click_position) and not self.menu_at(global_pos):
                self.run_script_command()
                return
            target = self.player.mapToGlobal(self.click_position)
            self.fake_cursor.setPixmap(self.cursor_normal)
            if not self.fake_cursor.isVisible():
                self.fake_cursor.move(self.mapToGlobal(self.rect().center()))
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.start_cursor_motion()
            return
        if command == r"\curhide":
            self.fake_cursor.hide()
            self.run_script_command()
            return
        if command.startswith(r"\pos("):
            parts = [part.strip() for part in command[5:-1].split(",")]
            easing = EASINGS.get(parts[2] if len(parts) > 2 else "out_cubic", QEasingCurve.Type.OutCubic)
            self.move_animation.setEasingCurve(easing)
            self.move_animation.setStartValue(self.pos())
            point = QPoint(relative_coordinate(parts[0], self.player.width()), relative_coordinate(parts[1], self.player.height()))
            self.move_animation.setEndValue(self.player.mapToGlobal(point))
            self.start_wizard_motion()
            return
        if command.startswith(r"\res"):
            if self.start_position is None:
                self.run_script_command()
                return
            easing_name = command[5:-1].strip() if command.startswith(r"\res(") else "out_cubic"
            self.move_animation.setEasingCurve(EASINGS.get(easing_name, QEasingCurve.Type.OutCubic))
            self.move_animation.setStartValue(self.pos())
            self.move_animation.setEndValue(self.visible_position(self.player.mapToGlobal(self.start_position)))
            self.start_wizard_motion()
            return
        if command.startswith(r"\nw"):
            self.ready_for_reading(round(min(float(command[3:]), 3600) * 1000))
            return
        if command == r"\flow":
            self.script_busy = False
            self.advance_dialogue()
            return
        if command == r"\n":
            self.tokens = []
            self.script_commands = []
            self.ready_for_reading()
            return
        if command == r"\e":
            if any("reaction" in frame for frame in self._call_stack):
                while "reaction" not in self._call_stack[-1]:
                    self._call_stack.pop()
                self.return_from_call()
                return
            self._call_stack.clear()
            self._read_delay_ms = None
            self.timer.stop()
            self.script_busy = False
            self.dialogue_lines = []
            self.space.setEnabled(False)
            self.dialogue_indicator.hide()
            self.next.setEnabled(True)
            self.back.setEnabled(self.page > 0)
            self._show_setup_controls()
        elif command == r"\c":
            self.return_to_setup = True
            self.dialogue_index = len(self.dialogue_lines) - 1
            self.script_busy = False
        else:
            self.run_script_command()

    def show_conditional_text(self, text):
        self.splice_dialogue([("Setup Wizard", text)])

    def splice_dialogue(self, lines):
        continuation = "".join(self.script_commands) + "".join(value for _, value in self.tokens)
        insert = list(lines)
        if continuation:
            has_text = any(kind == "text" and value for kind, value in self.tokens)
            insert.append(("Setup Wizard", continuation + ("" if has_text else r"\flow")))
        self.dialogue_lines = (self.dialogue_lines[:self.dialogue_index + 1] + insert
                               + self.dialogue_lines[self.dialogue_index + 1:])
        self._cancel_dialogue_activity()
        self.advance_dialogue()

    def call_route(self, route):
        lines = load_route(route)
        if not lines or len(self._call_stack) >= 64:
            logging.warning("Unable to call onboarding label %s (missing/empty label or call depth limit)", route)
            self.run_script_command()
            return
        remaining = [("command", command) for command in self.script_commands] + list(self.tokens)
        if not any(kind == "text" and value for kind, value in remaining):
            remaining.append(("command", r"\flow"))
        self._call_stack.append({
            "lines": self.dialogue_lines, "index": self.dialogue_index,
            "tokens": remaining, "speaker": self.title.text(),
            "full_text": self.full_text, "visible_text": self.visible_text,
            "event_auto_advance": self.event_auto_advance,
        })
        self._cancel_dialogue_activity()
        self.dialogue_lines = lines
        self.dialogue_index = -1
        self.dialogue_indicator.show()
        self.advance_dialogue()

    def return_from_call(self):
        if not self._call_stack:
            # A top-level return leaves this conversation, as an explicit end does.
            self.script_commands = [r"\e"]
            self.run_script_command()
            return
        frame = self._call_stack.pop()
        self._cancel_dialogue_activity()
        if "reaction" in frame:
            self._restore_reaction(frame)
            return
        self.dialogue_lines = frame["lines"]
        self.dialogue_index = frame["index"]
        self.title.setText(frame["speaker"])
        self.full_text = frame["full_text"]
        self.visible_text = frame["visible_text"]
        self.description.setText(self.visible_text)
        self._reserve_dialogue_height(self.full_text)
        self.tokens = frame["tokens"]
        self.event_auto_advance = frame["event_auto_advance"]
        self.dialogue_indicator.show()
        self.run_script_command()

    def clear_route_buttons(self):
        while self.route_buttons.count():
            widget = self.route_buttons.takeAt(0).widget()
            if widget is not None:
                widget.hide()
                widget.deleteLater()

    def receive_event(self, name):
        name = name.lower()
        if name == "wizard_moved" or name in self._reaction_routes:
            self.pending_events = [event for event in self.pending_events if event != name]
        if name == "lyrics_search_started":
            self.pending_events = [event for event in self.pending_events if event not in {
                "lyrics_search_started", "lyrics_search_results", "lyrics_search_failed", "lyrics_search_empty", "lyrics_search_closed"
            }]
        self.pending_events.append(name)
        self.pending_events = self.pending_events[-32:]
        self.consume_event()

    def consume_event(self):
        if self._confirming_exit or self._dialogue_closed:
            return
        for index, event in enumerate(self.pending_events):
            if event in self.waiting_events:
                self.pending_events.pop(index)
                self.waiting_events.clear()
                self.idle_timer.stop()
                self.idle_cursor_timer.stop()
                self.last_event = event
                self.event_auto_advance = True
                self.run_script_command()
                return
        self._try_reaction()

    def _try_reaction(self):
        # Menus, scene windows, and scripted motion own interactions that cannot
        # safely be suspended. Leave their events queued until a dialogue wait.
        if (self.route_buttons.count()
                or self._scene_speaker != "Setup Wizard"
                or any("reaction" in frame for frame in self._call_stack)
                or (self.script_busy and not self._pause_active and not self.waiting_events)
                or self.move_animation.state() != QPropertyAnimation.State.Stopped
                or self.cursor_animation.state() != QPropertyAnimation.State.Stopped
                or self.cursor_hide_timer.isActive()):
            return
        for index, event in enumerate(self.pending_events):
            route = self._reaction_routes.get(event)
            if not route:
                continue
            self.pending_events.pop(index)
            lines = load_route(route)
            if not lines or len(self._call_stack) >= 64:
                logging.warning("Unable to react with onboarding label %s", route)
                return
            names = ("dialogue_lines", "dialogue_index", "tokens", "script_commands",
                     "current_text", "current_text_index", "visible_text", "full_text",
                     "waiting_events", "last_event", "event_auto_advance", "script_busy",
                     "_pause_active", "_read_delay_ms", "_voice_duration_ms", "_voice_scripted",
                     "idle_interval_ms", "idle_cursor_position", "return_to_setup",
                     "outside_click_enabled")
            state = {name: getattr(self, name) for name in names}
            state["waiting_events"] = set(self.waiting_events)
            timers = [(timer, max(0, timer.remainingTime()), timer.interval())
                      for timer in (self.timer, self.typing_timer, self.script_timer,
                                    self.idle_timer, self.idle_cursor_timer) if timer.isActive()]
            frame = {"reaction": state, "timers": timers, "speaker": self.title.text(),
                     "expression": self.expression_label.text(),
                     "text_speed": self.pacing["text_speed"],
                     "space_enabled": self.space.isEnabled(),
                     "indicator": not self.dialogue_indicator.isHidden()}
            self._call_stack.append(frame)
            self._cancel_dialogue_activity()
            self.last_event = event
            self.dialogue_lines = lines
            self.dialogue_index = -1
            self.space.setEnabled(True)
            self.dialogue_indicator.show()
            self.advance_dialogue()
            return

    def _restore_reaction(self, frame):
        self.close_scene_windows()
        for name, value in frame["reaction"].items():
            setattr(self, name, value)
        self._scene_speaker = "Setup Wizard"
        self.title.setText(frame["speaker"])
        self.expression_label.setText(frame["expression"])
        self.expression_label.setVisible(bool(frame["expression"]))
        self.set_scene_text(self.visible_text + self.current_text[:self.current_text_index])
        self._reserve_dialogue_height(self.full_text)
        self.dialogue_indicator.setVisible(frame["indicator"])
        self.space.setEnabled(frame["space_enabled"])
        for timer, remaining, interval in frame["timers"]:
            if timer is self.timer and not self.pacing["auto_read"]:
                continue
            if timer is self.typing_timer and frame["text_speed"] != self.pacing["text_speed"]:
                if self.pacing["text_speed"] == "instant":
                    self._defer_activity(self.finish_typing)
                    continue
                interval = settings.ONBOARDING_TEXT_INTERVALS[self.pacing["text_speed"]]
            timer.start(remaining if timer.isSingleShot() else interval)
        if (self.pacing["auto_read"] and self._read_delay_ms is not None
                and not self.script_busy and not self.typing_timer.isActive()
                and not self.timer.isActive()):
            self.timer.start(self._read_delay_ms)
        self._defer_activity(self.consume_event)

    def perform_script_click(self):
        self.fake_cursor.setPixmap(self.cursor_click)
        global_pos = self.player.mapToGlobal(self.click_position)
        if self.pending_control is not None:
            control = self.pending_control
            self.pending_control = None
            if not control.isVisible() or not control.isEnabled() or not control.rect().contains(control.mapFromGlobal(global_pos)):
                self.recover_from_disruption()
                return
            self.menu_recovery_route = None
            self._defer_activity(control.showMenu)
            self.cursor_hide_timer.start(180)
            return
        if self.pending_menu_action is not None:
            menu, action = self.watched_menu, self.pending_menu_action
            if not menu or not menu.isVisible() or not action.isEnabled() or not action.isVisible():
                self.recover_from_disruption()
                return
            self.pending_menu_action = None
            self.watched_menu = None
            self.menu_recovery_route = None
            menu.close()
            self._defer_activity(action.trigger)
            self.cursor_hide_timer.start(180)
            return
        popup = self.menu_at(global_pos)
        if popup:
            action = popup.actionAt(popup.mapFromGlobal(global_pos))
            if action and action.isEnabled() and action.isVisible() and not action.isSeparator():
                submenu = action.menu()
                if submenu:
                    submenu.popup(popup.mapToGlobal(popup.actionGeometry(action).topRight()))
                else:
                    popup.close()
                    self._defer_activity(action.trigger)
            self.cursor_hide_timer.start(180)
            return
        else:
            target = self.player.childAt(self.click_position) or self.player
        if target.isVisible() and target.isEnabled():
            local_pos = target.mapFromGlobal(global_pos)
            for event_type, buttons in ((QEvent.Type.MouseButtonPress, Qt.MouseButton.LeftButton),
                                        (QEvent.Type.MouseButtonRelease, Qt.MouseButton.NoButton)):
                event = QMouseEvent(event_type, QPointF(local_pos), QPointF(global_pos),
                                    Qt.MouseButton.LeftButton, buttons, Qt.KeyboardModifier.NoModifier)
                QCoreApplication.postEvent(target, event)
        self.cursor_hide_timer.start(180)

    def menu_at(self, global_pos):
        popup = QApplication.activePopupWidget()
        parent = popup
        while parent is not None and parent is not self.player:
            parent = parent.parentWidget()
        if (isinstance(popup, QMenu) and popup.isVisible()
                and popup.rect().contains(popup.mapFromGlobal(global_pos))
                and parent is self.player):
            return popup
        return None

    def visible_position(self, position):
        """Keep the wizard's outer frame within its current screen when possible."""
        screen = QApplication.screenAt(position) or self.player.screen() or QApplication.primaryScreen()
        if screen is None:
            return position
        bounds = screen.availableGeometry()
        size = self.frameGeometry().size()
        return QPoint(
            max(bounds.left(), min(position.x(), max(bounds.left(), bounds.right() - size.width() + 1))),
            max(bounds.top(), min(position.y(), max(bounds.top(), bounds.bottom() - size.height() + 1))),
        )

    @staticmethod
    def tool_button_menu_rect(control):
        option = QStyleOptionToolButton()
        option.initFrom(control)
        option.subControls = QStyle.SubControl.SC_All
        option.features = QStyleOptionToolButton.ToolButtonFeature.MenuButtonPopup
        rect = control.style().subControlRect(QStyle.ComplexControl.CC_ToolButton, option, QStyle.SubControl.SC_ToolButtonMenu, control)
        return rect if rect.isValid() and not rect.isEmpty() else control.rect()

    def recover_from_disruption(self):
        if self.disruption_pending or not self.menu_recovery_route:
            return
        route = self.menu_recovery_route
        self._cancel_dialogue_activity()
        self.disruption_pending = True

        def resume():
            self.disruption_pending = False
            if self.isVisible():
                self.select_route(route)

        self._defer_activity(resume)

    def finish_script_click(self):
        self.fake_cursor.setPixmap(self.cursor_normal)
        self.fake_cursor.raise_()
        self.run_script_command()

    def select_route(self, route):
        lines = load_route(route)
        if not lines:
            return
        self._cancel_dialogue_activity()
        self.space.setEnabled(True)
        self.dialogue_indicator.show()
        self.next.setEnabled(False)
        self.back.setEnabled(False)
        self.dialogue_lines = lines
        self.dialogue_index = -1
        self.script_commands = []
        self.script_busy = False
        self.advance_dialogue()

    def next_page(self):
        if self.page == FINISH_PAGE:
            self.accept()
        else:
            if self.page == READING_PAGE:
                self.apply_reading_preferences()
            self._advance_setup_page()

    def _advance_setup_page(self):
        self.set_page(self.page + 1)
        if self.page < FINISH_PAGE:
            self.show_dialogue()

    def previous_page(self):
        self.set_page(self.page - 1)
        self.show_dialogue()

    def _ask_exit_confirmation(self):
        box = QMessageBox(self)
        box.setWindowTitle("Leave setup?")
        box.setIcon(QMessageBox.Icon.Question)
        box.setText("Leave setup for now?")
        message = "Preferences already saved will be kept. You can reopen setup later."
        if self.page == READING_PAGE and self.reading_preferences is not None and self.reading_preferences.changes():
            message += " Your reading preview changes have not been saved."
        box.setInformativeText(message)
        leave = box.addButton("Leave setup", QMessageBox.ButtonRole.AcceptRole)
        keep = box.addButton("Keep setting up", QMessageBox.ButtonRole.RejectRole)
        box.setDefaultButton(keep)
        box.setEscapeButton(keep)
        box.setWindowModality(Qt.WindowModality.ApplicationModal)
        try:
            box.exec()
            return box.clickedButton() is leave
        finally:
            box.deleteLater()

    def request_exit(self):
        if self._dialogue_closed:
            return True
        if self.companion_mode:
            self.done(QDialog.DialogCode.Rejected)
            return True
        if self._confirming_exit:
            return False
        self._confirming_exit = True
        revision = self._activity_revision
        timers = []
        for timer in (self.timer, self.typing_timer, self.script_timer, self.idle_timer,
                      self.idle_cursor_timer, self.user_move_timer, self.motion_settle_timer,
                      self.cursor_hide_timer):
            if timer.isActive():
                timers.append((timer, max(0, timer.remainingTime()) if timer.isSingleShot() else timer.interval()))
                timer.stop()
        animations = []
        for animation in (self.move_animation, self.cursor_animation, self.size_animation):
            if animation.state() == QPropertyAnimation.State.Running:
                animation.pause()
                animations.append(animation)
        cursor_visible = self.fake_cursor.isVisible()
        self.fake_cursor.hide()
        self.sounds.stop()
        try:
            leave = self._ask_exit_confirmation()
        finally:
            self._confirming_exit = False
        if leave:
            self.done(QDialog.DialogCode.Rejected)
            return True
        if not self._dialogue_closed and revision == self._activity_revision:
            for timer, interval in timers:
                timer.start(self.idle_interval_ms if timer is self.idle_timer else interval)
            for animation in animations:
                if animation.state() == QPropertyAnimation.State.Paused:
                    animation.resume()
            if cursor_visible:
                self.fake_cursor.show()
            deferred, self._confirmation_deferred = self._confirmation_deferred, []
            for version, callback in deferred:
                if version == self._activity_revision:
                    self._defer_activity(callback)
            self.consume_event()
            self._fit_content()
        return self._dialogue_closed

    def reject(self):
        self.request_exit()

    def closeEvent(self, event):
        if self.request_exit():
            event.accept()
        else:
            event.ignore()

    def done(self, result):
        self._dialogue_closed = True
        if self.companion_chat is not None:
            self.companion_chat.close_chat()
        self.close_scene_windows()
        self._call_stack.clear()
        self.size_animation.stop()
        self._cancel_dialogue_activity()
        self.sounds.close()
        try:
            self.events.occurred.disconnect(self.receive_event)
        except (RuntimeError, TypeError):
            pass
        # Skipping or closing is also a deliberate choice to leave setup.
        payload = settings.load_all_settings()
        payload["onboarding_complete"] = True
        payload["onboarding_pacing"] = dict(self.pacing)
        settings.save_all_settings(payload)
        super().done(result)
