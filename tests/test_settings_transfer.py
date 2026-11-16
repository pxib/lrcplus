import json

import pytest
from PySide6.QtWidgets import QCheckBox, QDialog

from core import settings
from core.settings_transfer import export_settings, get_factory_settings, import_settings, merge_settings
from ui.settings_dialog import SettingsDialog


def test_export_round_trip_excludes_library_history_and_transport_state(tmp_path):
    path = tmp_path / "preferences.json"
    payload = {
        "appearance": {"theme_mode": "dark", "theme_color": "#ABCDEF"},
        "playback": {"volume": 210, "speed_percent": 150, "gapless_playback": True},
        "lyrics_global": {"font_family": "日本語", "font_size": 44},
        "updates": {"channel": "prerelease", "last_checked": "yesterday"},
        "plugin_settings": {"example": {"nested": {"enabled": True}}},
        "disabled_plugins": ["example"], "lrc_folders": ["C:/歌詞"],
        "music_library": {"tracks": ["song.mp3"]}, "recent_files": ["song.mp3"],
        "playlists": ["favorites"], "lyrics_local": {"song.mp3": {"font_size": 60}},
    }
    export_settings(path, payload)
    document = json.loads(path.read_text(encoding="utf-8"))
    assert document["format"] == "lrcplus-settings" and document["version"] == 1
    restored = import_settings(path)
    assert restored == {
        "appearance": {"theme_mode": "dark", "theme_color": "#abcdef"},
        "playback": {"gapless_playback": True},
        "lyrics_global": {"font_family": "日本語", "font_size": 44},
        "updates": {"channel": "prerelease"},
        "plugin_settings": payload["plugin_settings"],
        "disabled_plugins": ["example"], "lrc_folders": ["C:/歌詞"],
    }
    assert not settings.SETTINGS_PATH.exists()


@pytest.mark.parametrize("text", [
    "{", "[]", "{}", '{"appearance": []}', '{"playback": {"gapless_playback": "false"}}',
    '{"lyrics_global": {"font_size": []}}', '{"lrc_folders": [42]}',
    '{"plugin_settings": {"example": false}}', '{"appearance": {"background_blur": NaN}}',
    '{"format": "another-app", "version": 1, "settings": {}}',
    '{"format": "lrcplus-settings", "version": 2, "settings": {}}',
    '{"format": "lrcplus-settings", "version": true, "settings": {}}',
])
def test_invalid_imports_do_not_touch_settings(tmp_path, text):
    settings.save_all_settings({"startup": {"restore_playlist": True}})
    before = settings.SETTINGS_PATH.read_bytes()
    source = tmp_path / "invalid.json"
    source.write_text(text, encoding="utf-8")
    with pytest.raises(ValueError):
        import_settings(source)
    assert settings.SETTINGS_PATH.read_bytes() == before


def test_legacy_file_partial_import_preserves_existing_fields_and_plugin_choices(tmp_path):
    source = tmp_path / "old-settings.json"
    source.write_text(json.dumps({"playback": {"crossfade_seconds": 999},
                                 "plugin_settings": {"example": {"size": 14}},
                                 "unknown": True}), encoding="utf-8-sig")
    incoming = import_settings(source)
    original = {"playback": {"volume": 42, "shuffle_mode": "albums"},
                "plugin_settings": {"example": {"color": "red"}, "other": {"on": True}},
                "music_library": {"tracks": ["current.mp3"]}}
    merged = merge_settings(original, incoming)
    assert merged["playback"] == {"volume": 42, "shuffle_mode": "albums", "crossfade_seconds": 10}
    assert merged["plugin_settings"] == {"example": {"color": "red", "size": 14}, "other": {"on": True}}
    assert merged["music_library"] == original["music_library"]
    assert original["plugin_settings"]["example"] == {"color": "red"}


