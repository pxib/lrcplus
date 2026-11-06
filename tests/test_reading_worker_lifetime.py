"""Reading completion must survive eviction without retaining Qt rows."""

import gc
import subprocess
import sys
import weakref
from concurrent.futures import Future, ThreadPoolExecutor
from threading import Event

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QThread
from PySide6.QtTest import QTest

import lyrics.furigana_widget as renderer
from core.lyrics_controller import _FuriganaProgressBridge
from lyrics.scrolling_widget import ScrollingLyricsWidget


@pytest.mark.parametrize("karaoke", [False, True])
def test_korean_reading_worker_does_not_retain_evicted_row(qapp, monkeypatch, karaoke):
    started, release = Event(), Event()
    text = "안녕하세요"

    def generate(value):
        started.set()
        assert release.wait(5)
        return [{"text": value, "reading": "an-nyeong-ha-se-yo"}]

    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: None)
    monkeypatch.setattr(renderer, "build_furigana_segments", generate)
    with ThreadPoolExecutor(max_workers=1) as executor:
        monkeypatch.setattr(renderer, "_FURIGANA_EXECUTOR", executor)
        row = renderer.FuriganaWidget()
        try:
            if karaoke:
                row.set_segments(text, [{"text": text, "start": 0, "end": 2}], 2)
            else:
                row.set_lyric(text)
            assert started.wait(2)
            pending = row._furigana_future
            reference = weakref.ref(row)
            ScrollingLyricsWidget._discard_row(row)
            QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
            del row
            gc.collect()
            # Even unfinished provider work must not own a deleted Qt row.
            assert reference() is None
        finally:
            release.set()
        assert pending.result(timeout=2)[0]["text"] == text
        qapp.processEvents()


def test_newest_reading_result_is_applied_on_ui_thread(qapp, monkeypatch):
    futures = []

    class Executor:
        def submit(self, work):
            future = Future()
            future.set_running_or_notify_cancel()
            futures.append(future)
            return future

    monkeypatch.setattr(renderer, "_FURIGANA_EXECUTOR", Executor())
    monkeypatch.setattr(renderer, "get_cached_furigana_segments", lambda text: None)
    row = renderer.FuriganaWidget()
    applied = []
    original = row.furigana_ready

    def ready(text, result):
        applied.append(QThread.currentThread())
        original(text, result)

    monkeypatch.setattr(row, "furigana_ready", ready)
    try:
        row.set_lyric("이전")
        row.set_lyric("지금")
        futures[1].set_result([{"text": "지금", "reading": "ji-geum"}])
        QTest.qWait(60)
        assert row.segments[0]["reading"] == "ji-geum"
        assert applied == [qapp.thread()]
        futures[0].set_result([{"text": "이전", "reading": "i-jeon"}])
        QTest.qWait(60)
        assert row.segments[0]["reading"] == "ji-geum"
        assert len(applied) == 1
    finally:
        row.stop_workers()
        row.deleteLater()


def test_prefetch_delivers_only_current_request_on_ui_thread(qapp):
    bridge = _FuriganaProgressBridge()
    received = []
    bridge.progress.connect(lambda *args: received.append((args, QThread.currentThread())))
    finished = []
    bridge.finished.connect(finished.append)
    try:
        queue = bridge.start(1)
        bridge.start(2)

        def complete():
            queue.put(("progress", (1, 1, 1)))
            queue.put(("finished", (1,)))
            queue.put(("progress", (2, 2, 2)))
            queue.put(("finished", (2,)))

        with ThreadPoolExecutor(max_workers=1) as executor:
            executor.submit(complete).result(timeout=2)
        QTest.qWait(60)
        assert received == [((2, 2, 2), qapp.thread())]
        assert finished == [2]
        assert not bridge._timer.isActive()
    finally:
        bridge.deleteLater()


def test_crash_trace_persists_after_process_exit(tmp_path, monkeypatch):
    monkeypatch.setenv("LRCPLUS_DATA_DIR", str(tmp_path))
    code = (
        "import faulthandler; import core.crash_logging as log; "
        "log.install_crash_logging(); log.install_crash_logging(); "
        "faulthandler.dump_traceback(file=log._crash_file, all_threads=True)"
    )
    subprocess.run([sys.executable, "-c", code], check=True, timeout=10)
    trace = (tmp_path / "logs" / "crash.log").read_text(encoding="utf-8")
    assert trace.count("Session ") == 1
    assert 'File "<string>"' in trace
