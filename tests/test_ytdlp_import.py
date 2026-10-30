import json
from pathlib import Path
import sys
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent
from PySide6.QtTest import QSignalSpy, QTest

from core.dependencies import parse_dependencies
from core.plugin_manager import PluginManager
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.job import ImportJob, worker_command
from plugins.community.ytdlp_import.plugin import DEFAULTS, YtdlpImportPlugin
from plugins.community.ytdlp_import.worker import publish_download, single_track, validate_url


@pytest.mark.parametrize("url", ["", "ytsearch:song", "file:///music/song.mp3", "https://user:pass@example.com/a", "https://example.com/a b"])
def test_importer_rejects_non_web_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("info", [{"entries": []}, {"id": "x", "is_live": True}, {"id": "x", "live_status": "is_upcoming"}, None])
def test_importer_rejects_playlists_and_live_media(info):
    with pytest.raises(ValueError):
        single_track(info)


def test_completed_download_is_published_without_overwriting(tmp_path):
    folder = tmp_path / "downloads"
    folder.mkdir()
    original = folder / "song.m4a"
    original.write_bytes(b"original")
    stage = folder / ".lrcplus-import-test"
    stage.mkdir()
    source = stage / "song.m4a"
    source.write_bytes(b"downloaded")
    target = publish_download(source, folder)
    assert target.name == "song (2).m4a"
    assert target.read_bytes() == b"downloaded"
    assert original.read_bytes() == b"original"