def test_export_cannot_replace_live_or_legacy_settings():
    for path in (settings.SETTINGS_PATH, settings.LEGACY_SETTINGS_PATH):
        with pytest.raises(ValueError, match="different file"):
            export_settings(path, {"startup": {"remember_window": True}})
        assert not path.exists()


def test_failed_export_preserves_existing_file(tmp_path, monkeypatch):
    path = tmp_path / "settings.json"
    path.write_text("existing backup", encoding="utf-8")
    monkeypatch.setattr("core.file_io.os.replace", lambda *args: (_ for _ in ()).throw(OSError("disk error")))
    with pytest.raises(OSError, match="disk error"):
        export_settings(path, {"startup": {"remember_window": True}})
    assert path.read_text(encoding="utf-8") == "existing backup"
    assert list(tmp_path.glob("*.tmp")) == []


def test_advanced_actions_export_drafts_and_are_searchable(qapp, tmp_path, monkeypatch):
    path = tmp_path / "preset"
    settings.save_all_settings({"plugin_settings": {"example": {"mode": "small"}}})
    monkeypatch.setattr("ui.settings_transfer.QFileDialog.getSaveFileName", lambda *args: (str(path), ""))
    dialog = SettingsDialog([], settings.get_global_lyrics_settings())
    try:
        dialog.global_controls["font_size"].setValue(48)
        dialog.export_settings_button.click()
        assert import_settings(path.with_suffix(".json"))["lyrics_global"]["font_size"] == 48
        assert settings.load_all_settings() == {"plugin_settings": {"example": {"mode": "small"}}}
        search = dialog.settings_search
        search.search_edit.setText("backup")
        assert search.results.count() == 1
        search.activate(search.results.item(0))
        assert dialog.category_pages.currentWidget() is dialog.advanced_tab
        assert dialog.category_list.currentItem().text() == "Advanced"
    finally:
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("accepted", [True, False])
def test_import_review_accept_and_cancel_preserve_live_data(qapp, tmp_path, monkeypatch, accepted):
    original = {"music_library": {"tracks": ["song.mp3"]}, "playback": {"volume": 45},
                "plugin_settings": {"example": {"mode": "old", "local": True}},
                "disabled_plugins": ["uninstalled"]}
    settings.save_all_settings(original)
    path = tmp_path / "preset.json"
    export_settings(path, {
        "appearance": {"theme_mode": "dark", "theme_color": "#113355", "theme_surface": "neutral"},
        "startup": {"restore_playlist": True}, "playback": {"gapless_playback": True},
        "lyrics_global": {"font_size": 50, "show_romaji": True, "show_ruby": False},
        "karaoke": {"sweep_style": "glow"}, "lrc_folders": ["D:/lyrics"],
        "plugin_settings": {"example": {"mode": "new"}}, "disabled_plugins": ["example", "uninstalled"],
    })
    monkeypatch.setattr("ui.settings_transfer.QFileDialog.getOpenFileName", lambda *args: (str(path), ""))
    dialog = SettingsDialog([], settings.get_global_lyrics_settings(), current_file="song.mp3",
                            playback_settings={"volume": 45}, local_overrides={"font_size": 29})
    toggle = QCheckBox()
    toggle.setChecked(True)
    dialog.plugin_toggles["example"] = toggle
    try:
        dialog.import_settings_button.click()
        assert settings.load_all_settings() == original
        assert dialog.get_appearance_settings()["theme_surface"] == "neutral"
        assert dialog.get_appearance_settings()["theme_mode"] == "dark"
        assert dialog.get_global_lyrics_settings()["font_size"] == 50
        assert dialog.get_global_lyrics_settings()["show_romaji"] is True
        assert dialog.get_local_lyrics_overrides() == {"font_size": 29}
        assert dialog.get_karaoke_settings()["sweep_style"] == "glow"
        assert dialog.get_playback_settings()["volume"] == 45
        assert dialog.get_folders() == ["D:/lyrics"]
        assert dialog.get_disabled_plugins() == {"example", "uninstalled"}
        dialog.global_controls["font_size"].setValue(54)
        # Changes made elsewhere while Settings is open must survive acceptance.
        latest = {**original, "recent_files": ["new.mp3"]}
        settings.save_all_settings(latest)
        if accepted:
            dialog.accept()
            assert dialog.result() == QDialog.DialogCode.Accepted
            saved = settings.load_all_settings()
            assert saved["lyrics_global"]["font_size"] == 54
            assert saved["plugin_settings"]["example"] == {"mode": "new", "local": True}
            assert saved["music_library"] == original["music_library"]
            assert saved["recent_files"] == ["new.mp3"]
            assert saved["playback"]["volume"] == 45
        else:
            dialog.reject()
            assert settings.load_all_settings() == latest
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_invalid_import_and_save_error_keep_dialog_open(qapp, tmp_path, monkeypatch):
    warnings = []
    monkeypatch.setattr("ui.settings_transfer.QMessageBox.warning", lambda *args: warnings.append(args[2]))
    path = tmp_path / "broken.json"
    path.write_text("[]", encoding="utf-8")
    monkeypatch.setattr("ui.settings_transfer.QFileDialog.getOpenFileName", lambda *args: (str(path), ""))
    dialog = SettingsDialog([], {})
    try:
        dialog.show()
        dialog.import_settings_button.click()
        assert "Could not import" in warnings[-1]
        assert dialog._imported_settings == {}
        dialog._load_imported_settings({"startup": {"restore_playlist": True}})
        monkeypatch.setattr(settings, "save_all_settings", lambda payload: (_ for _ in ()).throw(OSError("read only")))
        dialog.accept()
        assert dialog.isVisible() and dialog.result() == QDialog.DialogCode.Rejected
        assert "read only" in warnings[-1]
        assert not settings.SETTINGS_PATH.exists()
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_import_applies_to_player_and_saved_themes_survive_reopening(qapp, tmp_path, monkeypatch):
    from ui.main_window import Player

    settings.save_playback_settings({"volume": 43, "shuffle_enabled": True})
    path = tmp_path / "preset.json"
    theme = {"name": "My theme", "description": "Custom", "background": "#112233",
             "surface": "#223344", "accent": "#445566", "text": "#ffffff",
             "secondaryText": "#aaaaaa", "advancedColors": {"dark": {"border": "#778899"}}}
    export_settings(path, {
        "appearance": {"theme_mode": "dark", "theme_preset_name": "My theme", "custom_themes": [theme]},
        "playback": {"shuffle_mode": "albums", "after_playback": "stop"},
        "lyrics_global": {"font_size": 46}, "karaoke": {"sweep_style": "soft"},
    })
    window = Player()

    def finish(dialog):
        dialog._load_imported_settings(import_settings(path))
        dialog.accept()
        return dialog.result()

    monkeypatch.setattr(SettingsDialog, "exec", finish)
    try:
        window.open_settings()
        assert window.appearance_settings["theme_mode"] == "dark"
        assert window.appearance_settings["custom_themes"] == [theme]
        assert window.playback_settings["volume"] == 43
        assert window.shuffle_enabled is True
        assert window.playback_settings["shuffle_mode"] == "albums"
        assert window.playback_settings["after_playback"] == "stop"
        assert window.global_lyrics_settings["font_size"] == 46
        assert window.karaoke_settings["sweep_style"] == "soft"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
    reopened = SettingsDialog([], settings.get_global_lyrics_settings(),
                              appearance_settings=settings.get_appearance_settings())
    try:
        assert reopened.theme_preset_combo.currentText() == "My theme"
        assert reopened.get_appearance_settings()["custom_themes"] == [theme]
    finally:
        reopened.deleteLater()
        qapp.processEvents()


