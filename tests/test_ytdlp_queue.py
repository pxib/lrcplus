from copy import deepcopy
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QObject, Signal, Qt
from PySide6.QtTest import QSignalSpy, QTest

from plugins.community.ytdlp_import.queue import DownloadQueue
from plugins.community.ytdlp_import.dialog import ImportDialog
from plugins.community.ytdlp_import.plugin import DEFAULTS
from plugins.community.ytdlp_import import worker


class API:
    def __init__(self):
        self.settings = {}
        self.imports = []
        self.attachments = []

    def get_setting(self, key, default=None):
        return deepcopy(self.settings.get(key, default))

    def set_setting(self, key, value):
        self.settings[key] = deepcopy(value)

    def import_audio_files(self, paths, *, add_to_queue=True):
        self.imports.append((paths, add_to_queue))
        return paths

    def attach_lyrics(self, audio, lyrics):
        self.attachments.append((audio, lyrics))


class Job(QObject):
    event_received = Signal(object)
    succeeded = Signal(object)
    failed = Signal(str)
    cancelled = Signal()
    finished = Signal()

    def __init__(self, request, parent):
        super().__init__(parent)
        self.request = request
        self.started = False
        self.stopped = False

    def start(self):
        self.started = True

    def complete(self, result):
        self.succeeded.emit(result)
        self.finished.emit()

    def fail(self, message):
        self.failed.emit(message)
        self.finished.emit()

    def cancel(self):
        self.stopped = True
        self.cancelled.emit()
        self.finished.emit()

    shutdown = cancel


@pytest.fixture
def queue(qapp, tmp_path):
    manager = DownloadQueue(API(), job_factory=Job)
    manager.options = {"config": {"automatically_find_lyrics": False}, "folder": str(tmp_path),
                       "codec": "m4a", "subtitle": None, "add_to_queue": True}
    yield manager
    manager.shutdown()
    manager.deleteLater()
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def add(queue, count=3, options=None):
    return queue.enqueue([{"url": f"https://example.com/{i}", "title": f"Track {i}"}
                          for i in range(count)], options or queue.options)


def prepared(queue, index):
    item = queue.items[index]
    job = queue.jobs[item["id"]]
    job.complete({"identity": f"Test:{index}", "title": f"Track {index}", "subtitle_tracks": []})
    return queue.jobs.get(item["id"])


def downloaded(queue, index, tmp_path):
    path = tmp_path / f"track{index}.m4a"
    path.touch()
    queue.jobs[queue.items[index]["id"]].complete({"identity": f"Test:{index}", "path": str(path)})
    return str(path)


def test_batch_timing_review_reports_suggestion_and_uncertain_track(queue, tmp_path):
    queue.options["config"]["review_lyrics_drift"] = True
    assert add(queue, count=2) == 2
    queue.options["config"]["review_lyrics_drift"] = False
    queue.set_paused(False)
    lyrics = tmp_path / "track0.lrc"
    original = "".join(f"[00:{time:02}.000]Verse {number}\n"
                       for number, time in enumerate((5, 15, 25, 35), 1))
    lyrics.write_text(original, encoding="utf-8")
    subtitles = tmp_path / "track0.en.manual.vtt"
    subtitles.write_text("WEBVTT\n\n" + "".join(
        f"00:00:{time:02}.000 --> 00:00:{time + 1:02}.000\nVerse {number}\n\n"
        for number, time in enumerate((6, 17, 28, 39), 1)
    ), encoding="utf-8")
    audio = tmp_path / "track0.m4a"
    audio.touch()
    job = prepared(queue, 0)
    prepared(queue, 1)
    downloaded(queue, 1, tmp_path)
    job.complete({"identity": "Test:0", "path": str(audio), "lyrics_path": str(lyrics),
                  "lyrics_source": "LRCLIB", "subtitle_path": str(subtitles), "duration": 45})

    assert "Timing correction available" in queue.items[0]["message"]
    assert "track0.en.manual.vtt" in queue.items[0]["details"]
    assert "Needs timing review" in queue.items[1]["message"]
    assert lyrics.read_text(encoding="utf-8") == original
    assert len(queue.api.imports) == 2
    assert queue.api.settings["download_queue"][0]["message"] == queue.items[0]["message"]


