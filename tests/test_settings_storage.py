import json

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
