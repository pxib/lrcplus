from copy import deepcopy
from dataclasses import dataclass
from pathlib import Path

from core.factory_defaults import FACTORY_DEFAULTS


def load_all_settings():
    from core.settings import load_all_settings as load

    return load()


def save_all_settings(payload):
    from core.settings import save_all_settings as save

    return save(payload)

LYRIC_LAYER_ORDER = ("lyrics", "romaji", "translation", "furigana")

@dataclass(frozen=True)
class LyricShadow:
    color: str = "#000000"
    opacity: int = 63
    offset_x: int = 2
    offset_y: int = 2
    blur: int = 0

    @property
    def spread(self):
        return 3 * self.blur


DEFAULT_LYRIC_SHADOW = LyricShadow()
LYRIC_SHADOW_FIELDS = {
    "lyrics_shadow_opacity": ("opacity", 0, 100),
    "lyrics_shadow_offset_x": ("offset_x", -12, 12),
    "lyrics_shadow_offset_y": ("offset_y", -12, 12),
    "lyrics_shadow_blur": ("blur", 0, 6),
}


def lyric_text_effects(settings):
    shadow = LyricShadow(
        color=settings["lyrics_shadow_color"],
        **{name: settings[key] for key, (name, _, _) in LYRIC_SHADOW_FIELDS.items()},
    )
    return (
        settings["lyrics_stroke_width"], settings["lyrics_stroke_color"], settings["lyrics_shadow"],
        shadow, settings["karaoke_stroke_color"],
    )


def text_effect_padding(stroke_width, enabled, shadow):
    if enabled and shadow.opacity:
        return (
            max(stroke_width, abs(shadow.offset_x) + shadow.spread),
            max(0, shadow.spread - shadow.offset_y),
            max(0, shadow.spread + shadow.offset_y),
        )
    return stroke_width, 0, 0


def normalize_layer_order(value):
    order = []
    for layer in value if isinstance(value, (list, tuple)) else ():
        if layer in LYRIC_LAYER_ORDER and layer not in order:
            order.append(layer)
    return order + [layer for layer in LYRIC_LAYER_ORDER if layer not in order]


LYRICS_DEFAULTS = {
    "lyric_layer_order": list(LYRIC_LAYER_ORDER),
    "show_lyrics": True,
    "show_translation": True,
    "scrolling_lyrics": False,
    "lyrics_direction": "horizontal",
    "lyrics_padding": 8,
    "vertical_padding": 0,
    "ruby_padding": 2,
    "ruby_font_size": 18,
    "show_ruby": True,
    "show_romaji": False,
    "all_romaji": False,
    "ruby_position": "above",
    "furigana_parser": "yomi",
    "mecab_parse_mode": "batch",
    "limited_reading_rendering": False,
    "limited_reading_range": 10,
    "fade_in_readings": True,
    "font_family": "",
    "font_size": 32,
    "focus_transition": True,
    "lyrics_alignment": "center",
    "highlight_current": True,
    "highlight_animation": "none",
    "highlight_animation_duration": 300,
    "highlight_animation_out_duration": 300,
    "highlight_animation_easing": "out_cubic",
    "highlight_color": "#ffaa00",
    "lyrics_color": "#ffffff",
    "lyrics_stroke_width": 0,
    "lyrics_stroke_color": "#000000",
    "karaoke_stroke_color": "",
    "lyrics_shadow": False,
    "lyrics_shadow_color": DEFAULT_LYRIC_SHADOW.color,
    **{key: getattr(DEFAULT_LYRIC_SHADOW, name) for key, (name, _, _) in LYRIC_SHADOW_FIELDS.items()},
    "scrolling_mode": "automatic",
    "continuous_scroll_speed": 35,
    "continuous_scroll_duration_based": True,
    "continuous_scroll_nudge_current": True,
    "scroll_animation_speed": 300,
    "scroll_animation_easing": "out_cubic",
    "scroll_behavior": "center",
    **FACTORY_DEFAULTS.get("lyrics_global", {}),
}


def _safe_int(value, fallback):
    try:
        return int(value)
    except (TypeError, ValueError):
        return fallback


def _safe_color(value, fallback):
    if not isinstance(value, str):
        return fallback
    text = value.strip()
    if len(text) == 7 and text.startswith("#"):
        try:
            int(text[1:], 16)
            return text.lower()
        except ValueError:
            pass
    return fallback


