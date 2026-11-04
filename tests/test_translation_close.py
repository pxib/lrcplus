from types import SimpleNamespace

import pytest
from PySide6.QtWidgets import QMessageBox, QWidget, QVBoxLayout


@pytest.mark.parametrize("close_action", ["close", "reject", "accept", "save_and_close"])
def test_all_editor_close_paths_warn_and_cancel_preserves_hidden_provider_drafts(
        editor, monkeypatch, close_action):
    from ui.translation_workspace import TranslationWorkspace

    class Panel(QWidget):
        def __init__(self, owner, *_args):
            super().__init__(owner)
            self.pending = True
            self.stopped = False

        def has_unapplied_translations(self):
            return self.pending

        def shutdown(self):
            self.stopped = True

    manager = SimpleNamespace(translation_providers={
        "first": {"name": "First provider", "factory": Panel},
        "second": {"name": "Second provider", "factory": Panel},
    })
    page = QWidget()
    QVBoxLayout(page)
    editor.translation_page = page
    editor.tabs.addTab(page, "Translations")
    workspace = TranslationWorkspace(manager, editor, editor._translation_document_for_panel,
                                      editor._apply_lyrics_translations)
    editor.translation_panel = workspace
    page.layout().addWidget(workspace)
    workspace.provider.setCurrentIndex(1)
    workspace.panels["second"].pending = False  # Hidden first provider still has a draft.
    editor.lrc_path.write_text("[00:02.000]Hello\n", encoding="utf-8")
    warnings = []
    def cancel(*args):
        warnings.append(args)
        return QMessageBox.StandardButton.Cancel
    monkeypatch.setattr(QMessageBox, "warning", cancel)
    getattr(editor, close_action)()
    assert len(warnings) == 1
    assert "First provider" in warnings[0][2]
    assert warnings[0][-1] == QMessageBox.StandardButton.Cancel
    assert not editor._closed
    assert workspace.panels["first"].pending
    assert not workspace.panels["first"].stopped
    # An explicit discard permits closing and stops all providers.
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Discard)
    getattr(editor, close_action)()
    assert editor._closed
    assert all(panel.stopped for panel in workspace.panels.values())


def test_applied_translations_do_not_warn_again(editor, monkeypatch):
    from ui.translation_workspace import TranslationWorkspace

    editor.translation_panel = TranslationWorkspace(SimpleNamespace(translation_providers={}),
        editor, editor._translation_document_for_panel, editor._apply_lyrics_translations)
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: pytest.fail("No unapplied translations"))
    editor.reject()
    assert editor._closed
