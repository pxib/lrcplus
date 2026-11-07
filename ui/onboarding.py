"""First-run setup and its data-driven conversations."""

import json
import base64
import logging
import re
import sys
from pathlib import Path

from PySide6.QtCore import QCoreApplication, QEasingCurve, QEvent, QEventLoop, QPoint, QPointF, QPropertyAnimation, Qt, QTimer, Signal
from PySide6.QtGui import QCursor, QKeySequence, QMouseEvent, QPixmap, QShortcut
from PySide6.QtWidgets import (
    QApplication, QComboBox, QDialog, QHBoxLayout, QLabel, QMenu, QPushButton, QStyle, QStyleOptionToolButton, QToolButton, QVBoxLayout, QWidget,
)

from core import settings
from core.onboarding_events import onboarding_events
from core.onboarding_script import SCRIPT_PATH, load_script


DIALOGUE_PATH = Path(__file__).resolve().parent.parent / "assets" / "onboarding" / "dialogue.json"
ROUTES_PATH = DIALOGUE_PATH.with_name("dialogueroute.json")
SCRIPT_COMMAND = re.compile(r"\\(?:iftext\(\s*[a-z_]+\s*,\s*[a-z0-9_=-]*\s*\)|idle\(\s*\d+(?:\.\d+)?\s*\)|pointat\(\s*lyrics_button(?:\s*,\s*[a-z_]+)?\s*\)|jump\(\s*[a-z_][a-z_0-9]*\s*\)|watchmenu\(\s*lyrics_menu\s*,\s*[a-z_][a-z_0-9]*\s*\)|clickmenu\(\s*lyrics_menu\s*,\s*search_lyrics\s*,\s*[a-z_][a-z_0-9]*\s*\)|clickcontrol\(\s*lyrics_button\s*,\s*[a-z_][a-z_0-9]*\s*\)|waitevent\(\s*(?:[a-z_]+|idle\(\d+(?:\.\d+)?\))(?:\s*,\s*(?:[a-z_]+|idle\(\d+(?:\.\d+)?\)))*\s*\)|if\(\s*[a-z_]+\s*,\s*[^(),]+\)|toggleoutsideclick(?:\(\s*(?:true|false)\s*\))?|button\([^(),]+,[^(),]+\)|click\(\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?\s*\)|pos\(\s*-?\d+(?:\.\d+)?%?\s*,\s*-?\d+(?:\.\d+)?%?(?:\s*,\s*[a-z_]+)?\s*\)|wait(?:\(\s*\d+(?:\.\d+)?\s*\))?|curhide|res(?:\(\s*[a-z_]+\s*\))?|cr\d+|c\d+|nw\d+|n|e|c)", re.IGNORECASE)

