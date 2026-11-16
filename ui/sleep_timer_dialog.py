"""Small themed editor for the player's one-shot sleep timer."""

from datetime import datetime, timedelta

from PySide6.QtWidgets import (
    QDialog, QFormLayout, QHBoxLayout, QLabel, QPushButton, QVBoxLayout,
)

from core.theme import build_theme_palette
from ui.player_sleep_timer import countdown_text
from ui.settings_controls import ThemedComboBox, ThemedSpinBox, THEMED_INPUT_STYLE, use_native_controls


class SleepTimerDialog(QDialog):
    def __init__(self, player, defaults):
        super().__init__(player)
        self.player = player
        self.timer = player.sleep_timer
        self.setWindowTitle("Sleep timer")
        self.setMinimumWidth(410)

        self.status_label = QLabel()
        self.status_label.setWordWrap(True)
        self.status_label.setObjectName("sleepStatus")
        self.status_label.setAccessibleName("Sleep timer status")

        self.duration_combo = ThemedComboBox()
        self.duration_combo.setAccessibleName("Sleep timer duration")
        for minutes in (15, 30, 45, 60, 90):
            self.duration_combo.addItem(f"{minutes} minutes", minutes)
        self.duration_combo.addItem("Custom…", None)
        index = self.duration_combo.findData(defaults["minutes"])
        self.duration_combo.setCurrentIndex(index if index >= 0 else 5)
        self.minutes_spin = ThemedSpinBox()
        self.minutes_spin.setRange(1, 720)
        self.minutes_spin.setSuffix(" minutes")
        self.minutes_spin.setValue(defaults["minutes"])
        self.minutes_spin.setAccessibleName("Custom sleep timer duration in minutes")
        self.duration_combo.currentIndexChanged.connect(self._duration_changed)
        self._duration_changed()

        self.action_combo = ThemedComboBox()
        self.action_combo.setAccessibleName("Sleep timer expiry action")
        self.action_combo.addItem("Pause immediately", "pause")
        self.action_combo.addItem("Finish the current song", "finish_track")
        self.action_combo.setCurrentIndex(self.action_combo.findData(defaults["action"]))
        form = QFormLayout()
        form.addRow("Duration", self.duration_combo)
        form.addRow("Custom duration", self.minutes_spin)
        form.addRow("When time is up", self.action_combo)

        self.start_button = QPushButton()
        self.start_button.setDefault(True)
        self.start_button.clicked.connect(self._start)
        self.end_song_button = QPushButton("At the end of this song")
        self.end_song_button.setEnabled(bool(player.current_audio_path))
        self.end_song_button.clicked.connect(self._end_song)
        self.add_button = QPushButton("Add 5 minutes")
        self.add_button.clicked.connect(self.timer.add_five_minutes)
        self.cancel_button = QPushButton("Cancel timer")
        self.cancel_button.clicked.connect(self.timer.cancel)
        active_row = QHBoxLayout()
        active_row.addWidget(self.add_button)
        active_row.addWidget(self.cancel_button)
        self.close_button = QPushButton("Close")
        self.close_button.clicked.connect(self.accept)

        hint = QLabel("The countdown continues while paused. Stop cancels the timer.")
        hint.setObjectName("sleepHint")
        hint.setWordWrap(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(20, 18, 20, 18)
        layout.setSpacing(12)
        layout.addWidget(self.status_label)
        layout.addLayout(form)
        layout.addWidget(self.start_button)
        layout.addWidget(self.end_song_button)
        layout.addLayout(active_row)
        layout.addWidget(hint)
        layout.addWidget(self.close_button)
        self._apply_theme(player.appearance_settings)
        self.timer.changed.connect(self._refresh)
        player.media_player.sourceChanged.connect(self._track_changed)
        player.queue_changed.connect(self._track_changed)
        self._refresh()

    def _apply_theme(self, settings):
        if settings.get("theme_mode") == "qt":
            use_native_controls(self, True)
            self.setStyleSheet("")
            return
        palette = build_theme_palette(
            settings.get("theme_mode", "light"), settings.get("theme_color", "#3a9879"),
            settings.get("theme_surface", "classic"), settings.get("theme_overrides", {}),
        )
        style = """
            QDialog { background: @window@; color: @text@; }
            QLabel { color: @text@; }
            QLabel#sleepStatus { font-size: 17px; font-weight: 600; padding-bottom: 4px; }
            QLabel#sleepHint { color: @muted@; }
            QPushButton, QComboBox, QSpinBox {
                background: @surface@; color: @text@; border: 1px solid @field_border@;
                border-radius: 5px; padding: 7px;
            }
            QPushButton:hover { background: @raised_hover@; }
            QPushButton:default { border: 2px solid @accent_border@; }
            QPushButton:disabled, QSpinBox:disabled { color: @muted@; background: @raised@; }
            QComboBox QAbstractItemView {
                background: @surface@; color: @text@; selection-background-color: @selection@;
                selection-color: @selection_text@;
            }
        """ + THEMED_INPUT_STYLE
        for key, value in palette.items():
            style = style.replace(f"@{key}@", value)
        self.setStyleSheet(style)

    def _track_changed(self, source=None):
        self.end_song_button.setEnabled(bool(self.player.current_audio_path))

    def _duration_changed(self):
        self.minutes_spin.setEnabled(self.duration_combo.currentData() is None)

    def _start(self):
        minutes = self.duration_combo.currentData()
        if minutes is None:
            minutes = self.minutes_spin.value()
        try:
            self.player._start_sleep_timer(minutes, self.action_combo.currentData())
        except OSError as error:
            self.status_label.setText(f"Could not save timer settings: {error}")
            return
        self.accept()

    def _end_song(self):
        self.player._sleep_at_end_of_song()
        self.accept()

    def _refresh(self):
        if self.timer.finishing:
            status = "Playback pauses at the end of this song."
        elif self.timer.active:
            remaining = self.timer.remaining_seconds
            finish = (datetime.now() + timedelta(seconds=remaining)).strftime("%H:%M")
            action = "finish the current song" if self.timer.action == "finish_track" else "pause playback"
            status = f"{countdown_text(remaining)} remaining · at {finish}, {action}."
        else:
            status = "No sleep timer is running."
        self.status_label.setText(status)
        self.start_button.setText("Replace timer" if self.timer.active else "Start timer")
        self.add_button.setEnabled(self.timer.state == "countdown")
        self.cancel_button.setEnabled(self.timer.active)
