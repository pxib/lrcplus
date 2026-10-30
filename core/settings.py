import json
import os
import re
import sys
import time
import uuid
from copy import deepcopy
from pathlib import Path

APP_ROOT = Path(__file__).resolve().parent.parent
LEGACY_SETTINGS_PATH = APP_ROOT / "lrc_player_settings.json"


def get_app_data_dir():
    """Writable application data, independent of the installation directory."""
    override = os.environ.get("LRCPLUS_DATA_DIR")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share")
    return root / "LRCPlus"


def _default_settings_path():
    override = os.environ.get("LRCPLUS_SETTINGS_PATH")
    if override:
        return Path(override).expanduser()
    if sys.platform == "win32":
        root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    elif sys.platform == "darwin":
        root = Path.home() / "Library" / "Application Support"
    else:
        root = Path(os.environ.get("XDG_CONFIG_HOME") or Path.home() / ".config")
    return root / "LRCPlus" / "lrc_player_settings.json"


SETTINGS_PATH = _default_settings_path()

STARTUP_DEFAULTS = {
    "remember_window": True,
    "restore_playlist": False,
}

PLAYBACK_DEFAULTS = {
    "precise_timestamp_fps": 30,
    "play_mode": "playlist",
    "shuffle_mode": "tracks",
    "after_playback": "next_track",
    "output_device": "",
    "volume": 80,
    "high_volume_warning_shown": False,
    "show_waveform": True,
    "pitch_mode": "Semitones",
    "pitch_value": 0,
    "speed_percent": 100,
    "pitch_linked_to_speed": False,
}

KARAOKE_DEFAULTS = {
    "sweep_fps": 60,
    "sweep_style": "classic",
    "sweep_easing": "linear",
    "softness": 8,
    "glow_intensity": 70,
    "glow_radius": 1,
    "shimmer_width": 10,
    "sweep_delay_ms": 100,
    "sweep_delay_percent": 10,
}



CANTONESE_READING_PROMPT_DEFAULTS = {
    "decision": None,
}


def get_cantonese_reading_prompt_decision(payload=None):
    """Return True/False for a remembered choice, or None if unset."""
    payload = load_all_settings() if payload is None else payload
    values = payload.get("cantonese_reading_prompt", {})
    if not isinstance(values, dict):
        return None
    decision = values.get("decision", CANTONESE_READING_PROMPT_DEFAULTS["decision"])
    return decision if isinstance(decision, bool) else None


def save_cantonese_reading_prompt_decision(decision):
    """Persist a remembered Generate/Skip choice; None clears it."""
    payload = load_all_settings()
    if decision is None:
        payload.pop("cantonese_reading_prompt", None)
    else:
        payload["cantonese_reading_prompt"] = {"decision": bool(decision)}
    save_all_settings(payload)

def _safe_fps(value, fallback):
    try:
        value = int(value)
    except (TypeError, ValueError):
        return fallback
    return value if value in {15, 30, 60, 120} else fallback


def get_playback_settings(payload=None):
    payload = load_all_settings() if payload is None else payload
    values = payload.get("playback", {})
    if not isinstance(values, dict):
        values = {}
    play_mode = str(values.get("play_mode", PLAYBACK_DEFAULTS["play_mode"])).lower()
    if play_mode not in {"playlist", "library"}:
        play_mode = PLAYBACK_DEFAULTS["play_mode"]
    shuffle_mode = str(values.get("shuffle_mode", PLAYBACK_DEFAULTS["shuffle_mode"])).lower()
    if shuffle_mode not in {"tracks", "albums"}:
        shuffle_mode = PLAYBACK_DEFAULTS["shuffle_mode"]
    after_playback = str(values.get("after_playback", PLAYBACK_DEFAULTS["after_playback"])).lower()
    if after_playback not in {"next_track", "stop", "repeat_all", "repeat_track"}:
        after_playback = PLAYBACK_DEFAULTS["after_playback"]
    pitch_mode = str(values.get("pitch_mode", PLAYBACK_DEFAULTS["pitch_mode"]))
    if pitch_mode not in {"Octaves", "Percent", "Semitones"}:
        pitch_mode = PLAYBACK_DEFAULTS["pitch_mode"]
    try:
        pitch_value = float(values.get("pitch_value", PLAYBACK_DEFAULTS["pitch_value"]))
    except (TypeError, ValueError):
        pitch_value = 0.0
    if not (pitch_value == pitch_value and abs(pitch_value) < 100000):
        pitch_value = 0.0
    try:
        volume = int(values.get("volume", PLAYBACK_DEFAULTS["volume"]))
    except (TypeError, ValueError):
        volume = PLAYBACK_DEFAULTS["volume"]
    try:
        speed_percent = int(values.get("speed_percent", PLAYBACK_DEFAULTS["speed_percent"]))
    except (TypeError, ValueError):
        speed_percent = PLAYBACK_DEFAULTS["speed_percent"]
    return {
        "precise_timestamp_fps": _safe_fps(
            values.get("precise_timestamp_fps",
                       PLAYBACK_DEFAULTS["precise_timestamp_fps"]),
            PLAYBACK_DEFAULTS["precise_timestamp_fps"],
        ),
        "play_mode": play_mode,
        "shuffle_mode": shuffle_mode,
        "after_playback": after_playback,
        "output_device": str(values.get("output_device", PLAYBACK_DEFAULTS["output_device"]) or ""),
        "volume": max(0, min(300, volume)),
        "high_volume_warning_shown": bool(
            values.get(
                "high_volume_warning_shown",
                PLAYBACK_DEFAULTS["high_volume_warning_shown"],
            )
        ),
        "show_waveform": bool(
            values.get("show_waveform", PLAYBACK_DEFAULTS["show_waveform"])
        ),
        "pitch_mode": pitch_mode,
        "pitch_value": pitch_value,
        "speed_percent": max(25, min(200, speed_percent)),
        "pitch_linked_to_speed": bool(
            values.get(
                "pitch_linked_to_speed",
                PLAYBACK_DEFAULTS["pitch_linked_to_speed"],
            )
        ),
    }


