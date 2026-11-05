"""Portable lyric-card style values stored in the application's preferences."""

from dataclasses import asdict

from PySide6.QtGui import QColor

from core.settings import load_all_settings, save_all_settings
from ui.lyrics_card_renderer import CardStyle, CARD_SIZES, CARD_THEMES, CARD_TEMPLATES, WATERMARK_POSITIONS, DETAILS_PLACEMENTS


PRESETS_KEY = "lyrics_card_presets"
STYLE_RANGES = {
    "font_size": (20, 96), "gradient_angle": (0, 360), "image_blur": (0, 40),
    "image_dim": (0, 90), "image_zoom": (100, 250), "crop_x": (0, 100),
    "crop_y": (0, 100), "padding": (48, 160), "line_spacing": (100, 200),
    "vertical_position": (0, 100),
    "cover_size": (96, 240),
    "stroke_width": (1, 12), "shadow_opacity": (0, 100), "shadow_blur": (0, 30),
    "shadow_offset_x": (-40, 40), "shadow_offset_y": (-40, 40),
    "watermark_width": (80, 320), "watermark_opacity": (0, 100),
    "furigana_scale": (25, 80),
}


def style_from_preset(values):
    """Validate stored preferences without trusting their types or enum values."""
    if not isinstance(values, dict):
        raise ValueError("Invalid card preset.")
    defaults = CardStyle()
    result = {}
    for key, choices in (("theme", CARD_THEMES), ("template", CARD_TEMPLATES),
                         ("background_mode", ("Gradient", "Solid", "Image")),
                         ("alignment", ("left", "center", "right")),
                         ("details_placement", DETAILS_PLACEMENTS.values()),
                         ("watermark_position", WATERMARK_POSITIONS)):
        value = values.get(key)
        if isinstance(value, str) and value in choices:
            result[key] = value
    size = (values.get("width"), values.get("height"))
    if all(type(dimension) is int for dimension in size) and size in CARD_SIZES.values():
        result.update(width=size[0], height=size[1])
    for key, (minimum, maximum) in STYLE_RANGES.items():
        value = values.get(key, getattr(defaults, key))
        if type(value) is int:
            result[key] = max(minimum, min(maximum, value))
    for key, maximum in (("font_family", 200), ("image_path", 4096)):
        value = values.get(key)
        if isinstance(value, str):
            result[key] = value[:maximum]
    for key in ("centered", "show_album_cover", "bold", "italic", "underline", "strikeout",
                "stroke_enabled", "shadow_enabled", "watermark_enabled", "preserve_lrcx_formatting", "include_furigana"):
        if isinstance(values.get(key), bool):
            result[key] = values[key]
    for key in ("stroke_color", "shadow_color"):
        value = values.get(key)
        if isinstance(value, str) and QColor(value).isValid():
            result[key] = QColor(value).name()
    colors = values.get("colors")
    if (isinstance(colors, (list, tuple)) and len(colors) == 6
            and all(isinstance(color, str) and QColor(color).isValid() for color in colors)):
        result["colors"] = tuple(QColor(color).name() for color in colors)
    # Titles, credits, albums and lyric selections belong to the current song.
    return CardStyle(**result)


def _payload(style):
    result = asdict(style)
    result.pop("title")
    result.pop("credit")
    result.pop("album")
    return result


def load_card_presets():
    values = load_all_settings().get(PRESETS_KEY, {})
    if not isinstance(values, dict):
        return {}
    return {name: _payload(style_from_preset(value)) for name, value in values.items()
            if isinstance(name, str) and name.strip() and len(name) <= 80 and isinstance(value, dict)}


def save_card_preset(name, style):
    name = name.strip()
    if not name or len(name) > 80:
        raise ValueError("Choose a preset name between 1 and 80 characters.")
    settings = load_all_settings()
    presets = settings.get(PRESETS_KEY, {})
    presets = dict(presets) if isinstance(presets, dict) else {}
    presets[name] = _payload(style_from_preset(_payload(style)))
    settings[PRESETS_KEY] = presets
    save_all_settings(settings)


def delete_card_preset(name):
    settings = load_all_settings()
    presets = settings.get(PRESETS_KEY, {})
    if isinstance(presets, dict) and name in presets:
        settings[PRESETS_KEY] = {key: value for key, value in presets.items() if key != name}
        save_all_settings(settings)