def test_concurrency_snapshot_and_import_order(queue, qapp, tmp_path):
    assert add(queue) == 3
    queue.options["codec"] = "mp3"
    queue.set_paused(False)
    assert len(queue.jobs) == 2
    assert all(job.request["codec"] == "m4a" for job in queue.jobs.values())
    prepared(queue, 0)
    prepared(queue, 1)
    second = downloaded(queue, 1, tmp_path)
    assert queue.api.imports == []
    queue.pump()
    assert queue.items[2]["id"] in queue.jobs
    first = downloaded(queue, 0, tmp_path)
    assert queue.api.imports == [([first], True), ([second], True)]
    assert queue.api.settings["downloads"]["Test:1:m4a"]["path"] == second


def test_stop_starting_cancel_retry_and_remove(queue):
    add(queue)
    queue.set_limit(1)
    queue.set_paused(False)
    queue.set_paused(True)
    first = queue.items[0]["id"]
    job = queue.jobs[first]
    job.fail("offline")
    assert not queue.jobs
    queue.pump()
    assert not queue.jobs
    queue.retry({first})
    assert queue.items[0]["state"] == "waiting"
    queue.set_paused(False)
    job = queue.jobs[first]
    queue.cancel({first})
    assert job.stopped
    assert queue.items[0]["state"] == "cancelled"
    queue.remove({first})
    assert all(item["id"] != first for item in queue.items)


def test_queue_suggests_nightly_after_flagged_download_failure(queue):
    add(queue, count=2)
    queue.set_paused(False)
    flagged = prepared(queue, 0)
    ordinary = prepared(queue, 1)
    suggested = QSignalSpy(queue.nightly_suggested)

    flagged.nightly_candidate = True
    flagged.fail("Unable to extract player response")
    ordinary.fail("Network timeout")

    assert suggested.count() == 1
    assert [(item["state"], item["message"]) for item in queue.items] == [
        ("failed", "Unable to extract player response"), ("failed", "Network timeout"),
    ]


def test_youtube_session_stays_with_one_track_and_clears_when_restored(queue, tmp_path):
    source = {"kind": "firefox", "path": str(tmp_path / "Selected profile")}
    url = "https://www.youtube.com/watch?v=abcdefghijk"
    queue.enqueue([{"url": url}], queue.options, youtube_session=source)
    add(queue, count=1)
    queue.set_paused(False)
    item, ordinary = queue.items
    assert queue.jobs[item["id"]].request["youtube_session"] == source
    assert "youtube_session" not in queue.jobs[ordinary["id"]].request
    assert prepared(queue, 0).request["youtube_session"] == source
    serialized = queue.api.get_setting("download_queue")
    assert source["path"] not in str(serialized)
    assert serialized[0]["state"] == "failed"
    assert serialized[1]["state"] == "preparing"

    queue.shutdown()
    restored = DownloadQueue(queue.api, job_factory=Job)
    try:
        assert restored.items[0]["state"] == "failed"
        assert "session cleared" in restored.items[0]["message"]
        assert restored.items[1]["state"] == "waiting"
        restored.retry_with_youtube_session(restored.items[0], source)
        restored.set_paused(False)
        authenticated_job = restored.jobs[item["id"]]
        assert authenticated_job.request["youtube_session"] == source
        authenticated_job.fail("Sign-in expired")
        restored.retry({item["id"]})
        assert "youtube_session" not in restored.jobs[item["id"]].request
    finally:
        restored.shutdown()
        restored.deleteLater()