def save_playback_settings(values):
    payload = load_all_settings()
    payload["playback"] = get_playback_settings(
        {"playback": dict(values or {})}
    )
    save_all_settings(payload)


def get_karaoke_settings(payload=None):
    payload = load_all_settings() if payload is None else payload
    values = payload.get("karaoke", {})
    if not isinstance(values, dict):
        values = {}
    sweep_style = str(
        values.get("sweep_style", KARAOKE_DEFAULTS["sweep_style"])
    ).lower()
    if sweep_style not in {"classic", "soft", "glow", "shimmer"}:
        sweep_style = KARAOKE_DEFAULTS["sweep_style"]

    def safe_int(value, fallback, minimum, maximum):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    sweep_easing = str(
        values.get("sweep_easing", KARAOKE_DEFAULTS["sweep_easing"])
    ).lower()
    if sweep_easing not in {"linear", "ease_in", "ease_out", "ease_in_out"}:
        sweep_easing = KARAOKE_DEFAULTS["sweep_easing"]

    return {
        "sweep_fps": _safe_fps(
            values.get("sweep_fps", KARAOKE_DEFAULTS["sweep_fps"]),
            KARAOKE_DEFAULTS["sweep_fps"],
        ),
        "sweep_style": sweep_style,
        "sweep_easing": sweep_easing,
        "softness": safe_int(
            values.get("softness", KARAOKE_DEFAULTS["softness"]),
            KARAOKE_DEFAULTS["softness"], 1, 40,
        ),
        "glow_intensity": safe_int(
            values.get("glow_intensity", KARAOKE_DEFAULTS["glow_intensity"]),
            KARAOKE_DEFAULTS["glow_intensity"], 0, 255,
        ),
        "glow_radius": safe_int(
            values.get("glow_radius", KARAOKE_DEFAULTS["glow_radius"]),
            KARAOKE_DEFAULTS["glow_radius"], 1, 8,
        ),
        "shimmer_width": safe_int(
            values.get(
                "shimmer_width",
                KARAOKE_DEFAULTS["shimmer_width"],
            ),
            KARAOKE_DEFAULTS["shimmer_width"],
            1,
            40,
        ),
        "sweep_delay_ms": safe_int(
            values.get(
                "sweep_delay_ms",
                KARAOKE_DEFAULTS["sweep_delay_ms"],
            ),
            KARAOKE_DEFAULTS["sweep_delay_ms"],
            0,
            500,
        ),
        "sweep_delay_percent": safe_int(
            values.get(
                "sweep_delay_percent",
                KARAOKE_DEFAULTS["sweep_delay_percent"],
            ),
            KARAOKE_DEFAULTS["sweep_delay_percent"],
            0,
            40,
        ),
    }


def save_karaoke_settings(values):
    payload = load_all_settings()
    payload["karaoke"] = get_karaoke_settings(
        {"karaoke": dict(values or {})}
    )
    save_all_settings(payload)


