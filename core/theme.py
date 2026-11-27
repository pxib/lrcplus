import json
import re
from copy import deepcopy
from pathlib import Path

from PySide6.QtGui import QColor, QGuiApplication, QPalette


THEME_COLOR_FIELDS = {
    "window": "Window", "header": "Header", "surface": "Surface",
    "surface_alt": "Alternate surface", "raised": "Raised controls",
    "raised_hover": "Hovered controls", "pressed": "Pressed controls",
    "text": "Text", "muted": "Muted text", "brand": "Brand text",
    "border": "Borders", "field_border": "Field borders",
    "header_button_text": "Header text", "dock": "Player background",
    "dock_border": "Player border", "dock_control": "Player controls",
    "dock_control_hover": "Player controls on hover",
    "dock_control_border": "Player control border", "dock_disabled": "Disabled player controls",
    "dock_text": "Player text", "dock_muted": "Muted player text",
    "seek_track": "Seek track", "slider_handle": "Slider handle",
    "scroll_handle": "Scroll handle",
    "selection": "Cell selection color",
    "item_hover": "Cell hover color",
}


def normalize_theme_overrides(value):
    """Keep only supported per-mode, opaque RGB color overrides."""
    if not isinstance(value, dict):
        return {}
    result = {}
    for mode in ("light", "dark"):
        colors = value.get(mode)
        if not isinstance(colors, dict):
            continue
        valid = {key: color.lower() for key, color in colors.items()
                 if key in THEME_COLOR_FIELDS and isinstance(color, str)
                 and re.fullmatch(r"#[0-9a-fA-F]{6}", color)}
        if valid:
            result[mode] = valid
    return result


def _relative_luminance(color):
    channels = []
    for value in (color.red(), color.green(), color.blue()):
        normalized = value / 255
        channels.append(
            normalized / 12.92
            if normalized <= 0.04045
            else ((normalized + 0.055) / 1.055) ** 2.4
        )
    return sum(value * weight for value, weight in zip(channels, (0.2126, 0.7152, 0.0722)))


