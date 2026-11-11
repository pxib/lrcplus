"""Returning-user companion controls, loaded only when the wizard is reopened."""

import json

from PySide6.QtCore import QEvent, QRect, Qt
from PySide6.QtWidgets import (
    QCheckBox, QDialog, QDialogButtonBox, QFormLayout, QFrame, QHBoxLayout, QLabel,
    QLineEdit, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget,
)

from core import settings
from core.background_job import BackgroundJob
from core.companion_chat import EXPRESSIONS, parse_advanced, request_reply, validate_advanced, validate_config
from core.secret_storage import SecretStorageError, decrypt_secret, encrypt_secret


class CompanionChat(QWidget):
    def __init__(self, wizard):
        super().__init__(wizard)
        self.wizard = wizard
        self.history = []
        self.exchanges = []
        self.current_page = -1
        self.pending_page = None
        self.session_tokens = 0
        self.unreported_replies = 0
        self.pending = None
        self.config = settings.load_all_settings().get("companion_ai", {})
        if not isinstance(self.config, dict):
            self.config = {}
        self.key = ""
        self.extra_headers = {}
        self.key_error = ""
        try:
            self.key = decrypt_secret(self.config.get("key"), "companion_ai")
        except SecretStorageError as error:
            self.key_error = str(error)
        try:
            saved_headers = decrypt_secret(self.config.get("headers_secret"), "companion_ai_headers")
            if saved_headers:
                self.extra_headers = validate_advanced(extra_headers=json.loads(saved_headers))[2]
        except (SecretStorageError, ValueError, TypeError):
            self.key_error = "Could not restore saved request headers. Enter them again in Advanced parameters."
        self.job = BackgroundJob(self, "companion-chat")
        self.job.finished.connect(self.receive_reply)
        self.job.failed.connect(self.failed)
        executor = self.job._executor
        self.destroyed.connect(lambda: executor.shutdown(wait=False, cancel_futures=True))
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        self.pages = QWidget()
        page_layout = QVBoxLayout(self.pages)
        page_layout.setContentsMargins(0, 0, 0, 0)
        self.page_text = QPlainTextEdit()
        self.page_text.setReadOnly(True)
        self.page_text.setAccessibleName("Conversation page")
        self.page_text.setFrameShape(QFrame.Shape.NoFrame)
        self.page_text.setFixedHeight(100)
        page_layout.addWidget(self.page_text)
        navigation = QHBoxLayout()
        self.previous_button = QPushButton("Previous")
        self.next_button = QPushButton("Next")
        self.page_number = QLabel("Page 0 of 0")
        self.page_number.setAccessibleName("Conversation page number")
        self.page_number.setAlignment(Qt.AlignmentFlag.AlignCenter)
        for button in (self.previous_button, self.next_button):
            button.setAutoDefault(False)
        self.previous_button.clicked.connect(lambda: self.show_page(self.current_page - 1))
        self.next_button.clicked.connect(lambda: self.show_page(self.current_page + 1))
        navigation.addWidget(self.previous_button)
        navigation.addWidget(self.page_number, 1)
        navigation.addWidget(self.next_button)
        page_layout.addLayout(navigation)
        self.pages.hide()
        layout.addWidget(self.pages)
        row = QHBoxLayout()
        self.input = QLineEdit()
        self.input.setPlaceholderText("Ask Ayami something…")
        self.input.setAccessibleName("Message to Ayami")
        self.input.setMaxLength(4000)
        self.input.installEventFilter(self)
        self.input.returnPressed.connect(self.send)
        self.send_button = QPushButton("Send")
        self.send_button.setAutoDefault(False)
        self.send_button.clicked.connect(self.send)
        self.configure_button = QPushButton("AI settings…")
        self.configure_button.setAutoDefault(False)
        self.configure_button.clicked.connect(self.configure)
        row.addWidget(self.input, 1)
        row.addWidget(self.send_button)
        row.addWidget(self.configure_button)
        layout.addLayout(row)
        self.status = QLabel("Optional AI · Uses your provider and key · Chat stays in this window")
        self.status.setWordWrap(True)
        layout.addWidget(self.status)
        self.token_counter = QLabel("Tokens · No usage reported yet")
        self.token_counter.setAccessibleName("AI token usage")
        self.token_counter.setWordWrap(True)
        self.token_counter.setTextFormat(Qt.TextFormat.PlainText)
        layout.addWidget(self.token_counter)
        self.update_token_tooltip()

    def show_page(self, index):
        if not 0 <= index < len(self.exchanges):
            return
        self.current_page = index
        exchange = self.exchanges[index]
        if exchange.get("failed"):
            answer = "Message failed. Edit or send it again."
        elif exchange.get("reply") is None:
            answer = "Ayami is thinking…"
        else:
            answer = "Ayami: " + exchange["reply"]
        self.wizard._reserve_dialogue_height("")
        text = "You: " + exchange["user"] + "\n\n" + answer
        self.page_text.setPlainText(text)
        text_height = self.page_text.fontMetrics().boundingRect(
            QRect(0, 0, max(1, self.wizard.width() - 50), 0),
            Qt.TextFlag.TextWordWrap | Qt.TextFlag.TextExpandTabs, text).height()
        self.page_text.setFixedHeight(min(240, max(64, text_height + 20)))
        self.page_number.setText(f"Page {index + 1} of {len(self.exchanges)}")
        self.previous_button.setEnabled(index > 0)
        self.next_button.setEnabled(index + 1 < len(self.exchanges))
        self.wizard.show_expression(exchange.get("expression"))
        self.pages.show()
        self.wizard._fit_content()

    def clear_pages(self):
        self.exchanges.clear()
        self.current_page = -1
        self.pending_page = None
        self.page_text.clear()
        self.pages.hide()
        self.wizard.show_expression()

    def pending_exchange(self):
        if self.pending_page is None:
            self.exchanges.append({"user": self.pending["content"], "reply": None})
            self.pending_page = len(self.exchanges) - 1
            self.current_page = self.pending_page
        return self.exchanges[self.pending_page]

    def update_token_tooltip(self, usage=None):
        description = ("Provider-reported usage for requests in this window, including instructions and resent history. "
                       "This is not the current context size or a billing estimate. Failed requests may still consume tokens. "
                       "Cached and reasoning token details, when provided, are already part of the provider's counts.")
        if usage:
            details = [f"{name.replace('_', ' ')}: {count:,}" for name, count in usage.items()]
            description += "\nLatest reply: " + "; ".join(details)
        self.token_counter.setToolTip(description)

    def record_usage(self, usage):
        usage = usage or {}
        total = usage.get("total_tokens")
        if total is None:
            self.unreported_replies += 1
        else:
            self.session_tokens += total
        if usage:
            count = lambda name: f"{usage[name]:,}" if name in usage else "?"
            latest = f"{count('prompt_tokens')} in / {count('completion_tokens')} out / {count('total_tokens')} total"
        else:
            latest = "not reported"
        session = f"{self.session_tokens:,} reported"
        if self.unreported_replies:
            session += f" (+{self.unreported_replies} unreported)"
        self.token_counter.setText(f"Tokens · Last: {latest} · Session: {session}")
        self.update_token_tooltip(usage)

    def eventFilter(self, watched, event):
        if watched is self.input and event.type() == QEvent.Type.ShortcutOverride:
            if event.key() in (Qt.Key.Key_Space, Qt.Key.Key_Return, Qt.Key.Key_Enter):
                event.accept()
                return True
        return super().eventFilter(watched, event)

    def configure(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Ayami · AI settings")
        dialog.setMinimumWidth(560)
        form = QFormLayout(dialog)
        notice = QLabel("Use a provider with an OpenAI-compatible Chat Completions API. "
                        "Sending a message shares this chat and Ayami's character instructions with that provider; charges may apply. "
                        "Songs, lyrics, and settings are not attached.")
        notice.setWordWrap(True)
        form.addRow(notice)
        endpoint = QLineEdit(self.config.get("endpoint", ""))
        endpoint.setPlaceholderText("https://your-provider.example/v1")
        model = QLineEdit(self.config.get("model", ""))
        key = QLineEdit(self.key)
        key.setEchoMode(QLineEdit.EchoMode.Password)
        key.setPlaceholderText("Optional for local servers")
        remember = QCheckBox("Remember key and custom headers encrypted for this Windows account")
        remember.setChecked(bool(self.config.get("key") or self.config.get("headers_secret")))
        form.addRow("API base URL", endpoint)
        form.addRow("Model ID", model)
        form.addRow("API key", key)
        form.addRow(remember)
        advanced_toggle = QPushButton("Advanced parameters")
        advanced_toggle.setCheckable(True)
        advanced_toggle.setAutoDefault(False)
        form.addRow(advanced_toggle)
        advanced = QWidget()
        advanced_form = QFormLayout(advanced)
        advanced_form.setContentsMargins(0, 0, 0, 0)
        body = QPlainTextEdit()
        body.setObjectName("extra_body")
        body.setAccessibleName("Additional body parameters")
        body.setPlaceholderText('{"service_tier": "flex", "reasoning_effort": "low"}')
        body.setPlainText(json.dumps(self.config.get("extra_body", {}), indent=2, ensure_ascii=False))
        excluded = QPlainTextEdit()
        excluded.setObjectName("exclude_body")
        excluded.setAccessibleName("Excluded body parameters")
        excluded.setPlaceholderText('["temperature", "top_p"]')
        excluded.setPlainText(json.dumps(self.config.get("exclude_body", []), indent=2))
        headers = QPlainTextEdit()
        headers.setObjectName("extra_headers")
        headers.setAccessibleName("Additional request headers")
        headers.setPlaceholderText('{"X-Custom-Header": "value"}')
        headers.setPlainText(json.dumps(self.extra_headers, indent=2))
        for label, editor in (("Include body parameters (JSON object)", body),
                              ("Exclude body parameters (JSON array)", excluded),
                              ("Include request headers (JSON object)", headers)):
            editor.setFixedHeight(75)
            advanced_form.addRow(QLabel(label))
            advanced_form.addRow(editor)
        hint = QLabel("Exclusions are applied last. Model, messages, and stream are managed by the app. "
                      "Use the key field for Bearer authentication. Custom headers stay session-only unless remembered; "
                      "do not put credentials in body parameters.")
        hint.setWordWrap(True)
        advanced_form.addRow(hint)
        form.addRow(advanced)
        advanced.hide()
        advanced_toggle.toggled.connect(advanced.setVisible)
        error_label = QLabel(self.key_error)
        error_label.setWordWrap(True)
        form.addRow(error_label)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        form.addRow(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            storage_notice = ""
            try:
                url, model_id = validate_config(endpoint.text(), model.text())
                extra_body, exclude_body, extra_headers = parse_advanced(
                    body.toPlainText(), excluded.toPlainText(), headers.toPlainText())
                config = {"endpoint": url, "model": model_id,
                          "extra_body": extra_body, "exclude_body": exclude_body}
                if remember.isChecked():
                    try:
                        if key.text().strip():
                            config["key"] = encrypt_secret(key.text().strip(), "companion_ai")
                        if extra_headers:
                            config["headers_secret"] = encrypt_secret(json.dumps(extra_headers), "companion_ai_headers")
                    except SecretStorageError as error:
                        config.pop("key", None)
                        config.pop("headers_secret", None)
                        storage_notice = "Could not encrypt credentials. The key and custom headers remain session-only."
                payload = settings.load_all_settings()
                payload["companion_ai"] = config
                settings.save_all_settings(payload)
            except (ValueError, SecretStorageError, OSError) as error:
                error_label.setText(str(error))
                return
            # Switching providers starts a fresh chat, avoiding accidental history sharing.
            if config != self.config or key.text().strip() != self.key or extra_headers != self.extra_headers:
                self.history.clear()
                self.clear_pages()
            self.config, self.key = config, key.text().strip()
            self.extra_headers = extra_headers
            self.key_error = ""
            self.status.setText("AI configured. Credentials are encrypted on disk." if config.get("key") or config.get("headers_secret") else
                                "AI configured. Credentials are kept only while this window is open.")
            if storage_notice:
                self.status.setText(storage_notice)
            dialog.accept()

        buttons.accepted.connect(save)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def send(self):
        text = self.input.text().strip()
        if not text or self.pending is not None:
            return
        if not self.config.get("endpoint") or not self.config.get("model"):
            self.configure()
            if not self.config.get("endpoint") or not self.config.get("model"):
                return
        self.wizard._cancel_dialogue_activity()
        self.wizard.dialogue_lines = []
        self.wizard.space.setEnabled(False)
        self.wizard.dialogue_indicator.hide()
        self.wizard.next.setEnabled(True)
        self.wizard.title.setText("Ayami")
        self.pending = {"role": "user", "content": text}
        # Retrying an unchanged failed message reuses its page.
        if self.exchanges and self.exchanges[-1].get("failed") and self.exchanges[-1]["user"] == text:
            self.pending_page = len(self.exchanges) - 1
            self.exchanges[self.pending_page] = {"user": text, "reply": None}
        else:
            self.exchanges.append({"user": text, "reply": None})
            self.pending_page = len(self.exchanges) - 1
        self.show_page(self.pending_page)
        self.input.clear()
        self.set_busy(True)
        self.status.setText("Ayami is thinking…")
        self.job.start(request_reply, self.config["endpoint"], self.config["model"], self.key,
                       [*self.history[-20:], self.pending], self.config.get("extra_body"),
                       self.config.get("exclude_body"), dict(self.extra_headers))
        self.wizard._fit_content()

    def set_busy(self, busy):
        self.input.setEnabled(not busy)
        self.send_button.setEnabled(not busy)
        self.configure_button.setEnabled(not busy)
        self.wizard.restart_setup.setEnabled(not busy)

    def receive_reply(self, reply):
        if self.pending is None or self.wizard._dialogue_closed:
            return
        text = reply.text
        self.history.extend([self.pending, {"role": "assistant", "content": text}])
        self.history = self.history[-20:]
        exchange = self.pending_exchange()
        # Only a fixed cue name can reach the existing sound engine, never model paths.
        expression = reply.expression if reply.expression in EXPRESSIONS else None
        exchange.update(reply=text, expression=expression)
        if self.current_page == self.pending_page:
            self.wizard.sounds.stop()
            self.show_page(self.current_page)
            if expression:
                self.wizard.sounds.play(expression)
        else:
            self.show_page(self.current_page)
        self.pending = None
        self.pending_page = None
        self.record_usage(reply.usage)
        self.status.setText("AI reply · Conversation is kept only until this window closes")
        self.set_busy(False)
        self.input.setFocus()

    def failed(self, message):
        if self.pending is None or self.wizard._dialogue_closed:
            return
        self.input.setText(self.pending["content"])
        self.pending_exchange()["failed"] = True
        self.show_page(self.current_page)
        self.pending = None
        self.pending_page = None
        # Never display raw transport exceptions, which can include credentials.
        self.status.setText(message.replace(self.key, "[key]") if self.key else message)
        self.set_busy(False)

    def close_chat(self):
        self.job.close()
        self.pending = None
        self.history.clear()
        self.clear_pages()
        self.key = ""
        self.extra_headers.clear()