def test_import_preserves_forced_continuous_mode_for_plain_lyrics(qapp):
    dialog = SettingsDialog([], settings.get_global_lyrics_settings(), current_file="lyrics.txt")
    try:
        dialog._load_imported_settings({"lyrics_global": {"scrolling_mode": "automatic"}})
        assert dialog.get_global_lyrics_settings()["scrolling_mode"] == "continuous"
        assert not dialog.global_controls["scrolling_mode"].isEnabled()
    finally:
        dialog.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("accepted", [True, False])
def test_factory_restore_reviews_defaults_and_preserves_personal_data(qapp, accepted):
    defaults = get_factory_settings()
    theme = {"name": "Keep me", "description": "Custom", "background": "#112233",
             "surface": "#223344", "accent": "#445566", "text": "#ffffff",
             "secondaryText": "#aaaaaa", "advancedColors": {}}
    original = {
        "appearance": {"theme_mode": "dark" if defaults["appearance"]["theme_mode"] == "light" else "light",
                       "custom_themes": [theme], "custom_background_folders": ["D:/backgrounds"]},
        "startup": {"remember_window": not defaults["startup"]["remember_window"]},
        "lyrics_global": {"font_size": defaults["lyrics_global"]["font_size"] + 1},
        "playback": {"volume": 43, "gapless_playback": not defaults["playback"]["gapless_playback"]},
        "lrc_folders": ["D:/lyrics"], "disabled_plugins": ["example"],
        "plugin_settings": {"example": {"size": 12}},
        "music_library": {"tracks": ["song.mp3"]}, "recent_files": ["song.mp3"],
        "lyrics_local": {"song.mp3": {"font_size": 28}},
    }
    settings.save_all_settings(original)
    dialog = SettingsDialog(
        original["lrc_folders"], settings.get_global_lyrics_settings(),
        startup_settings=settings.get_startup_settings(),
        appearance_settings=settings.get_appearance_settings(),
        playback_settings=settings.get_playback_settings(),
        current_file="song.mp3", local_overrides={"font_size": 28},
    )
    try:
        dialog.restore_defaults_button.click()
        assert settings.load_all_settings() == original
        assert dialog.get_startup_settings() == defaults["startup"]
        assert dialog.get_global_lyrics_settings() == defaults["lyrics_global"]
        assert dialog.get_karaoke_settings() == defaults["karaoke"]
        for key, value in defaults["appearance"].items():
            assert dialog.get_appearance_settings()[key] == value
        assert dialog.get_appearance_settings()["custom_themes"] == [theme]
        assert dialog.get_appearance_settings()["custom_background_folders"] == ["D:/backgrounds"]
        assert dialog.get_playback_settings()["volume"] == 43
        assert dialog.get_folders() == ["D:/lyrics"]
        assert dialog.get_local_lyrics_overrides() == {"font_size": 28}
        assert dialog.get_disabled_plugins() == {"example"}
        if accepted:
            dialog.accept()
            saved = settings.load_all_settings()
            assert saved["startup"] == defaults["startup"]
            assert saved["lyrics_global"] == defaults["lyrics_global"]
            for key in ("music_library", "recent_files", "lyrics_local", "lrc_folders",
                        "disabled_plugins", "plugin_settings"):
                assert saved[key] == original[key]
        else:
            dialog.reject()
            assert settings.load_all_settings() == original
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_factory_restore_is_searchable_and_waits_for_dependency_operations(qapp, monkeypatch):
    dialog = SettingsDialog([], settings.get_global_lyrics_settings())
    try:
        search = dialog.settings_search
        search.search_edit.setText("factory reset")
        assert search.results.count() == 1
        search.activate(search.results.item(0))
        assert dialog.category_pages.currentWidget() is dialog.advanced_tab
        before = dialog.get_appearance_settings()
        monkeypatch.setattr(type(dialog.dependencies_tab), "busy", property(lambda self: True))
        dialog.restore_defaults_button.click()
        assert "Wait for the dependency operation" in dialog.settings_transfer_status.text()
        assert dialog.get_appearance_settings() == before
        assert dialog._imported_settings == {}
    finally:
        dialog.deleteLater()
        qapp.processEvents()
