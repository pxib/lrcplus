"""Provider-neutral lyrics editor translation workspace."""
from PySide6.QtWidgets import QWidget, QVBoxLayout, QHBoxLayout, QLabel, QComboBox, QStackedWidget, QMessageBox


class TranslationWorkspace(QWidget):
    def __init__(self, manager, editor, document_loader, apply_translations):
        super().__init__(editor)
        self.manager = manager
        self.editor = editor
        self.document_loader = document_loader
        self.apply_translations = apply_translations
        self.panels = {}
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        row = QHBoxLayout()
        row.addWidget(QLabel("Provider:"))
        self.provider = QComboBox()
        self.provider.setAccessibleName("Translation provider")
        for provider_id, entry in manager.translation_providers.items():
            self.provider.addItem(entry["name"], provider_id)
        row.addWidget(self.provider, 1)
        layout.addLayout(row)
        self.stack = QStackedWidget()
        layout.addWidget(self.stack, 1)
        self.provider.currentIndexChanged.connect(self._select_provider)
        self._select_provider()

    def _select_provider(self, *_args):
        provider_id = self.provider.currentData()
        entry = self.manager.translation_providers.get(provider_id)
        if entry is None:
            return
        if provider_id not in self.panels:
            panel = entry["factory"](self.editor, self.document_loader, self.apply_translations)
            self.panels[provider_id] = panel
            self.stack.addWidget(panel)
        self.stack.setCurrentWidget(self.panels[provider_id])
        self.editor.quota_button.setEnabled(callable(getattr(self.panels[provider_id], "show_quota", None)))

    def show_quota(self):
        panel = self.stack.currentWidget()
        show = getattr(panel, "show_quota", None)
        if callable(show):
            show()

    def shutdown(self):
        for panel in self.panels.values():
            panel.shutdown()

    def confirm_close(self):
        pending = [self.provider.itemText(self.provider.findData(provider_id))
                   for provider_id, panel in self.panels.items()
                   if callable(getattr(panel, "has_unapplied_translations", None))
                   and panel.has_unapplied_translations()]
        if not pending:
            return True
        answer = QMessageBox.warning(
            self.editor, "Unapplied Translations",
            "Closing will discard unapplied translations and cancel any running translation for:\n\n"
            + "\n".join(pending)
            + "\n\nUse Apply to lyrics editor before saving to keep these translations. "
              "Saving the lyrics alone does not include unapplied translations.",
            QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Cancel,
        )
        return answer == QMessageBox.StandardButton.Discard
