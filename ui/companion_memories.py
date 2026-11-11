"""Automatic memory suggestions, explicit review, and provenance."""

from copy import deepcopy

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QHBoxLayout, QLabel, QListWidget, QPlainTextEdit, QPushButton, QVBoxLayout, QWidget

from core.companion_job import CompanionJob
from core.companion_memories import apply_candidates, build_memory_request, conflicts_for, normalized, parse_candidates


class AutomaticMemories:
    def init_auto_memories(self, layout):
        self.memory_context = None
        self.memory_job = CompanionJob(self, request_builder=build_memory_request)
        self.memory_job.finished.connect(self.memory_received)
        self.memory_job.failed.connect(self.memory_failed)
        self.memory_notice = QWidget()
        row = QHBoxLayout(self.memory_notice)
        row.setContentsMargins(0, 0, 0, 0)
        self.memory_status = QLabel()
        self.memory_status.setWordWrap(True)
        self.memory_status.setTextFormat(Qt.TextFormat.PlainText)
        row.addWidget(self.memory_status, 1)
        self.review_memories_button = QPushButton("Review")
        self.review_memories_button.setAutoDefault(False)
        self.review_memories_button.clicked.connect(self.review_memories)
        row.addWidget(self.review_memories_button)
        self.stop_memory_button = QPushButton("Stop check")
        self.stop_memory_button.setAutoDefault(False)
        self.stop_memory_button.clicked.connect(self.cancel_memory_check)
        row.addWidget(self.stop_memory_button)
        layout.addWidget(self.memory_notice)
        self.update_memory_notice()

    def update_memory_notice(self, message=None):
        count = len(self.store.data.get("memory_suggestions", []))
        running = self.memory_context is not None
        self.review_memories_button.setVisible(bool(count))
        self.review_memories_button.setEnabled(bool(count))
        self.review_memories_button.setText(f"Review ({count})")
        self.stop_memory_button.setVisible(running)
        self.memory_status.setText(message or ("Checking for memories…" if running else f"{count} suggested memories awaiting review"))
        self.memory_notice.setVisible(bool(count or running or message))
        self.wizard._fit_content()

    def cancel_memory_check(self):
        self.memory_job.cancel()
        self.memory_context = None
        self.update_memory_notice()

    def check_memories(self, force=False):
        if (self.memory_context is not None or self.pending is not None or self.wizard._dialogue_closed
                or self.store.error or not self.store.data.get("memories_enabled", True)
                or self.store.data.get("auto_memory_mode", "suggest") == "off"
                or not self.config.get("endpoint") or not self.config.get("model")
                or not self.conversation_id):
            return
        checked = self.store.data.get("memory_checked_through", {}).get(self.conversation_id, 0)
        messages = [{"page": index + 1, "text": item["user"]} for index, item in enumerate(self.exchanges)
                    if item.get("reply") is not None and (force or index + 1 > checked)]
        if not messages or not force and len(messages) < 3:
            return
        # Up to ten newly completed user messages; no assistant dialogue is sent.
        messages = messages[:10] if not force else messages[-3:]
        data = deepcopy(self.store.data)
        data.setdefault("memory_checked_through", {})[self.conversation_id] = messages[-1]["page"]
        if not self.write_store(data):
            return
        self.memory_context = {"chat_id": self.conversation_id, "messages": messages}
        self.update_memory_notice()
        self.memory_job.start(None, self.config["endpoint"], self.config["model"], self.key,
                              messages, list(data.get("memories", [])), list(data.get("dismissed_memories", [])),
                              self.config.get("extra_body"), self.config.get("exclude_body"), dict(self.extra_headers))

    def memory_received(self, reply):
        context, self.memory_context = self.memory_context, None
        if context is None or self.wizard._dialogue_closed:
            return
        self.record_usage(reply.usage)
        self.token_counter.setText(self.token_counter.text().replace("Last:", "Last memory check:"))
        data = deepcopy(self.store.data)
        try:
            candidates = parse_candidates(reply.text, context["messages"], data.get("memories", []),
                                          data.get("dismissed_memories", []), context["chat_id"])
        except ValueError as error:
            self.update_memory_notice(str(error))
            return
        if data.get("auto_memory_mode", "suggest") != "off" and data.get("memories_enabled", True):
            before = len(data.get("memories", []))
            mode = data.get("auto_memory_mode", "suggest")
            if getattr(self, "memory_editing", False):
                data["auto_memory_mode"] = "suggest"
            apply_candidates(data, candidates)
            data["auto_memory_mode"] = mode
            if not self.write_store(data):
                self.update_memory_notice("Could not save memory suggestions.")
                return
            saved = len(data.get("memories", [])) - before
            self.update_memory_notice(f"Saved {saved} ordinary preference(s) automatically." if saved else None)
        else:
            self.update_memory_notice()

    def memory_failed(self, message):
        if self.memory_context is None:
            return
        self.memory_context = None
        self.update_memory_notice("Memory check failed. Your conversation is unaffected; use Check now to retry.")

    def memory_source_text(self, item):
        chat = next((chat for chat in self.store.data["conversations"] if chat["id"] == item.get("chat_id")), None)
        title = chat["title"] if chat else "Deleted conversation"
        return f"Source: {title}, page {item.get('page', '?')}\nYou: {item.get('evidence', '')}"

    def show_memory_sources(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Saved memories · Sources")
        dialog.resize(520, 360)
        layout = QVBoxLayout(dialog)
        text = QPlainTextEdit()
        text.setReadOnly(True)
        sections = []
        for fact in self.store.data.get("memories", []):
            source = self.store.data.get("memory_sources", {}).get(fact)
            sections.append(fact + "\n" + (self.memory_source_text(source) if source else "Manually saved or edited."))
        text.setPlainText("\n\n".join(sections) or "No saved memories.")
        layout.addWidget(text)
        close = QPushButton("Close")
        close.clicked.connect(dialog.accept)
        layout.addWidget(close)
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()

    def review_memories(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Ayami · Suggested memories")
        dialog.resize(540, 440)
        layout = QVBoxLayout(dialog)
        listing = QListWidget()
        listing.setAccessibleName("Suggested memories")
        layout.addWidget(listing)
        fact = QPlainTextEdit()
        fact.setFixedHeight(70)
        fact.setAccessibleName("Edit suggested memory")
        layout.addWidget(fact)
        source_label = QPlainTextEdit()
        source_label.setReadOnly(True)
        source_label.setAccessibleName("Memory source and conflicts")
        source_label.setFixedHeight(140)
        layout.addWidget(source_label)
        status = QLabel()
        status.setWordWrap(True)
        layout.addWidget(status)
        row = QHBoxLayout()
        save, dismiss, close = QPushButton("Save"), QPushButton("Dismiss"), QPushButton("Close")
        for button in (save, dismiss, close):
            button.setAutoDefault(False)
            row.addWidget(button)
        layout.addLayout(row)
        close.clicked.connect(dialog.accept)

        def current():
            row = listing.currentRow()
            values = self.store.data.get("memory_suggestions", [])
            return values[row] if 0 <= row < len(values) else None

        def selected():
            item = current()
            save.setEnabled(item is not None)
            dismiss.setEnabled(item is not None)
            if item is None:
                fact.clear()
                source_label.setPlainText("No pending suggestions.")
                return
            fact.setPlainText(item["fact"])
            conflicts = conflicts_for(item, self.store.data.get("memories", []), self.store.data.get("memory_sources", {}))
            source_label.setPlainText(self.memory_source_text(item) + ("\n\nWould replace:\n" + "\n".join(conflicts) if conflicts else ""))
            save.setText("Replace" if conflicts else "Save")

        def refresh():
            listing.clear()
            listing.addItems([item["fact"] for item in self.store.data.get("memory_suggestions", [])])
            listing.setCurrentRow(0)
            selected()

        def act(accept):
            item = current()
            if item is None:
                return
            data = deepcopy(self.store.data)
            facts = data.setdefault("memories", [])
            sources = data.setdefault("memory_sources", {})
            if accept:
                text = fact.toPlainText().strip()
                if not text or len(text) > 200:
                    status.setText("Use 1–200 characters for a memory.")
                    return
                conflicts = conflicts_for(item, facts, sources)
                facts[:] = [existing for existing in facts if existing not in conflicts]
                for existing in conflicts:
                    sources.pop(existing, None)
                if normalized(text) not in {normalized(existing) for existing in facts}:
                    if len(facts) >= 20:
                        status.setText("Memory is full. Remove an existing fact first.")
                        return
                    facts.append(text)
                    sources[text] = {**item, "origin": "approved"}
                    if text != item["fact"]:
                        sources[text].pop("topic", None)
            else:
                dismissed = data.setdefault("dismissed_memories", [])
                dismissed.append(normalized(item["fact"]))
                data["dismissed_memories"] = dismissed[-200:]
            data["memory_suggestions"] = [entry for entry in data["memory_suggestions"] if entry["id"] != item["id"]]
            if self.write_store(data):
                status.clear()
                refresh()
                self.update_memory_notice()

        listing.currentRowChanged.connect(selected)
        save.clicked.connect(lambda: act(True))
        dismiss.clicked.connect(lambda: act(False))
        refresh()
        try:
            dialog.exec()
        finally:
            dialog.deleteLater()