def test_importer_can_queue_and_retry_with_a_user_selected_youtube_profile(queue, tmp_path, monkeypatch):
    from plugins.community.ytdlp_import import session_dialog

    profile = tmp_path / "LibreWolf profile"
    profile.mkdir()
    (profile / "cookies.sqlite").touch()
    url = "https://www.youtube.com/watch?v=abcdefghijk&rco=1"
    error = "ERROR: [youtube] abcdefghijk: Sorry, this content is age-restricted. This video is age-restricted and only available on YouTube"

    def choose_profile(dialog):
        dialog.kind.setCurrentIndex(dialog.kind.findData("file"))
        dialog.path.setText(str(tmp_path / "missing cookies.txt"))
        dialog.use.click()
        assert dialog.source is None
        assert "existing YouTube cookies.txt" in dialog.status.text()
        dialog.kind.setCurrentIndex(dialog.kind.findData("firefox"))
        dialog.path.setText(str(tmp_path))
        dialog.test.click()
        assert dialog._job is None
        assert "containing cookies.sqlite" in dialog.status.text()
        dialog.path.setText(str(profile))
        dialog.test.click()
        assert dialog._job.request["url"] == url
        assert dialog._job.request["youtube_session"] == {"kind": "firefox", "path": str(profile)}
        dialog._job.fail(error)
        assert error in dialog.status.text()
        assert "video already plays" in dialog.status.text()
        dialog.use.click()
        return dialog.result()

    monkeypatch.setattr(session_dialog, "ImportJob", Job)
    monkeypatch.setattr(session_dialog.YouTubeSessionDialog, "exec", choose_profile)
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    try:
        dialog.url.setText(url)
        action = next(action for action in dialog.actions_button.menu().actions()
                      if action.text() == "Download with YouTube session…")
        action.trigger()
        queue.pump()
        item = queue.items[0]
        assert dialog.tabs.currentWidget() is dialog.queue_view
        job = queue.jobs[item["id"]]
        assert job.request["url"] == url
        assert job.request["youtube_session"] == {"kind": "firefox", "path": str(profile)}
        job.fail(error)
        assert "video already plays" in item["message"]
        assert error in item["details"]
        dialog.queue_view.table.topLevelItem(0).setSelected(True)
        assert not dialog.queue_view.retry_session.isHidden()
        dialog.queue_view.retry_session.click()
        assert queue.jobs[item["id"]].request["youtube_session"]["path"] == str(profile)
    finally:
        dialog.close()
        dialog.deleteLater()


def test_anonymous_age_restriction_points_to_session_retry(queue):
    queue.enqueue([{"url": "https://www.youtube.com/watch?v=abcdefghijk"}], queue.options)
    queue.pump()
    item = queue.items[0]
    error = "ERROR: [youtube] abcdefghijk: This video is age-restricted and only available on YouTube"
    queue.jobs[item["id"]].fail(error)
    assert "Retry with YouTube session" in item["message"]
    assert error in item["details"]


def test_shutdown_restores_running_and_waiting_without_autostart(queue):
    add(queue)
    queue.set_paused(False)
    queue.shutdown()
    assert not queue.jobs
    assert all(item["state"] == "waiting" for item in queue.items)
    restored = DownloadQueue(queue.api, job_factory=Job)
    assert len(restored.items) == 3
    assert restored.paused
    assert not restored.jobs
    restored.set_paused(False)
    assert len(restored.jobs) == 2
    restored.shutdown()
    restored.deleteLater()


def test_deduplicates_youtube_variants_but_allows_force(queue):
    entries = [{"url": "https://youtu.be/abcdefghijk"},
               {"url": "https://www.youtube.com/watch?v=abcdefghijk&list=PL123"}]
    assert queue.enqueue(entries, queue.options) == 1
    assert queue.enqueue(entries[:1], queue.options, force=True) == 1
    assert len(queue.items) == 2


def test_batch_validation_is_atomic(queue):
    with pytest.raises(ValueError):
        queue.enqueue([{"url": "https://example.com/valid"}, {"url": "file:///invalid"}], queue.options)
    assert queue.items == []


