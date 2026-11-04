import json
from types import SimpleNamespace
from urllib.parse import parse_qs, urlparse

import pytest
from PySide6.QtCore import QObject, Signal, QByteArray
from PySide6.QtNetwork import QNetworkRequest

from core.plugin_manager import PluginManager
from plugins.community.lyrics_translator import cloud_providers as providers
from plugins.community.lyrics_translator.cloud_job import CloudTranslationJob
from plugins.community.lyrics_translator.plugin import LyricsTranslatorPlugin
from test_argos_translation_dialog import wait_for, api


@pytest.fixture
def transport(monkeypatch):
    class Reply(QObject):
        finished = Signal()

        def __init__(self):
            super().__init__()
            self.status, self.data, self.aborted = 200, b"", False

        def attribute(self, attribute):
            return self.status

        def readAll(self):
            return QByteArray(self.data)

        def respond(self, payload, status=200):
            self.status = status
            self.data = json.dumps(payload).encode("utf-8")
            self.finished.emit()

        def abort(self):
            self.aborted = True
            self.finished.emit()

    class Network:
        def __init__(self, parent):
            self.calls = []

        def post(self, request, body):
            reply = Reply()
            self.calls.append((request, bytes(body), reply))
            return reply

        def get(self, request):
            return self.post(request, b"")

    monkeypatch.setattr("plugins.community.lyrics_translator.cloud_job.QNetworkAccessManager", Network)


@pytest.mark.parametrize("provider", providers.PROVIDERS)
def test_official_requests_authentication_codes_and_plain_text(provider):
    method, url, headers, body = providers.request(provider, "ja", "zh-Hant", ["Original <text>"],
                                                   {"api_key": "secret", "region": "japaneast", "plan": "pro"})
    assert url.startswith("https://") and "secret" not in url
    if provider == "azure":
        assert parse_qs(urlparse(url).query)["to"] == ["zh-Hant"]
        assert headers["Ocp-Apim-Subscription-Key"] == "secret"
        assert headers["Ocp-Apim-Subscription-Region"] == "japaneast"
        assert json.loads(body) == [{"Text": "Original <text>"}]
    elif provider == "google":
        assert headers["X-Goog-Api-Key"] == "secret"
        assert json.loads(body)["target"] == "zh-TW"
        assert json.loads(body)["format"] == "text"
    elif provider == "deepl":
        assert url == "https://api.deepl.com/v2/translate"
        assert headers["Authorization"] == "DeepL-Auth-Key secret"
        assert json.loads(body)["target_lang"] == "ZH-HANT"
    else:
        assert method == "GET"
        assert parse_qs(urlparse(url).query) == {"q": ["Original <text>"], "langpair": ["ja|zh-TW"]}


@pytest.mark.parametrize("provider,payload", [
    ("azure", [{"translations": [{"text": "Translated"}]}]),
    ("google", {"data": {"translations": [{"translatedText": "Translated"}]}}),
    ("deepl", {"translations": [{"text": "Translated"}]}),
    ("mymemory", {"responseStatus": 200, "responseData": {"translatedText": "Translated"}}),
])
def test_cloud_transport_deduplicates_and_preserves_exact_row_ids(qapp, transport, provider, payload):
    job = CloudTranslationJob(provider)
    results, progress, errors = [], [], []
    job.succeeded.connect(results.append)
    job.failed.connect(errors.append)
    job.progress.connect(lambda *value: progress.append(value))
    job.start({"action": "translate", "source": "ja", "target": "en", "settings": {"api_key": "secret"},
               "rows": [{"index": 2, "text": "Same"}, {"index": 9, "text": "Same"}]})
    assert len(job.network.calls) == 1
    assert job.estimated_characters_sent == 4  # Repeated row is not counted twice.
    query, _, reply = job.network.calls[0]
    assert query.attribute(QNetworkRequest.Attribute.RedirectPolicyAttribute) == QNetworkRequest.RedirectPolicy.ManualRedirectPolicy
    assert query.transferTimeout() == 30000
    reply.respond(payload)
    assert results == [{"translations": {"2": "Translated", "9": "Translated"}}]
    assert progress == [(2, 2)] and not errors


def test_quota_failure_and_cancellation_never_apply_partial_results(qapp, transport):
    job = CloudTranslationJob("mymemory")
    results, failures, finished = [], [], []
    job.succeeded.connect(results.append)
    job.failed.connect(failures.append)
    job.finished.connect(lambda: finished.append(True))
    request = {"action": "translate", "source": "ja", "target": "en", "settings": {},
               "rows": [{"index": 0, "text": "First"}, {"index": 1, "text": "Second"}]}
    job.start(request)
    job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Partial"}})
    job.reply.respond({"responseStatus": 429, "responseDetails": "Private lyric / credential"}, 429)
    assert failures and not results and job.values == {}
    assert job.estimated_characters_sent == 11  # Both dispatched requests may consume quota.
    assert "Private" not in failures[0]
    job.start(request)
    reply = job.reply
    job.cancel()
    assert reply.aborted and not results and len(finished) == 2
    assert job.estimated_characters_sent == 16
    job.start(request)
    job.reply.respond({"responseStatus": 200, "responseData": {"translatedText": "Fresh"}})
    job.cancel()
    assert not results and len(finished) == 3