EASINGS = {"linear": QEasingCurve.Type.Linear, "in_out_cubic": QEasingCurve.Type.InOutCubic,
           "out_cubic": QEasingCurve.Type.OutCubic}


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
    def __init__(self, player, parent=None):
        super().__init__(parent if parent is not None else player)
        self.player = player
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
        self.typing_timer.setInterval(25)
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
        self.script_timer = QTimer(self)
        self.script_timer.setSingleShot(True)
        self.script_timer.timeout.connect(self.run_script_command)
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
        self.setFixedSize(620, 320)
        layout = QVBoxLayout(self)
        self.title = QLabel()
        self.title.setStyleSheet("font-size: 20px; font-weight: bold;")
        self.description = DialogueText()
        self.description.setTextFormat(Qt.TextFormat.PlainText)
        self.description.setWordWrap(True)
        self.description.clicked.connect(self.advance_dialogue)
        layout.addWidget(self.title)
        text_row = QHBoxLayout()
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
        layout.addStretch()
        controls = QHBoxLayout()
        self.skip = QPushButton("Skip setup")
        self.skip.clicked.connect(self.accept)
        self.back = QPushButton("Back")
        self.back.clicked.connect(self.previous_page)
        self.next = QPushButton("Next")
        self.next.clicked.connect(self.next_page)
        self.debug_button = QPushButton("Debug")
        self.debug_button.clicked.connect(self.show_debug)
        self.debug_dialog = None
        for button in (self.debug_button, self.skip, self.back, self.next):
            controls.addWidget(button)
        layout.addLayout(controls)
        self.set_page(0)
        self.show_dialogue()

    def showEvent(self, event):
        super().showEvent(event)
        if self.start_position is None:
            self.start_position = self.player.mapFromGlobal(self.pos())
        QTimer.singleShot(0, self.enable_window_move_tracking)

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
        self.move_animation.start()

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
        self.page = page
        pages = [
            ("Welcome to LRCPlus", ""),
            ("Your music library", ""),
            ("Appearance", ""),
            ("Lyrics", ""),
            ("Ready to listen", "Setup is complete. You can change your settings and manage your music library at any time."),
        ]
        self.setWindowTitle(pages[page][0])
        self.title.setText("Setup Wizard")
        self.description.setText(pages[page][1])
        self.language_combo.setVisible(page == 0)
        self.theme_combo.setVisible(page == 2)
        self.preset_combo.setVisible(page == 2)
        self.folder_button.setVisible(page == 1)
        self.folders.setVisible(page == 1)
        self.folders.setText("\n".join(self.player.library_folders) or "No folders added yet.")
        self.back.setEnabled(page > 0)
        self.next.setText("Finish" if page == 4 else "Next")

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
            self.debug_step.addItems(["Welcome", "Music library", "Appearance", "Lyrics", "Ready"])
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
        self.waiting_events.clear()
        self.event_auto_advance = False
        self.script_busy = False
        self.clear_route_buttons()

    def debug_skip_line(self):
        if not self.dialogue_lines:
            return
        self.debug_cancel_activity()
        self.dialogue_indicator.show()
        self.space.setEnabled(True)
        self.advance_dialogue()
        self.update_debug()

    def debug_jump_step(self):
        page = self.debug_step.currentIndex()
        self.debug_cancel_activity()
        self.dialogue_lines = []
        self.space.setEnabled(False)
        self.dialogue_indicator.hide()
        self.next.setEnabled(True)
        self.set_page(page)
        if page < 4:
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
            f"Pending commands: {', '.join(self.script_commands) or 'none'}\n"
            f"Checkpoints: {', '.join(sorted(self.checkpoints)) or 'none'}"
        )
        if self.debug_text.text() != text:
            self.debug_text.setText(text)

    def show_dialogue(self):
        key = ("firststeplanguage", "secondstepmusiclibraryfolders", "thirdstepappearance", "fourthsteplyrics")[self.page]
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
            self.folder_button.setVisible(False)
            self.folders.setVisible(False)
            self.theme_combo.setVisible(False)
            self.preset_combo.setVisible(False)
            self.advance_dialogue()

    def advance_dialogue(self):
        if not self.dialogue_lines:
            return
        if self.typing_timer.isActive():
            self.finish_typing()
            return
        if self.script_busy:
            return
        self.timer.stop()
        self.dialogue_index += 1
        if self.dialogue_index >= len(self.dialogue_lines):
            self.dialogue_lines = []
            self.space.setEnabled(False)
            self.dialogue_indicator.hide()
            self.next.setEnabled(True)
            if self.return_to_setup:
                self.set_page(self.page)
            else:
                self.back.setEnabled(self.page > 0)
                self.folder_button.setVisible(self.page == 1)
                self.folders.setVisible(self.page == 1)
                self.theme_combo.setVisible(self.page == 2)
                self.preset_combo.setVisible(self.page == 2)
            return
        speaker, text = self.dialogue_lines[self.dialogue_index]
        self.title.setText(speaker)
        self.full_text, _ = parse_dialogue_line(text)
        self.tokens = dialogue_tokens(text)
        self.script_commands = []
        self.visible_text = ""
        self.description.setText("")
        self.resume_line()

    def resume_line(self):
        self.script_busy = False
        if not self.tokens:
            if self.event_auto_advance:
                self.event_auto_advance = False
                self.advance_dialogue()
            return
        self.event_auto_advance = False
        kind, value = self.tokens.pop(0)
        if kind == "text":
            self.current_text = value
            self.current_text_index = 0
            self.typing_timer.start()
            return
        self.script_commands = [value]
        while self.tokens and self.tokens[0][0] == "command":
            self.script_commands.append(self.tokens.pop(0)[1])
        self.script_busy = True
        self.run_script_command()

    def type_character(self):
        self.current_text_index += 1
        self.description.setText(self.visible_text + self.current_text[:self.current_text_index])
        if self.current_text_index >= len(self.current_text):
            self.finish_typing()

    def finish_typing(self):
        self.typing_timer.stop()
        self.visible_text += self.current_text
        self.current_text = ""
        self.description.setText(self.visible_text)
        self.resume_line()

    def run_script_command(self):
        if not self.script_commands:
            self.resume_line()
            return
        original_command = self.script_commands.pop(0)
        command = original_command.lower()
        if command.startswith(r"\iftext("):
            event_name, encoded = (part.strip() for part in original_command[8:-1].split(",", 1))
            if event_name.lower() == self.last_event:
                text = base64.urlsafe_b64decode(encoded).decode("utf-8")
                continuation = "".join(self.script_commands) + "".join(value for _, value in self.tokens)
                insert = [("Setup Wizard", text)]
                if continuation:
                    insert.append(("Setup Wizard", continuation + r"\nw0"))
                self.dialogue_lines = (self.dialogue_lines[:self.dialogue_index + 1] + insert
                                       + self.dialogue_lines[self.dialogue_index + 1:])
                self.script_commands = []
                self.tokens = []
                self.event_auto_advance = False
                self.script_busy = False
                self.advance_dialogue()
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
            self.fake_cursor.show()
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.cursor_animation.start()
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
            self.fake_cursor.show()
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.cursor_animation.start()
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
                                    steps = ("firststeplanguage", "secondstepmusiclibraryfolders", "thirdstepappearance", "fourthsteplyrics")
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
            self.fake_cursor.show()
            self.cursor_animation.setStartValue(self.fake_cursor.pos())
            self.cursor_animation.setEndValue(target)
            self.cursor_animation.start()
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
            self.script_busy = False
            self.timer.start(int(command[3:]) * 1000)
            return
        if command == r"\n":
            self.tokens = []
            self.script_commands = []
            self.script_busy = False
            return
        if command == r"\e":
            self.script_busy = False
            self.dialogue_lines = []
            self.space.setEnabled(False)
            self.dialogue_indicator.hide()
            self.next.setEnabled(True)
            self.back.setEnabled(self.page > 0)
            self.folder_button.setVisible(self.page == 1)
            self.folders.setVisible(self.page == 1)
            self.theme_combo.setVisible(self.page == 2)
            self.preset_combo.setVisible(self.page == 2)
        elif command == r"\c":
            self.return_to_setup = True
            self.dialogue_index = len(self.dialogue_lines) - 1
            self.script_busy = False
        else:
            self.run_script_command()

    def clear_route_buttons(self):
        while self.route_buttons.count():
            widget = self.route_buttons.takeAt(0).widget()
            if widget is not None:
                widget.deleteLater()

    def receive_event(self, name):
        name = name.lower()
        if name == "wizard_moved":
            self.pending_events = [event for event in self.pending_events if event != name]
        if name == "lyrics_search_started":
            self.pending_events = [event for event in self.pending_events if event not in {
                "lyrics_search_started", "lyrics_search_results", "lyrics_search_failed", "lyrics_search_empty", "lyrics_search_closed"
            }]
        self.pending_events.append(name)
        self.pending_events = self.pending_events[-32:]
        self.consume_event()

    def consume_event(self):
        if not self.waiting_events:
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
            QTimer.singleShot(0, control.showMenu)
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
            QTimer.singleShot(0, action.trigger)
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
                    QTimer.singleShot(0, action.trigger)
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
        self.disruption_pending = True
        self.user_move_timer.stop()
        self.watched_menu = None
        self.menu_recovery_route = None
        self.pending_menu_action = None
        self.pending_control = None
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
        self.script_commands = []
        self.tokens = []
        self.script_busy = False
        def resume():
            self.disruption_pending = False
            if self.isVisible():
                self.select_route(route)
        QTimer.singleShot(0, resume)

    def finish_script_click(self):
        self.fake_cursor.setPixmap(self.cursor_normal)
        self.fake_cursor.raise_()
        self.run_script_command()

    def select_route(self, route):
        lines = load_route(route)
        if not lines:
            return
        self.clear_route_buttons()
        self.dialogue_indicator.show()
        self.dialogue_lines = lines
        self.dialogue_index = -1
        self.script_commands = []
        self.script_busy = False
        self.advance_dialogue()

    def next_page(self):
        if self.page == 4:
            self.accept()
        else:
            self.set_page(self.page + 1)
            if self.page < 4:
                self.show_dialogue()

    def previous_page(self):
        self.set_page(self.page - 1)
        self.show_dialogue()

    def done(self, result):
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
        try:
            self.events.occurred.disconnect(self.receive_event)
        except (RuntimeError, TypeError):
            pass
        # Skipping or closing is also a deliberate choice to leave setup.
        payload = settings.load_all_settings()
        payload["onboarding_complete"] = True
        settings.save_all_settings(payload)
        super().done(result)
