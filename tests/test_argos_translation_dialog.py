import time
from pathlib import Path

import pytest
from PySide6.QtCore import QProcess
from PySide6.QtTest import QTest
from PySide6.QtCore import Qt

from core.translation_document import render_translations, snapshot_document
from lyrics.lrcx_parser import parse_lrcx
from plugins.community.lyrics_translator.dialog import TranslationDialog, TranslationPanel
from plugins.community.lyrics_translator.job import TranslationJob


def wait_for(qapp, predicate):
    deadline = time.monotonic() + 8
    while not predicate() and time.monotonic() < deadline:
        qapp.processEvents()
        time.sleep(.005)
    assert predicate()


@pytest.fixture
def fake_native_packages(tmp_path, monkeypatch):
    # Real child-process transport with tiny deterministic native-engine doubles.
    root = tmp_path / "python-libraries"
    argos = root / "argostranslate"
    argos.mkdir(parents=True)
    (argos / "__init__.py").write_text("")
    (argos / "package.py").write_text(
        "from pathlib import Path\nfrom types import SimpleNamespace\n"
        "def get_installed_packages():\n"
        "    def package(a,b):\n"
        "        return SimpleNamespace(from_code=a,to_code=b,from_name=a,to_name=b,type='translate',"
        "package_path=Path('.'),target_prefix='',tokenizer=SimpleNamespace(encode=lambda t:[t],decode=lambda ts:''.join(ts)))\n"
        "    return [package('ja','en'),package('en','zh')]\n", encoding="utf-8")
    (root / "ctranslate2.py").write_text(
        "import time\nfrom types import SimpleNamespace\n"
        "class Translator:\n"
        "    def __init__(self,*args,**kwargs): pass\n"
        "    def translate_batch(self,values,**kwargs):\n"
        "        if values[0][0]=='SLOW': time.sleep(30)\n"
        "        if values[0][0]=='ERROR': raise RuntimeError('Inference failed')\n"
        "        return [SimpleNamespace(hypotheses=[['Translated '+values[0][0]]])]\n", encoding="utf-8")
    monkeypatch.syspath_prepend(str(root))


def test_worker_progress_cancellation_and_no_stale_result(qapp, tmp_path, fake_native_packages):
    job = TranslationJob()
    progress, results, failures, finished = [], [], [], []
    job.progress.connect(lambda *value: progress.append(value))
    job.succeeded.connect(results.append)
    job.failed.connect(failures.append)
    job.finished.connect(lambda: finished.append(True))
    try:
        job.start({"action": "translate", "folder": str(tmp_path / "models"), "source": "ja", "target": "en",
                   "rows": [{"index": 3, "text": "First"}, {"index": 5, "text": "SLOW"}]})
        wait_for(qapp, lambda: bool(progress))
        assert progress == [(1, 2)]
        job.cancel()
        wait_for(qapp, lambda: bool(finished))
        assert not results and not failures
        assert job.process.state() == QProcess.ProcessState.NotRunning
        job.start({"action": "translate", "folder": str(tmp_path / "models"), "source": "ja", "target": "en",
                   "rows": [{"index": 9, "text": "Next"}]})
        wait_for(qapp, lambda: bool(results))
        assert results == [{"translations": {"9": "Translated Next"}}]
    finally:
        job.shutdown()


@pytest.fixture
def api(tmp_path):
    source = tmp_path / "song.lrcx"
    audio = tmp_path / "song.mp3"
    audio.write_bytes(b"audio")
    source.write_text("[00:01.000]First<00:02.000> word<00:03.000>\n"
                      r"[00:04.000]Already{\nt}Provided" + "\n[00:05.000]\n", encoding="utf-8")
    class API:
        def __init__(self):
            self.settings = {"models_folder": str(tmp_path / "models")}
            self.saved = []
        def get_setting(self, key, default=None):
            return self.settings.get(key, default)
        def set_setting(self, key, value):
            self.settings[key] = value
        def get_current_lyrics_document(self):
            return snapshot_document(audio, source)
        def render_lyrics_translations(self, document, values):
            return render_translations(document, values)
        def save_lyrics_translations(self, document, values, path):
            content = self.render_lyrics_translations(document, values)
            self.saved.append((document, values, path))
            Path(path).write_text(content, encoding="utf-8")
            return path
    return API()


def test_dialog_offline_preview_edit_save_and_quality_label(qapp, tmp_path, fake_native_packages, api, monkeypatch):
    dialog = TranslationDialog(api)
    try:
        assert "(poor quality)" in dialog.windowTitle()
        assert dialog.table.rowCount() == 1  # Existing translations and blank rows are skipped.
        wait_for(qapp, lambda: not dialog._busy and dialog.source.count() == 3)
        assert dialog.pair() == ("ja", "en")
        assert dialog.translate_button.isEnabled()
        dialog.translate_button.click()
        assert not dialog.save_button.isEnabled() and not dialog.load_button.isEnabled()
        wait_for(qapp, lambda: not dialog._busy)
        assert dialog.table.item(0, 1).text() == "Translated First word"
        assert "{\\nt}" in dialog.preview.toPlainText()
        dialog.table.item(0, 1).setText("Edited translation")
        assert "Edited translation" in dialog.preview.toPlainText()
        assert not api.saved
        output = tmp_path / "translated.lrcx"
        monkeypatch.setattr("ui.translation_panel.QFileDialog.getSaveFileName",
                            lambda *args: (str(output), ""))
        dialog.save_button.click()
        assert len(api.saved) == 1
        loaded = parse_lrcx(output.read_text(encoding="utf-8"), infer_ends=False)
        assert loaded[0].text == "First word\nEdited translation"
        assert loaded[0].segments[0].start == 1
        assert loaded[0].segments[1].start == 2
        assert loaded[0].end == 3
        assert loaded[1].text == "Already\nProvided"
        assert "{\\nt}" not in Path(api.saved[0][0]["lyrics_path"]).read_text(encoding="utf-8").splitlines()[0]
        dialog.show()
        qapp.processEvents()
        assert dialog.grab().save(str(tmp_path / "argos-dialog.png"))
        dialog.target.setCurrentIndex(dialog.target.findData("zh"))
        assert not dialog.save_button.isEnabled()
        assert dialog.route.text() == "Via en"
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()