def build_theme_palette(mode, accent_color, surface_style="classic", overrides=None):
    if mode == "qt":
        palette = QGuiApplication.palette()
        roles = {
            "Window": ("window", "header", "dock"),
            "Base": ("surface",),
            "AlternateBase": ("surface_alt",),
            "Button": ("raised", "raised_hover", "pressed", "dock_control", "dock_control_hover", "dock_disabled"),
            "WindowText": ("text", "brand", "header_button_text", "dock_text", "slider_handle"),
            "PlaceholderText": ("muted", "dock_muted"),
            "Mid": ("border", "field_border", "dock_border", "dock_control_border", "seek_track", "scroll_handle"),
            "Highlight": ("selection", "item_hover"),
        }
        colors = {key: palette.color(getattr(QPalette.ColorRole, role)).name()
                  for role, keys in roles.items() for key in keys}
        result = build_theme_palette("light", palette.color(QPalette.ColorRole.Highlight).name(),
                                     overrides={"light": colors})
        result["selection_text"] = palette.color(QPalette.ColorRole.HighlightedText).name()
        result["mode"] = "qt"
        return result
    dark = str(mode).casefold() == "dark"
    accent = QColor(str(accent_color))
    if not accent.isValid():
        accent = QColor("#3a9879")

    if dark:
        surface = "#202724"
        accent_soft_base = QColor("#202724")
        accent_ratio = 0.28
        accent_hover = accent.lighter(118).name()
        values = {
            "window": "#171c1a",
            "header": "#171c1a",
            "surface": surface,
            "surface_alt": "#252e2a",
            "raised": "#29332e",
            "raised_hover": "#303b35",
            "pressed": "#36423b",
            "text": "#e2eae5",
            "muted": "#a3b1aa",
            "brand": "#eff7f2",
            "border": "#39463f",
            "field_border": "#46554c",
            "header_button_text": "#dce7e0",
            "dock": "#111714",
            "dock_border": "#303e36",
            "dock_control": "#25312b",
            "dock_control_hover": "#303d35",
            "dock_control_border": "#39473f",
            "dock_disabled": "#1b2420",
            "dock_text": "#dce9e2",
            "dock_muted": "#84968e",
            "seek_track": "#39473f",
            "slider_handle": "#e9f0eb",
            "scroll_handle": "#52635a",
        }
    else:
        surface = "#ffffff"
        accent_soft_base = QColor("#ffffff")
        accent_ratio = 0.12
        accent_hover = accent.darker(108).name()
        values = {
            "window": "#eef2ef",
            "header": "#eef2ef",
            "surface": surface,
            "surface_alt": "#f4f8f6",
            "raised": "#e1eae5",
            "raised_hover": "#f7faf8",
            "pressed": "#e5eee9",
            "text": "#1b302a",
            "muted": "#53665e",
            "brand": "#18352d",
            "border": "#d3ded8",
            "field_border": "#cbd8d1",
            "header_button_text": "#30463e",
            "dock": "#20372f",
            "dock_border": "#365348",
            "dock_control": "#304b40",
            "dock_control_hover": "#3a5b4d",
            "dock_control_border": "#466558",
            "dock_disabled": "#293f36",
            "dock_text": "#dce9e2",
            "dock_muted": "#84968e",
            "seek_track": "#496258",
            "slider_handle": "#f5f7f5",
            "scroll_handle": "#bdcec5",
        }

    if surface_style == "neutral":
        for key, value in values.items():
            color = QColor(value)
            gray = round(0.2126 * color.red() + 0.7152 * color.green() + 0.0722 * color.blue())
            values[key] = QColor(gray, gray, gray).name()
        accent_soft_base = QColor(values["surface"])

    mode_name = "dark" if dark else "light"
    values.update(normalize_theme_overrides(overrides).get(mode_name, {}))
    accent_soft_base = QColor(values["surface"])

    values["accent"] = accent.name()
    values["accent_hover"] = accent_hover
    values["accent_border"] = accent.lighter(135).name()
    dark_text = QColor("#173d31")
    accent_luminance = _relative_luminance(accent)
    white_contrast = 1.05 / (accent_luminance + 0.05)
    dark_contrast = (accent_luminance + 0.05) / (
        _relative_luminance(dark_text) + 0.05
    )
    values["accent_text"] = (
        "#ffffff" if white_contrast >= dark_contrast else dark_text.name()
    )
    values["accent_soft"] = QColor(
        round(accent_soft_base.red() * (1 - accent_ratio) + accent.red() * accent_ratio),
        round(accent_soft_base.green() * (1 - accent_ratio) + accent.green() * accent_ratio),
        round(accent_soft_base.blue() * (1 - accent_ratio) + accent.blue() * accent_ratio),
    ).name()
    values.setdefault("selection", values["accent_soft"])
    values.setdefault("item_hover", values["raised_hover"])
    selection_color = QColor(values["selection"])
    selection_luminance = _relative_luminance(selection_color)
    white_selection_contrast = 1.05 / (selection_luminance + 0.05)
    dark_selection_contrast = (selection_luminance + 0.05) / (
        _relative_luminance(dark_text) + 0.05
    )
    values["selection_text"] = (
        "#ffffff" if white_selection_contrast >= dark_selection_contrast else dark_text.name()
    )
    values["mode"] = "dark" if dark else "light"
    # Keep practice markers distinct and visible against custom player colors.
    background = QColor(values["dock"])
    hue = accent.hsvHueF()
    secondary = QColor.fromHsvF(((hue if hue >= 0 else 0.15) + 0.42) % 1,
                                max(0.55, accent.hsvSaturationF()),
                                max(0.65, accent.valueF()))
    background_luminance = _relative_luminance(background)
    for role, marker in (("loop_start", QColor(accent)), ("loop_end", secondary)):
        target = QColor("#ffffff" if background_luminance < 0.179 else "#000000")
        for _ in range(20):
            luminance = _relative_luminance(marker)
            contrast = (max(luminance, background_luminance) + 0.05) / (min(luminance, background_luminance) + 0.05)
            if contrast >= 3:
                break
            marker = QColor(*(round(a * 0.9 + b * 0.1) for a, b in zip(
                (marker.red(), marker.green(), marker.blue()),
                (target.red(), target.green(), target.blue()))))
        values[role] = marker.name()
    return values


