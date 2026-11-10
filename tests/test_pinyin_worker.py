"""Pinyin subprocess lifecycle, batching, failure handling and frozen dispatch."""

from collections import OrderedDict
import importlib.util
import json
from pathlib import Path
import subprocess
import sys
import threading
from types import SimpleNamespace

import pytest

import lyrics.furigana as readings
from lyrics import pinyin_client
from core import pinyin_worker


def test_worker_converts_full_phrases_with_tones(monkeypatch):
    calls = []

    def pinyin(text, **options):
        calls.append((text, options))
        return [["chóng"], ["qìng"]]

    monkeypatch.setitem(sys.modules, "pypinyin", SimpleNamespace(
        Style=SimpleNamespace(TONE="tone"), pinyin=pinyin,
    ))
    assert pinyin_worker.convert_runs(["重庆"]) == [["chóng", "qìng"]]
    assert calls == [("重庆", {"style": "tone", "heteronym": False, "strict": False})]


def test_mixed_text_and_duplicate_runs_share_one_request(monkeypatch):
    calls = []

    def convert(runs):
        calls.append(runs)
        return [["nǐ", "hǎo"], ["chóng", "qìng"]]

    monkeypatch.setattr(readings, "read_pinyin_runs", convert)
    texts = ["你好!", "Hello 你好\n重庆", "", "カナ 123"]
    result = readings.build_pinyin_segments_batch(texts)
    assert calls == [["你好", "重庆"]]
    assert ["".join(part["text"] for part in row) for row in result] == texts
    assert [part["reading"] for part in result[1]] == [None, "nǐ", "hǎo", None, "chóng", "qìng"]
    assert result[0][0] is not result[1][1]  # Cached results can be edited independently.


@pytest.mark.parametrize("mode", ["pinyin", "auto"])
def test_song_prefetch_uses_one_worker_and_reuses_cached_readings(monkeypatch, mode):
    calls = []
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "_READING_MODE", mode)
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "find_reading_provider", lambda *args, **kwargs: None)
    monkeypatch.setattr(readings, "available_reading_providers", lambda: {})
    monkeypatch.setattr(readings, "read_pinyin_runs", lambda runs: calls.append(runs) or [["nǐ", "hǎo"], ["chóng", "qìng"]])
    progress = []
    assert readings.prefetch_furigana_batches(
        ["你好", "重庆", "你好"], progress_callback=lambda *values: progress.append(values),
    ) == (2, 2)
    assert calls == [["你好", "重庆"]]
    assert progress == [(0, 2), (1, 2), (2, 2)]
    assert readings.build_furigana_segments("重庆") == [
        {"text": "重", "reading": "chóng"}, {"text": "庆", "reading": "qìng"},
    ]
    assert readings.prefetch_furigana_batches(["你好", "重庆"]) == (0, 0)
    assert len(calls) == 1


def test_translation_rows_are_not_sent_to_pinyin_worker(monkeypatch):
    calls = []
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "available_reading_providers", lambda: {})
    monkeypatch.setattr(readings, "read_pinyin_runs", lambda runs: calls.append(runs) or [["nǐ", "hǎo"]])
    source = r"你好{\nt}译文"
    assert readings.prefetch_furigana_batches([source]) == (1, 1)
    result = readings.build_furigana_segments(source)
    assert calls == [["你好"]]
    assert "".join(part["text"] for part in result) == "你好\n译文"
    assert result[-1] == {"text": "译文", "reading": None}


def test_editor_generate_all_batches_pinyin_requests(editor, monkeypatch):
    from lyrics.models import LyricLine
    from PySide6.QtWidgets import QMessageBox

    calls = []
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    mapping = {"你好": ["nǐ", "hǎo"], "重庆": ["chóng", "qìng"]}
    monkeypatch.setattr(readings, "read_pinyin_runs", lambda runs: calls.append(runs) or [mapping[text] for text in runs])
    monkeypatch.setattr(QMessageBox, "information", lambda *args: None)
    editor.reading_mode_combo.setCurrentIndex(editor.reading_mode_combo.findData("pinyin"))
    editor.lines = [LyricLine(1, "你好"), LyricLine(2, "重庆")]
    editor._populate_lines()
    editor.generate_readings_for_all_lyrics()
    assert calls == [["你好", "重庆"]]


