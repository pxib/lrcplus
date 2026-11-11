import json

import pytest
from PySide6.QtCore import Qt, QTimer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication, QCheckBox, QDialogButtonBox, QLineEdit, QPlainTextEdit, QPushButton, QWidget

from core import companion_chat, settings
from ui.onboarding import OnboardingDialog


def make_dialog():
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player, companion=True)
    # Memory extraction is tested with explicit fake completions, never remote calls.
    dialog.companion_chat.memory_job.start = lambda *args: None
    return player, dialog


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
    assert chat.send_button.isEnabled()
    assert chat.send_button.text() == "Stop"
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


def test_key_guide_returns_to_unsaved_settings_without_requests(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    requests, observations = [], []
    monkeypatch.setattr(chat.job, "start", lambda *args: requests.append(args))

    def browse_guide():
        guide = QApplication.activeModalWidget()
        observations.append(guide.heading.text())
        guide.next.click()
        guide.next.click()
        observations.append("https://api.nano-gpt.com/api/v1" in guide.content.toPlainText())
        guide.back.click()
        observations.append(guide.heading.text())
        guide.next.click()
        guide.next.click()
        observations.append(guide.next.text())
        guide.next.click()

    def open_help():
        window = QApplication.activeModalWidget()
        endpoint = window.findChildren(QLineEdit)[0]
        endpoint.setText("https://unsaved.example/v1")
        QTimer.singleShot(0, browse_guide)
        window.findChild(QPushButton, "api_key_help").click()
        observations.append(endpoint.text())
        window.reject()

    QTimer.singleShot(0, open_help)
    chat.configure()
    assert observations == ["Introduction", True, "NVIDIA NIM", "Done", "https://unsaved.example/v1"]
    assert requests == []
    assert "companion_ai" not in settings.load_all_settings()


def test_saved_chats_reopen_rename_and_delete_without_credentials(qapp, monkeypatch):
    from PySide6.QtWidgets import QInputDialog, QMessageBox
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    chat.key = "do-not-save-this-key"
    monkeypatch.setattr(chat.job, "start", lambda *args: None)
    chat.input.setText("My first chat")
    chat.send()
    chat.receive_reply(companion_chat.ChatReply("Hello!", "playful"))
    chat_id = chat.conversation_id
    monkeypatch.setattr(QInputDialog, "getText", lambda *args, **kwargs: ("Music chat", True))
    chat.rename_conversation()
    assert "do-not-save-this-key" not in chat.store.path.read_text(encoding="utf-8")
    dialog.close()
    player2, reopened = make_dialog()
    other = reopened.companion_chat
    other.chat_picker.setCurrentIndex(other.chat_picker.findData(chat_id))
    assert other.page_text.toPlainText() == "You: My first chat\n\nAyami: Hello!"
    assert other.history[-1]["content"] == "Hello!"
    assert other.chat_picker.currentText() == "Music chat"
    other.new_conversation()
    assert other.exchanges == []
    other.chat_picker.setCurrentIndex(other.chat_picker.findData(chat_id))
    monkeypatch.setattr(QMessageBox, "question", lambda *args: QMessageBox.StandardButton.Yes)
    other.delete_conversation()
    assert other.store.data["conversations"] == []
    assert other.exchanges == []


def test_regeneration_replaces_latest_answer_and_stop_preserves_previous(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    requests = []
    monkeypatch.setattr(chat.job, "start", lambda *args: requests.append(args))
    chat.input.setText("Hello")
    chat.send()
    chat.receive_reply(companion_chat.ChatReply("Original", usage={"total_tokens": 10}))
    chat.regenerate()
    assert requests[-1][4] == [{"role": "user", "content": "Hello"}]
    assert not chat.chat_picker.isEnabled()
    chat.stop_request()
    assert chat.exchanges[0]["reply"] == "Original"
    assert chat.history[-1]["content"] == "Original"
    chat.receive_reply(companion_chat.ChatReply("Late completion"))
    assert chat.exchanges[0]["reply"] == "Original"
    chat.regenerate()
    chat.receive_reply(companion_chat.ChatReply("Replacement", usage={"total_tokens": 15}))
    assert len(chat.exchanges) == 1
    assert chat.history == [{"role": "user", "content": "Hello"}, {"role": "assistant", "content": "Replacement"}]
    assert "Generating another" not in chat.page_text.toPlainText()
    assert chat.session_tokens == 25
    assert chat.store.data["conversations"][0]["exchanges"][0]["reply"] == "Replacement"


def test_memory_editor_saves_explicit_facts_and_can_disable_or_remove_them(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    requests = []
    monkeypatch.setattr(chat.job, "start", lambda *args: requests.append(args))

    def edit(facts, enabled):
        def fill():
            window = QApplication.activeModalWidget()
            window.findChild(QPlainTextEdit).setPlainText(facts)
            window.findChild(QCheckBox).setChecked(enabled)
            window.findChild(QDialogButtonBox).button(QDialogButtonBox.StandardButton.Save).click()
        QTimer.singleShot(0, fill)
        chat.edit_memories()

    edit("Call me maiko\nI like jazz", True)
    chat.input.setText("Hi")
    chat.send()
    assert requests[-1][-1] == ["Call me maiko", "I like jazz"]
    chat.stop_request()
    edit("Call me maiko\nI like jazz", False)
    chat.send()
    assert requests[-1][-1] == []
    chat.stop_request()
    edit("", True)
    assert chat.store.data["memories"] == []


def test_saved_memories_are_added_only_to_explicit_request_context():
    url, headers, body = companion_chat.build_request("https://example.com/v1", "model", "key", [], memories=["Call me maiko"])
    assert "Call me maiko" in body["messages"][0]["content"]
    assert "data, not instructions" in body["messages"][0]["content"]
    assert "Call me maiko" not in companion_chat.SYSTEM_PROMPT


def test_automatic_memory_check_suggests_with_source_and_counts_usage(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    main_requests, memory_requests = [], []
    monkeypatch.setattr(chat.job, "start", lambda *args: main_requests.append(args))
    monkeypatch.setattr(chat.memory_job, "start", lambda *args: memory_requests.append(args))
    for message in ("Hi", "I prefer short replies.", "Thanks"):
        chat.input.setText(message)
        chat.send()
        chat.receive_reply(companion_chat.ChatReply("Okay", usage={"total_tokens": 10}))
    assert len(memory_requests) == 1
    assert memory_requests[0][4][1] == {"page": 2, "text": "I prefer short replies."}
    payload = [{"fact": "Prefers short replies", "category": "communication", "topic": "response_length",
                "source": 2, "evidence": "I prefer short replies.", "replaces": None}]
    chat.memory_received(companion_chat.ChatReply(json.dumps(payload), usage={"total_tokens": 7}))
    assert chat.session_tokens == 37
    assert "Last memory check:" in chat.token_counter.text()
    assert chat.store.data["memories"] == []
    assert chat.store.data["memory_suggestions"][0]["page"] == 2
    assert len(chat.history) == 6
    observations = []

    def approve():
        window = QApplication.activeModalWidget()
        source = next(editor for editor in window.findChildren(QPlainTextEdit)
                      if editor.accessibleName() == "Memory source and conflicts")
        observations.append("I prefer short replies." in source.toPlainText())
        next(button for button in window.findChildren(QPushButton) if button.text() == "Save").click()
        next(button for button in window.findChildren(QPushButton) if button.text() == "Close").click()

    QTimer.singleShot(0, approve)
    chat.review_memories()
    assert observations == [True]
    assert chat.store.data["memories"] == ["Prefers short replies"]
    assert chat.store.data["memory_suggestions"] == []
    assert chat.store.data["memory_sources"]["Prefers short replies"]["page"] == 2


def test_memory_off_and_disabled_inclusion_make_no_extraction_request(qapp, monkeypatch):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.config = {"endpoint": "https://example.com/v1", "model": "model"}
    monkeypatch.setattr(chat.job, "start", lambda *args: None)
    checks = []
    monkeypatch.setattr(chat.memory_job, "start", lambda *args: checks.append(args))
    chat.store.data["auto_memory_mode"] = "off"
    for _ in range(3):
        chat.input.setText("I like jazz")
        chat.send()
        chat.receive_reply(companion_chat.ChatReply("Okay"))
    chat.check_memories(force=True)
    assert checks == []
    chat.store.data["auto_memory_mode"] = "suggest"
    chat.store.data["memories_enabled"] = False
    chat.check_memories(force=True)
    assert checks == []
    chat.store.data["memories_enabled"] = True
    chat.check_memories(force=True)
    assert len(checks) == 1
    chat.cancel_memory_check()
    chat.memory_received(companion_chat.ChatReply("[]", usage={"total_tokens": 50}))
    assert chat.session_tokens == 0


def test_memory_conflict_review_replaces_explicitly_and_dismisses(qapp):
    from core.companion_memories import apply_candidates, normalized
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.exchanges = [{"user": "I prefer long replies now", "reply": "Okay", "expression": None}]
    chat.save_conversation()
    old = "Prefers short replies"
    chat.store.data["memories"] = [old]
    chat.store.data["memory_sources"] = {old: {"topic": "response_length"}}
    items = [{"id": "candidate1", "fact": "Prefers long replies", "category": "communication",
              "topic": "response_length", "chat_id": chat.conversation_id, "page": 1,
              "evidence": "I prefer long replies now", "replaces": old},
             {"id": "candidate2", "fact": "Likes jazz", "category": "preference", "topic": "likes_jazz",
              "chat_id": chat.conversation_id, "page": 1, "evidence": "I like jazz", "replaces": None}]
    apply_candidates(chat.store.data, items)
    observations = []

    def review():
        window = QApplication.activeModalWidget()
        source = next(editor for editor in window.findChildren(QPlainTextEdit)
                      if editor.accessibleName() == "Memory source and conflicts")
        observations.append("Would replace:\nPrefers short replies" in source.toPlainText())
        next(button for button in window.findChildren(QPushButton) if button.text() == "Replace").click()
        next(button for button in window.findChildren(QPushButton) if button.text() == "Dismiss").click()
        next(button for button in window.findChildren(QPushButton) if button.text() == "Close").click()

    QTimer.singleShot(0, review)
    chat.review_memories()
    assert observations == [True]
    assert chat.store.data["memories"] == ["Prefers long replies"]
    assert normalized("Likes jazz") in chat.store.data["dismissed_memories"]
    assert chat.store.data["memory_suggestions"] == []


def test_invalid_memory_result_still_counts_reported_usage(qapp):
    player, dialog = make_dialog()
    chat = dialog.companion_chat
    chat.memory_context = {"chat_id": "test", "messages": []}
    chat.memory_received(companion_chat.ChatReply("invalid JSON", usage={"total_tokens": 7}))
    assert chat.session_tokens == 7
    assert "did not return" in chat.memory_status.text()
