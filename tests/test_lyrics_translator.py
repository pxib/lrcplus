import json
from pathlib import Path
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QItemSelectionModel

from core.plugin_manager import PluginManager
from core.settings import load_all_settings, save_all_settings
from core.translation_document import document_from_lines
from lyrics.models import LyricLine
from plugins.community.lyrics_translator.plugin import LyricsTranslatorPlugin
from ui.translation_panel import TranslationPanel
from test_argos_translation_dialog import api, fake_native_packages, wait_for
from test_translation_providers import transport


@pytest.fixture
def selected_panel(qapp, api, transport, monkeypatch):
    document = document_from_lines(
        [LyricLine(1, "First"), LyricLine(1, "Second"), LyricLine(2, "First")],
        "song.mp3", "song.lrcx")
    monkeypatch.setattr(api, "get_current_lyrics_document", lambda: document)
    panel = TranslationPanel(api, cloud_provider="mymemory")
    wait_for(qapp, lambda: panel.source.count() > 0 and not panel._busy)
    yield panel
    panel.shutdown()
    panel.close()
    panel.deleteLater()
    qapp.processEvents()


def test_selected_rows_keep_unselected_drafts_and_missing_rows_send_only_remaining(selected_panel):
    panel = selected_panel
    panel.table.item(1, 1).setText("Reviewed draft")
    assert not panel.translate_selected_button.isEnabled()
    panel.table.selectRow(2)
    panel.translate_selected_button.click()
    assert panel.job.request["rows"] == [{"index": 2, "text": "First"}]
    assert "~5 characters" in panel.usage_estimate.text()
    panel.job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Translated First"}})
    assert [panel.table.item(row, 1).text() for row in range(3)] == ["", "Reviewed draft", "Translated First"]
    panel.translate_button.click()
    assert panel.job.request["rows"] == [{"index": 0, "text": "First"}]
    panel.job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Another First"}})
    assert panel.table.item(1, 1).text() == "Reviewed draft"
    assert panel.table.item(2, 1).text() == "Translated First"
    assert not panel.translate_button.isEnabled()
    assert panel.translate_selected_button.isEnabled()  # Explicitly retry a chosen row.


def test_noncontiguous_selection_deduplicates_text_without_losing_row_ids(selected_panel):
    panel = selected_panel
    panel.table.selectRow(0)
    panel.table.selectionModel().select(panel.table.model().index(2, 0),
        QItemSelectionModel.SelectionFlag.Select | QItemSelectionModel.SelectionFlag.Rows)
    panel.translate_selected_button.click()
    assert [row["index"] for row in panel.job.request["rows"]] == [0, 2]
    assert len(panel.job.network.calls) == 1
    panel.job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Same result"}})
    assert panel.translations() == {0: "Same result", 2: "Same result"}


def test_failed_selected_retry_preserves_reviewed_values(selected_panel):
    panel = selected_panel
    panel.table.item(0, 1).setText("Reviewed")
    panel.table.selectRow(0)
    panel.translate_selected_button.click()
    panel.job.reply.respond({}, 429)
    assert panel.table.item(0, 1).text() == "Reviewed"
    assert panel.save_button.isEnabled()


def test_partial_translation_does_not_mix_language_pairs_or_discard_other_drafts(selected_panel):
    panel = selected_panel
    panel.table.item(0, 1).setText("English draft")
    panel.target.setCurrentIndex(panel.target.findData("zh"))
    panel.table.selectRow(2)
    panel.translate_selected_button.click()
    panel.job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Chinese draft"}})
    assert "English draft" not in panel.preview.toPlainText()
    applied = []
    panel._apply_translations = lambda doc, values: applied.append(values)
    panel.save_button.click()
    assert applied == [{2: "Chinese draft"}]
    assert panel.table.item(0, 1).text() == "English draft"
    assert panel.has_unapplied_translations()


def test_argos_selected_rows_use_the_same_subset_contract(qapp, api, monkeypatch, fake_native_packages):
    document = document_from_lines([LyricLine(1, "First"), LyricLine(1, "Second")], "song.mp3", "song.lrcx")
    monkeypatch.setattr(api, "get_current_lyrics_document", lambda: document)
    panel = TranslationPanel(api)
    try:
        wait_for(qapp, lambda: panel.source.count() > 0 and not panel._busy)
        panel.table.selectRow(1)
        panel.translate_selected_button.click()
        wait_for(qapp, lambda: not panel._busy)
        assert panel.translations() == {1: "Translated Second"}
        assert panel.table.item(0, 1).text() == ""
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()


def test_plugin_merge_preserves_settings_and_encrypted_records(monkeypatch):
    ciphertext = {"scheme": "windows-dpapi-v1", "data": "synthetic-encrypted-record"}
    save_all_settings({"plugin_settings": {
        "cloud_translation": {"encrypted_azure_api_key": ciphertext, "source": "ja"},
        "argos_translation": {"models_folder": "local-models", "target": "en"},
        "lyrics_translator": {"target": "zh"}, "unrelated": {"keep": True},
    }})
    manager = PluginManager()
    plugin = LyricsTranslatorPlugin()
    try:
        plugin.on_load(SimpleNamespace(api=manager.get_api("lyrics_translator")))
        settings = load_all_settings()["plugin_settings"]
        assert settings["lyrics_translator"] == {
            "encrypted_azure_api_key": ciphertext, "source": "ja", "models_folder": "local-models", "target": "zh"}
        assert settings["unrelated"] == {"keep": True}
        assert "cloud_translation" not in settings and "argos_translation" not in settings
        assert set(manager.translation_providers) == {"argos", "azure", "google", "deepl", "mymemory", "datpmt"}
        assert manager.translation_providers["argos"]["name"] == "Argos Translate (poor quality)"
    finally:
        plugin.on_unload(None)


def test_merged_plugin_remains_loadable_without_argos_dependencies(monkeypatch):
    root = Path(__file__).resolve().parents[1]
    metadata = json.loads((root / "plugins/community/lyrics_translator/manifest.json").read_text())
    metadata["plugin_id"] = "lyrics_translator"
    manager = PluginManager()
    manager.dependency_manager.configure([metadata])
    monkeypatch.setattr(manager.dependency_manager, "status", lambda name: {"state": "missing", "text": "Missing"})
    assert manager.dependency_manager.plugin_problems(metadata) == []
    manager.load_from_file("lyrics_translator", root / "plugins/community/lyrics_translator/plugin.py", metadata)
    assert len(manager.translation_providers) == 6
    manager.unload("lyrics_translator")
    assert not manager.translation_providers
