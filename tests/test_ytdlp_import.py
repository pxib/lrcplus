import json
from contextlib import closing
from pathlib import Path
import sqlite3
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
from plugins.community.ytdlp_import.worker import publish_download, single_track, validate_url, validate_youtube_session_url


@pytest.mark.parametrize("url", ["", "ytsearch:song", "file:///music/song.mp3", "https://user:pass@example.com/a", "https://example.com/a b"])
def test_importer_rejects_non_web_or_ambiguous_urls(url):
    with pytest.raises(ValueError):
        validate_url(url)


@pytest.mark.parametrize("url", [
    "http://www.youtube.com/watch?v=abcdefghijk",
    "https://youtube.com.example.com/watch?v=abcdefghijk",
    "https://www.youtube.com:8443/watch?v=abcdefghijk",
    "https://www.youtube.com/playlist?list=abcdefghijk",
])
def test_youtube_sessions_are_limited_to_https_youtube_video_urls(url):
    with pytest.raises(ValueError, match="one YouTube video"):
        validate_youtube_session_url(url)


def test_check_youtube_session_uses_the_native_firefox_cookie_reader(tmp_path, monkeypatch):
    from plugins.community.ytdlp_import import worker

    url = "https://www.youtube.com/watch?v=abcdefghijk&rco=1"
    (tmp_path / "cookies.sqlite").touch()
    source = {"kind": "firefox", "path": str(tmp_path)}
    cookies = SimpleNamespace(get_cookies_for_url=lambda _: [
        SimpleNamespace(name=name, value="synthetic") for name in ("LOGIN_INFO", "SAPISID")
    ])
    error = "[youtube] abcdefghijk: This video is age-restricted and only available on YouTube"
    responses = iter([RuntimeError(error), {"id": "abcdefghijk"}])

    class YoutubeDL:
        def __init__(self, options):
            assert options["verbose"]
            assert options["cookiesfrombrowser"] == ("firefox", str(tmp_path), None, "none")
            self.logger = options["logger"]
            self.cookiejar = cookies

        def __enter__(self):
            return self

        def __exit__(self, *_):
            pass

        def extract_info(self, target, *, download):
            assert target == url
            assert self.cookiejar is cookies
            assert download is False
            self.logger.debug("[debug] [youtube] Found YouTube account cookies")
            result = next(responses)
            if isinstance(result, Exception):
                self.logger.info("[youtube] YouTube is requiring account age-verification")
                self.logger.debug("[debug] unrelated synthetic cookie value")
                raise result
            return result

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setitem(sys.modules, "yt_dlp.version", SimpleNamespace(__version__="2026.11.23"))
    monkeypatch.setitem(sys.modules, "yt_dlp.cookies", SimpleNamespace(
        load_cookies=lambda *_: pytest.fail("The native jar should be used as-is"),
    ))
    monkeypatch.setattr(worker, "options", lambda config: {})
    request = {"url": url, "config": {}, "youtube_session": source}
    with pytest.raises(ValueError, match="age-restricted") as failure:
        worker.check_youtube_session(request)
    assert "yt-dlp 2026.11.23" in str(failure.value)
    assert "recognized the account cookies locally" in str(failure.value)
    assert "player response was still restricted" in str(failure.value)
    assert "synthetic cookie value" not in str(failure.value)
    assert "could read this video" in worker.check_youtube_session(request)["message"]