def get_startup_settings(payload=None):
    payload = load_all_settings() if payload is None else payload
    values = payload.get("startup", {})
    if not isinstance(values, dict):
        values = {}

    return {
        "remember_window": bool(
            values.get(
                "remember_window",
                STARTUP_DEFAULTS["remember_window"],
            )
        ),
        "restore_playlist": bool(
            values.get(
                "restore_playlist",
                STARTUP_DEFAULTS["restore_playlist"],
            )
        ),
    }


def save_startup_settings(values):
    payload = load_all_settings()
    payload["startup"] = {
        "remember_window": bool(
            values.get(
                "remember_window",
                STARTUP_DEFAULTS["remember_window"],
            )
        ),
        "restore_playlist": bool(
            values.get(
                "restore_playlist",
                STARTUP_DEFAULTS["restore_playlist"],
            )
        ),
    }
    save_all_settings(payload)


APPEARANCE_DEFAULTS = {
    "theme_mode": "light",
    "theme_color": "#3a9879",
    "background_enabled": True,
    "background_layout": "fit",
    "background_blur_mode": "quality",
    "background_blur_bleed": False,
    "background_blur": 12,
    "background_darkness": 155,
    "artwork_source": "album_first",
    "booru_tags": "scenery",
    "empty_timestamp_placeholder_enabled": True,
    "empty_timestamp_placeholder": "♪ {countdown}",
}


def get_appearance_settings(payload=None):
    payload = load_all_settings() if payload is None else payload
    values = payload.get("appearance", {})
    if not isinstance(values, dict):
        values = {}

    def safe_int(value, fallback, minimum, maximum):
        try:
            value = int(value)
        except (TypeError, ValueError):
            value = fallback
        return max(minimum, min(maximum, value))

    theme_color = str(
        values.get("theme_color", APPEARANCE_DEFAULTS["theme_color"])
    ).strip()
    if not re.fullmatch(r"#[0-9a-fA-F]{6}", theme_color):
        theme_color = APPEARANCE_DEFAULTS["theme_color"]
    theme_mode = values.get("theme_mode")
    if theme_mode not in {"light", "dark"}:
        theme_mode = APPEARANCE_DEFAULTS["theme_mode"]

    return {
        "theme_mode": theme_mode,
        "theme_color": theme_color.lower(),
        "background_enabled": bool(
            values.get(
                "background_enabled",
                APPEARANCE_DEFAULTS["background_enabled"],
            )
        ),
        "background_blur_mode": (
            "low_quality"
            if values.get("background_blur_mode") == "low_quality"
            else "quality"
        ),
        "background_layout": (
            values.get("background_layout")
            if values.get("background_layout") in (
                "center", "fill", "fit", "stretch", "tile",
            )
            else APPEARANCE_DEFAULTS["background_layout"]
        ),
        "background_blur_bleed": bool(
            values.get(
                "background_blur_bleed",
                APPEARANCE_DEFAULTS["background_blur_bleed"],
            )
        ),
        "background_blur": safe_int(
            values.get("background_blur"),
            APPEARANCE_DEFAULTS["background_blur"],
            0,
            30,
        ),
        "background_darkness": safe_int(
            values.get("background_darkness"),
            APPEARANCE_DEFAULTS["background_darkness"],
            0,
            220,
        ),
        "artwork_source": (
            values.get("artwork_source")
            if values.get("artwork_source") in {
                "album_first",
                "booru_first",
                "album_only",
                "booru_only",
            }
            else APPEARANCE_DEFAULTS["artwork_source"]
        ),
        "booru_tags": " ".join(
            str(
                values.get(
                    "booru_tags",
                    APPEARANCE_DEFAULTS["booru_tags"],
                )
            ).split()
        ) or APPEARANCE_DEFAULTS["booru_tags"],
        "empty_timestamp_placeholder_enabled": bool(
            values.get(
                "empty_timestamp_placeholder_enabled",
                APPEARANCE_DEFAULTS["empty_timestamp_placeholder_enabled"],
            )
        ),
        "empty_timestamp_placeholder": str(
            values.get(
                "empty_timestamp_placeholder",
                APPEARANCE_DEFAULTS["empty_timestamp_placeholder"],
            )
        ).strip() or APPEARANCE_DEFAULTS["empty_timestamp_placeholder"],
    }


def save_appearance_settings(values):
    payload = load_all_settings()
    normalized = get_appearance_settings(
        {"appearance": dict(values or {})}
    )
    payload["appearance"] = normalized
    save_all_settings(payload)


