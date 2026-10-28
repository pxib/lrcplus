from core.settings import get_appearance_settings
from core.theme import build_theme_palette


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