def test_completed_track_can_be_added_again_without_redownloading(queue, tmp_path):
    add(queue, 1)
    queue.set_paused(False)
    prepared(queue, 0)
    audio = downloaded(queue, 0, tmp_path)
    assert add(queue, 1) == 1
    queue.pump()
    job = queue.jobs[queue.items[1]["id"]]
    job.complete({"identity": "Test:0", "title": "Track 0", "subtitle_tracks": []})
    assert queue.api.imports == [([audio], True), ([audio], True)]
    assert not queue.jobs


def test_different_urls_resolving_to_same_track_are_skipped(queue):
    add(queue, 2)
    queue.set_paused(False)
    prepared(queue, 0)
    job = queue.jobs[queue.items[1]["id"]]
    job.complete({"identity": "Test:0", "title": "Duplicate", "subtitle_tracks": []})
    assert queue.items[1]["state"] == "skipped"
    assert len(queue.jobs) == 1


def test_existing_download_reused_and_missing_subtitles_are_nonfatal(queue, tmp_path):
    audio = tmp_path / "existing.m4a"
    audio.touch()
    queue.api.settings["downloads"] = {"Test:0:m4a": {"path": str(audio)}}
    options = dict(queue.options, subtitle={"language": "en", "kind": "manual"})
    add(queue, 1, options)
    queue.set_paused(False)
    assert prepared(queue, 0) is None
    assert queue.api.imports == [([str(audio)], True)]
    assert "unavailable" in queue.items[0]["details"]


def test_existing_download_can_fetch_missing_lyrics(queue, tmp_path):
    audio = tmp_path / "existing.m4a"
    audio.touch()
    queue.api.settings["downloads"] = {"Test:0:m4a": {"path": str(audio)}}
    options = deepcopy(queue.options)
    options["config"]["automatically_find_lyrics"] = True
    add(queue, 1, options)
    queue.set_paused(False)
    job = prepared(queue, 0)
    assert job.request["action"] == "lyrics"
    lyrics = tmp_path / "existing.lrc"
    lyrics.touch()
    job.complete({"path": str(audio), "lyrics_path": str(lyrics), "lyrics_source": "LRCLIB"})
    assert queue.api.attachments == [(str(audio), str(lyrics))]
    assert queue.api.imports == [([str(audio)], True)]


def test_failures_do_not_block_later_imports(queue, tmp_path):
    add(queue, 2)
    queue.set_paused(False)
    prepared(queue, 1)
    second = downloaded(queue, 1, tmp_path)
    assert queue.api.imports == []
    queue.jobs[queue.items[0]["id"]].fail("unavailable")
    assert queue.api.imports == [([second], True)]
    assert queue.items[0]["state"] == "failed"


def test_closing_dialog_keeps_queue_running_and_settings_are_frozen(queue, qapp, tmp_path):
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    dialog._succeeded("playlist", {"title": "Playlist", "results": [
        {"url": "https://example.com/a", "title": "A", "available": True},
        {"url": "", "title": "Private", "available": False},
        {"url": "https://example.com/b", "title": "B", "available": True}], "next_start": 101})
    assert dialog.search_results.count() == 3
    assert not dialog.search_results.item(1).flags() & Qt.ItemFlag.ItemIsEnabled
    dialog.search_results.selectAll()
    dialog._enqueue_selected()
    assert len(queue.items) == 2
    assert dialog.tabs.currentIndex() == 0
    assert dialog.tabs.tabText(1) == "Downloads (2)"
    assert dialog.status.text() == "Added 2 tracks to downloads."
    qapp.processEvents()
    assert len(queue.jobs) == 2
    dialog.codec.setCurrentIndex(1)
    assert queue.items[0]["request"]["codec"] == "m4a"
    dialog.view_downloads_button.click()
    assert dialog.tabs.currentIndex() == 1
    dialog.close()
    assert len(queue.jobs) == 2
    assert not any(job.stopped for job in queue.jobs.values())
    dialog.deleteLater()