def load_theme_color_presets():
    """Read named accent colors from the repository's shared theme reference."""
    path = Path(__file__).resolve().parents[1] / "themedata" / "optionalthemes.json"
    try:
        with path.open(encoding="utf-8") as theme_file:
            themes = json.load(theme_file).get("themes", [])
    except (OSError, ValueError, AttributeError):
        return []
    return [
        (theme["name"], theme)
        for theme in themes
        if isinstance(theme, dict)
        and isinstance(theme.get("name"), str)
        and all(isinstance(theme.get(key), str) for key in (
            "background", "surface", "accent", "text", "secondaryText"
        ))
    ]


def _blend_theme_colors(first, second, amount):
    first, second = QColor(first), QColor(second)
    return QColor(
        round(first.red() * (1 - amount) + second.red() * amount),
        round(first.green() * (1 - amount) + second.green() * amount),
        round(first.blue() * (1 - amount) + second.blue() * amount),
    ).name()


def apply_theme_preset(appearance, preset):
    """Apply a preset using the same color mapping as the Settings UI."""
    result = deepcopy(appearance)
    if isinstance(preset, str):
        # Core color presets cover the whole UI and replace the previous theme.
        # Start with neutral surfaces so every hue has its own coherent palette.
        overrides = {}
        foreground_roles = {
            "text", "muted", "brand", "header_button_text", "dock_text",
            "dock_muted", "slider_handle",
        }
        for mode in ("light", "dark"):
            base = build_theme_palette(mode, preset, "neutral")
            colors = {}
            for role in THEME_COLOR_FIELDS:
                if role in foreground_roles:
                    amount = 0.08
                elif role.startswith("dock") or role in {"selection", "item_hover"}:
                    amount = 0.30
                elif role in {"surface", "surface_alt"}:
                    amount = 0.12 if mode == "light" else 0.20
                else:
                    amount = 0.20 if mode == "light" else 0.26
                colors[role] = _blend_theme_colors(base[role], preset, amount)
            overrides[mode] = colors
        result.update(theme_color=preset, theme_preset_name=None,
                      theme_overrides=overrides)
        return result
    mode = "dark" if QColor(preset["background"]).lightness() < 128 else "light"
    background, surface, accent, text, secondary = (QColor(preset[key]) for key in
        ("background", "surface", "accent", "text", "secondaryText"))
    dock = background if mode == "dark" else text
    dock_control = surface if mode == "dark" else dock.lighter(135)
    dock_text = text if mode == "dark" else surface
    raised_base = surface if mode == "dark" else background
    overrides = normalize_theme_overrides(result.get("theme_overrides"))
    colors = overrides.setdefault(mode, {})
    colors.update({
        "window": background.name(), "header": background.name(), "surface": surface.name(),
        "surface_alt": _blend_theme_colors(background, surface, 0.55),
        "raised": _blend_theme_colors(raised_base, text, 0.10),
        "raised_hover": _blend_theme_colors(raised_base, accent, 0.10),
        "pressed": _blend_theme_colors(raised_base, accent, 0.20),
        "text": text.name(), "brand": text.name(), "muted": secondary.name(),
        "border": _blend_theme_colors(background, text, 0.16),
        "field_border": _blend_theme_colors(background, text, 0.22),
        "header_button_text": text.name(), "dock": dock.name(),
        "dock_border": dock.lighter(150).name(), "dock_control": dock_control.name(),
        "dock_control_hover": dock_control.lighter(115).name(),
        "dock_control_border": dock_control.lighter(130).name(),
        "dock_disabled": dock.darker(110).name(), "dock_text": dock_text.name(),
        "dock_muted": secondary.name(), "seek_track": accent.name(),
        "slider_handle": dock_text.name(), "scroll_handle": accent.name(),
    })
    for key, values in normalize_theme_overrides(preset.get("advancedColors", {})).items():
        overrides.setdefault(key, {}).update(values)
    result.update(theme_preset_name=preset["name"], theme_mode=mode,
                  theme_color=accent.name(), theme_overrides=overrides)
    return result
