"""Settings dialog controls for importing and exporting preference presets."""

from pathlib import Path

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QFileDialog, QHBoxLayout, QLabel, QLineEdit,
    QMessageBox, QPushButton, QVBoxLayout, QWidget,
)

from core import settings
from core.settings_transfer import export_settings, get_factory_settings, import_settings, merge_settings
from ui.settings_controls import DeferredOptionsComboBox


class SettingsTransferMixin:
    def _create_advanced_tab(self):
        page = QWidget()
        layout = QVBoxLayout(page)
        self._add_section_heading(layout, "Import and export settings")
        description = QLabel(
            "Save your preferences to a JSON file or load them on another installation. "
            "Includes appearance, saved themes, playback and lyric preferences, "
            "lyric search folders, update preferences, and plugin settings."
        )
        description.setWordWrap(True)
        layout.addWidget(description)
        note = QLabel(
            "Your music library, playlists, history, per-song overrides, and reading "
            "dictionaries are kept separately. Folder paths may need updating on another computer. "
            "Restart LRCPlus after importing to apply plugin settings."
        )
        note.setWordWrap(True)
        layout.addWidget(note)
        actions = QHBoxLayout()
        self.export_settings_button = QPushButton("Export settings…")
        self.export_settings_button.setProperty("settingsLabel", "Export settings")
        self.export_settings_button.setProperty("settingsKeywords", "backup save preferences JSON preset")
        self.export_settings_button.setToolTip("Export the settings currently shown, including unsaved edits.")
        self.export_settings_button.clicked.connect(self._export_settings)
        actions.addWidget(self.export_settings_button)
        self.import_settings_button = QPushButton("Import settings…")
        self.import_settings_button.setProperty("settingsLabel", "Import settings")
        self.import_settings_button.setProperty("settingsKeywords", "restore load preferences JSON preset")
        self.import_settings_button.clicked.connect(self._import_settings)
        actions.addWidget(self.import_settings_button)
        actions.addStretch(1)
        layout.addLayout(actions)
        self.settings_transfer_status = QLabel(
            "Imported settings can be reviewed before clicking OK. Cancel discards the import."
        )
        self.settings_transfer_status.setTextFormat(Qt.TextFormat.PlainText)
        self.settings_transfer_status.setWordWrap(True)
        layout.addWidget(self.settings_transfer_status)

        self._add_section_heading(layout, "Factory defaults")
        factory_note = QLabel(
            "Load the app's factory defaults for review. Keeps saved themes, folders, "
            "plugin settings, your music library, playlists, history, per-song overrides, "
            "and reading dictionaries. Click OK to apply or Cancel to discard."
        )
        factory_note.setWordWrap(True)
        layout.addWidget(factory_note)
        self.restore_defaults_button = QPushButton("Restore factory defaults…")
        self.restore_defaults_button.setProperty("settingsLabel", "Restore factory defaults")
        self.restore_defaults_button.setProperty("settingsKeywords", "reset original preferences undo changes")
        self.restore_defaults_button.clicked.connect(self._restore_factory_defaults)
        layout.addWidget(self.restore_defaults_button, alignment=Qt.AlignmentFlag.AlignLeft)
        recorder_note = QLabel(
            "To choose a new factory setup, use Debug → Record settings changes, adjust the tabs, "
            "then use Debug → Copy factory defaults. Recordings stay available when you reopen Settings."
        )
        recorder_note.setWordWrap(True)
        recorder_note.setProperty("settingsLabel", "Settings change recorder")
        recorder_note.setProperty("settingsKeywords", "debug record factory defaults copy JSON")
        layout.addWidget(recorder_note)
        layout.addStretch(1)
        return self._scroll_page(page)

    def _restore_factory_defaults(self):
        if self.dependencies_tab.busy:
            self.settings_transfer_status.setText(
                "Wait for the dependency operation to finish before restoring defaults."
            )
            return
        self._load_imported_settings(get_factory_settings())
        self.settings_transfer_status.setText(
            "Factory defaults loaded. Review the tabs, then click OK to apply or Cancel to discard."
        )

    def _settings_for_transfer(self):
        payload = merge_settings(settings.load_all_settings(), self._imported_settings)
        payload.update({
            "startup": self.get_startup_settings(),
            "appearance": self.get_appearance_settings(),
            "playback": self.get_playback_settings(),
            "karaoke": self.get_karaoke_settings(),
            "lyrics_global": self.get_global_lyrics_settings(),
            "lrc_folders": self.get_folders(),
            "updates": self.get_update_settings(),
            "listening_statistics": self.get_statistics_settings(),
            "disabled_plugins": sorted(self.get_disabled_plugins()),
        })
        return payload

    def _export_settings(self):
        path, _ = QFileDialog.getSaveFileName(
            self, "Export Settings", "lrcplus-settings.json", "JSON files (*.json)"
        )
        if not path:
            return
        if not Path(path).suffix:
            path += ".json"
        try:
            export_settings(path, self._settings_for_transfer())
        except (OSError, ValueError, TypeError) as error:
            QMessageBox.warning(self, "Export Settings", f"Could not export settings:\n{error}")
            return
        self.settings_transfer_status.setText(f"Settings exported to {path}.")

    def _import_settings(self):
        if self.dependencies_tab.busy:
            self.settings_transfer_status.setText("Wait for the dependency operation to finish before importing settings.")
            return
        path, _ = QFileDialog.getOpenFileName(
            self, "Import Settings", "", "JSON files (*.json)"
        )
        if not path:
            return
        try:
            incoming = import_settings(path)
        except (OSError, ValueError, TypeError, RecursionError) as error:
            QMessageBox.warning(self, "Import Settings", f"Could not import settings:\n{error}")
            return
        self._load_imported_settings(incoming)
        self.settings_transfer_status.setText(
            f"Loaded settings from {Path(path).name}. Review the tabs, then click OK to apply "
            "or Cancel to discard. Restart LRCPlus to apply plugin settings."
        )

    @staticmethod
    def _set_imported_control(widget, value):
        if isinstance(widget, QCheckBox):
            widget.setChecked(value)
        elif isinstance(widget, DeferredOptionsComboBox):
            widget.setCurrentValue(value)
        elif isinstance(widget, QComboBox):
            if widget.property("_forced_continuous"):
                value = "continuous"
            widget.setCurrentIndex(max(0, widget.findData(value)))
        elif isinstance(widget, QLineEdit):
            widget.setText(value)
        elif isinstance(widget, QPushButton):
            widget.setText(value)
            widget.setStyleSheet(f"QPushButton {{ text-align: left; background: {value}; }}")
        else:
            widget.setValue(value)

    def _load_imported_settings(self, incoming):
        payload = merge_settings(self._settings_for_transfer(), incoming)
        # Only apply supplied sections; unrelated drafts and file overrides survive.
        bindings = {
            "startup": (settings.get_startup_settings, {
                "remember_window": "remember_window_checkbox", "restore_playlist": "restore_playlist_checkbox",
            }),
            "updates": (settings.get_update_settings, {
                "automatic_check": "automatic_update_checkbox", "channel": "update_channel_combo",
            }),
            "listening_statistics": (settings.get_statistics_settings, {"enabled": "statistics_enabled_checkbox"}),
            "playback": (settings.get_playback_settings, {
                "precise_timestamp_fps": "precise_timestamp_fps", "play_mode": "play_mode_combo",
                "shuffle_mode": "shuffle_mode_combo", "after_playback": "after_playback_combo",
                "gapless_playback": "gapless_checkbox", "crossfade_seconds": "crossfade_seconds",
                "replaygain_mode": "replaygain_combo", "replaygain_preamp": "replaygain_preamp",
                "replaygain_prevent_clipping": "replaygain_clipping", "output_device": "output_device_combo",
                "show_waveform": "show_waveform_checkbox",
            }),
            "appearance": (settings.get_appearance_settings, {
                "background_enabled": "background_enabled_checkbox", "background_layout": "background_layout_combo",
                "artwork_source": "artwork_source_combo", "booru_tags": "booru_tags_edit",
                "background_blur_mode": "background_blur_mode_combo", "background_blur_bleed": "background_blur_bleed_checkbox",
                "background_blur": "background_blur_slider", "background_darkness": "background_darkness_slider",
                "empty_timestamp_placeholder_enabled": "empty_timestamp_placeholder_checkbox",
                "empty_timestamp_placeholder": "empty_timestamp_placeholder_edit",
            }),
        }
        for section, (normalize, controls) in bindings.items():
            if section in incoming:
                values = normalize(payload)
                for key, name in controls.items():
                    self._set_imported_control(getattr(self, name), values[key])
        if "device_lyric_delays_ms" in incoming.get("playback", {}):
            self._device_lyric_delays = settings.get_playback_settings(payload)["device_lyric_delays_ms"]
            self._show_device_lyric_delay()
        if "playback" in incoming:
            playback = settings.get_playback_settings(payload)
            for key in ("radio_auto_lyrics", "resume_live_from_buffer"):
                self.playback_settings[key] = playback[key]
        if "appearance" in incoming:
            self.appearance_settings = values = settings.get_appearance_settings(payload)
            for key in ("theme_mode", "theme_color", "theme_surface", "theme_overrides", "theme_preset_name", "custom_themes"):
                setattr(self, key, values[key])
            self.light_theme_button.setChecked(self.theme_mode == "light")
            self.dark_theme_button.setChecked(self.theme_mode == "dark")
            self.qt_theme_button.setChecked(self.theme_mode == "qt")
            self.custom_theme_panel.setEnabled(self.theme_mode != "qt")
            self.theme_surface_combo.blockSignals(True)
            self._set_imported_control(self.theme_surface_combo, self.theme_surface)
            self.theme_surface_combo.blockSignals(False)
            self._reload_theme_presets(self.theme_preset_name)
            self._refresh_advanced_theme()
            self.custom_background_folders_list.clear()
            self.custom_background_folders_list.addItems(values["custom_background_folders"])
            self._update_custom_folder_controls()
        for section, controls, normalize in (
            ("lyrics_global", self.global_controls, settings.get_global_lyrics_settings),
            ("karaoke", self.karaoke_controls, settings.get_karaoke_settings),
        ):
            if section in incoming:
                values = normalize(payload)
                for key, value in values.items():
                    if key in controls:
                        self._set_imported_control(controls[key], value)
        if "lyrics_global" in incoming:
            self.global_lyrics = settings.get_global_lyrics_settings(payload)
            self._update_compact_lyrics_controls(self.global_controls)
        if "lrc_folders" in incoming:
            self.folder_list.clear()
            self.folder_list.addItems(payload["lrc_folders"])
        if "disabled_plugins" in incoming:
            for plugin_id, toggle in self.plugin_toggles.items():
                if toggle.isEnabled():
                    toggle.setChecked(plugin_id not in payload["disabled_plugins"])
        self._imported_settings = merge_settings(self._imported_settings, incoming)

    def _save_imported_settings(self):
        if self._imported_settings:
            # Re-read at commit so playback/library updates during the dialog survive.
            from core.settings_transfer import normalize_transfer_settings
            preferences = normalize_transfer_settings(self._settings_for_transfer())
            settings.save_all_settings(merge_settings(settings.load_all_settings(), preferences))