def test_adding_tracks_from_multiple_searches_respects_pause(queue, qapp, tmp_path):
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    dialog.tabs.setCurrentWidget(dialog.queue_view)
    dialog.queue_view.run.click()
    assert queue.paused
    dialog.tabs.setCurrentIndex(0)

    for index, query in enumerate(("first search", "second search"), 1):
        dialog.url.setText(query)
        dialog._succeeded("search", {"results": [
            {"url": f"https://example.com/{index}", "title": query},
        ]})
        dialog.search_results.item(0).setSelected(True)
        dialog._enqueue_selected()
        qapp.processEvents()
        assert queue.paused and not queue.jobs
        assert [item["state"] for item in queue.items] == ["waiting"] * index
        assert dialog.tabs.currentIndex() == 0
        assert dialog.tabs.tabText(1) == f"Downloads ({index})"
        assert "Added 1 track" in dialog.status.text()
        assert "queue is paused" in dialog.status.text()

    dialog.view_downloads_button.click()
    assert dialog.tabs.currentWidget() is dialog.queue_view
    dialog.queue_view.run.click()
    assert not queue.paused
    assert len(queue.jobs) == 2
    for job in list(queue.jobs.values()):
        job.fail("offline")
    assert dialog.tabs.tabText(1) == "Downloads"
    dialog.close()
    dialog.deleteLater()


def test_batch_dialog_validates_before_enqueuing(queue, qapp, tmp_path):
    from PySide6.QtCore import QTimer
    from PySide6.QtWidgets import QApplication, QPlainTextEdit, QPushButton

    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    queue.set_limit(4)

    def submit():
        modal = QApplication.activeModalWidget()
        field = modal.findChild(QPlainTextEdit)
        button = modal.findChild(QPushButton)
        field.setPlainText("https://example.com/a\nfile:///invalid")
        button.click()
        assert not queue.items
        assert modal.isVisible()
        field.setPlainText("https://example.com/a\n\nhttps://example.com/b")
        button.click()

    QTimer.singleShot(0, submit)
    dialog._paste_batch()
    assert len(queue.items) == 2
    assert queue.api.settings["concurrent_downloads"] == 4
    assert dialog.tabs.currentIndex() == 0
    qapp.processEvents()
    assert len(queue.jobs) == 2
    dialog.close()
    dialog.deleteLater()


def test_import_window_reveals_options_and_actions_when_relevant(queue, qapp, tmp_path, monkeypatch):
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    assert dialog.options_panel.isHidden()
    assert dialog.track_preview.isHidden()
    assert dialog.result_actions.isHidden()
    assert dialog.download_button.isHidden()
    assert dialog.cancel_button.isHidden()
    assert [action.text() for action in dialog.actions_button.menu().actions() if not action.isSeparator()] == [
        "Load playlist", "Paste batch", "Download with YouTube session…", "Check setup",
    ]
    dialog.show()
    qapp.processEvents()
    search_height = dialog.height()
    dialog.tabs.setCurrentWidget(dialog.queue_view)
    qapp.processEvents()
    assert dialog.height() > search_height
    dialog.tabs.setCurrentIndex(0)
    qapp.processEvents()
    assert dialog.height() == search_height

    dialog.options_button.click()
    assert not dialog.options_panel.isHidden()
    dialog.options_button.click()
    assert dialog.options_panel.isHidden()

    starts = []
    monkeypatch.setattr(dialog, "_start", starts.append)
    dialog.actions_button.menu().actions()[-1].trigger()
    assert starts == ["check"]
    dialog.url.setText("https://www.youtube.com/watch?v=example&list=example")
    assert dialog.preview_button.text() == "Preview"
    dialog.url.setText("https://www.youtube.com/playlist?list=example")
    assert dialog.preview_button.text() == "Load playlist"
    dialog.preview_button.click()
    assert starts == ["check", "playlist"]
    dialog._succeeded("playlist", {"results": [{"title": "Song", "url": "https://example.com/song"}]})
    assert not dialog.result_actions.isHidden()

    dialog._succeeded("preview", {"identity": "Youtube:song", "title": "Song"})
    assert not dialog.track_preview.isHidden()
    assert not dialog.download_button.isHidden()
    dialog.url.clear()
    assert dialog.track_preview.isHidden()
    assert dialog.result_actions.isHidden()
    assert dialog.download_button.isHidden()
    dialog.close()
    dialog.deleteLater()