def test_firefox_session_uses_snapshot_if_native_loader_has_no_sign_in_cookies(tmp_path, monkeypatch):
    from plugins.community.ytdlp_import import worker

    native = SimpleNamespace(get_cookies_for_url=lambda _: [])
    snapshot = SimpleNamespace(get_cookies_for_url=lambda _: [
        SimpleNamespace(name=name, value="synthetic") for name in ("LOGIN_INFO", "SAPISID")
    ])
    downloader = SimpleNamespace(cookiejar=native)

    def make_snapshot(source, destination):
        assert source == tmp_path / "cookies.sqlite"
        destination.touch()

    def load_snapshot(cookie_file, browser, logger):
        assert cookie_file is None
        engine, profile, _, container = browser
        assert (engine, container) == ("firefox", "none")
        assert Path(profile) != tmp_path
        assert (Path(profile) / "cookies.sqlite").is_file()
        return snapshot

    monkeypatch.setattr(worker, "_snapshot_browser_cookies", make_snapshot)
    monkeypatch.setitem(sys.modules, "yt_dlp.cookies", SimpleNamespace(load_cookies=load_snapshot))
    worker._prepare_youtube_session(downloader, "firefox", tmp_path)
    assert downloader.cookiejar is snapshot


def test_browser_session_snapshot_reads_committed_wal_updates(tmp_path):
    from plugins.community.ytdlp_import.worker import _snapshot_browser_cookies

    profile = tmp_path / "browser profile"
    profile.mkdir()
    database = profile / "cookies.sqlite"
    snapshot = tmp_path / "snapshot.sqlite"
    with closing(sqlite3.connect(database)) as browser:
        assert browser.execute("PRAGMA journal_mode=WAL").fetchone() == ("wal",)
        browser.execute("PRAGMA wal_autocheckpoint=0")
        browser.execute("CREATE TABLE moz_cookies (value TEXT)")
        browser.execute("INSERT INTO moz_cookies VALUES ('old login')")
        browser.commit()
        browser.execute("PRAGMA wal_checkpoint(TRUNCATE)")
        browser.execute("UPDATE moz_cookies SET value = 'current login'")
        browser.commit()
        wal = profile / "cookies.sqlite-wal"
        original_wal = wal.read_bytes()
        _snapshot_browser_cookies(database, snapshot)
        with closing(sqlite3.connect(snapshot)) as copied:
            assert copied.execute("SELECT value FROM moz_cookies").fetchone() == ("current login",)
        assert wal.read_bytes() == original_wal


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


