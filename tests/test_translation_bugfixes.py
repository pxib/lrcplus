import pytest
from PySide6.QtWidgets import QMessageBox

from test_lyrics_translator import selected_panel
from test_argos_translation_dialog import api, fake_native_packages, wait_for
from test_translation_providers import transport
from ui.translation_panel import TranslationDialog


def test_reload_cancel_keeps_reviewed_translations(selected_panel, monkeypatch):
    panel = selected_panel
    panel.table.item(0, 1).setText("Reviewed translation")
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel)
    panel.load_current()
    assert panel.table.item(0, 1).text() == "Reviewed translation"


def test_edit_after_swap_does_not_relabel_an_existing_translation(selected_panel):
    panel = selected_panel
    panel.table.item(0, 1).setText("English draft")
    assert panel.pair() == ("ja", "en")
    panel.swap_button.click()
    assert panel.pair() == ("en", "ja")
    panel.table.item(0, 1).setText("Edited English draft")
    assert panel._current_translations() == {}
    panel.swap_button.click()
    assert panel._current_translations() == {0: "Edited English draft"}


@pytest.mark.parametrize("action", ["close", "reject"])
def test_standalone_cancel_close_keeps_drafts(qapp, api, transport, monkeypatch, action):
    dialog = TranslationDialog(api, cloud_provider="mymemory")
    try:
        wait_for(qapp, lambda: dialog.source.count() > 0 and not dialog._busy)
        dialog.table.item(0, 1).setText("Reviewed")
        monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Cancel)
        dialog.show()
        getattr(dialog, action)()
        assert dialog.isVisible()
        assert dialog.has_unapplied_translations()
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()


def test_successful_standalone_save_clears_unsaved_state(qapp, api, transport, monkeypatch, tmp_path):
    dialog = TranslationDialog(api, cloud_provider="mymemory")
    try:
        wait_for(qapp, lambda: dialog.source.count() > 0 and not dialog._busy)
        dialog.table.item(0, 1).setText("Saved translation")
        monkeypatch.setattr("ui.translation_panel.QFileDialog.getSaveFileName",
                            lambda *args: (str(tmp_path / "output.lrcx"), ""))
        dialog.save()
        assert not dialog.has_unapplied_translations()
        dialog.table.item(0, 1).setText("Unsaved edit")
        assert dialog.has_unapplied_translations()
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()


def test_shutdown_suppresses_deferred_worker_start(qapp, api, monkeypatch):
    dialog = TranslationDialog(api)
    calls = []
    monkeypatch.setattr(dialog.job, "start", calls.append)
    dialog.shutdown()
    qapp.processEvents()
    assert not calls
    dialog.close()
    dialog.deleteLater()


def test_refresh_models_keeps_the_selected_language_pair(qapp, api, fake_native_packages):
    dialog = TranslationDialog(api)
    try:
        wait_for(qapp, lambda: dialog.source.count() > 0 and not dialog._busy)
        dialog.target.setCurrentIndex(dialog.target.findData("zh"))
        dialog.refresh_button.click()
        wait_for(qapp, lambda: not dialog._busy)
        assert dialog.pair() == ("ja", "zh")
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()


def test_standalone_reopens_after_shutdown(qapp, api, fake_native_packages):
    from plugins.community.lyrics_translator.plugin import LyricsTranslatorPlugin
    plugin = LyricsTranslatorPlugin()
    plugin.api = api
    try:
        plugin.open_dialog()
        dialog = plugin.dialog
        wait_for(qapp, lambda: dialog.source.count() > 0 and not dialog._busy)
        dialog.close()
        assert dialog._stopped
        plugin.open_dialog()
        assert plugin.dialog is dialog and not dialog._stopped
        assert dialog.translate_button.isEnabled()
        dialog.translate_button.click()
        wait_for(qapp, lambda: not dialog._busy)
        assert dialog.table.item(0, 1).text() == "Translated First word"
    finally:
        plugin.shutdown()
