import re

from PySide6.QtGui import QColor


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
    return values