def test_spotify_playlist_fills_search_and_stays_available_between_searches(queue, qapp, tmp_path, monkeypatch):
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    actions = []
    monkeypatch.setattr(dialog, "_start", actions.append)
    dialog.url.setText("spotify:playlist:37i9dQZF1DWWQRwui0ExPn")
    assert dialog.preview_button.text() == "Load playlist"
    dialog.preview()
    assert actions == ["spotify_playlist"]
    dialog._event({"event": "spotify_playlist_page", "loaded": 2, "total": 2, "entries": [
        {"title": "Song", "artist": "Artist", "query": "Artist Song"},
        {"title": "Unavailable song at position 2"},
    ]})
    dialog._succeeded("spotify_playlist", {"title": "Mix", "loaded": 2, "searchable": 1})
    dialog._finished()
    assert dialog.spotify_results.count() == 2
    assert not dialog.spotify_results.item(1).flags() & Qt.ItemFlag.ItemIsEnabled
    dialog.show()
    dialog.spotify_results.setCurrentRow(0)
    dialog.spotify_results.setFocus()
    QTest.keyClick(dialog.spotify_results, Qt.Key.Key_Return)
    assert dialog.url.text() == "Artist Song"
    assert actions == ["spotify_playlist"]
    dialog._enqueue_selected()
    assert not queue.items
    dialog.preview()
    assert actions == ["spotify_playlist", "search"]
    assert dialog.spotify_results.isHidden()
    dialog.spotify_playlist_toggle.click()
    assert not dialog.spotify_results.isHidden()
    assert dialog.spotify_results.count() == 2
    dialog.close()
    dialog.deleteLater()


def test_playlist_pages_keep_the_original_url_after_preview(queue, qapp, tmp_path, monkeypatch):
    dialog = ImportDialog(queue.api, dict(DEFAULTS, download_folder=str(tmp_path)), download_queue=queue)
    actions = []
    monkeypatch.setattr(dialog, "_start", actions.append)
    dialog.url.setText("https://example.com/playlist")
    dialog._load_playlist()
    assert actions == ["playlist"]
    assert dialog._playlist_next == 1
    dialog._succeeded("playlist", {"results": [{"title": "Song", "url": "https://example.com/song"}], "next_start": 101})
    dialog._select_result(dialog.search_results.item(0))
    assert dialog.url.text() == "https://example.com/song"
    dialog._load_playlist(more=True)
    assert dialog._playlist_url == "https://example.com/playlist"
    assert dialog._playlist_next == 101
    assert actions == ["playlist", "preview", "playlist"]
    dialog.close()
    dialog.deleteLater()


def test_provider_requests_are_paced_across_processes(tmp_path):
    import subprocess
    import sys

    script = '''import sys, tempfile, time
from plugins.community.ytdlp_import.provider_pacing import wait_for_provider
tempfile.gettempdir = lambda: sys.argv[1]
wait_for_provider("test", interval=0.08)
print(time.time(), flush=True)
'''
    processes = [subprocess.Popen([sys.executable, "-c", script, str(tmp_path)],
                                  stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
                                  creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0)) for _ in range(3)]
    results = [process.communicate(timeout=10) for process in processes]
    assert all(process.returncode == 0 for process in processes), results
    times = sorted(float(output) for output, _error in results)
    assert all(later - earlier >= 0.06 for earlier, later in zip(times, times[1:]))