def test_editor_translation_uses_unsaved_document_and_applies_undoably(
        qapp, fake_native_packages, api, editor):
    editor.lines[0].text = "Unsaved words"
    editor.lines[0].segments[0].text = "Unsaved words"
    editor._populate_lines(preserve_line_index=0)
    editor._mark_document_dirty()
    document = editor._translation_document()
    original = document["lrcx"]
    existed = editor.lrc_path.exists()
    dialog = TranslationPanel(api, editor, document_loader=editor._translation_document_for_panel,
                               apply_translations=editor._apply_lyrics_translations)
    try:
        assert not dialog.load_button.isHidden()
        assert dialog.table.item(0, 0).text() == "Unsaved words"
        wait_for(qapp, lambda: not dialog._busy and dialog.source.count() > 0)
        dialog.translate_button.click()
        wait_for(qapp, lambda: not dialog._busy)
        dialog.table.item(0, 1).setText("Reviewed translation")
        dialog.save_button.click()
        assert editor.lines[0].text == "Unsaved words\nReviewed translation"
        assert editor.lines[0].translation_lines == [1]
        assert editor.document_dirty
        assert not api.saved
        assert editor.lrc_path.exists() == existed
        assert editor.lines[0].segments[0].end == 3.5
        assert editor.lines[0].end == 4
        editor.undo()
        assert editor._translation_document()["lrcx"] == original
        editor.redo()
        assert editor.lines[0].translation_lines == [1]
    finally:
        dialog.shutdown()
        dialog.close()
        dialog.deleteLater()


def test_editor_translation_service_and_stale_document_guard(
        qapp, monkeypatch, make_lyrics_editor, fake_native_packages, api):
    from types import SimpleNamespace
    from ui.lrc_editor import LrcEditorDialog
    from plugins.community.lyrics_translator.plugin import LyricsTranslatorPlugin

    plugin = LyricsTranslatorPlugin()
    plugin.api = api
    build_ui = LrcEditorDialog._build_ui
    def build_with_plugin(editor):
        editor.player = SimpleNamespace(plugin_manager=SimpleNamespace(
            translation_providers={"argos": {"name": plugin.name, "factory": plugin.create_editor_panel}}))
        build_ui(editor)
    monkeypatch.setattr(LrcEditorDialog, "_build_ui", build_with_plugin)
    editor = make_lyrics_editor()
    assert editor.translation_page is not None
    assert editor.translation_panel is None  # No model process until tab is opened.
    editor.tabs.setCurrentWidget(editor.translation_page)
    workspace = editor.translation_panel
    panel = workspace.panels["argos"]
    assert isinstance(panel, TranslationPanel)
    assert not panel.isWindow()
    assert panel.parentWidget() == workspace.stack
    assert editor.line_list_panel.isHidden()
    wait_for(qapp, lambda: not panel._busy and panel.source.count() > 0)
    document = editor._translation_document()
    editor.tabs.setCurrentIndex(0)
    assert not editor.line_list_panel.isHidden()
    editor.tabs.setCurrentWidget(editor.translation_page)
    assert editor.translation_panel is workspace
    editor._apply_lyrics_translations(document, {0: "Translation"})
    assert editor.lines[0].text == "Hello\nTranslation"
    with pytest.raises(ValueError, match="lyrics changed"):
        editor._apply_lyrics_translations(document, {1: "Stale"})
    panel.load_button.click()
    assert panel.table.rowCount() == 1  # Applied row is preserved and skipped.
    panel.table.item(0, 0).setText("SLOW")
    panel.translate_button.click()
    wait_for(qapp, lambda: panel._busy)
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Discard)
    editor.document_dirty = False
    editor.close()
    assert panel.job.process.state() == QProcess.ProcessState.NotRunning
    editor.player = None


def test_worker_error_and_escape_do_not_save(qapp, fake_native_packages, api, monkeypatch):
    from PySide6.QtWidgets import QMessageBox
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: QMessageBox.StandardButton.Discard)
    dialog = TranslationDialog(api)
    try:
        wait_for(qapp, lambda: not dialog._busy and dialog.source.count() > 0)
        dialog.table.item(0, 0).setText("ERROR")
        dialog.translate_button.click()
        wait_for(qapp, lambda: not dialog._busy)
        assert "Inference failed" in dialog.status.text()
        assert not api.saved and not dialog.save_button.isEnabled()
        dialog.table.item(0, 0).setText("SLOW")
        dialog.translate_button.click()
        dialog.show()
        qapp.processEvents()
        QTest.keyClick(dialog, Qt.Key.Key_Escape)
        assert dialog.job.process.state() == QProcess.ProcessState.NotRunning
        assert not api.saved
    finally:
        dialog.shutdown()
        dialog.deleteLater()
