import json

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QLineEdit, QPlainTextEdit, QWidget

from core import companion_chat, settings
from ui.onboarding import OnboardingDialog


def make_dialog():
    player = QWidget()
    player.library_folders = []
    return player, OnboardingDialog(player, companion=True)


def test_returning_user_can_type_spaces_and_submit_without_advancing_setup(qapp, monkeypatch):
    player, dialog = make_dialog()
    dialog.show()
    chat = dialog.companion_chat
    assert dialog.title.text() == "Ayami"
    assert not dialog.language_combo.isVisible()
    assert chat.isVisible()
    assert dialog._content_layout.indexOf(chat) < dialog._content_layout.indexOf(dialog.dialogue_separator)
    chat.config = {"endpoint": "https://example.com/v1", "model": "test-model"}
    calls = []
    monkeypatch.setattr(chat.job, "start", lambda *args: calls.append(args))
    chat.input.setFocus()
    qapp.processEvents()
    QTest.keyClicks(chat.input, "hello there")
    assert chat.input.text() == "hello there"
    QTest.keyClick(chat.input, Qt.Key.Key_Return)
    assert len(calls) == 1
    assert chat.pending["content"] == "hello there"
    assert not chat.send_button.isEnabled()
    # Replies remain literal, including text that resembles script commands.
    chat.receive_reply(companion_chat.ChatReply(r"Hello! \click(100,200) <b>plain text</b>"))
    assert r"\click(100,200) <b>plain text</b>" in chat.page_text.toPlainText()
    assert chat.input.isEnabled()
    assert chat.history[-1]["role"] == "assistant"
    dialog.close()
    assert chat.history == []
    assert chat.key == ""


def test_returning_user_can_restart_original_setup(qapp):
    player, dialog = make_dialog()
    dialog.show()
    dialog.start_setup_again()
    assert not dialog.companion_mode
    assert dialog.companion_chat.isHidden()
    assert dialog.page == 0
    assert dialog.dialogue_lines
    assert dialog.skip.isVisible()