def test_cancelled_prefetch_does_not_start_worker(monkeypatch):
    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "available_reading_providers", lambda: {})
    monkeypatch.setattr(readings, "read_pinyin_runs", lambda *_: pytest.fail("Cancelled work started"))
    assert readings.prefetch_furigana_batches(["你好"], should_continue=lambda: False) == (0, 1)
    assert readings.get_cached_furigana_segments("你好") is None


def test_cancellation_during_worker_does_not_cache_results(monkeypatch):
    running = [True]

    def convert(runs):
        running[0] = False
        return [["nǐ", "hǎo"]]

    monkeypatch.setattr(readings, "_READING_MODE", "pinyin")
    monkeypatch.setattr(readings, "_AUTOMATIC_READINGS_ENABLED", True)
    monkeypatch.setattr(readings, "_SEGMENT_CACHE", OrderedDict())
    monkeypatch.setattr(readings, "available_reading_providers", lambda: {})
    monkeypatch.setattr(readings, "read_pinyin_runs", convert)
    assert readings.prefetch_furigana_batches(["你好"], should_continue=lambda: running[0]) == (0, 1)
    assert readings.get_cached_furigana_segments("你好") is None


@pytest.mark.parametrize("frozen", [False, True])
def test_client_command_and_temporary_files_are_cleaned(monkeypatch, frozen):
    paths = []
    monkeypatch.setattr(sys, "frozen", frozen, raising=False)

    def run(command, **options):
        request = Path(command[command.index("--input") + 1])
        response = Path(command[command.index("--output") + 1])
        paths.extend([request, response])
        assert json.loads(request.read_text(encoding="utf-8")) == {"runs": ["你好"]}
        if frozen:
            assert command[:2] == [sys.executable, "--lrcplus-pinyin-worker"]
        else:
            assert command[:3] == [sys.executable, "-I", "-u"]
            assert Path(command[3]).resolve() == Path(pinyin_worker.__file__).resolve()
        assert options["timeout"] == 30
        assert options["stdout"] == subprocess.DEVNULL
        assert options["creationflags"] == getattr(subprocess, "CREATE_NO_WINDOW", 0)
        response.write_text(json.dumps([["nǐ", "hǎo"]]), encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pinyin_client.subprocess, "run", run)
    assert pinyin_client.read_pinyin_runs(["你好"]) == [["nǐ", "hǎo"]]
    assert all(not path.exists() for path in paths)
    assert not paths[0].parent.exists()


@pytest.mark.parametrize("failure", ["timeout", "crash", "malformed", "short", "missing", "types"])
def test_failed_worker_preserves_text_and_cleans_files(monkeypatch, failure):
    folders = []

    def run(command, **options):
        response = Path(command[command.index("--output") + 1])
        folders.append(response.parent)
        if failure == "timeout":
            raise subprocess.TimeoutExpired(command, options["timeout"])
        if failure == "crash":
            return SimpleNamespace(returncode=1)
        if failure != "missing":
            response.write_text({
                "malformed": "no JSON", "short": '[[]]', "types": '[[1,2]]',
            }[failure], encoding="utf-8")
        return SimpleNamespace(returncode=0)

    monkeypatch.setattr(pinyin_client.subprocess, "run", run)
    assert readings.build_pinyin_segments("你好!") == [{"text": "你好!", "reading": None}]
    assert folders and all(not folder.exists() for folder in folders)


