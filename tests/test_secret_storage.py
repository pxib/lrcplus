import json
import sys
import ctypes

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from core.secret_storage import encrypt_secret, decrypt_secret, SecretStorageError
from test_translation_providers import transport


@pytest.fixture
def windows_dpapi():
    if sys.platform != "win32":
        pytest.skip("Requires Windows DPAPI")
    try:
        encrypt_secret("synthetic-profile-probe", "test/probe")
    except SecretStorageError:
        if ctypes.get_last_error() in {2, 5}:
            pytest.skip("Windows account profile is unavailable in this sandbox; run outside it for DPAPI checks")
        raise


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises real Windows DPAPI")
def test_dpapi_roundtrip_context_isolation_and_tampering(windows_dpapi):
    value = "test-credential-with-unicode-字"
    record = encrypt_secret(value, "translation/azure/api_key")
    assert value not in json.dumps(record)
    assert decrypt_secret(record, "translation/azure/api_key") == value
    assert encrypt_secret(value, "translation/azure/api_key") != record
    with pytest.raises(SecretStorageError):
        decrypt_secret(record, "translation/google/api_key")
    with pytest.raises(SecretStorageError):
        decrypt_secret({**record, "data": record["data"][:-20]}, "translation/azure/api_key")


@pytest.mark.parametrize("record", ["plaintext", {}, {"scheme": "other"},
                                    {"scheme": "windows-dpapi-v1", "data": "!invalid!"}])
def test_invalid_records_fail_without_returning_plaintext(record):
    with pytest.raises(SecretStorageError, match="invalid"):
        decrypt_secret(record, "translation/azure/api_key")
    assert decrypt_secret(None, "context") == ""


def test_unsupported_platform_never_falls_back_to_plaintext(monkeypatch):
    monkeypatch.setattr("core.secret_storage.sys.platform", "linux")
    with pytest.raises(SecretStorageError, match="session-only"):
        encrypt_secret("credential", "context")


@pytest.mark.skipif(sys.platform != "win32", reason="Exercises real Windows DPAPI persistence")
def test_entered_keys_are_encrypted_on_disk_restored_and_clearable(qapp, transport, windows_dpapi):
    from core.plugin_manager import PluginManager
    from core.settings import SETTINGS_PATH
    from ui.translation_panel import TranslationPanel

    api = PluginManager().get_api("lyrics_translator")
    panels = []
    def panel(provider):
        result = TranslationPanel(api, cloud_provider=provider)
        panels.append(result)
        return result
    try:
        first = panel("azure")
        field = first.credentials["api_key"]
        QTest.keyClicks(field, "test-persisted-api-key")
        field.editingFinished.emit()
        contents = SETTINGS_PATH.read_text(encoding="utf-8")
        assert "test-persisted-api-key" not in contents
        assert "windows-dpapi-v1" in contents
        assert not first._dirty_credentials
        restored = panel("azure")
        assert restored.credentials["api_key"].text() == "test-persisted-api-key"
        assert panel("google").credentials["api_key"].text() == ""
        field.setFocus()
        field.selectAll()
        QTest.keyClick(field, Qt.Key.Key_Backspace)
        field.editingFinished.emit()
        assert api.get_setting("encrypted_azure_api_key") is None
        assert panel("azure").credentials["api_key"].text() == ""
    finally:
        for item in panels:
            item.shutdown()
            item.close()
            item.deleteLater()
        qapp.processEvents()


def test_encryption_failure_and_environment_values_are_not_saved(qapp, transport, monkeypatch):
    from core.plugin_manager import PluginManager
    from ui.translation_panel import TranslationPanel

    monkeypatch.setenv("LRCPLUS_GOOGLE_API_KEY", "environment-key")
    api = PluginManager().get_api("lyrics_translator")
    writes = []
    monkeypatch.setattr(api, "set_setting", lambda *args: writes.append(args))
    def unavailable(*args):
        raise SecretStorageError("Unavailable")
    monkeypatch.setattr("core.secret_storage.encrypt_secret", unavailable)
    panel = TranslationPanel(api, cloud_provider="google")
    try:
        assert panel.credentials["api_key"].text() == "environment-key"
        panel._save_credentials()
        assert not writes
        panel.credentials["api_key"].selectAll()
        QTest.keyClicks(panel.credentials["api_key"], "edited-key")
        panel.credentials["api_key"].editingFinished.emit()
        assert not writes
        assert panel.credentials["api_key"].text() == "edited-key"
        assert "Could not save" in panel.credential_status.text()
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()
        qapp.processEvents()
