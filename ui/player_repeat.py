"""Dock repeat controls and a session-only, counted track repeat."""

from PySide6.QtGui import QActionGroup
from PySide6.QtWidgets import QInputDialog, QMenu, QToolButton

from core.settings import save_playback_settings


class PlayerRepeatMixin:
    def _init_repeat(self):
        self._repeat_total = self._repeat_remaining = None
        self.repeat_button = QToolButton()
        self.repeat_button.setObjectName("dockAction")
        self.repeat_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        menu = QMenu(self.repeat_button)
        group = QActionGroup(menu)
        self._repeat_actions = {}
        for mode, label in (("next_track", "Repeat off"),
                            ("repeat_all", "Repeat all"),
                            ("repeat_track", "Repeat current track")):
            action = menu.addAction(label)
            action.setCheckable(True)
            group.addAction(action)
            action.triggered.connect(lambda _checked=False, value=mode: self._set_repeat_mode(value))
            self._repeat_actions[mode] = action
        menu.addSeparator()
        self.repeat_count_action = menu.addAction("Play current track N times...", self._choose_repeat_count)
        self.repeat_button.setMenu(menu)
        menu.aboutToShow.connect(self._refresh_repeat)
        self._refresh_repeat()

    def _set_repeat_mode(self, mode):
        self._clear_counted_repeat()
        self.playback_settings["after_playback"] = mode
        save_playback_settings(self.playback_settings)
        self._invalidate_gapless_preload()
        self._refresh_repeat()

    def _choose_repeat_count(self):
        if not self.current_audio_path:
            return
        count, accepted = QInputDialog.getInt(
            self, "Repeat current track",
            "Total plays, including the current play (then advance normally):",
            self._repeat_total or 3, 2, 999,
        )
        if accepted:
            self._start_counted_repeat(count)

    def _start_counted_repeat(self, count):
        if not self.current_audio_path:
            return
        self._set_repeat_mode("next_track")
        self._repeat_total = self._repeat_remaining = count
        # Keep the current decoder; do not queue a successor or crossfade away
        # from a track that must play again. Normal preloading resumes at exit.
        self._invalidate_gapless_preload()
        self._refresh_repeat()

    def _clear_counted_repeat(self):
        self._repeat_total = self._repeat_remaining = None
        self._refresh_repeat()

    def _refresh_repeat(self):
        button = getattr(self, "repeat_button", None)
        if button is None:
            return
        remaining = self._repeat_remaining
        mode = self.playback_settings.get("after_playback", "next_track")
        for value, action in self._repeat_actions.items():
            action.setChecked(remaining is None and value == ("next_track" if mode == "stop" else mode))
        self.repeat_count_action.setEnabled(bool(self.current_audio_path))
        if remaining is not None:
            current = self._repeat_total - remaining + 1
            text = f"Repeat: {current}/{self._repeat_total}"
            tip = f"Play {current} of {self._repeat_total}, then advance normally. Stop or change tracks to cancel."
        else:
            text = {"repeat_all": "Repeat: All", "repeat_track": "Repeat: One",
                    "stop": "Repeat: Off", "next_track": "Repeat: Off"}.get(mode, "Repeat: Off")
            tip = {"repeat_all": "Repeat the active library or queue.",
                   "repeat_track": "Repeat the current track until turned off.",
                   "stop": "Repeat off. Stop at the end of the active library or queue."}.get(
                       mode, "Repeat off. Play through the active library or queue once.")
        button.setText(text)
        button.setToolTip(tip)
        button.setAccessibleName(text)
