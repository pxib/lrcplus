"""Opening and selecting lyrics must not wait for in-flight reading requests."""

from copy import deepcopy
import threading
import time
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QTimer

import lyrics.furigana as readings
from lyrics.models import LyricLine, LyricSegment
import ui.lrc_editor as editor_module


@pytest.mark.parametrize("mode", ["auto", "furigana"])
def test_open_and_select_while_yomi_request_is_pending(tmp_path, qapp, monkeypatch, mode):
    previous_mode = readings.get_reading_mode()
    previous_context = readings.get_auto_language_context()
    previous_parser = readings.get_active_parser()
    started = threading.Event()
    release = threading.Event()
    ui_generation_calls = []
    workers = []
    dialog = None
    main_thread = threading.current_thread()
    real_build = readings.build_furigana_segments

    def post(url, *, data, **kwargs):
        workers.append(threading.current_thread())
        started.set()
        assert release.wait(5), "test did not release pending Yomi request"
        words = [
            {"surface": char, "reading": {"猫": "ネコ", "犬": "イヌ"}.get(char, char)}
            for char in data["text"] if char != "\n"
        ]
        return SimpleNamespace(raise_for_status=lambda: None, json=lambda: {"words": words})

    def build(text, **kwargs):
        if threading.current_thread() is main_thread:
            # Record the regression without hanging the test behind the lock.
            ui_generation_calls.append(text)
            return [{"text": text, "reading": None}]
        workers.append(threading.current_thread())
        return real_build(text, **kwargs)

    monkeypatch.setattr(readings.requests, "post", post)
    monkeypatch.setattr(editor_module, "build_furigana_segments", build)
    readings.set_active_parser("yomi")
    readings.set_reading_mode(mode)
    readings.set_auto_language_context("japanese")
    readings.clear_reading_segment_cache()
    with readings._CACHE_LOCK:
        readings._YOMI_CACHE.clear()
    path = tmp_path / "pending.lrc"
    path.write_text(f"[g:{mode}]\n[00:01.000]猫だ\n[00:02.000]犬だ\n", encoding="utf-8")
    player_worker = threading.Thread(target=lambda: real_build("猫だ"), daemon=True)
    player_worker.start()

    try:
        assert started.wait(2)
        dialog = editor_module.LrcEditorDialog(path)
        original_lines = deepcopy(dialog.lines)
        dialog.show()
        qapp.processEvents()
        dialog.line_list.setCurrentRow(1)
        heartbeat = []
        QTimer.singleShot(0, lambda: heartbeat.append(True))
        qapp.processEvents()

        assert heartbeat == [True]
        assert not release.is_set()
        assert ui_generation_calls == []
        assert dialog.furigana_table.item(0, 0).text() == "犬だ"
        assert dialog.furigana_table.item(0, 1).text() == ""

        release.set()
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            qapp.processEvents()
            if dialog.furigana_table.item(0, 1).text():
                break
            threading.Event().wait(0.01)

        assert dialog.furigana_table.item(0, 1).text() == "いぬ"
        assert "犬(いぬ)" in dialog.ruby_preview.text()
        assert ui_generation_calls == []
        assert dialog.lines == original_lines
        assert not dialog.document_dirty

        # Editing the selected text must also regenerate off the UI thread.
        dialog.line_text.setPlainText("犬だ犬")
        deadline = time.monotonic() + 3
        while time.monotonic() < deadline:
            qapp.processEvents()
            if dialog.furigana_table.rowCount() == 3:
                break
            threading.Event().wait(0.01)
        assert dialog.furigana_table.rowCount() == 3
        assert dialog.furigana_table.item(2, 0).text() == "犬"
        assert dialog.furigana_table.item(2, 1).text() == "いぬ"
        assert ui_generation_calls == []
    finally:
        release.set()
        if dialog is not None:
            dialog.document_dirty = False
            dialog.reject()
            dialog.deleteLater()
        player_worker.join(2)
        for worker in set(workers):
            worker.join(2)
        qapp.processEvents()
        readings.set_reading_mode(previous_mode)
        readings.set_auto_language_context(previous_context)
        readings.set_active_parser(previous_parser)
        readings.clear_reading_segment_cache()
        with readings._CACHE_LOCK:
            readings._YOMI_CACHE.clear()


def test_cold_cache_keeps_manual_readings_in_separate_rows(editor, monkeypatch):
    monkeypatch.setattr(editor_module, "get_cached_furigana_segments", lambda text: None)
    line = LyricLine(1, "猫と犬", [
        LyricSegment(1, "猫と犬", ruby_spans=[(0, 1, "ねこ"), (2, 3, "いぬ")]),
    ])
    original = deepcopy(line)

    editor._populate_furigana_editor(line)

    assert [
        (editor.furigana_table.item(row, 0).text(), editor.furigana_table.item(row, 1).text())
        for row in range(editor.furigana_table.rowCount())
    ] == [("猫", "ねこ"), ("と", ""), ("犬", "いぬ")]
    assert line == original