def load_all_settings():
    try:
        path = SETTINGS_PATH
        # Read the original configuration until the first save migrates it.
        # An explicit override is isolated (useful for portable installs/tests).
        if not path.exists() and not os.environ.get("LRCPLUS_SETTINGS_PATH"):
            path = LEGACY_SETTINGS_PATH
        if not path.exists():
            return {}
        payload = json.loads(path.read_text(encoding="utf-8"))
        return payload if isinstance(payload, dict) else {}
    except (OSError, ValueError, TypeError):
        return {}


def save_all_settings(payload):
    # Use a unique temporary file. A fixed ".tmp" name can collide with another
    # save attempt, and Windows may briefly keep the destination locked.
    temp_path = SETTINGS_PATH.with_name(
        f"{SETTINGS_PATH.name}.{uuid.uuid4().hex}.tmp"
    )
    try:
        SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
        temp_path.write_text(
            json.dumps(payload, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

        last_error = None
        for attempt in range(5):
            try:
                temp_path.replace(SETTINGS_PATH)
                return
            except PermissionError as error:
                last_error = error
                if attempt < 4:
                    time.sleep(0.05 * (attempt + 1))
                    continue
                raise
        if last_error is not None:
            raise last_error
    except (OSError, TypeError, ValueError):
        try:
            temp_path.unlink(missing_ok=True)
        except OSError:
            pass
        raise



def get_disabled_plugins(payload=None):
    payload = load_all_settings() if payload is None else payload
    disabled = payload.get("disabled_plugins", [])
    if not isinstance(disabled, list):
        return set()
    return {str(plugin_id) for plugin_id in disabled if str(plugin_id).strip()}


def save_disabled_plugins(plugin_ids):
    payload = load_all_settings()
    payload["disabled_plugins"] = sorted(
        {str(plugin_id) for plugin_id in plugin_ids if str(plugin_id).strip()}
    )
    save_all_settings(payload)

def load_lrc_search_folders():
    payload = load_all_settings()
    folders = payload.get("lrc_folders", [])

    cleaned = []
    for folder in folders:
        if not folder:
            continue
        path = str(Path(folder).expanduser())
        if path not in cleaned:
            cleaned.append(path)

    return cleaned


def save_lrc_search_folders(folders):
    payload = load_all_settings()
    payload["lrc_folders"] = [
        str(Path(folder).expanduser())
        for folder in folders
        if folder
    ]
    save_all_settings(payload)


def load_music_library():
    payload = load_all_settings()
    values = payload.get("music_library", {})
    if not isinstance(values, dict):
        values = {}

    folders = []
    for folder in values.get("folders", []):
        try:
            text = str(Path(folder).expanduser())
        except (TypeError, ValueError):
            continue
        if text and text not in folders:
            folders.append(text)

    tracks = values.get("tracks", [])
    if not isinstance(tracks, list):
        tracks = []

    return {"folders": folders, "tracks": tracks, "columns": values.get("columns", {}) if isinstance(values.get("columns", {}), dict) else {}}


def save_music_library(folders, tracks, columns=None):
    payload = load_all_settings()
    payload["music_library"] = {
        "folders": [str(Path(folder).expanduser()) for folder in folders if folder],
        "tracks": tracks,
        "columns": columns or {},
    }
    save_all_settings(payload)


def _normalise_association_path(path):
    if not path:
        return ""
    try:
        return str(Path(path).expanduser().resolve())
    except Exception:
        return str(path)

def get_lyrics_attachment(audio_path):
    """Return a persisted explicit lyrics association for an audio file."""
    key = _normalise_association_path(audio_path)
    payload = load_all_settings()
    values = payload.get("lyrics_attachments", {})
    if not isinstance(values, dict):
        return None
    value = values.get(key)
    return str(value) if value else None

def set_lyrics_attachment(audio_path, lyrics_path):
    """Persist an explicit audio -> lyrics association. None removes it."""
    key = _normalise_association_path(audio_path)
    if not key:
        return
    payload = load_all_settings()
    values = payload.get("lyrics_attachments", {})
    if not isinstance(values, dict):
        values = {}
    if lyrics_path:
        values[key] = _normalise_association_path(lyrics_path)
    else:
        values.pop(key, None)
    payload["lyrics_attachments"] = values
    save_all_settings(payload)


from core.lyrics_settings import (
    LYRICS_DEFAULTS,
    normalize_lyrics_settings,
    get_global_lyrics_settings,
    save_global_lyrics_settings,
    file_settings_key,
    get_local_lyrics_overrides,
    save_local_lyrics_overrides,
    get_effective_lyrics_settings,
)
