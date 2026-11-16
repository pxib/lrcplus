from copy import deepcopy

from core import settings
from core.factory_defaults import FACTORY_DEFAULTS
from core.settings_transfer import get_factory_settings
from core.theme import build_theme_palette
from ui.settings_dialog import SettingsDialog


def test_fresh_settings_and_restore_share_selected_factory_baseline():
    assert settings.load_all_settings() == {}
    assert settings.get_startup_settings()["restore_playlist"] is True
    assert settings.get_statistics_settings() == {"enabled": False}
    assert settings.get_update_settings()["automatic_check"] is False
    appearance = settings.get_appearance_settings()
    assert appearance["theme_mode"] == "dark"
    assert appearance["theme_color"] == "#88c0d0"
    assert appearance["theme_preset_name"] == "Nordic Frost"
    assert appearance["background_blur"] == 0
    assert appearance["theme_overrides"] == FACTORY_DEFAULTS["appearance"]["theme_overrides"]
    defaults = get_factory_settings()
    for section, values in FACTORY_DEFAULTS.items():
        for key, value in values.items():
            assert defaults[section][key] == value
    # Unselected values retain their original built-in defaults.
    assert defaults["startup"]["remember_window"] is True
    assert defaults["playback"]["play_mode"] == "library"
    assert defaults["lyrics_global"]["font_size"] == 32
    assert defaults["lyrics_global"]["scrolling_lyrics"] is False
    assert not settings.SETTINGS_PATH.exists()


def test_recorded_palettes_apply_exactly_and_cannot_mutate_defaults():
    original = deepcopy(FACTORY_DEFAULTS)
    appearance = settings.get_appearance_settings({})
    for mode in ("light", "dark"):
        palette = build_theme_palette(mode, appearance["theme_color"],
                                      appearance["theme_surface"], appearance["theme_overrides"])
        for key, value in FACTORY_DEFAULTS["appearance"]["theme_overrides"][mode].items():
            assert palette[key] == value
    appearance["theme_overrides"]["dark"]["window"] = "#000000"
    assert settings.get_appearance_settings({})["theme_overrides"]["dark"]["window"] == "#242c3a"
    assert FACTORY_DEFAULTS == original


def test_explicit_saved_choices_override_factory_baseline_without_migration():
    payload = {
        "startup": {"restore_playlist": False},
        "appearance": {"theme_mode": "light", "theme_color": "#3a9879",
                       "theme_preset_name": "", "theme_overrides": {}, "background_blur": 12},
        "listening_statistics": {"enabled": True}, "updates": {"automatic_check": True},
    }
    settings.save_all_settings(payload)
    before = settings.SETTINGS_PATH.read_bytes()
    assert settings.get_startup_settings()["restore_playlist"] is False
    assert settings.get_statistics_settings() == {"enabled": True}
    assert settings.get_update_settings()["automatic_check"] is True
    appearance = settings.get_appearance_settings()
    for key, value in payload["appearance"].items():
        assert appearance[key] == value
    assert settings.SETTINGS_PATH.read_bytes() == before


def test_settings_dialog_shows_factory_values_and_restore_uses_recorded_colors(qapp):
    dialog = SettingsDialog([], settings.get_global_lyrics_settings())
    try:
        assert dialog.restore_playlist_checkbox.isChecked()
        assert not dialog.automatic_update_checkbox.isChecked()
        assert not dialog.statistics_enabled_checkbox.isChecked()
        assert dialog.theme_preset_combo.currentText() == "Nordic Frost"
        assert dialog.theme_mode == "dark"
        assert dialog.background_blur_slider.value() == 0
        dialog.light_theme_button.click()
        dialog.background_blur_slider.setValue(17)
        dialog.automatic_update_checkbox.setChecked(True)
        dialog.restore_defaults_button.click()
        for key, value in FACTORY_DEFAULTS["appearance"].items():
            assert dialog.get_appearance_settings()[key] == value
        assert not dialog.get_update_settings()["automatic_check"]
        assert not settings.SETTINGS_PATH.exists()
        dialog.reject()
        assert not settings.SETTINGS_PATH.exists()
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_player_starts_with_selected_defaults_and_statistics_disabled(qapp):
    from ui.main_window import Player

    window = Player()
    try:
        assert window.startup_settings["restore_playlist"] is True
        assert window.update_settings["automatic_check"] is False
        assert window.statistics.enabled is False
        assert window.appearance_settings["theme_mode"] == "dark"
        assert window.appearance_settings["theme_overrides"]["light"]["window"] == "#e7d6c7"
        assert window.appearance_settings["theme_overrides"]["dark"]["window"] == "#242c3a"
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
