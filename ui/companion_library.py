"""Saved conversation and explicit memory controls for the companion."""

from copy import deepcopy
from datetime import datetime, timezone
import uuid

from PySide6.QtWidgets import (
    QCheckBox, QComboBox, QDialog, QDialogButtonBox, QHBoxLayout, QInputDialog,
    QLabel, QMessageBox, QPlainTextEdit, QPushButton, QVBoxLayout,
)

from core.companion_store import CompanionStore, validate_memories
from ui.companion_memories import AutomaticMemories


class CompanionLibrary(AutomaticMemories):
    def init_library(self, layout):
        self.store = CompanionStore()
        self.conversation_id = None
        self.chat_picker = QComboBox()
        self.chat_picker.setAccessibleName("Saved conversations")
        self.chat_picker.currentIndexChanged.connect(self.select_conversation)
        layout.addWidget(self.chat_picker)
        row = QHBoxLayout()
        self.new_chat_button = QPushButton("New conversation")
        self.rename_chat_button = QPushButton("Rename")
        self.delete_chat_button = QPushButton("Delete")
        self.memories_button = QPushButton("Memories…")
        for button, callback in ((self.new_chat_button, self.new_conversation),
                                 (self.rename_chat_button, self.rename_conversation),
                                 (self.delete_chat_button, self.delete_conversation),
                                 (self.memories_button, self.edit_memories)):
            button.setAutoDefault(False)
            button.clicked.connect(callback)
            row.addWidget(button)
        layout.addLayout(row)
        self.refresh_conversations()

    def write_store(self, data):
        old = self.store.data
        self.store.data = data
        try:
            self.store.save()
        except (OSError, ValueError) as error:
            self.store.data = old
            self.status.setText("Could not save companion data. " + str(error))
            return False
        return True

    def refresh_conversations(self):
        self.chat_picker.blockSignals(True)
        self.chat_picker.clear()
        self.chat_picker.addItem("New conversation", None)
        for chat in reversed(self.store.data["conversations"]):
            self.chat_picker.addItem(chat["title"], chat["id"])
        self.chat_picker.setCurrentIndex(max(0, self.chat_picker.findData(self.conversation_id)))
        self.chat_picker.blockSignals(False)
        self.rename_chat_button.setEnabled(self.conversation_id is not None and self.pending is None)
        self.delete_chat_button.setEnabled(self.conversation_id is not None and self.pending is None)

    def save_conversation(self):
        if not self.exchanges:
            return True
        if self.conversation_id is None:
            self.conversation_id = uuid.uuid4().hex
        data = deepcopy(self.store.data)
        chat = next((item for item in data["conversations"] if item["id"] == self.conversation_id), None)
        if chat is None:
            chat = {"id": self.conversation_id, "title": self.exchanges[0]["user"][:60] or "Conversation"}
            data["conversations"].append(chat)
        chat["exchanges"] = []
        for exchange in self.exchanges:
            item = {name: exchange.get(name) for name in ("user", "reply", "expression")}
            if exchange.get("failed"):
                item["failed"] = True
            elif exchange.get("stopped") or exchange.get("reply") is None:
                item["stopped"] = True
            chat["exchanges"].append(item)
        chat["updated_at"] = datetime.now(timezone.utc).isoformat()
        if not self.write_store(data):
            return False
        self.refresh_conversations()
        return True

    def rebuild_history(self, before=None):
        history = []
        for exchange in self.exchanges[:before]:
            if exchange.get("reply") is not None:
                history.extend([{"role": "user", "content": exchange["user"]},
                                {"role": "assistant", "content": exchange["reply"]}])
        return history[-20:]

    def reset_conversation(self):
        self.cancel_memory_check()
        self.wizard._cancel_dialogue_activity()
        self.wizard.dialogue_lines = []
        self.wizard.space.setEnabled(False)
        self.wizard.dialogue_indicator.hide()
        self.wizard.next.setEnabled(True)
        self.history.clear()
        self.clear_pages()
        self.input.clear()
        self.wizard.description.setText("Ready when you are. Start a new conversation or reopen a saved one.")
        self.wizard._reserve_dialogue_height(self.wizard.description.text())
        self.regenerate_button.setEnabled(False)

    def new_conversation(self):
        if self.pending is not None:
            return
        self.conversation_id = None
        self.reset_conversation()
        self.refresh_conversations()
        self.status.setText("New conversation · Chats are saved locally")

    def select_conversation(self, index):
        if self.pending is not None:
            return
        chat_id = self.chat_picker.itemData(index)
        if chat_id is None:
            self.new_conversation()
            return
        chat = next((item for item in self.store.data["conversations"] if item["id"] == chat_id), None)
        if chat is None:
            return
        self.reset_conversation()
        self.conversation_id = chat_id
        self.exchanges = deepcopy(chat["exchanges"])
        self.history = self.rebuild_history()
        if self.exchanges:
            self.show_page(len(self.exchanges) - 1)
        self.refresh_conversations()
        self.set_busy(False)
        self.status.setText("Saved conversation · Send continues with the selected AI provider")

    def rename_conversation(self):
        chat = next((item for item in self.store.data["conversations"] if item["id"] == self.conversation_id), None)
        if chat is None:
            return
        title, accepted = QInputDialog.getText(self, "Rename conversation", "Name", text=chat["title"])
        if accepted and title.strip():
            data = deepcopy(self.store.data)
            next(item for item in data["conversations"] if item["id"] == self.conversation_id)["title"] = title.strip()[:100]
            if self.write_store(data):
                self.refresh_conversations()

    def delete_conversation(self):
        if self.conversation_id is None:
            return
        if QMessageBox.question(self, "Delete conversation?", "Delete this saved conversation?",
                                QMessageBox.StandardButton.Yes | QMessageBox.StandardButton.No,
                                QMessageBox.StandardButton.No) != QMessageBox.StandardButton.Yes:
            return
        data = deepcopy(self.store.data)
        data["conversations"] = [item for item in data["conversations"] if item["id"] != self.conversation_id]
        if self.write_store(data):
            self.new_conversation()

    def edit_memories(self):
        dialog = QDialog(self)
        dialog.setWindowTitle("Ayami · Saved memories")
        dialog.resize(540, 450)
        layout = QVBoxLayout(dialog)
        info = QLabel("Write one fact per line, such as your preferred name or favorite genre. "
                      "These are saved locally and shared with the selected AI provider when enabled. "
                      "Automatic extraction uses extra API requests. Suggestions need approval by default. "
                      "Up to 20 facts, 200 characters each.")
        info.setWordWrap(True)
        layout.addWidget(info)
        enabled = QCheckBox("Include these memories in future messages")
        enabled.setChecked(self.store.data.get("memories_enabled", True))
        layout.addWidget(enabled)
        mode = QComboBox()
        mode.setAccessibleName("Automatic memory mode")
        mode.addItem("Off", "off")
        mode.addItem("Suggest for approval", "suggest")
        mode.addItem("Automatically save ordinary preferences", "auto")
        mode.setCurrentIndex(max(0, mode.findData(self.store.data.get("auto_memory_mode", "suggest"))))
        layout.addWidget(QLabel("Check for new facts every 3 completed exchanges:"))
        layout.addWidget(mode)
        tools = QHBoxLayout()
        review = QPushButton(f"Review suggestions ({len(self.store.data.get('memory_suggestions', []))})")
        review.setEnabled(bool(self.store.data.get("memory_suggestions")))
        sources = QPushButton("Sources…")
        sources.clicked.connect(self.show_memory_sources)
        check = QPushButton("Check now")
        check.setToolTip("Save these settings and check recent messages. Uses an API request.")
        for button in (review, sources, check):
            button.setAutoDefault(False)
            tools.addWidget(button)
        layout.addLayout(tools)
        editor = QPlainTextEdit("\n".join(self.store.data.get("memories", [])))
        editor.setAccessibleName("Saved memory facts, one per line")
        layout.addWidget(editor)
        error = QLabel()
        error.setWordWrap(True)
        layout.addWidget(error)
        buttons = QDialogButtonBox(QDialogButtonBox.StandardButton.Save | QDialogButtonBox.StandardButton.Cancel)
        layout.addWidget(buttons)
        buttons.rejected.connect(dialog.reject)

        def save():
            try:
                facts = validate_memories([line.strip() for line in editor.toPlainText().splitlines() if line.strip()])
            except ValueError as failure:
                error.setText(str(failure))
                return
            data = deepcopy(self.store.data)
            from core.companion_memories import normalized
            removed = [normalized(fact) for fact in data.get("memories", []) if fact not in facts]
            data["dismissed_memories"] = [*data.get("dismissed_memories", []), *removed][-200:]
            data.update(memories=facts, memories_enabled=enabled.isChecked(), auto_memory_mode=mode.currentData())
            data["memory_sources"] = {fact: source for fact, source in data.get("memory_sources", {}).items() if fact in facts}
            if self.write_store(data):
                if not enabled.isChecked() or mode.currentData() == "off":
                    self.cancel_memory_check()
                dialog.accept()
                return True
            else:
                error.setText(self.status.text())
            return False

        buttons.accepted.connect(save)
        check.clicked.connect(lambda: self.check_memories(force=True) if save() else None)
        review.clicked.connect(lambda: self.review_memories() if save() else None)
        self.memory_editing = True
        try:
            dialog.exec()
        finally:
            self.memory_editing = False
            dialog.deleteLater()