def test_cancelled_browser_session_worker_removes_temporary_snapshot(qapp, tmp_path, monkeypatch):
    helper = tmp_path / "cookie_snapshot_worker.py"
    helper.write_text(
        "import json, os, sys, tempfile, time\n"
        "from pathlib import Path\n"
        "if os.name != 'nt': os.setsid()\n"
        "json.load(sys.stdin)\n"
        "Path(tempfile.gettempdir(), 'synthetic-snapshot.sqlite').touch()\n"
        "print('LRCPLUS_YTDLP {\"event\": \"snapshot_ready\"}', flush=True)\n"
        "time.sleep(30)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("plugins.community.ytdlp_import.job.worker_command", lambda: [sys.executable, "-u", str(helper)])
    job = ImportJob({"action": "check_youtube_session", "youtube_session": {"kind": "firefox"}})
    snapshots = []

    def cancel_after_snapshot(event):
        if event["event"] == "snapshot_ready":
            snapshot = Path(job._session_workspace.name) / "synthetic-snapshot.sqlite"
            snapshots.append((snapshot, snapshot.is_file()))
            job.cancel()

    job.event_received.connect(cancel_after_snapshot)
    try:
        wait_finished(job)
        [(snapshot, was_created)] = snapshots
        assert was_created
        assert not snapshot.parent.exists()
    finally:
        job.shutdown()
        job.deleteLater()


@pytest.mark.parametrize(("warnings", "error", "suggested"), [
    ([], "[youtube] id: Unable to extract player response", True),
    (["[youtube] id: n challenge solving failed"], "[youtube] id: Requested format is not available", True),
    (["[youtube] No supported JavaScript runtime could be found", "[youtube] id: Signature solving failed"],
     "[youtube] id: Requested format is not available", False),
    ([], "[youtube] id: Requested format is not available", False),
    (["[youtube] id: Signature solving failed"], "[youtube] id: Private video. Sign in first.", False),
    ([], "[youtube] id: HTTP Error 403: Forbidden", False),
    ([], "[youtube] id: The read operation timed out", False),
    ([], "FFmpeg and ffprobe are required.", False),
])
def test_worker_failure_only_flags_youtube_extractor_errors(qapp, tmp_path, monkeypatch, warnings, error, suggested):
    from plugins.community.ytdlp_import.worker import PREFIX

    events = [{"event": "log", "message": f"Warning: {text}"} for text in warnings]
    events.append({"event": "error", "message": f"ERROR: {error}"})
    output = "\n".join(PREFIX + json.dumps(event) for event in events)
    command = [sys.executable, "-u", "-c", f"print({output!r}); raise SystemExit(1)"]
    monkeypatch.setattr("plugins.community.ytdlp_import.job.worker_command", lambda: command)
    job = ImportJob({"action": "download", "folder": str(tmp_path)})
    failures = QSignalSpy(job.failed)
    try:
        wait_finished(job)
        assert failures.at(0)[0] == f"ERROR: {error}"
        assert job.nightly_candidate is suggested
    finally:
        job.shutdown()
        job.deleteLater()


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


@pytest.mark.parametrize(("running", "installed", "source", "visible"), [
    ("stable", "stable", "managed", True),
    ("stable", "nightly", "managed", False),
    ("nightly", "stable", "managed", False),
    ("stable", "stable", "app", False),
])
def test_failed_download_offers_installable_nightly_build(qapp, monkeypatch, running, installed, source, visible):
    from ui.dependency_settings import DependencyManagerDialog

    plugin_manager = PluginManager()
    manager = plugin_manager.dependency_manager
    manager.configure([{"plugin_id": "ytdlp", "name": "yt-dlp", "dependencies": [
        {"requirement": "yt-dlp", "import_name": "yt_dlp"},
    ]}])
    registry = {"environment": "new", "requirements": {"yt-dlp": ["yt-dlp"]}}
    manager.start_environment = "running"
    existing_status = manager.status

    def status(name, *, state=None):
        if name != "yt-dlp":
            return existing_status(name, state=state)
        channel = running if state and state["environment"] == "running" else installed
        return {"source": source, "channel": channel, "state": "available", "text": channel.title()}

    monkeypatch.setattr(manager, "state", lambda: registry)
    monkeypatch.setattr(manager, "status", status)

    opened = []

    def open_manager(dialog):
        widget = dialog.dependencies
        opened.append(widget.yt_dlp_channel.currentData())
        assert not widget.yt_dlp_channel.isHidden()
        assert widget._buttons["install"].isEnabled()
        assert widget.tree.currentItem().text(0) == "Online audio downloads\nyt-dlp"

    monkeypatch.setattr(DependencyManagerDialog, "exec", open_manager)
    dialog = ImportDialog(ImportAPI(), DEFAULTS, plugin_manager=plugin_manager)
    job = ImportJob({"action": "download"}, dialog)
    job.nightly_candidate = True
    dialog.job = job
    try:
        dialog._failed("Unable to extract player response")
        dialog._finished()
        assert dialog.status.text() == "Unable to extract player response"
        assert dialog.nightly_prompt.isHidden() is not visible
        if visible:
            dialog.try_nightly_button.click()
            assert opened == ["nightly"]
    finally:
        dialog.deleteLater()


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
    actions = []
    monkeypatch.setattr(plugin.dialog, "_start", actions.append)
    search = manager.get_service("ytdlp_import.search")
    assert search("Artist Song")
    assert plugin.dialog.url.text() == "Artist Song"
    assert actions == ["search"]
    manager.unload_all()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert plugin.dialog is None
    assert manager.get_settings_widgets() == []
    assert manager._pending_menu_actions == []
    assert manager.get_service("ytdlp_import.search") is None


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
