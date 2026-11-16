"""Portable application preferences, separate from library and session data."""

import json
from copy import deepcopy
from pathlib import Path

from core import settings
from core.file_io import atomic_write_text


FORMAT = "lrcplus-settings"
VERSION = 1

# Transport controls belong to the current session rather than a preset.
_PLAYBACK_SESSION_KEYS = {
    "volume", "high_volume_warning_shown", "shuffle_enabled", "pitch_mode",
    "pitch_value", "speed_percent", "pitch_linked_to_speed",
}
_NORMALIZERS = {
    "startup": settings.get_startup_settings,
    "appearance": settings.get_appearance_settings,
    "playback": settings.get_playback_settings,
    "karaoke": settings.get_karaoke_settings,
    "lyrics_global": settings.get_global_lyrics_settings,
    "listening_statistics": settings.get_statistics_settings,
    "updates": settings.get_update_settings,
}


def get_factory_settings():
    """Use the same defaults as a fresh installation, preserving user resources."""
    return get_factory_candidate({})


def get_factory_candidate(payload):
    """Capture application preferences suitable for a shared factory baseline."""
    defaults = normalize_transfer_settings({
        section: normalize(payload) for section, normalize in _NORMALIZERS.items()
    })
    # Saved themes and folder lists are user resources rather than preferences.
    defaults["appearance"].pop("custom_themes", None)
    defaults["appearance"].pop("custom_background_folders", None)
    # Device IDs are specific to the computer used to choose the defaults.
    defaults["playback"]["output_device"] = ""
    return defaults


def normalize_transfer_settings(payload):
    """Validate known preferences and ignore unrelated or future fields.

    Keep only supplied fields so older or partial exports preserve newer choices.
    """
    if not isinstance(payload, dict):
        raise ValueError("Settings must be a JSON object.")
    result = {}
    for section, normalize in _NORMALIZERS.items():
        if section not in payload:
            continue
        values = payload[section]
        if not isinstance(values, dict):
            raise ValueError(f"{section} must be an object.")
        defaults = normalize({})
        if section == "playback":
            defaults = {key: value for key, value in defaults.items()
                        if key not in _PLAYBACK_SESSION_KEYS}
        elif section == "updates":
            defaults = {key: defaults[key] for key in ("automatic_check", "channel")}
        selected = {key: value for key, value in values.items() if key in defaults}
        for key, value in selected.items():
            if type(value) is not type(defaults[key]):
                raise ValueError(f"{section}.{key} has an invalid value type.")
        normalized = normalize({section: selected})
        if selected:
            result[section] = {key: normalized[key] for key in selected}

    for section in ("lrc_folders", "disabled_plugins"):
        if section not in payload:
            continue
        values = payload[section]
        if not isinstance(values, list) or any(not isinstance(v, str) for v in values):
            raise ValueError(f"{section} must be a list of strings.")
        result[section] = list(dict.fromkeys(v.strip() for v in values if v.strip()))

    if "plugin_settings" in payload:
        values = payload["plugin_settings"]
        if not isinstance(values, dict) or any(not isinstance(v, dict) for v in values.values()):
            raise ValueError("plugin_settings must contain an object for each plugin.")
        result["plugin_settings"] = deepcopy(values)
    if not result:
        raise ValueError("This file does not contain any supported LRCPlus settings.")
    return result


def merge_settings(base, incoming):
    """Overlay imported fields without replacing unrelated preferences or data."""
    result = deepcopy(base)
    for section, values in incoming.items():
        if isinstance(values, dict):
            previous = result.get(section, {})
            previous = previous if isinstance(previous, dict) else {}
            if section == "plugin_settings":
                result[section] = merge_settings(previous, values)
            else:
                result[section] = {**previous, **deepcopy(values)}
        else:
            result[section] = deepcopy(values)
    return result


def settings_json(payload):
    """Serialize preferences for either clipboard copying or file export."""
    document = {"format": FORMAT, "version": VERSION,
                "settings": normalize_transfer_settings(payload)}
    return json.dumps(document, ensure_ascii=False, indent=2, allow_nan=False) + "\n"


def export_settings(path, payload):
    if Path(path).resolve() in {settings.SETTINGS_PATH.resolve(), settings.LEGACY_SETTINGS_PATH.resolve()}:
        raise ValueError("Choose a different file from the active settings file.")
    atomic_write_text(path, settings_json(payload))


def import_settings(path):
    def invalid_constant(value):
        raise ValueError(f"Invalid JSON value: {value}.")

    document = json.loads(Path(path).read_text(encoding="utf-8-sig"),
                          parse_constant=invalid_constant)
    if not isinstance(document, dict):
        raise ValueError("Settings must be a JSON object.")
    if "format" in document or "version" in document or "settings" in document:
        if document.get("format") != FORMAT:
            raise ValueError("This is not an LRCPlus settings export.")
        if type(document.get("version")) is not int or document["version"] != VERSION:
            raise ValueError("This settings export version is not supported.")
        document = document.get("settings")
    # Existing lrc_player_settings.json files can also supply preferences.
    return normalize_transfer_settings(document)