def test_frozen_worker_requires_the_private_python_runtime(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.delenv("LRCPLUS_DEPENDENCY_PYTHON", raising=False)
    with pytest.raises(RuntimeError, match="restart"):
        worker_command()
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setenv("LRCPLUS_DEPENDENCY_PYTHON", str(python))
    assert worker_command()[:3] == [str(python), "-I", "-u"]


def wait_finished(job):
    spy = QSignalSpy(job.finished)
    job.start()
    if not spy.count():
        assert spy.wait(10000), "Downloader process did not finish"


def test_worker_uses_private_dependencies_and_ignores_non_protocol_output(qapp, tmp_path, monkeypatch):
    site = tmp_path / "site"
    site.mkdir()
    (site / "yt_dlp.py").write_text(
        "print('third-party startup diagnostic')\n"
        "class YoutubeDL:\n"
        "    def __init__(self, options): pass\n"
        "    def __enter__(self): return self\n"
        "    def __exit__(self, *args): pass\n"
        "    def extract_info(self, *args, **kwargs):\n"
        "        return {'id': 'abc', 'extractor_key': 'Test', 'title': '日本語', 'duration': 32}\n",
        encoding="utf-8",
    )
    monkeypatch.setenv("LRCPLUS_DEPENDENCY_SITE", str(site))
    job = ImportJob({"action": "preview", "config": {}, "url": "https://example.com/track"})
    succeeded, failed = QSignalSpy(job.succeeded), QSignalSpy(job.failed)
    try:
        wait_finished(job)
        assert failed.count() == 0
        assert succeeded.count() == 1
        assert succeeded.at(0)[0]["identity"] == "Test:abc"
        assert succeeded.at(0)[0]["title"] == "日本語"
    finally:
        job.shutdown()
        job.deleteLater()
        qapp.processEvents()


def test_failed_job_cleans_only_its_own_staging_directory(qapp, tmp_path, monkeypatch):
    helper = tmp_path / "fail.py"
    helper.write_text("raise RuntimeError('test download failed')\n", encoding="utf-8")
    monkeypatch.setattr("plugins.community.ytdlp_import.job.worker_command", lambda: [sys.executable, "-u", str(helper)])
    folder = tmp_path / "downloads"
    folder.mkdir()
    existing = folder / "keep.mp3"
    existing.write_bytes(b"keep")
    unrelated = folder / ".lrcplus-import-other"
    unrelated.mkdir()
    job = ImportJob({"action": "download", "folder": str(folder)})
    failed = QSignalSpy(job.failed)
    try:
        wait_finished(job)
        assert failed.count() == 1
        assert "test download failed" in failed.at(0)[0]
        assert sorted(path.name for path in folder.iterdir()) == [unrelated.name, existing.name]
        assert existing.read_bytes() == b"keep"
    finally:
        job.shutdown()
        job.deleteLater()
        qapp.processEvents()


def test_cancellation_stops_converter_child_and_never_imports(qapp, tmp_path, monkeypatch):
    marker = tmp_path / "child-finished.txt"
    child_code = f"import time; from pathlib import Path; time.sleep(1.2); Path({str(marker)!r}).write_text('finished')"
    helper = tmp_path / "download.py"
    helper.write_text(
        "import subprocess, sys, time, json\n"
        "import os\n"
        "if os.name != 'nt': os.setsid()\n"
        "json.load(sys.stdin)\n"
        f"subprocess.Popen([sys.executable, '-c', {child_code!r}])\n"
        "print('LRCPLUS_YTDLP {\"event\": \"child_started\"}', flush=True)\n"
        "time.sleep(30)\n", encoding="utf-8",
    )
    monkeypatch.setattr("plugins.community.ytdlp_import.job.worker_command", lambda: [sys.executable, "-u", str(helper)])
    job = ImportJob({"action": "download", "folder": str(tmp_path / "downloads")})
    succeeded, cancelled = QSignalSpy(job.succeeded), QSignalSpy(job.cancelled)
    job.event_received.connect(lambda event: job.cancel() if event.get("event") == "child_started" else None)
    try:
        wait_finished(job)
        assert cancelled.count() == 1
        assert succeeded.count() == 0
        QTest.qWait(1400)
        assert not marker.exists()
        assert list((tmp_path / "downloads").iterdir()) == []
    finally:
        job.shutdown()
        job.deleteLater()
        qapp.processEvents()


class ImportAPI:
    def __init__(self):
        self.settings = {}
        self.imports = []

    def get_setting(self, key, default=None):
        return self.settings.get(key, default)

    def set_setting(self, key, value):
        self.settings[key] = value

    def import_audio_files(self, paths, *, add_to_queue=True):
        self.imports.append((paths, add_to_queue))
        return paths


def test_dialog_detects_previous_download_and_reuses_it(qapp, tmp_path):
    api = ImportAPI()
    audio = tmp_path / "song.m4a"
    audio.touch()
    api.settings["downloads"] = {"Test:abc:m4a": {"path": str(audio)}}
    dialog = ImportDialog(api, DEFAULTS)
    try:
        dialog.url.setText("https://example.com/song")
        dialog._succeeded("preview", {"identity": "Test:abc", "title": "Song", "duration": 40})
        assert dialog.download_button.text() == "Add existing"
        dialog.add_to_queue.setChecked(False)
        dialog.download_button.click()
        assert api.imports == [([str(audio)], False)]
        assert dialog.job is None
        dialog.url.setText("https://example.com/other")
        assert not dialog.download_button.isEnabled()
        assert dialog.info is None
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_completed_file_is_remembered_even_if_host_import_fails(qapp, tmp_path):
    api = ImportAPI()
    audio = tmp_path / "song.m4a"
    audio.touch()
    api.import_audio_files = lambda *args, **kwargs: (_ for _ in ()).throw(OSError("settings locked"))
    dialog = ImportDialog(api, DEFAULTS)
    try:
        dialog.info = {"identity": "Test:abc"}
        dialog._succeeded("download", {"identity": "Test:abc", "path": str(audio), "url": "https://example.com/song", "title": "Song"})
        assert api.settings["downloads"]["Test:abc:m4a"]["path"] == str(audio)
        assert "Audio saved at" in dialog.status.text()
        assert "Add existing" in dialog.status.text()
        assert audio.exists()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_plugin_registers_owned_ui_and_unloads_cleanly(qapp, monkeypatch):
    manager = PluginManager()
    path = Path(__file__).resolve().parents[1] / "plugins/community/ytdlp_import/plugin.py"
    manager.load_from_file("ytdlp_import", path)
    assert manager._pending_menu_actions[0][0] == "ytdlp_import"
    assert manager.get_settings_widgets()[0]["owner"] == "ytdlp_import"
    plugin = manager.plugins["ytdlp_import"].instance
    plugin.open_import()
    assert plugin.dialog is not None
    manager.unload_all()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert plugin.dialog is None
    assert manager.get_settings_widgets() == []
    assert manager._pending_menu_actions == []


def test_import_api_validates_all_files_before_mutating_host(tmp_path):
    imported, queued = [], []
    host = SimpleNamespace(_register_queue_paths=lambda paths: imported.extend(paths),
                           enqueue_tracks=lambda paths: queued.extend(paths))
    manager = PluginManager()
    manager.set_host_window(host)
    api = manager.get_api("test")
    audio = tmp_path / "song.mp3"
    audio.touch()
    with pytest.raises(ValueError):
        api.import_audio_files([str(audio), str(tmp_path / "missing.mp3")])
    assert imported == queued == []
    assert api.import_audio_files([str(audio)], add_to_queue=False) == [str(audio)]
    assert imported == [audio]
    assert queued == []
    api.import_audio_files([str(audio)])
    assert queued == [audio]


def test_manifest_declares_matching_ytdlp_default_dependencies():
    manifest = Path(__file__).resolve().parents[1] / "plugins/community/ytdlp_import/manifest.json"
    data = json.loads(manifest.read_text(encoding="utf-8"))
    declared = parse_dependencies(data["dependencies"])
    assert declared[0].name == "yt-dlp"
    assert "[default]" in declared[0].requirement
    assert data["api_version"] == 4