def normalize_lyrics_settings(values):
    values = values or {}
    result = deepcopy(LYRICS_DEFAULTS)

    result["scrolling_lyrics"] = bool(
        values.get("scrolling_lyrics", result["scrolling_lyrics"])
    )

    # Older builds used lyrics_direction for left/center/right alignment.
    # Keep those settings working: migrate them to the new alignment field.
    legacy_direction = str(values.get("lyrics_direction", "")).lower()
    if legacy_direction in {"left", "center", "right"}:
        if "lyrics_alignment" not in values:
            result["lyrics_alignment"] = legacy_direction
        result["lyrics_direction"] = "horizontal"
    else:
        result["lyrics_direction"] = (
            legacy_direction
            if legacy_direction in {"horizontal", "vertical", "auto"}
            else "horizontal"
        )

    result["lyrics_padding"] = max(
        0,
        min(24, _safe_int(
            values.get("lyrics_padding"),
            result["lyrics_padding"],
        )),
    )

    result["vertical_padding"] = max(
        0,
        min(200, _safe_int(
            values.get("vertical_padding"),
            result["vertical_padding"],
        )),
    )

    result["ruby_padding"] = max(
        0,
        min(18, _safe_int(
            values.get("ruby_padding"),
            result["ruby_padding"],
        )),
    )

    result["ruby_font_size"] = max(
        6,
        min(72, _safe_int(
            values.get("ruby_font_size"),
            result["ruby_font_size"],
        )),
    )

    result["show_ruby"] = bool(
        values.get("show_ruby", result["show_ruby"])
    )
    result["show_romaji"] = bool(
        values.get("show_romaji", result["show_romaji"])
    )
    result["all_romaji"] = bool(
        values.get("all_romaji", result["all_romaji"])
    )
    ruby_position = str(values.get("ruby_position", result["ruby_position"])).lower()
    result["ruby_position"] = ruby_position if ruby_position in {"above", "below"} else "above"
    if "lyric_layer_order" not in values and result["all_romaji"]:
        result["show_ruby"] = False
        result["show_romaji"] = False
    elif "lyric_layer_order" not in values and result["show_romaji"]:
        result["show_ruby"] = False

    result["lyric_layer_order"] = normalize_layer_order(values.get("lyric_layer_order"))
    result["show_lyrics"] = bool(values.get("show_lyrics", not result["all_romaji"]))
    result["show_translation"] = bool(values.get("show_translation", True))
    # The earlier replacement mode becomes a standalone Romaji layer.
    if result["all_romaji"] and "lyric_layer_order" not in values:
        result["show_romaji"] = True
    if not any(result[key] for key in (
        "show_lyrics", "show_romaji", "show_translation", "show_ruby", "all_romaji",
    )):
        result["show_lyrics"] = True

    parser = str(values.get("furigana_parser", result["furigana_parser"])).lower()
    result["furigana_parser"] = parser if parser in {"yomi", "mecab_local"} else "yomi"
    mecab_mode = str(values.get("mecab_parse_mode", result["mecab_parse_mode"])).lower()
    result["mecab_parse_mode"] = mecab_mode if mecab_mode in {"batch", "line"} else "batch"

    result["limited_reading_rendering"] = bool(
        values.get("limited_reading_rendering", result["limited_reading_rendering"])
    )
    result["limited_reading_range"] = max(
        5, min(15, _safe_int(
            values.get("limited_reading_range"),
            result["limited_reading_range"],
        ))
    )
    result["fade_in_readings"] = bool(
        values.get("fade_in_readings", result["fade_in_readings"])
    )

    result["font_family"] = (
        str(values.get("font_family", result["font_family"])).strip()
    )

    result["font_size"] = max(
        10,
        min(96, _safe_int(
            values.get("font_size"),
            result["font_size"],
        )),
    )

    alignment = str(values.get(
        "lyrics_alignment",
        result["lyrics_alignment"],
    )).lower()
    result["lyrics_alignment"] = (
        alignment if alignment in {"left", "center", "right"}
        else "center"
    )

    result["highlight_current"] = bool(
        values.get("highlight_current", result["highlight_current"])
    )
    result["focus_transition"] = bool(values.get("focus_transition", result["focus_transition"]))

    animation = str(values.get(
        "highlight_animation",
        result["highlight_animation"],
    )).lower()
    result["highlight_animation"] = (
        animation
        if animation in {"none", "fade", "pop", "scale", "slide"}
        else "none"
    )

    result["highlight_animation_duration"] = max(
        0,
        min(2000, _safe_int(
            values.get("highlight_animation_duration"),
            result["highlight_animation_duration"],
        )),
    )

    result["highlight_animation_out_duration"] = max(
        0,
        min(2000, _safe_int(
            values.get("highlight_animation_out_duration"),
            result["highlight_animation_out_duration"],
        )),
    )

    easing = str(values.get(
        "highlight_animation_easing",
        result["highlight_animation_easing"],
    )).lower()
    result["highlight_animation_easing"] = (
        easing
        if easing in {
            "linear",
            "in_quad",
            "out_quad",
            "in_out_quad",
            "in_cubic",
            "out_cubic",
            "in_out_cubic",
            "out_back",
            "out_bounce",
        }
        else "out_cubic"
    )

    result["highlight_color"] = _safe_color(
        values.get("highlight_color"),
        result["highlight_color"],
    )
    result["lyrics_color"] = _safe_color(
        values.get("lyrics_color"),
        result["lyrics_color"],
    )
    result["lyrics_stroke_width"] = max(0, min(3, _safe_int(values.get("lyrics_stroke_width"), 0)))
    result["lyrics_stroke_color"] = _safe_color(
        values.get("lyrics_stroke_color"), result["lyrics_stroke_color"],
    )
    result["karaoke_stroke_color"] = _safe_color(values.get("karaoke_stroke_color"), "")
    result["lyrics_shadow"] = bool(values.get("lyrics_shadow", result["lyrics_shadow"]))
    result["lyrics_shadow_color"] = _safe_color(
        values.get("lyrics_shadow_color"), result["lyrics_shadow_color"],
    )
    for key, (_, minimum, maximum) in LYRIC_SHADOW_FIELDS.items():
        result[key] = max(minimum, min(maximum, _safe_int(values.get(key), result[key])))

    mode = str(values.get(
        "scrolling_mode", result["scrolling_mode"]
    )).lower()
    result["scrolling_mode"] = (
        mode if mode in {"automatic", "continuous"} else "automatic"
    )

    result["continuous_scroll_speed"] = max(
        1,
        min(500, _safe_int(
            values.get("continuous_scroll_speed"),
            result["continuous_scroll_speed"],
        )),
    )

    result["continuous_scroll_duration_based"] = bool(
        values.get("continuous_scroll_duration_based", result["continuous_scroll_duration_based"])
    )
    result["continuous_scroll_nudge_current"] = bool(
        values.get("continuous_scroll_nudge_current", result["continuous_scroll_nudge_current"])
    )

    result["scroll_animation_speed"] = max(
        0,
        min(1000, _safe_int(
            values.get("scroll_animation_speed"),
            result["scroll_animation_speed"],
        )),
    )

    easing = str(values.get("scroll_animation_easing", result["scroll_animation_easing"])).lower()
    result["scroll_animation_easing"] = easing if easing in {"linear", "in_out_quad", "out_cubic", "out_quart", "out_expo"} else "out_cubic"

    behavior = str(values.get(
        "scroll_behavior",
        result["scroll_behavior"],
    )).lower()
    result["scroll_behavior"] = (
        behavior if behavior in {"center", "focus_current", "ensure_visible", "off"}
        else "center"
    )

    return result