def test_concurrent_requests_use_only_one_dictionary_process(monkeypatch):
    started = threading.Event()
    release = threading.Event()
    calls = []
    results = []

    def run(command, **options):
        calls.append(command)
        started.set()
        assert release.wait(3)
        response = Path(command[command.index("--output") + 1])
        response.write_text('[["nǐ", "hǎo"]]', encoding="utf-8")
        return SimpleNamespace(returncode=0)

    def read():
        results.append(pinyin_client.read_pinyin_runs(["你好"]))

    monkeypatch.setattr(pinyin_client.subprocess, "run", run)
    first = threading.Thread(target=read)
    second = threading.Thread(target=read)
    first.start()
    try:
        assert started.wait(3)
        second.start()
        assert len(calls) == 1
    finally:
        release.set()
        first.join(3)
        if second.ident is not None:
            second.join(3)
    assert not first.is_alive() and not second.is_alive()
    assert results == [[["nǐ", "hǎo"]], [["nǐ", "hǎo"]]]


def test_worker_missing_dependency_reports_file_error(tmp_path, monkeypatch):
    request = tmp_path / "input.json"
    response = tmp_path / "output.json"
    request.write_text('{"runs":["你好"]}', encoding="utf-8")
    monkeypatch.setattr(sys, "argv", ["worker", "--input", str(request), "--output", str(response)])
    monkeypatch.setitem(sys.modules, "pypinyin", None)
    assert pinyin_worker.main() == 1
    assert "ModuleNotFoundError" in json.loads(response.read_text(encoding="utf-8"))["error"]


def test_worker_uses_frozen_runtime_and_active_dependency_site(tmp_path, monkeypatch):
    from core import dependencies

    request = tmp_path / "input.json"
    response = tmp_path / "output.json"
    request.write_text('{"runs":["你好"]}', encoding="utf-8")
    site = str(tmp_path / "active-site")
    activated = []
    monkeypatch.setattr(sys, "argv", ["worker", "--input", str(request), "--output", str(response)])
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setenv("LRCPLUS_DEPENDENCY_SITE", site)
    monkeypatch.setattr(dependencies, "activate_runtime_libraries", lambda: activated.append(True))

    def convert(runs):
        assert activated == [True]
        assert sys.path[-1] == site
        return [["nǐ", "hǎo"]]

    monkeypatch.setattr(pinyin_worker, "convert_runs", convert)
    assert pinyin_worker.main() == 0
    assert json.loads(response.read_text(encoding="utf-8")) == [["nǐ", "hǎo"]]


def test_actual_worker_exits_without_importing_dictionary_in_parent():
    if importlib.util.find_spec("pypinyin") is None:
        pytest.skip("Optional pypinyin package is not installed")
    script = """
import sys
from lyrics.furigana import build_pinyin_segments_batch
assert 'pypinyin' not in sys.modules
assert build_pinyin_segments_batch(['重庆', '你好!']) == [
    [{'text':'重','reading':'chóng'}, {'text':'庆','reading':'qìng'}],
    [{'text':'你','reading':'nǐ'}, {'text':'好','reading':'hǎo'}, {'text':'!','reading':None}],
]
assert not any(name.split('.')[0] == 'pypinyin' for name in sys.modules)
"""
    result = subprocess.run([sys.executable, "-c", script], capture_output=True, timeout=20,
                            encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stdout + result.stderr


def test_executable_worker_dispatch_precedes_gui_imports(tmp_path):
    if importlib.util.find_spec("pypinyin") is None:
        pytest.skip("Optional pypinyin package is not installed")
    request = tmp_path / "input.json"
    response = tmp_path / "output.json"
    request.write_text('{"runs":["重庆"]}', encoding="utf-8")
    script = """
import builtins, runpy, sys
original_import = builtins.__import__
def guard(name, *args, **kwargs):
    assert name.split('.')[0] not in {'PySide6', 'numpy', 'requests'}, name
    return original_import(name, *args, **kwargs)
builtins.__import__ = guard
sys.argv = ['main.py', '--lrcplus-pinyin-worker', '--input', sys.argv[1], '--output', sys.argv[2]]
runpy.run_path('main.py', run_name='__main__')
"""
    result = subprocess.run([sys.executable, "-c", script, str(request), str(response)],
                            capture_output=True, timeout=20, encoding="utf-8", errors="replace")
    assert result.returncode == 0, result.stdout + result.stderr
    assert json.loads(response.read_text(encoding="utf-8")) == [["chóng", "qìng"]]
