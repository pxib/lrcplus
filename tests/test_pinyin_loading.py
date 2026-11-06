"""Keep Mandarin dictionaries out of startup without losing pinyin readings."""

import builtins
import importlib.util
from pathlib import Path
import subprocess
import sys

import pytest

from lyrics.furigana import build_pinyin_segments


def test_library_startup_does_not_import_mandarin_dictionaries():
    # Use a fresh interpreter: other reading tests may have loaded pypinyin.
    script = """
import builtins
from pathlib import Path
attempts = []
original_import = builtins.__import__
def observe(name, *args, **kwargs):
    if name.split('.')[0] == 'pypinyin':
        attempts.append(name)
    return original_import(name, *args, **kwargs)
builtins.__import__ = observe
import main
app = main.QApplication([])
manager = main.PluginManager(disabled_plugins=[])
manager.discover_and_load(Path('plugins'))
main.Player._automatic_update_check = lambda self: None
window = main.Player(plugin_manager=manager)
try:
    manager.set_host_window(window)
    manager.emit('app_ready', window=window)
    window.show()
    app.processEvents()
    assert not attempts, attempts
finally:
    window.close()
    manager.unload_all()
"""
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=25,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_non_han_text_does_not_attempt_pinyin_import(monkeypatch):
    attempts = []
    original_import = builtins.__import__

    def observe(name, *args, **kwargs):
        if name.split(".")[0] == "pypinyin":
            attempts.append(name)
        return original_import(name, *args, **kwargs)

    monkeypatch.setattr(builtins, "__import__", observe)
    assert build_pinyin_segments("") == []
    assert build_pinyin_segments("Hello 123!\nカナ") == [{"text": "Hello 123!\nカナ", "reading": None}]
    assert attempts == []


def test_first_pinyin_request_keeps_tones_and_non_han_text(monkeypatch):
    calls = []

    def read(runs):
        calls.append(runs)
        return [["nǐ", "hǎo"]]

    monkeypatch.setattr("lyrics.furigana.read_pinyin_runs", read)
    assert build_pinyin_segments("Hello 你好!\n") == [
        {"text": "Hello ", "reading": None}, {"text": "你", "reading": "nǐ"},
        {"text": "好", "reading": "hǎo"}, {"text": "!\n", "reading": None},
    ]
    assert calls == [["你好"]]


def test_missing_optional_pinyin_keeps_original_text(monkeypatch):
    def unavailable(_runs):
        raise RuntimeError("ModuleNotFoundError: No module named 'pypinyin'")

    monkeypatch.setattr("lyrics.furigana.read_pinyin_runs", unavailable)
    assert build_pinyin_segments("你好!") == [{"text": "你好!", "reading": None}]


def test_real_pinyin_dictionary_preserves_phrase_pronunciation():
    if importlib.util.find_spec("pypinyin") is None:
        pytest.skip("Optional pypinyin package is not installed")
    assert build_pinyin_segments("重庆") == [
        {"text": "重", "reading": "chóng"}, {"text": "庆", "reading": "qìng"},
    ]
