"""Regressions for deferred networking/renderers and shared library metadata."""

from pathlib import Path
import subprocess
import sys

from PySide6.QtCore import Qt

from core.music_library import LibraryTrack
from core.settings import save_global_lyrics_settings
from ui.main_window import Player


def test_default_startup_defers_requests_and_lyrics_renderers():
    script = """
import sys
from pathlib import Path
import main
from PySide6.QtTest import QTest
app = main.QApplication([])
manager = main.PluginManager(disabled_plugins=[])
manager.discover_and_load(Path('plugins'))
main.Player._automatic_update_check = lambda self: None
window = main.Player(plugin_manager=manager)
try:
    manager.set_host_window(window)
    manager.emit('app_ready', window=window)
    window.show()
    QTest.qWait(100)
    assert 'requests' not in sys.modules
    for name in ('ui.settings_dialog', 'ui.lrc_editor', 'ui.translation_panel',
                 'plugins.community.lyrics_translator.dialog'):
        assert name not in sys.modules, name
    assert manager.plugins['floating_lyrics'].instance.floating is None
    assert not window._lyrics_renderers_ready
    assert window._lyric_widget is None and window._scrolling_lyrics_widget is None
    window.close()
    assert not window._lyrics_renderers_ready
    from core.http_client import requests
    assert 'requests' not in sys.modules
    exception = requests.RequestException
    assert exception is sys.modules['requests'].RequestException
finally:
    window.close()
    manager.unload_all()
"""
    result = subprocess.run([sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
                            capture_output=True, encoding="utf-8", errors="replace", timeout=20)
    assert result.returncode == 0, result.stdout + result.stderr


def test_http_proxy_shares_transport_patches_with_requests(monkeypatch):
    import requests as actual
    from core.http_client import requests
    from core import lrclib
    from lyrics import furigana

    marker = object()
    monkeypatch.setattr(requests, "get", lambda *args, **kwargs: marker)
    assert actual.get("unused") is marker
    assert lrclib.requests.get("unused") is marker
    monkeypatch.setattr(actual, "post", lambda *args, **kwargs: marker)
    assert furigana.requests.post("unused") is marker
    assert requests.Timeout is actual.Timeout
    monkeypatch.setattr(requests, "_lrcplus_test_marker", marker, raising=False)
    assert actual._lrcplus_test_marker is marker


def test_http_proxy_restores_transport_after_patch(monkeypatch):
    import requests as actual
    from core.http_client import requests

    assert vars(requests) is vars(actual)
    for name in ("get", "post"):
        original = getattr(actual, name)
        with monkeypatch.context() as patch:
            replacement = lambda *args, **kwargs: None
            patch.setattr(requests, name, replacement)
            assert getattr(actual, name) is replacement
        assert getattr(requests, name) is original
        assert getattr(actual, name) is original


def test_first_lyrics_workspace_uses_latest_settings_and_reuses_widgets(qapp):
    window = Player()
    try:
        assert not window._lyrics_renderers_ready
        save_global_lyrics_settings({"font_size": 47, "ruby_font_size": 21})
        window.refresh_lyrics_settings_for_current_file()
        assert not window._lyrics_renderers_ready
        window.workspace_stack.setCurrentWidget(window.lyrics_page)
        assert window._lyrics_renderers_ready
        compact, scrolling = window.lyric_widget, window.scrolling_lyrics_widget
        assert compact.base_font_size == 47
        assert scrolling.font_size == 47
        assert compact.manual_overrides is scrolling.manual_overrides
        for _ in range(3):
            window.workspace_stack.setCurrentWidget(window.library_page)
            window.workspace_stack.setCurrentWidget(window.lyrics_page)
        assert window.lyric_widget is compact
        assert window.scrolling_lyrics_widget is scrolling
        assert window.lyric_display_stack.count() == 2
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_plugin_renderer_access_and_first_lyric_load_work(qapp, tmp_path, monkeypatch):
    monkeypatch.setattr(Player, "_start_furigana_prefetch", lambda self: None)
    window = Player()
    try:
        compact = window.lyric_widget  # Existing plugin-facing access initializes on demand.
        assert window._lyrics_renderers_ready
        path = tmp_path / "first.lrc"
        path.write_text("[00:00]First line\n[00:02]Second line", encoding="utf-8")
        window.load_lrc_file(path)
        assert window.lyric_widget is compact
        assert window.scrolling_lyrics_widget.lyric_count == 2
        assert window.current_line == 0
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_library_cells_and_sorting_read_shared_track_metadata(qapp):
    window = Player()
    try:
        first = LibraryTrack("C:/songs/first.mp3", "First", album="Album", duration=120)
        second = LibraryTrack("C:/songs/second.mp3", "Second", album="Album", duration=600)
        window.library_tracks = [first, second]
        window._populate_library_view()
        album = window.library_table.topLevelItem(0)
        item = album.child(0)
        first.title, first.duration, first.bitrate = "Renamed", 900, 192
        assert item.text(2) == "Renamed"
        assert item.text(5) == "15:00"
        assert item.text(7) == "192 kbps"
        window.library_table.sortItems(5, Qt.SortOrder.AscendingOrder)
        assert album.child(1) is item
        window.library_search.setText("renamed")
        window._filter_library_view()
        assert not item.isHidden()
        assert album.child(0).isHidden()
        window.library_search.clear()
        window._filter_library_view()
        assert not album.child(0).isHidden()
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_karaoke_attaches_after_lazy_renderer_creation(qapp):
    from types import SimpleNamespace
    from PySide6.QtMultimedia import QMediaPlayer
    from lyrics.models import LyricLine, LyricSegment
    from plugins.core.karaoke.plugin import KaraokePlugin

    window = Player()
    plugin = KaraokePlugin()
    plugin.on_load(SimpleNamespace(on=lambda *args: None))
    try:
        plugin.on_app_ready(window)
        assert not window._lyrics_renderers_ready
        window.current_lyrics_path = Path("song.lrcx")
        window.lyric_lines = [LyricLine(0, "Word", [LyricSegment(0, "Word", end=1)], has_karaoke=True)]
        window.show()
        window.workspace_stack.setCurrentWidget(window.lyrics_page)
        qapp.processEvents()
        assert window._lyrics_renderers_ready
        assert len(plugin._watched_displays) == 2
        plugin.on_playback_state_changed(QMediaPlayer.PlaybackState.PlayingState)
        assert plugin.timer.isActive()
        window.workspace_stack.setCurrentWidget(window.library_page)
        qapp.processEvents()
        assert not plugin.timer.isActive()
    finally:
        plugin.on_unload(None)
        window.close()
        window.deleteLater()
        qapp.processEvents()


def test_lyric_match_sorting_preserves_selection_and_clears_results(qapp, monkeypatch):
    from core.library_lyrics import LyricMatch

    window = Player()
    try:
        window.library_tracks = [LibraryTrack("C:/one.mp3", "One", album="Album"),
                                 LibraryTrack("C:/two.mp3", "Two", album="Album")]
        window._populate_library_view()
        group = window.library_table.topLevelItem(0)
        first = group.child(0)
        first.setSelected(True)
        window.library_search_filter.setCurrentIndex(1)
        column = window.library_lyrics_column
        window.library_table.sortItems(column, Qt.SortOrder.AscendingOrder)
        matches = {"C:/one.mp3": LyricMatch("Zulu", 0, 1), "C:/two.mp3": LyricMatch("Alpha", 0, 1)}
        monkeypatch.setattr(window, "_search_library_lyrics", lambda query: matches)
        window.library_search.setText("query")
        window._filter_library_view()
        assert group.child(0).text(column) == "Alpha"
        assert group.child(1) is first and first.isSelected()
        window.library_search.clear()
        window._filter_library_view()
        assert group.child(0) is first and first.isSelected()
        assert first.text(column) == ""
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()
