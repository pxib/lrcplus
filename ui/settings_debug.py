"""Debug menu for recording preference edits without saving or applying them."""

import json

from PySide6.QtCore import QTimer
from PySide6.QtWidgets import (
    QAbstractButton, QAbstractSpinBox, QApplication, QComboBox, QDialog,
    QDialogButtonBox, QLabel, QLineEdit, QMenu, QPlainTextEdit, QSlider,
    QTabWidget, QToolButton, QVBoxLayout, QWidget,
)

from core.settings_recorder import SettingsChangeRecorder
from core.settings_transfer import settings_json


class SettingsDebugMixin:
    def _create_settings_debug_button(self):
        host = self.parentWidget()
        self._persistent_settings_recording = host is not None
        self.settings_recorder = getattr(host, "_settings_change_recorder", None)
        if self.settings_recorder is None:
            self.settings_recorder = SettingsChangeRecorder()
            if host is not None:
                host._settings_change_recorder = self.settings_recorder
        self._settings_record_timer = QTimer(self)
        self._settings_record_timer.setSingleShot(True)
        self._settings_record_timer.setInterval(0)
        self._settings_record_timer.timeout.connect(self._capture_settings_recording)

        self.settings_debug_button = QToolButton()
        self.settings_debug_button.setText("Debug")
        self.settings_debug_button.setAccessibleName("Settings debug menu")
        self.settings_debug_button.setPopupMode(QToolButton.ToolButtonPopupMode.InstantPopup)
        self.settings_debug_menu = QMenu(self.settings_debug_button)
        self.settings_debug_button.setMenu(self.settings_debug_menu)
        self.record_settings_action = self.settings_debug_menu.addAction("Record settings changes")
        self.record_settings_action.setCheckable(True)
        self.record_settings_action.setChecked(self.settings_recorder.recording)
        self.record_settings_action.toggled.connect(self._set_settings_recording)
        self.view_settings_recording_action = self.settings_debug_menu.addAction("View recording…")
        self.view_settings_recording_action.triggered.connect(self._view_settings_recording)
        self.settings_debug_menu.addSeparator()
        self.copy_settings_changes_action = self.settings_debug_menu.addAction("Copy changes as JSON")
        self.copy_settings_changes_action.triggered.connect(self._copy_settings_changes)
        self.copy_factory_defaults_action = self.settings_debug_menu.addAction("Copy factory defaults")
        self.copy_factory_defaults_action.triggered.connect(self._copy_factory_defaults)
        self.settings_debug_menu.addSeparator()
        self.clear_settings_recording_action = self.settings_debug_menu.addAction("Clear recording")
        self.clear_settings_recording_action.triggered.connect(self._clear_settings_recording)
        self.settings_record_status_action = self.settings_debug_menu.addAction("No recording")
        self.settings_record_status_action.setEnabled(False)
        # Capture once at the end of an edit's signals, after dependent controls
        # synchronize (for example, the mutually exclusive reading options).
        for widget in self.findChildren(QWidget):
            if isinstance(widget, QAbstractButton):
                widget.clicked.connect(self._schedule_settings_recording)
                widget.toggled.connect(self._schedule_settings_recording)
            elif isinstance(widget, QComboBox):
                widget.currentIndexChanged.connect(self._schedule_settings_recording)
            elif isinstance(widget, (QSlider, QAbstractSpinBox)):
                widget.valueChanged.connect(self._schedule_settings_recording)
            elif isinstance(widget, QLineEdit):
                widget.textChanged.connect(self._schedule_settings_recording)
        from ui.lyric_layers import LyricLayerOrder
        for widget in self.findChildren(LyricLayerOrder):
            widget.model().rowsMoved.connect(self._schedule_settings_recording)
            widget.model().rowsInserted.connect(self._schedule_settings_recording)
            widget.model().rowsRemoved.connect(self._schedule_settings_recording)
        self.settings_debug_menu.aboutToShow.connect(self._capture_settings_recording)
        self._capture_settings_recording()
        return self.settings_debug_button

    def _factory_preferences_in_dialog(self):
        return {
            "startup": self.get_startup_settings(), "updates": self.get_update_settings(),
            "appearance": self.get_appearance_settings(), "playback": self.get_playback_settings(),
            "karaoke": self.get_karaoke_settings(), "lyrics_global": self.get_global_lyrics_settings(),
            "listening_statistics": self.get_statistics_settings(),
        }

    def _set_settings_recording(self, enabled):
        self._settings_record_timer.stop()
        preferences = self._factory_preferences_in_dialog()
        if enabled:
            self.settings_recorder.start(preferences)
        else:
            self.settings_recorder.stop(preferences)
        self._update_settings_recording_actions()

    def _schedule_settings_recording(self, *_args):
        if self.settings_recorder.recording:
            self._settings_record_timer.start()

    def _capture_settings_recording(self):
        if self.settings_recorder.recording:
            self.settings_recorder.capture(self._factory_preferences_in_dialog())
        self._update_settings_recording_actions()

    def _update_settings_recording_actions(self):
        recorder = self.settings_recorder
        available = recorder.baseline is not None
        count = sum(len(values) for values in recorder.changes().values())
        self.record_settings_action.setText("Stop recording" if recorder.recording else "Record settings changes")
        self.settings_debug_button.setText("Debug • Recording" if recorder.recording else "Debug")
        self.view_settings_recording_action.setEnabled(available)
        self.copy_settings_changes_action.setEnabled(bool(count))
        self.copy_factory_defaults_action.setEnabled(available)
        self.clear_settings_recording_action.setEnabled(available)
        state = "Recording" if recorder.recording else "Stopped"
        self.settings_record_status_action.setText(
            f"{state}: {count} changed setting{'s' if count != 1 else ''}" if available else "No recording"
        )

    def _clear_settings_recording(self):
        self.record_settings_action.setChecked(False)
        self._settings_record_timer.stop()
        self.settings_recorder.clear()
        self._update_settings_recording_actions()

    def _finish_settings_recording(self):
        if self._persistent_settings_recording:
            self._capture_settings_recording()
        else:
            self.record_settings_action.setChecked(False)
        self._settings_record_timer.stop()

    def _copy_settings_changes(self):
        self._capture_settings_recording()
        changes = self.settings_recorder.changes()
        if changes:
            QApplication.clipboard().setText(settings_json(changes))
            self.settings_record_status_action.setText("Changed values copied as JSON")

    def _copy_factory_defaults(self):
        self._capture_settings_recording()
        if self.settings_recorder.current is not None:
            QApplication.clipboard().setText(settings_json(self.settings_recorder.current))
            self.settings_record_status_action.setText("Factory defaults JSON copied")

    def _view_settings_recording(self):
        self._capture_settings_recording()
        recorder = self.settings_recorder
        if recorder.current is None:
            return
        dialog = QDialog(self)
        dialog.setWindowTitle("Settings change recorder")
        dialog.resize(720, 540)
        layout = QVBoxLayout(dialog)
        note = QLabel(
            "Records unsaved application preferences. Recordings stay available when you "
            "reopen Settings, until cleared or the app closes. Copying does not change the factory baseline."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        tabs = QTabWidget()
        for title, contents in (
            ("Change log", json.dumps({"earlier_events_omitted": recorder.omitted_events,
                                       "changes": recorder.history}, ensure_ascii=False, indent=2)),
            ("Changed values", settings_json(recorder.changes()) if recorder.changes() else "{}"),
            ("Factory defaults", settings_json(recorder.current)),
        ):
            text = QPlainTextEdit()
            text.setReadOnly(True)
            text.setPlainText(contents)
            tabs.addTab(text, title)
        layout.addWidget(tabs, 1)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Close)
        copy_changes = buttons.addButton("Copy changes as JSON", QDialogButtonBox.ButtonRole.ActionRole)
        copy_changes.setEnabled(bool(recorder.changes()))
        copy_changes.clicked.connect(self._copy_settings_changes)
        copy_factory = buttons.addButton("Copy factory defaults", QDialogButtonBox.ButtonRole.ActionRole)
        copy_factory.clicked.connect(self._copy_factory_defaults)
        buttons.rejected.connect(dialog.reject)
        layout.addWidget(buttons)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
