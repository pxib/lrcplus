import sys
from types import SimpleNamespace
from weakref import WeakValueDictionary

from PySide6.QtCore import Qt
from PySide6.QtWidgets import QDialog, QTreeWidget, QTreeWidgetItem, QVBoxLayout, QWidget
import pytest

import core.idle_cache as caches
from core.cache_cleanup import clear_idle_caches
from core.idle_cache import IdleCache
from lyrics.line_breaks import reading_source


@pytest.fixture
def workspace(qapp, monkeypatch):
    time = [0.0]
    monkeypatch.setattr(caches, "monotonic", lambda: time[0])
    monkeypatch.setattr(caches, "_caches", WeakValueDictionary())

    class Player(QWidget):
        pass

    class Editor(QDialog):
        pass

    player = Player()
    player.lyric_lines = [SimpleNamespace(text="song\ntranslation", translation_lines=[1])]
    player._library_cover_cache = IdleCache()
    player.library_table = QTreeWidget(player)
    player.library_table.setHeaderHidden(True)
    player.library_table.setColumnCount(1)
    QVBoxLayout(player).addWidget(player.library_table)
    player.resize(300, 160)
    for index in range(100):
        item = QTreeWidgetItem(player.library_table, [str(index)])
        item.setData(0, Qt.ItemDataRole.UserRole, str(index))
        player._library_cover_cache[str(index)] = object()
    editor = Editor(player)
    editor.lines = [SimpleNamespace(text="editor draft", translation_lines=[])]
    segments, yomi = IdleCache(), IdleCache()
    readings = SimpleNamespace(_SEGMENT_CACHE=segments, _YOMI_CACHE=yomi)
    monkeypatch.setitem(sys.modules, "ui.main_window", SimpleNamespace(Player=Player))
    monkeypatch.setitem(sys.modules, "ui.lrc_editor", SimpleNamespace(LrcEditorDialog=Editor))
    monkeypatch.setitem(sys.modules, "lyrics.furigana", readings)
    app = SimpleNamespace(topLevelWidgets=lambda: [player, editor])
    yield SimpleNamespace(player=player, editor=editor, app=app, time=time,
                          segments=segments, yomi=yomi)
    editor.close()
    player.close()
    editor.deleteLater()
    player.deleteLater()


def test_song_and_open_editor_readings_survive_idle_cleanup(workspace):
    w = workspace
    source = reading_source("song\ntranslation", [1])
    for text in (source, "editor draft", "previous song"):
        w.segments[("parser", "mode", text)] = [{"text": text}]
        w.yomi[text] = [text]
    metadata = IdleCache()
    metadata["previous song"] = object()
    w.editor.show()
    w.time[0] = 600
    clear_idle_caches(w.app)
    assert set(w.segments) == {("parser", "mode", source), ("parser", "mode", "editor draft")}
    assert set(w.yomi) == {source, "editor draft"}
    assert not metadata

    # Once closed or unloaded, protected content becomes eligible immediately.
    w.editor.hide()
    w.player.lyric_lines = []
    clear_idle_caches(w.app)
    assert not w.segments and not w.yomi


def test_visible_covers_survive_until_library_is_hidden(workspace, qapp):
    w = workspace
    w.player.show()
    qapp.processEvents()
    w.time[0] = 600
    clear_idle_caches(w.app)
    assert "0" in w.player._library_cover_cache
    assert "99" not in w.player._library_cover_cache
    w.player.library_table.hide()
    clear_idle_caches(w.app)
    assert not w.player._library_cover_cache


def test_cleanup_does_not_import_unused_features(qapp, monkeypatch):
    monkeypatch.delitem(sys.modules, "ui.main_window", raising=False)
    monkeypatch.delitem(sys.modules, "ui.lrc_editor", raising=False)
    monkeypatch.delitem(sys.modules, "lyrics.furigana", raising=False)
    clear_idle_caches(SimpleNamespace(topLevelWidgets=lambda: []))
    assert "ui.main_window" not in sys.modules
    assert "ui.lrc_editor" not in sys.modules
    assert "lyrics.furigana" not in sys.modules
