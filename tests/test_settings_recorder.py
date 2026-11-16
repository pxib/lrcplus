import json

from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtWidgets import QApplication, QDialog, QPlainTextEdit

from core import settings
from core.settings_recorder import SettingsChangeRecorder
from core.settings_transfer import get_factory_candidate, import_settings
from ui.settings_dialog import SettingsDialog


def test_recorder_keeps_history_but_omits_reverted_values_from_changes():
    recorder = SettingsChangeRecorder()
    recorder.start({"lyrics_global": {"font_size": 32}})
    recorder.capture({"lyrics_global": {"font_size": 44}})
    assert recorder.changes() == {"lyrics_global": {"font_size": 44}}
    assert recorder.history == [{"setting": "lyrics_global.font_size", "before": 32, "after": 44}]
    recorder.capture({"lyrics_global": {"font_size": 32}})
    assert recorder.changes() == {}
    assert recorder.history[-1] == {"setting": "lyrics_global.font_size", "before": 44, "after": 32}
    assert not settings.SETTINGS_PATH.exists()


def test_stop_freezes_snapshot_and_restart_uses_new_baseline():
    recorder = SettingsChangeRecorder()
    recorder.start({"appearance": {"theme_mode": "light"}})
    recorder.stop({"appearance": {"theme_mode": "dark"}})
    recorder.capture({"appearance": {"theme_mode": "light"}})
    assert recorder.changes() == {"appearance": {"theme_mode": "dark"}}
    recorder.start({"appearance": {"theme_mode": "dark"}})
    assert recorder.changes() == {} and recorder.history == []
    recorder.clear()
    assert recorder.current is None and recorder.baseline is None and recorder.changes() == {}


def test_history_limit_preserves_complete_final_changes():
    recorder = SettingsChangeRecorder()
    recorder.HISTORY_LIMIT = 2
    recorder.start({"lyrics_global": {"font_size": 32}})
    for size in (33, 34, 35):
        recorder.capture({"lyrics_global": {"font_size": size}})
    assert len(recorder.history) == 2 and recorder.omitted_events == 1
    assert recorder.changes() == {"lyrics_global": {"font_size": 35}}


def test_factory_candidate_excludes_resources_and_machine_specific_data():
    payload = {
        "appearance": {"custom_background_folders": ["D:/private"], "custom_themes": [], "theme_mode": "dark"},
        "playback": {"output_device": "private-device-id", "volume": 43, "speed_percent": 150},
        "plugin_settings": {"example": {"api_key": "secret"}}, "disabled_plugins": ["example"],
        "lrc_folders": ["D:/lyrics"], "music_library": {"tracks": ["private.mp3"]},
        "lyrics_local": {"private.mp3": {"font_size": 50}}, "recent_files": ["private.mp3"],
        "updates": {"last_checked": "yesterday"},
    }
    candidate = get_factory_candidate(payload)
    assert candidate["appearance"]["theme_mode"] == "dark"
    assert candidate["playback"]["output_device"] == ""
    assert "volume" not in candidate["playback"]
    assert "speed_percent" not in candidate["playback"]
    assert "custom_themes" not in candidate["appearance"]
    assert "custom_background_folders" not in candidate["appearance"]
    assert set(candidate["updates"]) == {"automatic_check", "channel"}
    assert "private" not in json.dumps(candidate) and "secret" not in json.dumps(candidate)