def get_global_lyrics_settings(payload=None):
    payload = load_all_settings() if payload is None else payload

    # Backward compatibility: existing installations stored these directly
    # at the root of the settings file.
    legacy = {
        key: payload[key]
        for key in LYRICS_DEFAULTS
        if key in payload
    }

    nested = payload.get("lyrics_global", {})
    # Keep the user's choice from the earlier Karaoke settings location.
    old_karaoke = payload.get("karaoke", {})
    if isinstance(old_karaoke, dict) and "focus_transition" in old_karaoke:
        legacy.setdefault("focus_transition", old_karaoke["focus_transition"])
    merged = {**legacy, **nested}
    return normalize_lyrics_settings(merged)


def save_global_lyrics_settings(values):
    payload = load_all_settings()
    payload["lyrics_global"] = normalize_lyrics_settings(values)
    save_all_settings(payload)


def file_settings_key(path):
    return str(Path(path).expanduser().resolve())


def get_local_lyrics_overrides(path, payload=None):
    if not path:
        return {}

    payload = load_all_settings() if payload is None else payload
    all_local = payload.get("lyrics_local", {})
    overrides = all_local.get(file_settings_key(path), {})

    return {
        key: overrides[key]
        for key in LYRICS_DEFAULTS
        if key in overrides and key != "furigana_parser"
    }


def save_local_lyrics_overrides(path, overrides):
    if not path:
        return

    payload = load_all_settings()
    all_local = payload.setdefault("lyrics_local", {})
    key = file_settings_key(path)

    cleaned = {
        setting: overrides[setting]
        for setting in LYRICS_DEFAULTS
        if setting in overrides and setting != "furigana_parser"
    }

    if cleaned:
        all_local[key] = cleaned
    else:
        all_local.pop(key, None)

    save_all_settings(payload)


def get_effective_lyrics_settings(path=None, payload=None):
    payload = load_all_settings() if payload is None else payload
    global_values = get_global_lyrics_settings(payload)
    local_overrides = get_local_lyrics_overrides(path, payload)

    effective = dict(global_values)
    effective.update(local_overrides)
    return normalize_lyrics_settings(effective)

# ---------------------------------------------------------------------------
# Music library
# ---------------------------------------------------------------------------