def test_failed_message_can_be_retried_without_polluting_history(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    chat.key = "secret-key"
    monkeypatch.setattr(chat.job, "start", lambda *args: None)
    chat.input.setText("Try this")
    chat.send()
    chat.failed("Failure secret-key")
    assert chat.input.text() == "Try this"
    assert chat.history == []
    assert "secret-key" not in chat.status.text()
    assert chat.send_button.isEnabled()


@pytest.mark.parametrize("url", ["http://example.com/v1", "https://user:key@example.com/v1",
                                     "https://example.com/v1?key=secret", "file:///tmp/model"])
def test_chat_rejects_unsafe_endpoints(url):
    with pytest.raises(ValueError):
        companion_chat.validate_config(url, "model")


def test_chat_request_only_sends_explicit_history(monkeypatch):
    calls = []

    class Response:
        status_code = 200

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def iter_content(self, size):
            yield json.dumps({"choices": [{"message": {"content": "Hello!"}}]}).encode()

    def post(url, **kwargs):
        calls.append((url, kwargs))
        return Response()

    monkeypatch.setattr(companion_chat.requests, "post", post)
    assert companion_chat.request_reply("http://localhost:1234/v1", "local-model", "key",
                                        [{"role": "user", "content": "Hi"}],
                                        {"service_tier": "flex", "reasoning_effort": "low", "temperature": 0.8},
                                        ["temperature", "top_p"], {"X-Provider-Option": "test"}).text == "Hello!"
    url, request = calls[0]
    assert url == "http://localhost:1234/v1/chat/completions"
    assert request["allow_redirects"] is False
    assert request["json"]["messages"][-1] == {"role": "user", "content": "Hi"}
    assert len(request["json"]["messages"]) == 2
    assert request["json"]["service_tier"] == "flex"
    assert request["json"]["reasoning_effort"] == "low"
    assert "temperature" not in request["json"]
    assert request["headers"]["X-Provider-Option"] == "test"
    assert request["headers"]["Authorization"] == "Bearer key"
    assert request["json"]["stream"] is False


def test_chat_transport_error_does_not_expose_key(monkeypatch):
    def post(*args, **kwargs):
        raise companion_chat.requests.ConnectionError("unsafe secret-key exception")

    monkeypatch.setattr(companion_chat.requests, "post", post)
    with pytest.raises(ValueError) as error:
        companion_chat.request_reply("https://example.com/v1", "model", "secret-key", [])
    assert "secret-key" not in str(error.value)


@pytest.mark.parametrize("encryption_available", [True, False])
def test_byok_settings_encrypt_key_and_allow_forgetting_it(qapp, monkeypatch, encryption_available):
    player, dialog = make_dialog()
    chat = dialog.companion_chat

    def encrypt(value, context):
        assert value == "prototype-test-key" and context == "companion_ai"
        if not encryption_available:
            from core.secret_storage import SecretStorageError
            raise SecretStorageError("Encryption unavailable. Key remains session-only.")
        return {"scheme": "test-encrypted", "data": "encrypted-record"}

    monkeypatch.setattr("ui.onboarding_chat.encrypt_secret", encrypt)

    def fill_settings():
        config_dialog = QApplication.activeModalWidget()
        endpoint, model, key = config_dialog.findChildren(QLineEdit)
        endpoint.setText("https://example.com/v1")
        model.setText("my-model")
        key.setText("prototype-test-key")
        config_dialog.findChild(QCheckBox).setChecked(True)
        config_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, fill_settings)
    chat.configure()
    saved = settings.load_all_settings()["companion_ai"]
    if encryption_available:
        assert saved["key"]["data"] == "encrypted-record"
    else:
        assert "key" not in saved
        assert "session-only" in chat.status.text()
    assert "prototype-test-key" not in settings.SETTINGS_PATH.read_text(encoding="utf-8")
    assert chat.key == "prototype-test-key"

    def forget_key():
        config_dialog = QApplication.activeModalWidget()
        config_dialog.findChildren(QLineEdit)[2].clear()
        config_dialog.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, forget_key)
    chat.configure()
    assert "key" not in settings.load_all_settings()["companion_ai"]
    assert chat.key == ""


@pytest.mark.parametrize("body,excluded,headers", [
    ('{"stream": true}', '[]', '{}'),
    ('{}', '["messages"]', '{}'),
    ('{"model": "override"}', '[]', '{}'),
    ('{"temperature": NaN}', '[]', '{}'),
    ('[]', '[]', '{}'),
    ('null', '[]', '{}'),
    ('{}', '{}', '{}'),
    ('{}', '[]', '{"Authorization": "secret"}'),
    ('{}', '[]', '{"Host": "other.example"}'),
    ('{}', '[]', '{"X-Test": "bad\\r\\nvalue"}'),
    ('{}', '[]', '{"X-Test": "one", "x-test": "two"}'),
    ('{}', '[]', '{"X-Test": 1}'),
    ('{"malformed":}', '[]', '{}'),
])
def test_advanced_settings_reject_invalid_or_managed_values(body, excluded, headers):
    with pytest.raises(ValueError):
        companion_chat.parse_advanced(body, excluded, headers)


