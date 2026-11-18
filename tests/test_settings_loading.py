"""System enumeration must not delay opening or saving Settings."""

from types import SimpleNamespace

from PySide6.QtCore import Qt
from PySide6.QtTest import QSignalSpy, QTest

from core.settings import get_global_lyrics_settings
from ui.settings_dialog import SettingsDialog


def test_open_and_save_settings_without_enumerating_fonts_or_devices(qapp, monkeypatch):
    def unexpected_scan():
        raise AssertionError("Settings should enumerate choices only on interaction")

    monkeypatch.setattr("ui.settings_dialog.QFontDatabase.families", unexpected_scan)
    monkeypatch.setattr("ui.settings_dialog.QMediaDevices.audioOutputs", unexpected_scan)
    values = dict(get_global_lyrics_settings(), font_family="Saved font")
    dialog = SettingsDialog(
        [], values, current_file="song.mp3",
        local_overrides={"font_family": "Local font"},
        playback_settings={"output_device": "deadbeef"},
    )
    try:
        dialog.show()
        qapp.processEvents()
        dialog.restore_playlist_checkbox.setChecked(True)
        assert dialog.get_startup_settings()["restore_playlist"] is True
        assert dialog.get_global_lyrics_settings() == values
        assert dialog.get_local_lyrics_overrides() == {"font_family": "Local font"}
        assert dialog.get_playback_settings()["output_device"] == "deadbeef"
        dialog._load_imported_settings({
            "lyrics_global": {"font_family": "Imported font"},
            "playback": {"output_device": "cafebabe"},
        })
        assert dialog.get_global_lyrics_settings()["font_family"] == "Imported font"
        assert dialog.get_playback_settings()["output_device"] == "cafebabe"
        assert dialog.get_local_lyrics_overrides() == {"font_family": "Local font"}
        dialog.accept()
        assert dialog.result() == dialog.DialogCode.Accepted
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_font_choices_load_once_for_both_scopes_and_support_keyboard(qapp, monkeypatch):
    scans = []

    def fonts():
        scans.append(True)
        return ["First font", "Saved font", "Last font"]

    monkeypatch.setattr("ui.settings_dialog.QFontDatabase.families", fonts)
    dialog = SettingsDialog(
        [], dict(get_global_lyrics_settings(), font_family="Saved font"),
        current_file="song.mp3", local_overrides={"font_family": "Local font"},
    )
    try:
        combo = dialog.global_controls["font_family"]
        changed = QSignalSpy(combo.currentIndexChanged)
        combo.showPopup()
        combo.hidePopup()
        assert scans == [True]
        assert changed.count() == 0
        assert combo.currentData() == "Saved font"
        assert combo.currentText() == "Saved font"
        assert combo.count() == 4
        QTest.keyClick(combo, Qt.Key.Key_End)
        assert dialog.get_global_lyrics_settings()["font_family"] == "Last font"
        combo.showPopup()
        combo.hidePopup()
        local = dialog.local_controls["font_family"][1]
        local.showPopup()
        local.hidePopup()
        assert scans == [True]
        assert local.findData("Last font") >= 0
        assert dialog.get_local_lyrics_overrides() == {"font_family": "Local font"}
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_audio_choices_resolve_saved_name_without_changing_selection(qapp, monkeypatch):
    scans = []

    def outputs():
        scans.append(True)
        return [SimpleNamespace(description=lambda: "Speakers", id=lambda: b"\xde\xad")]

    monkeypatch.setattr("ui.settings_dialog.QMediaDevices.audioOutputs", outputs)
    dialog = SettingsDialog([], {}, playback_settings={"output_device": "dead"})
    try:
        combo = dialog.output_device_combo
        changed = QSignalSpy(combo.currentIndexChanged)
        assert scans == []
        combo.showPopup()
        combo.hidePopup()
        assert scans == [True]
        assert changed.count() == 0
        assert combo.currentText() == "Speakers"
        assert dialog.get_playback_settings()["output_device"] == "dead"
        combo.setCurrentIndex(0)
        assert dialog.get_playback_settings()["output_device"] == ""
        combo.showPopup()
        combo.hidePopup()
        assert scans == [True]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