def test_playlist_extraction_is_bounded_and_preserves_unavailable_rows(monkeypatch):
    import sys
    state = SimpleNamespace(calls=[], options=None, consumed=0)

    def entries():
        for index in range(200):
            state.consumed += 1
            if index == 1:
                yield None
            else:
                yield {"id": f"{index:011d}", "ie_key": "Youtube", "title": f"Song {index}",
                       "availability": "private" if index == 2 else None}

    class YoutubeDL:
        def __init__(self, options):
            state.options = options

        def __enter__(self):
            return self

        def __exit__(self, *args):
            pass

        def extract_info(self, url, *, download):
            state.calls.append((url, download))
            return {"title": "Playlist", "entries": entries()}

    monkeypatch.setitem(sys.modules, "yt_dlp", SimpleNamespace(YoutubeDL=YoutubeDL))
    monkeypatch.setattr(worker, "options", lambda config: {})
    result = worker.playlist("https://example.com/playlist", {}, start=101)
    assert state.consumed == 101
    assert state.options["playliststart"] == 101
    assert state.options["playlistend"] == 201
    assert state.options["noplaylist"] is False
    assert state.options["extract_flat"] is True
    assert len(result["results"]) == 100
    assert result["next_start"] == 201
    assert result["results"][0]["url"] == "https://www.youtube.com/watch?v=00000000000"
    assert result["results"][1]["available"] is False
    assert result["results"][2]["available"] is False


def test_malformed_saved_queue_is_ignored(qapp):
    api = API()
    api.settings = {"concurrent_downloads": "bad", "download_queue": [None, {}, {"id": "bad", "request": {}}]}
    manager = DownloadQueue(api, job_factory=Job)
    assert manager.items == []
    assert manager.limit == 2
    manager.deleteLater()


def test_queue_runs_real_subprocesses_and_cleans_stages(qapp, tmp_path, monkeypatch):
    import sys
    import time
    from PySide6.QtTest import QTest
    from plugins.community.ytdlp_import.job import ImportJob
    import plugins.community.ytdlp_import.job as transport

    script = tmp_path / "fake_worker.py"
    script.write_text('''import json, pathlib, sys, time
request = json.load(sys.stdin)
index = request["url"].rsplit("/", 1)[-1]
if request["action"] == "preview":
    result = {"identity": "Test:" + index, "title": "Track " + index, "subtitle_tracks": []}
else:
    time.sleep(0.25 if index == "0" else 0.02)
    path = pathlib.Path(request["folder"]) / (index + ".m4a")
    path.write_bytes(b"audio")
    result = {"identity": "Test:" + index, "path": str(path)}
print("LRCPLUS_YTDLP " + json.dumps({"event": "result", "result": result}), flush=True)
''', encoding="utf-8")
    monkeypatch.setattr(transport, "worker_command", lambda: [sys.executable, "-I", "-u", str(script)])
    api = API()
    manager = DownloadQueue(api, job_factory=ImportJob)
    options = {"folder": str(tmp_path), "codec": "m4a", "config": {}, "subtitle": None, "add_to_queue": True}
    manager.enqueue([{"url": f"https://example.com/{i}"} for i in range(3)], options)
    manager.set_paused(False)
    deadline = time.monotonic() + 8
    while any(item["state"] not in {"completed", "failed"} for item in manager.items) and time.monotonic() < deadline:
        QTest.qWait(10)
    assert [item["state"] for item in manager.items] == ["completed"] * 3
    assert [paths[0] for paths, _queued in api.imports] == [str(tmp_path / f"{i}.m4a") for i in range(3)]
    assert not list(tmp_path.glob(".lrcplus-import-*"))
    manager.shutdown()


def test_parallel_publication_keeps_colliding_bundles_together(tmp_path):
    from concurrent.futures import ThreadPoolExecutor

    destination = tmp_path / "downloads"
    destination.mkdir()
    bundles = []
    for index in range(4):
        stage = tmp_path / str(index)
        stage.mkdir()
        files = {}
        for extension in (".lrc", ".m4a"):
            path = stage / ("song" + extension)
            path.write_text(str(index))
            files[extension] = path
        bundles.append(files)
    with ThreadPoolExecutor(max_workers=4) as pool:
        results = list(pool.map(lambda files: worker.publish_bundle("song", files, destination), bundles))
    assert len({str(result[".m4a"]) for result in results}) == 4
    for index, result in enumerate(results):
        assert result[".m4a"].read_text() == str(index)
        assert result[".lrc"].read_text() == str(index)
        assert result[".m4a"].stem == result[".lrc"].stem