def test_mymemory_utf8_limits_preflight_before_network(qapp, transport):
    job = CloudTranslationJob("mymemory")
    failures = []
    job.failed.connect(failures.append)
    job.start({"action": "translate", "source": "ja", "target": "en", "settings": {},
               "rows": [{"index": 0, "text": "Short"}, {"index": 1, "text": "字" * 167}]})
    assert "500 UTF-8 bytes" in failures[0]
    assert not job.network.calls
    assert job.estimated_characters_sent == 0
    with pytest.raises(ValueError, match="incomplete"):
        providers.response("deepl", {"translations": []}, 1)
    with pytest.raises(ValueError, match="quota"):
        providers.response("mymemory", {"responseStatus": 200, "quotaFinished": True}, 1)


def test_deepl_account_endpoints_and_cloud_batch_limits():
    for key, plan, expected in (("key:fx", "auto", "api-free.deepl.com"),
                                 ("key", "auto", "api.deepl.com"),
                                 ("key:fx", "pro", "api.deepl.com"),
                                 ("key", "free", "api-free.deepl.com")):
        _, url, _, _ = providers.request("deepl", "ja", "en", ["Text"], {"api_key": key, "plan": plan})
        assert urlparse(url).hostname == expected
    groups = providers.batches("azure", [{"index": i, "text": "row " + str(i)} for i in range(81)])
    assert [len(group) for group in groups] == [40, 40, 1]


def test_registration_ownership_and_cloud_independent_of_argos():
    manager = PluginManager()
    plugin = LyricsTranslatorPlugin()
    plugin.on_load(SimpleNamespace(api=manager.get_api("cloud")))
    assert set(manager.translation_providers) == {"argos", *providers.PROVIDERS}
    manager.get_api("other").register_translation_provider("other", "Other", lambda *args: None)
    with pytest.raises(ValueError, match="already registered"):
        manager.get_api("other").register_translation_provider("azure", "Duplicate", lambda *args: None)
    with pytest.raises(ValueError):
        manager.get_api("other").register_translation_provider("invalid", "Invalid", None)
    manager._remove_owned("cloud")
    assert list(manager.translation_providers) == ["other"]
    plugin.on_unload(None)


def test_deepl_quota_reference_tracks_account_and_does_not_send(qapp, transport, api):
    from ui.translation_panel import TranslationPanel
    panel = TranslationPanel(api, cloud_provider="deepl")
    try:
        wait_for(qapp, lambda: panel.source.count() > 0 and not panel._busy)
        # Only the untranslated, nonblank row is included ("First word").
        assert "~10 characters (1 unique / 1 rows)" in panel.usage_estimate.text()
        assert "1,000,000 characters/total" in panel.usage_estimate.text()
        panel.credentials["api_key"].setText("secret:fx")
        assert "500,000 characters/month" in panel.usage_estimate.text()
        panel.plan.setCurrentIndex(panel.plan.findData("pro"))
        assert "1,000,000 characters/total" in panel.usage_estimate.text()
        panel.quota_plan.setCurrentIndex(panel.quota_plan.findData("growth_annual"))
        assert "12,000,000 characters/year" in panel.usage_estimate.text()
        assert not panel.job.network.calls
    finally:
        panel.shutdown()
        panel.close()
        panel.deleteLater()


def test_provider_workspace_cloud_review_apply_and_switching(
        qapp, transport, monkeypatch, make_lyrics_editor):
    from ui.lrc_editor import LrcEditorDialog
    manager = PluginManager()
    settings = {}
    monkeypatch.setattr(manager, "get_plugin_setting", lambda owner, key, default=None: settings.get(key, default))
    monkeypatch.setattr(manager, "set_plugin_setting", lambda owner, key, value: settings.__setitem__(key, value))
    plugin = LyricsTranslatorPlugin()
    plugin.on_load(SimpleNamespace(api=manager.get_api("cloud")))
    build_ui = LrcEditorDialog._build_ui
    def build_with_plugin(editor):
        editor.player = SimpleNamespace(plugin_manager=manager)
        build_ui(editor)
    monkeypatch.setattr(LrcEditorDialog, "_build_ui", build_with_plugin)
    editor = make_lyrics_editor()
    editor.tabs.setCurrentWidget(editor.translation_page)
    workspace = editor.translation_panel
    assert workspace.provider.count() == 5
    workspace.provider.setCurrentIndex(workspace.provider.findData("azure"))
    panel = workspace.panels["azure"]
    wait_for(qapp, lambda: panel.source.count() > 0 and not panel._busy)
    assert not panel.job.network.calls  # Opening the tab sends nothing.
    assert "~10 characters" in panel.usage_estimate.text()
    assert "2,000,000 characters/month" in panel.usage_estimate.text()
    assert not panel.translate_button.isEnabled()
    panel.credentials["api_key"].setText("session-secret")
    panel.translate_button.click()
    panel.job.reply.respond([{"translations": [{"text": "One"}]}, {"translations": [{"text": "Two"}]}])
    assert "Sent from this tab: ~10 characters" in panel.usage_estimate.text()
    assert "{\\nt}" in panel.preview.toPlainText()
    panel.table.item(0, 1).setText("Reviewed")
    workspace.provider.setCurrentIndex(workspace.provider.findData("mymemory"))
    memory = workspace.panels["mymemory"]
    wait_for(qapp, lambda: memory.source.count() > 0 and not memory._busy)
    assert memory.translate_button.isEnabled()  # No key required.
    workspace.provider.setCurrentIndex(workspace.provider.findData("azure"))
    assert workspace.stack.currentWidget() is panel
    assert panel.table.item(0, 1).text() == "Reviewed"
    panel.save_button.click()
    assert editor.lines[0].text == "Hello\nReviewed"
    assert editor.document_dirty and not editor.lrc_path.exists()
    editor.undo()
    assert editor.lines[0].text == "Hello"
    assert "session-secret" not in str(settings)
    workspace.shutdown()
    editor.player = None
