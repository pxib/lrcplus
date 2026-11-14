import pytest
from PySide6.QtTest import QTest

from core.search_history import load_search_history, save_search_history
from core.settings import load_all_settings, save_all_settings
from ui.search_history import SearchHistoryLineEdit


@pytest.mark.parametrize("category,limit", [("lyrics", 15), ("library", 20), ("ytdlp", 50)])
def test_history_limits_recency_and_persistence(qapp, category, limit):
    field = SearchHistoryLineEdit(category)
    for index in range(limit + 3):
        field.setText(f"Search {index}")
        field.remember_search()
    field.setText("  SEARCH 4  ")
    field.remember_search()
    history = load_search_history(category)
    assert len(history) == limit
    assert history[0] == "SEARCH 4"
    assert "Search 4" not in history
    assert "Search 0" not in history
    reopened = SearchHistoryLineEdit(category)
    assert reopened.completer().model().stringList() == history
    reopened._recall(history[-1])
    assert reopened.text() == history[-1]
    reopened.clear_history()
    assert load_search_history(category) == []
    assert reopened.completer().model().stringList() == []


def test_histories_are_independent_and_tolerate_malformed_settings():
    save_all_settings({"volume": 42, "search_history": {"lyrics": [None, "", " a ", "A", 12, "b"]}})
    assert load_search_history("lyrics") == ["a", "b"]
    save_search_history("library", ["library query"])
    assert load_search_history("lyrics") == ["a", "b"]
    assert load_search_history("library") == ["library query"]
    assert load_all_settings()["volume"] == 42
    save_all_settings({"search_history": []})
    assert load_search_history("lyrics") == []
    save_search_history("lyrics", ["new"])
    assert load_search_history("lyrics") == ["new"]


def test_library_records_after_idle_or_edit_finished(qapp):
    field = SearchHistoryLineEdit("library", remember_idle=True)
    field.setText("art")
    field.setText("artist song")
    assert load_search_history("library") == []
    QTest.qWait(1300)
    assert load_search_history("library") == ["artist song"]
    field.setText("another song")
    field.editingFinished.emit()
    assert load_search_history("library") == ["another song", "artist song"]
    field.clear()
    field.editingFinished.emit()
    assert load_search_history("library") == ["another song", "artist song"]


def test_ytdlp_uses_plugin_settings_and_validated_submissions(qapp, monkeypatch):
    from types import SimpleNamespace
    from plugins.community.ytdlp_import.dialog import ImportDialog
    from plugins.community.ytdlp_import.plugin import DEFAULTS

    settings = {"downloads": {"existing": "preserved"}}
    api = SimpleNamespace(get_setting=lambda key, default=None: settings.get(key, default),
                          set_setting=lambda key, value: settings.__setitem__(key, value))
    dialog = ImportDialog(api, DEFAULTS)
    monkeypatch.setattr(dialog, "_start", lambda action: None)
    for value in ("", "x" * 501, "file:///bad"):
        dialog.url.setText(value)
        dialog.preview()
    assert "search_history" not in settings
    for value in ("artist song", "https://www.youtube.com/watch?v=abcdefghijk"):
        dialog.url.setText(value)
        dialog.preview()
    assert settings["search_history"] == ["https://www.youtube.com/watch?v=abcdefghijk", "artist song"]
    assert settings["downloads"] == {"existing": "preserved"}
    assert load_search_history("ytdlp") == []
    dialog.close()
    dialog.deleteLater()
