"""Shuffle survives restarts and settings changes without losing its mode."""

import pytest
from PySide6.QtWidgets import QDialog

from core.settings import get_playback_settings, save_all_settings, save_playback_settings
from ui.main_window import Player
from ui.settings_dialog import SettingsDialog


def close_player(window, qapp):
    window.close()
    window.deleteLater()
    qapp.processEvents()


@pytest.mark.parametrize("mode", ["tracks", "albums"])
@pytest.mark.parametrize("enabled", [None, False, True])
def test_startup_restores_shuffle_and_defaults_older_settings_to_off(qapp, mode, enabled):
    values = {"shuffle_mode": mode, "volume": 43}
    if enabled is not None:
        values["shuffle_enabled"] = enabled
    save_all_settings({"playback": values})
    window = Player()
    try:
        assert window.shuffle_enabled is bool(enabled)
        assert window.playback_settings["shuffle_enabled"] is bool(enabled)
        assert window.shuffle_button.text() == (f"Shuffle: {mode.title()}" if enabled else "Shuffle: Off")
    finally:
        close_player(window, qapp)


@pytest.mark.parametrize("mode", ["tracks", "albums"])
def test_toggles_save_immediately_and_survive_two_restarts(qapp, mode):
    save_playback_settings({"shuffle_mode": mode, "play_mode": "playlist", "volume": 43})
    window = Player()
    try:
        for enabled in (True, False):
            window.shuffle_button.click()
            saved = get_playback_settings()
            assert saved["shuffle_enabled"] is enabled
            assert saved["shuffle_mode"] == mode
            assert saved["play_mode"] == "playlist"
            assert saved["volume"] == 43
            close_player(window, qapp)
            window = Player()
            assert window.shuffle_enabled is enabled
            assert window.shuffle_button.text() == (f"Shuffle: {mode.title()}" if enabled else "Shuffle: Off")
    finally:
        close_player(window, qapp)


@pytest.mark.parametrize("enabled", [False, True])
def test_applying_other_settings_preserves_shuffle(qapp, monkeypatch, enabled):
    save_playback_settings({"shuffle_enabled": enabled, "shuffle_mode": "albums", "volume": 43})
    window = Player()

    def accept_dialog(dialog):
        dialog.after_playback_combo.setCurrentIndex(dialog.after_playback_combo.findData("stop"))
        return QDialog.DialogCode.Accepted

    monkeypatch.setattr(SettingsDialog, "exec", accept_dialog)
    try:
        window.open_settings()
        saved = get_playback_settings()
        assert saved["shuffle_enabled"] is enabled
        assert saved["shuffle_mode"] == "albums"
        assert saved["after_playback"] == "stop"
        assert window.shuffle_enabled is enabled
        assert window.shuffle_button.text() == ("Shuffle: Albums" if enabled else "Shuffle: Off")
    finally:
        close_player(window, qapp)


def test_shuffle_toggle_updates_next_button_at_end_of_queue(qapp, tmp_path):
    save_playback_settings({"play_mode": "playlist", "volume": 43})
    paths = [tmp_path / "first.mp3", tmp_path / "last.mp3"]
    for path in paths:
        path.touch()
    window = Player()
    try:
        window.playlist = paths
        window.current_index = 1
        window.current_audio_path = paths[-1]
        window.update_transport_buttons()
        assert not window.next_button.isEnabled()
        window.shuffle_button.click()
        assert window.next_button.isEnabled()
        window.shuffle_button.click()
        assert not window.next_button.isEnabled()
    finally:
        close_player(window, qapp)
