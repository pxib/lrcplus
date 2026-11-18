import json
from pathlib import Path
import subprocess
import sys

import pytest

import core.settings as settings


def test_legacy_settings_migrate_on_save_without_modifying_original(monkeypatch):
    monkeypatch.delenv("LRCPLUS_SETTINGS_PATH")
    original = {"playback": {"volume": 42}, "plugins": {"example": {"enabled": True}}}
    settings.LEGACY_SETTINGS_PATH.write_text(json.dumps(original), encoding="utf-8")
    payload = settings.load_all_settings()
    assert payload == original
    assert not settings.SETTINGS_PATH.exists()
    settings.save_all_settings(payload)
    assert settings.load_all_settings() == original
    assert json.loads(settings.LEGACY_SETTINGS_PATH.read_text(encoding="utf-8")) == original


def test_new_settings_take_priority_over_legacy(monkeypatch):
    monkeypatch.delenv("LRCPLUS_SETTINGS_PATH")
    settings.LEGACY_SETTINGS_PATH.write_text('{"old": true}', encoding="utf-8")
    settings.save_all_settings({"new": True})
    assert settings.load_all_settings() == {"new": True}


def test_explicit_settings_override_does_not_read_legacy():
    settings.LEGACY_SETTINGS_PATH.write_text('{"old": true}', encoding="utf-8")
    assert settings.load_all_settings() == {}


def test_invalid_top_level_payload_is_ignored():
    settings.SETTINGS_PATH.parent.mkdir()
    settings.SETTINGS_PATH.write_text("[]", encoding="utf-8")
    assert settings.load_all_settings() == {}


def test_incomplete_json_write_preserves_preferences_and_cleans_temporary_file():
    original = {"playback": {"volume": 42}, "title": "日本語 한글"}
    settings.save_all_settings(original)
    with pytest.raises(TypeError):
        settings.save_all_settings({"valid": ["日本語 한글"] * 3000, "invalid": object()})
    assert settings.load_all_settings() == original
    assert not list(settings.SETTINGS_PATH.parent.glob("*.tmp"))


def test_settings_write_retries_locked_destination(monkeypatch):
    replace = Path.replace
    attempts = []

    def locked_once(source, destination):
        attempts.append(destination)
        if len(attempts) == 1:
            raise PermissionError("Locked")
        return replace(source, destination)

    monkeypatch.setattr(Path, "replace", locked_once)
    monkeypatch.setattr(settings.time, "sleep", lambda _: None)
    settings.save_all_settings({"playback": {"volume": 42}})
    assert len(attempts) == 2
    assert settings.load_all_settings() == {"playback": {"volume": 42}}
    assert not list(settings.SETTINGS_PATH.parent.glob("*.tmp"))


def test_large_settings_save_does_not_allocate_another_complete_serialized_library():
    script = """
import tracemalloc
import core.settings as settings
payload = {'music_library': {'tracks': [
    {'path': f'C:/music/{i}.mp3', 'title': f'Song {i}', 'artist': 'Artist',
     'album': 'Album', 'duration': 180.0, 'codec': 'MP3'} for i in range(10000)
]}}
tracemalloc.start()
settings.save_all_settings(payload)
_, peak = tracemalloc.get_traced_memory()
tracemalloc.stop()
assert peak < 1024 * 1024, peak
assert settings.load_all_settings() == payload
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True,
                            encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr
