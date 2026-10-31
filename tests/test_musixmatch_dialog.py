from types import SimpleNamespace
import threading
import time

import pytest
from PySide6.QtCore import QTimer
from PySide6.QtWidgets import QComboBox, QDialog, QLabel, QLineEdit, QPushButton, QTableWidget, QTextEdit, QWidget

from core import musixmatch
import ui.main_window as main_window


@pytest.mark.parametrize("outcome", ["synced", "plain", "error", "cancel", "switch"])
@pytest.mark.parametrize("auth_mode", ["token", "api_key"])
def test_musixmatch_preview_stays_responsive_and_saves(qapp, monkeypatch, tmp_path, outcome, auth_mode):
    entered, release = threading.Event(), threading.Event()
    calls = []
    parent = QWidget()
    parent.current_audio_path = tmp_path / "Song.mp3"
    loaded = []
    parent.load_lrc_file = loaded.append
    parent.clear_lyrics_search_index = lambda: None
    monkeypatch.setenv("MUSIXMATCH_API_KEY", "private-key")
    monkeypatch.setenv("MUSIXMATCH_TOKEN", "private-token")

    def get(url, **kwargs):
        calls.append((url, threading.get_ident()))
        if auth_mode == "token":
            assert kwargs["params"]["usertoken"] == "private-token"
            assert "apikey" not in kwargs["params"]
            assert url.startswith("https://apic-appmobile.musixmatch.com/")
        else:
            assert kwargs["params"]["apikey"] == "private-key"
            assert "usertoken" not in kwargs["params"]
            assert url.startswith("https://api.musixmatch.com/")
        if url.endswith("track.search"):
            body = {"track_list": [{"track": {
                "track_id": 42, "track_name": "Song", "has_subtitles": 1,
                "track_share_url": "https://www.musixmatch.com/lyrics/Artist/Song",
            }}]}
            status = 200
        else:
            entered.set()
            assert release.wait(5)
            if outcome == "error":
                status, body = 401, []
            elif outcome == "plain" and url.endswith("track.subtitle.get"):
                status, body = 403, []
            elif outcome == "plain":
                status, body = 200, {"lyrics": {"lyrics_body": "Hello", "lyrics_copyright": "Owner"}}
            else:
                status, body = 200, {"subtitle": {"subtitle_body": "[00:01.00]Hello", "lyrics_copyright": "Owner"}}
        return SimpleNamespace(
            status_code=200, headers={}, close=lambda: None,
            json=lambda: {"message": {"header": {"status_code": status}, "body": body}},
        )

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert predicate()

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText("Musixmatch")
        auth = next(combo for combo in dialog.findChildren(QComboBox)
                    if combo.accessibleName() == "Musixmatch authentication method")
        assert auth.currentData() == "token"
        auth.setCurrentIndex(auth.findData(auth_mode))
        query = dialog.findChild(QLineEdit)
        query.setText("Song")
        search = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search")
        use = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Use Selected Lyrics")
        preview = dialog.findChild(QTextEdit)
        search.click()
        wait_for(entered.is_set)
        assert not use.isEnabled()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        assert all(thread_id != threading.get_ident() for _, thread_id in calls)
        if outcome == "cancel":
            dialog.reject()
            return 0
        if outcome == "switch":
            provider.setCurrentText("LRCLIB")
            release.set()
            wait_for(lambda: all(not job._future or job._future.done()
                                 for job in dialog.findChildren(main_window.BackgroundJob)))
            qapp.processEvents()
            assert not preview.toPlainText()
            assert dialog.findChild(QTableWidget).rowCount() == 0
            assert not use.isEnabled()
            return 0
        release.set()
        if outcome == "error":
            wait_for(lambda: "invalid or expired" in preview.toPlainText())
            assert not use.isEnabled()
            return 0
        wait_for(use.isEnabled)
        assert "Hello" in preview.toPlainText()
        use.click()
        return 1

    monkeypatch.setattr(musixmatch.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
        if outcome in {"synced", "plain"}:
            destination = parent.current_audio_path.with_suffix(".lrc" if outcome == "synced" else ".txt")
            assert loaded == [str(destination)]
            text = destination.read_text(encoding="utf-8")
            assert "Hello" in text
            assert "Lyrics provided by Musixmatch" in text
            assert "Owner" in text
            assert "private-key" not in text
            assert "private-token" not in text
        else:
            assert not loaded
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("outcome", ["success", "error", "cancel", "switch", "edit", "auth_switch", "placeholder"])
def test_get_token_is_async_and_ignores_cancelled_results(qapp, monkeypatch, outcome):
    entered, release, completed = threading.Event(), threading.Event(), threading.Event()
    parent = QWidget()
    parent.current_audio_path = None
    if outcome == "placeholder":
        parent._musixmatch_token = "existing-token"
    monkeypatch.delenv("MUSIXMATCH_TOKEN", raising=False)
    monkeypatch.setenv("MUSIXMATCH_API_KEY", "developer-key")

    def get(url, **kwargs):
        assert url.endswith("/token.get")
        assert "apikey" not in kwargs["params"]
        assert "usertoken" not in kwargs["params"]
        assert threading.get_ident() != threading.main_thread().ident
        entered.set()
        assert release.wait(5)
        completed.set()
        return SimpleNamespace(
            status_code=200, headers={}, close=lambda: None,
            json=lambda: {"message": {
                "header": {"status_code": 200 if outcome != "error" else 503},
                "body": {"user_token": "0000000000000" if outcome == "placeholder" else "generated-token"},
            }},
        )

    def wait_for(predicate):
        deadline = time.monotonic() + 4
        while not predicate() and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert predicate()

    def interact(dialog):
        provider = dialog.findChild(QComboBox)
        provider.setCurrentText("Musixmatch")
        auth = next(combo for combo in dialog.findChildren(QComboBox)
                    if combo.accessibleName() == "Musixmatch authentication method")
        field = next(field for field in dialog.findChildren(QLineEdit)
                     if field.accessibleName() == "Musixmatch authentication token")
        get_button = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Get token")
        search = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Search")
        show = next(button for button in dialog.findChildren(QPushButton) if button.text() == "Show")
        assert field.text() == ("existing-token" if outcome == "placeholder" else "")
        get_button.click()
        wait_for(entered.is_set)
        assert not get_button.isEnabled()
        assert not search.isEnabled()
        ticks = []
        QTimer.singleShot(0, lambda: ticks.append(True))
        qapp.processEvents()
        assert ticks
        if outcome == "cancel":
            dialog.reject()
            return 0
        if outcome == "switch":
            provider.setCurrentText("LRCLIB")
        elif outcome == "auth_switch":
            auth.setCurrentIndex(auth.findData("api_key"))
            assert field.text() == "developer-key"
        elif outcome == "edit":
            field.setText("pasted-token")
            field.textEdited.emit(field.text())
        release.set()
        wait_for(completed.is_set)
        if outcome in {"switch", "auth_switch", "edit"}:
            # Drain completion polling long enough to catch a late token result.
            drained = []
            QTimer.singleShot(100, lambda: drained.append(True))
            wait_for(lambda: drained)
            expected = {"switch": "", "auth_switch": "developer-key", "edit": "pasted-token"}[outcome]
            assert field.text() == expected
            assert getattr(parent, "_musixmatch_token", "") == ("pasted-token" if outcome == "edit" else "")
        elif outcome in {"error", "placeholder"}:
            wait_for(lambda: any("Could not get a Musixmatch token" in label.text()
                                 for label in dialog.findChildren(QLabel)))
            if outcome == "placeholder":
                assert field.text() == "existing-token"
                assert parent._musixmatch_token == "existing-token"
                assert any("all-zero placeholder" in label.text() for label in dialog.findChildren(QLabel))
            else:
                assert not field.text()
            assert get_button.isEnabled()
        else:
            wait_for(lambda: field.text() == "generated-token")
            assert parent._musixmatch_token == "generated-token"
            assert field.echoMode() == QLineEdit.EchoMode.Password
            show.click()
            assert field.echoMode() == QLineEdit.EchoMode.Normal
            auth.setCurrentIndex(auth.findData("api_key"))
            assert field.text() == "developer-key"
            auth.setCurrentIndex(auth.findData("token"))
            assert field.text() == "generated-token"
            assert field.echoMode() == QLineEdit.EchoMode.Password
        assert search.isEnabled()
        return 0

    monkeypatch.setattr(musixmatch.requests, "get", get)
    monkeypatch.setattr(QDialog, "exec", interact)
    try:
        main_window.Player.search_lrclib_lyrics(parent)
    finally:
        release.set()
        parent.deleteLater()
        qapp.processEvents()
