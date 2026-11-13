from core.settings import get_appearance_settings
from core.theme import build_theme_palette
import pytest


@pytest.mark.parametrize("mode", ["light", "dark"])
def test_core_presets_recolor_surfaces_and_controls_in_both_modes(mode):
    from ui.settings_dialog import apply_theme_preset

    palettes = []
    for accent in ("#3a9879", "#4477cc", "#895cc4", "#c45278", "#bd691f"):
        appearance = apply_theme_preset({"theme_mode": mode}, accent)
        assert appearance["theme_mode"] == mode
        palettes.append(build_theme_palette(
            mode, accent, overrides=appearance["theme_overrides"]))
    for role in ("window", "header", "surface", "raised", "border", "dock", "selection"):
        assert len({palette[role] for palette in palettes}) == 5


def test_core_preset_replaces_previous_theme_colors_without_mutating_preferences():
    from copy import deepcopy
    from ui.settings_dialog import apply_theme_preset

    previous = {"theme_mode": "dark", "theme_preset_name": "Sakura",
                "theme_overrides": {"light": {"window": "#ff0000"},
                                    "dark": {"surface": "#00ff00"}}}
    original = deepcopy(previous)
    selected = apply_theme_preset(previous, "#4477cc")
    fresh = apply_theme_preset({"theme_mode": "dark"}, "#4477cc")
    assert selected == fresh
    assert previous == original


def test_appearance_settings_normalize_theme_mode_and_accent():
    appearance = get_appearance_settings({
        "appearance": {
            "theme_mode": "dark",
            "theme_color": "#4477CC",
        }
    })

    assert appearance["theme_mode"] == "dark"
    assert appearance["theme_color"] == "#4477cc"


def test_invalid_theme_preferences_fall_back_to_light_defaults():
    appearance = get_appearance_settings({
        "appearance": {
            "theme_mode": "system",
            "theme_color": "not-a-color",
        }
    })

    assert appearance["theme_mode"] == "light"
    assert appearance["theme_color"] == "#3a9879"


def test_light_and_dark_palettes_retain_the_custom_accent():
    light = build_theme_palette("light", "#4477cc")
    dark = build_theme_palette("dark", "#4477cc")

    assert light["window"] != dark["window"]
    assert light["text"] != dark["text"]
    assert light["accent"] == dark["accent"] == "#4477cc"


def test_bright_accent_uses_dark_foreground_for_contrast():
    palette = build_theme_palette("light", "#ffff00")

    assert palette["accent_text"] == "#173d31"

def test_surface_palette_persists_and_invalid_values_fall_back():
    assert get_appearance_settings({"appearance": {"theme_surface": "neutral"}})["theme_surface"] == "neutral"
    assert get_appearance_settings({"appearance": {"theme_surface": []}})["theme_surface"] == "classic"


def test_neutral_surface_keeps_accent_in_both_modes():
    for mode in ("light", "dark"):
        classic = build_theme_palette(mode, "#4477cc")
        neutral = build_theme_palette(mode, "#4477cc", "neutral")
        assert neutral["window"] != classic["window"]
        assert neutral["accent"] == "#4477cc"
        assert neutral["window"][1:3] == neutral["window"][3:5] == neutral["window"][5:7]


def test_theme_controls_update_saved_preferences(qapp):
    from ui.settings_dialog import SettingsDialog

    dialog = SettingsDialog([], {}, appearance_settings={})
    try:
        dialog.theme_surface_combo.setCurrentIndex(dialog.theme_surface_combo.findData("neutral"))
        dialog._choose_theme_preset(dialog.theme_preset_combo.findData("#4477cc"))
        settings = dialog.get_appearance_settings()
        assert settings["theme_surface"] == "neutral"
        assert settings["theme_color"] == "#4477cc"
    finally:
        dialog.close()


def test_advanced_colors_are_validated_and_separate_by_mode():
    appearance = get_appearance_settings({"appearance": {"theme_overrides": {
        "light": {"window": "#AABBCC", "text": "red", "unknown": "#ffffff"},
        "dark": {"window": "#123456"},
        "other": {"window": "#ffffff"},
    }}})
    overrides = appearance["theme_overrides"]
    assert overrides == {"light": {"window": "#aabbcc"}, "dark": {"window": "#123456"}}
    assert build_theme_palette("light", "#4477cc", "classic", overrides)["window"] == "#aabbcc"
    assert build_theme_palette("dark", "#4477cc", "classic", overrides)["window"] == "#123456"


def test_advanced_colors_save_and_reset_by_mode(qapp, monkeypatch):
    from PySide6.QtGui import QColor
    from ui.settings_dialog import SettingsDialog, QColorDialog
    from core.settings import save_appearance_settings

    dialog = SettingsDialog([], {}, appearance_settings={})
    try:
        dialog.theme_role_combo.setCurrentIndex(dialog.theme_role_combo.findData("window"))
        monkeypatch.setattr(QColorDialog, "getColor", lambda *args: QColor("#123456"))
        dialog._choose_theme_role_color()
        dialog._set_theme_mode("dark")
        monkeypatch.setattr(QColorDialog, "getColor", lambda *args: QColor("#abcdef"))
        dialog._choose_theme_role_color()
        save_appearance_settings(dialog.get_appearance_settings())
        assert get_appearance_settings()["theme_overrides"] == {
            "light": {"window": "#123456"}, "dark": {"window": "#abcdef"}}
        dialog._reset_theme_role_color()
        assert dialog.get_appearance_settings()["theme_overrides"] == {"light": {"window": "#123456"}}
        dialog._set_theme_mode("light")
        dialog._reset_theme_mode_colors()
        assert dialog.get_appearance_settings()["theme_overrides"] == {}
    finally:
        dialog.close()


def test_simple_colors_and_collapsible_advanced_controls_stay_in_sync(qapp, monkeypatch):
    from PySide6.QtGui import QColor
    from ui.settings_dialog import SettingsDialog, QColorDialog

    dialog = SettingsDialog([], {}, appearance_settings={})
    try:
        assert dialog.advanced_theme_panel.isHidden()
        dialog.advanced_theme_toggle.setChecked(True)
        assert not dialog.advanced_theme_panel.isHidden()
        monkeypatch.setattr(QColorDialog, "getColor", lambda *args: QColor("#123456"))
        dialog.theme_simple_color_buttons["surface"].click()
        dialog.theme_role_combo.setCurrentIndex(dialog.theme_role_combo.findData("surface"))
        assert dialog.theme_role_color_button.text() == "#123456"
        assert dialog.get_appearance_settings()["theme_overrides"]["light"]["surface"] == "#123456"
        dialog.theme_role_reset_button.click()
        assert not dialog.theme_simple_reset_buttons["surface"].isEnabled()
        dialog.advanced_theme_toggle.setChecked(False)
        assert dialog.advanced_theme_panel.isHidden()
    finally:
        dialog.close()