def test_debug_menu_captures_edits_and_copies_importable_snapshots(qapp, tmp_path):
    original = {"music_library": {"tracks": ["private.mp3"]},
                "plugin_settings": {"example": {"api_key": "secret"}}}
    settings.save_all_settings(original)
    dialog = SettingsDialog([], settings.get_global_lyrics_settings(),
                            appearance_settings={"theme_mode": "light"})
    try:
        assert dialog.settings_debug_button.menu() is dialog.settings_debug_menu
        assert not dialog.copy_factory_defaults_action.isEnabled()
        dialog.record_settings_action.trigger()
        dialog.global_controls["font_size"].setValue(48)
        dialog.dark_theme_button.click()
        dialog.global_controls["show_romaji"].setChecked(True)
        qapp.processEvents()
        changes = dialog.settings_recorder.changes()
        assert changes["lyrics_global"]["font_size"] == 48
        assert changes["lyrics_global"]["show_romaji"] is True
        assert changes["lyrics_global"]["show_ruby"] is False
        assert changes["appearance"]["theme_mode"] == "dark"
        assert "Recording" in dialog.settings_debug_button.text()
        dialog.copy_settings_changes_action.trigger()
        copied_changes = json.loads(QApplication.clipboard().text())
        assert copied_changes["settings"] == changes
        # Opening the menu flushes even an edit made before the queued capture.
        dialog.global_controls["font_size"].setValue(49)
        dialog.settings_debug_menu.aboutToShow.emit()
        dialog.copy_factory_defaults_action.trigger()
        clipboard = QApplication.clipboard().text()
        assert "private.mp3" not in clipboard and "secret" not in clipboard
        path = tmp_path / "factory-defaults.json"
        path.write_text(clipboard, encoding="utf-8")
        snapshot = import_settings(path)
        assert snapshot["lyrics_global"]["font_size"] == 49
        assert snapshot["appearance"]["theme_mode"] == "dark"
        assert "startup" in snapshot and "playback" in snapshot and "karaoke" in snapshot
        assert settings.load_all_settings() == original
        dialog.reject()
        assert not dialog.settings_recorder.recording
        assert not dialog._settings_record_timer.isActive()
        assert settings.load_all_settings() == original
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_stopping_and_clearing_recording_preserves_frozen_copy(qapp):
    dialog = SettingsDialog([], settings.get_global_lyrics_settings())
    try:
        dialog.record_settings_action.trigger()
        dialog.global_controls["font_size"].setValue(45)
        # Stopping flushes the pending final edit.
        dialog.record_settings_action.trigger()
        dialog.global_controls["font_size"].setValue(50)
        qapp.processEvents()
        dialog.copy_factory_defaults_action.trigger()
        assert json.loads(QApplication.clipboard().text())["settings"]["lyrics_global"]["font_size"] == 45
        dialog.record_settings_action.trigger()
        assert dialog.settings_recorder.current["lyrics_global"]["font_size"] == 50
        assert dialog.settings_recorder.changes() == {}
        dialog.clear_settings_recording_action.trigger()
        assert not dialog.settings_recorder.recording
        assert not dialog.copy_factory_defaults_action.isEnabled()
        assert not dialog.copy_settings_changes_action.isEnabled()
        assert not dialog.record_settings_action.isChecked()
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_recording_view_shows_history_and_releases_dialog(qapp, monkeypatch):
    dialog = SettingsDialog([], settings.get_global_lyrics_settings())
    previews = []

    def inspect(preview):
        assert preview.windowTitle() == "Settings change recorder"
        contents = [text.toPlainText() for text in preview.findChildren(QPlainTextEdit)]
        assert len(contents) == 3
        assert any('"before": 32' in text and '"after": 42' in text for text in contents)
        assert any('"format": "lrcplus-settings"' in text for text in contents)
        previews.append(True)
        return QDialog.DialogCode.Rejected

    monkeypatch.setattr("ui.settings_debug.QDialog.exec", inspect)
    try:
        dialog.record_settings_action.trigger()
        dialog.global_controls["font_size"].setValue(42)
        dialog.view_settings_recording_action.trigger()
        assert previews == [True]
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert dialog.findChildren(QDialog) == []
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_recording_survives_applying_and_reopening_player_settings(qapp, monkeypatch):
    from ui.main_window import Player

    window = Player()
    recordings = []

    def first_dialog(dialog):
        dialog.record_settings_action.trigger()
        dialog.global_controls["font_size"].setValue(43)
        recordings.append(dialog.settings_recorder)
        dialog.accept()
        assert dialog.settings_recorder.recording
        assert not dialog._settings_record_timer.isActive()
        return dialog.result()

    def second_dialog(dialog):
        assert dialog.settings_recorder is recordings[0]
        assert dialog.record_settings_action.isChecked()
        assert dialog.settings_recorder.changes()["lyrics_global"]["font_size"] == 43
        dialog.global_controls["font_size"].setValue(48)
        dialog.record_settings_action.trigger()
        dialog.copy_factory_defaults_action.trigger()
        assert json.loads(QApplication.clipboard().text())["settings"]["lyrics_global"]["font_size"] == 48
        dialog.reject()
        return dialog.result()

    try:
        monkeypatch.setattr(SettingsDialog, "exec", first_dialog)
        window.open_settings()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert window.findChildren(SettingsDialog) == []
        assert window.global_lyrics_settings["font_size"] == 43
        monkeypatch.setattr(SettingsDialog, "exec", second_dialog)
        window.open_settings()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert window.findChildren(SettingsDialog) == []
        assert window.global_lyrics_settings["font_size"] == 43
        assert settings.get_global_lyrics_settings()["font_size"] == 43
        assert not recordings[0].recording
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