def test_advanced_settings_save_and_reopen_without_plaintext_header_secrets(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    encrypted = {}

    def encrypt(value, context):
        encrypted[context] = value
        return {"scheme": "test-encrypted", "data": context}

    monkeypatch.setattr("ui.onboarding_chat.encrypt_secret", encrypt)
    monkeypatch.setattr("ui.onboarding_chat.decrypt_secret",
                        lambda record, context: encrypted.get(context, "") if record else "")

    def fill_settings():
        window = QApplication.activeModalWidget()
        fields = window.findChildren(QLineEdit)
        fields[0].setText("https://example.com/v1")
        fields[1].setText("test-model")
        window.findChild(QCheckBox).setChecked(True)
        window.findChild(QPlainTextEdit, "extra_body").setPlainText('{"service_tier": "flex", "reasoning_effort": "low"}')
        window.findChild(QPlainTextEdit, "exclude_body").setPlainText('["temperature"]')
        window.findChild(QPlainTextEdit, "extra_headers").setPlainText('{"X-Api-Key": "private-header-secret"}')
        window.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()

    QTimer.singleShot(0, fill_settings)
    chat.configure()
    saved = settings.load_all_settings()["companion_ai"]
    assert saved["extra_body"]["reasoning_effort"] == "low"
    assert saved["exclude_body"] == ["temperature"]
    assert "private-header-secret" not in settings.SETTINGS_PATH.read_text(encoding="utf-8")
    assert chat.extra_headers == {"X-Api-Key": "private-header-secret"}
    player2, reopened = make_dialog()
    assert reopened.companion_chat.extra_headers == chat.extra_headers
    assert reopened.companion_chat.config["extra_body"] == saved["extra_body"]
    reopened.close()
    assert reopened.companion_chat.extra_headers == {}


def test_advanced_validation_blocks_save_without_losing_editor_text(qapp):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    results = []

    def invalid_save():
        window = QApplication.activeModalWidget()
        fields = window.findChildren(QLineEdit)
        fields[0].setText("https://example.com/v1")
        fields[1].setText("test-model")
        body = window.findChild(QPlainTextEdit, "extra_body")
        body.setPlainText('{"stream":true}')
        window.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        results.append(window.isVisible() and body.toPlainText() == '{"stream":true}')
        window.reject()

    QTimer.singleShot(0, invalid_save)
    chat.configure()
    assert results == [True]
    assert "companion_ai" not in settings.load_all_settings()


@pytest.mark.parametrize("tag,expected", [
    ("playful", "playful"), ("THINKING", "thinking"), ("none", None),
    ("unknown", None), ("../../voice", None), ("https://example.com/voice.ogg", None),
])
def test_model_expression_only_accepts_bundled_names(tag, expected):
    reply = companion_chat.parse_reply({"choices": [{"message": {
        "content": f"[expression:{tag}]\nHello!"}}]})
    assert reply.text == "Hello!"
    assert reply.expression == expected


def test_expression_text_elsewhere_is_not_a_command():
    reply = companion_chat.parse_reply({"choices": [{"message": {
        "content": "Example: [expression:thinking]"}}]})
    assert reply.expression is None
    assert reply.text == "Example: [expression:thinking]"


@pytest.mark.parametrize("usage,expected", [
    (None, None),
    ({"prompt_tokens": 120, "completion_tokens": 30},
     {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}),
    ({"prompt_tokens": True, "completion_tokens": -1, "total_tokens": "20"}, None),
    ({"total_tokens": 0}, {"total_tokens": 0}),
    ({"total_tokens": 80, "completion_tokens_details": {"reasoning_tokens": 20},
      "prompt_tokens_details": {"cached_tokens": 10}},
     {"total_tokens": 80, "reasoning_tokens": 20, "cached_tokens": 10}),
])
def test_usage_reports_only_valid_provider_counts(usage, expected):
    reply = companion_chat.parse_reply({"choices": [{"message": {"content": "Hello"}}], "usage": usage})
    assert reply.usage == expected


def test_reply_plays_one_sound_shows_expression_and_accumulates_usage(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    played = []
    monkeypatch.setattr(dialog.sounds, "play", lambda name: played.append(name) if dialog.sounds.enabled else False)
    dialog.change_sound(True)
    chat.pending = {"role": "user", "content": "Hi"}
    reply = companion_chat.parse_reply({"choices": [{"message": {
        "content": "[expression:playful]\nHello!"}}],
        "usage": {"prompt_tokens": 120, "completion_tokens": 30, "total_tokens": 150}})
    chat.receive_reply(reply)
    assert played == ["playful"]
    assert dialog.expression_label.text() == "Playful"
    assert "expression:" not in chat.page_text.toPlainText()
    assert chat.history[-1]["content"] == "Hello!"
    assert "120 in / 30 out / 150 total" in chat.token_counter.text()
    assert chat.session_tokens == 150
    # A duplicate completion must not replay sound or double-count usage.
    chat.receive_reply(reply)
    assert played == ["playful"] and chat.session_tokens == 150
    chat.pending = {"role": "user", "content": "And?"}
    dialog.change_sound(False)
    chat.receive_reply(companion_chat.ChatReply("Hmm.", "thinking", {"total_tokens": 40}))
    assert played == ["playful"]
    assert dialog.expression_label.text() == "Thinking..."
    assert chat.session_tokens == 190
    chat.pending = {"role": "user", "content": "Okay"}
    chat.receive_reply(companion_chat.ChatReply("Okay."))
    assert dialog.expression_label.text() == ""
    assert "Last: not reported" in chat.token_counter.text()
    assert "190 reported (+1 unreported)" in chat.token_counter.text()
    dialog.close()
    chat.receive_reply(reply)
    assert played == ["playful"]


def test_previous_and_next_browse_all_exchanges_without_requests_or_reactions(qapp, monkeypatch):
    player, dialog = make_dialog()
    dialog.show()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    requests, sounds = [], []
    monkeypatch.setattr(chat.job, "start", lambda *args: requests.append(args))
    monkeypatch.setattr(dialog.sounds, "play", sounds.append)
    for index in range(12):
        chat.input.setText(f"Question {index + 1}")
        chat.send()
        chat.receive_reply(companion_chat.ChatReply(f"Answer {index + 1}", "playful", {"total_tokens": 10}))
    assert chat.page_number.text() == "Page 12 of 12"
    assert not chat.next_button.isEnabled()
    assert len(chat.history) == 20  # Model context is bounded independently of visible history.
    for _ in range(11):
        chat.previous_button.click()
    assert chat.page_number.text() == "Page 1 of 12"
    assert chat.page_text.toPlainText() == "You: Question 1\n\nAyami: Answer 1"
    assert not chat.previous_button.isEnabled()
    chat.next_button.click()
    assert chat.page_text.toPlainText() == "You: Question 2\n\nAyami: Answer 2"
    assert len(requests) == len(sounds) == 12
    assert chat.session_tokens == 120
    # Sending from an older page continues the latest conversation and opens a new page.
    chat.input.setText("Another question")
    chat.send()
    assert chat.page_number.text() == "Page 13 of 13"
    assert "Ayami is thinking" in chat.page_text.toPlainText()
    assert requests[-1][4][-2]["content"] == "Answer 12"


def test_reply_arriving_while_browsing_keeps_selected_page(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    monkeypatch.setattr(chat.job, "start", lambda *args: None)
    sounds = []
    monkeypatch.setattr(dialog.sounds, "play", sounds.append)
    chat.input.setText("Hi!")
    chat.send()
    chat.receive_reply(companion_chat.ChatReply("Hello!", "playful"))
    chat.input.setText("How are you?")
    chat.send()
    chat.previous_button.click()
    chat.receive_reply(companion_chat.ChatReply("I'm good!", "thinking"))
    assert chat.current_page == 0
    assert chat.page_text.toPlainText() == "You: Hi!\n\nAyami: Hello!"
    assert dialog.expression_label.text() == "Playful"
    assert sounds == ["playful"]
    chat.next_button.click()
    assert chat.page_text.toPlainText() == "You: How are you?\n\nAyami: I'm good!"
    assert dialog.expression_label.text() == "Thinking..."
    assert sounds == ["playful"]


def test_retry_reuses_failed_page_and_preserves_earlier_exchanges(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    monkeypatch.setattr(chat.job, "start", lambda *args: None)
    chat.input.setText("Hello")
    chat.send()
    chat.failed("Unavailable")
    assert "Message failed" in chat.page_text.toPlainText()
    chat.send()
    assert chat.page_number.text() == "Page 1 of 1"
    chat.receive_reply(companion_chat.ChatReply("Hi!"))
    assert "Message failed" not in chat.page_text.toPlainText()
    assert chat.page_text.toPlainText() == "You: Hello\n\nAyami: Hi!"
    dialog.close()
    assert chat.exchanges == []
