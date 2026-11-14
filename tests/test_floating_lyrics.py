from types import SimpleNamespace
from pathlib import Path

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPushButton

from plugins.community.floating_lyrics.plugin import FloatingLyricsPlugin, FloatingLyricsWindow


@pytest.fixture
def load_floating_plugin(qapp):
    from core.plugin_manager import PluginManager

    managers, windows = [], []

    def load(**settings):
        manager = PluginManager()
        managers.append(manager)
        api = manager.get_api("floating_lyrics")
        for key, value in settings.items():
            api.set_setting(key, value)
        manager.load_from_file("floating_lyrics", Path(__file__).resolve().parents[1]
                               / "plugins/community/floating_lyrics/plugin.py")
        plugin = manager.plugins["floating_lyrics"].instance
        host = QPushButton("Main player")
        host.current_line = 0
        host.lyric_lines = [SimpleNamespace(text="Current lyric", translation_lines=[])]
        host.show_ruby = False
        windows.append(host)
        return plugin, host

    yield load
    for manager in managers:
        manager.unload_all()
    for window in windows:
        window.close()
        window.deleteLater()
    qapp.processEvents()


def test_disabled_plugin_defers_window_through_events_and_settings(
    load_floating_plugin, qapp,
):
    plugin, host = load_floating_plugin(hide_on_pause=True)
    assert plugin.floating is None
    plugin.on_app_ready(host)
    qapp.processEvents()
    plugin.on_position_changed(1000)
    plugin.on_track_changed("song.mp3")
    plugin.on_playback_state_changed("paused")
    plugin.on_playback_state_changed("playing")
    plugin.toggle_window(False)
    plugin.apply_settings_widget(SimpleNamespace(values=lambda: {"base_font_size": 37}))
    assert plugin.floating is None

    plugin.toggle_window(True)
    qapp.processEvents()
    assert plugin.floating.isVisible()
    assert plugin.floating.lyric_widget.base_font.pointSize() == 37
    assert plugin.floating.lyric_widget.lyric == "Current lyric"
    assert not plugin.floating.lyric_widget.show_ruby
    floating = plugin.floating
    plugin.toggle_window(False)
    plugin.toggle_window(True)
    assert plugin.floating is floating


def test_saved_enabled_plugin_restores_position_and_resumes_after_pause(
    load_floating_plugin, qapp,
):
    plugin, host = load_floating_plugin(enabled=True, window_x=123, window_y=234,
                                       hide_on_pause=True)
    assert plugin.floating is None
    plugin.on_app_ready(host)
    qapp.processEvents()
    assert plugin.floating.isVisible()
    assert plugin.floating.pos() == QPoint(123, 234)
    plugin.on_playback_state_changed("paused")
    assert not plugin.floating.isVisible()
    host.lyric_lines[0].text = "Latest lyric"
    plugin.on_playback_state_changed("playing")
    assert plugin.floating.isVisible()
    assert plugin.floating.lyric_widget.lyric == "Latest lyric"


def test_unloading_before_startup_callback_does_not_create_a_window(
    load_floating_plugin, qapp,
):
    plugin, host = load_floating_plugin(enabled=True)
    plugin.on_app_ready(host)
    plugin.on_unload(None)
    qapp.processEvents()
    assert plugin.floating is None


def test_unloading_destroys_created_floating_window(load_floating_plugin):
    from PySide6.QtCore import QCoreApplication, QEvent
    from shiboken6 import isValid

    plugin, host = load_floating_plugin()
    plugin.window = host
    plugin.toggle_window(True)
    floating = plugin.floating
    plugin.on_unload(None)
    QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
    assert plugin.floating is None
    assert not isValid(floating)


@pytest.mark.parametrize("nested", [False, True])
@pytest.mark.parametrize("show_after_dialog", [False, True])
def test_floating_window_can_drag_while_main_window_has_modal_dialog(
    qapp, nested, show_after_dialog
):
    main = QPushButton("Main player")
    clicks = []
    main.clicked.connect(lambda: clicks.append(True))
    floating = FloatingLyricsWindow(dict(FloatingLyricsPlugin.DEFAULTS))
    dialog = QDialog(main)
    dialog.setWindowModality(Qt.WindowModality.ApplicationModal)
    child = QDialog(dialog) if nested else None
    if child is not None:
        child.setWindowModality(Qt.WindowModality.ApplicationModal)
    try:
        main.show()
        floating.move(100, 100)
        if not show_after_dialog:
            floating.show()
        dialog.show()
        if child is not None:
            child.show()
        if show_after_dialog:
            floating.show()
        qapp.processEvents()

        def drag():
            before = floating.pos()
            handle = floating.windowHandle()
            QTest.mousePress(handle, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
            QTest.mouseMove(handle, QPoint(90, 50))
            assert floating.pos() == before + QPoint(50, 30)
            QTest.mouseRelease(handle, Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
            assert floating._drag_offset is None

        drag()
        QTest.mouseClick(main.windowHandle(), Qt.MouseButton.LeftButton)
        assert clicks == []
        assert dialog.isModal()
        floating.apply_settings({**floating.settings, "always_on_top": False})
        qapp.processEvents()
        drag()
        floating.apply_settings({**floating.settings, "lock_position": True})
        qapp.processEvents()
        before = floating.pos()
        QTest.mousePress(floating.windowHandle(), Qt.MouseButton.LeftButton, pos=QPoint(40, 20))
        QTest.mouseMove(floating.windowHandle(), QPoint(90, 50))
        QTest.mouseRelease(floating.windowHandle(), Qt.MouseButton.LeftButton, pos=QPoint(90, 50))
        assert floating.pos() == before
        floating.apply_settings({**floating.settings, "lock_position": False})
        qapp.processEvents()
        QTest.mouseClick(
            floating.windowHandle(), Qt.MouseButton.LeftButton,
            pos=floating.close_button.geometry().center(),
        )
        assert not floating.isVisible()
        floating.show()
        qapp.processEvents()
        drag()
        if child is not None:
            child.close()
            qapp.processEvents()
            drag()
        dialog.close()
        qapp.processEvents()
        assert floating.isVisible()
        drag()
        assert floating.windowHandle().transientParent() is None
    finally:
        for widget in (child, dialog, floating, main):
            if widget is not None:
                widget.close()
                widget.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("enabled", [True, False])
def test_furigana_visibility_is_forwarded_to_lyric_widget(enabled):
    class LyricWidgetStub:
        def __init__(self):
            self.ruby_visible = None

        def set_ruby_visible(self, visible):
            self.ruby_visible = visible

    window = FloatingLyricsWindow.__new__(FloatingLyricsWindow)
    window.lyric_widget = LyricWidgetStub()

    window.set_furigana_enabled(enabled)

    assert window.lyric_widget.ruby_visible is enabled


def test_saved_disabled_state_prevents_startup_and_resume_showing():
    class APIStub:
        def __init__(self):
            self.saved = {}
            self.menu_checked = None

        def set_setting(self, key, value):
            self.saved[key] = value

        def set_menu_action_checked(self, _text, checked):
            self.menu_checked = checked

    class FloatingStub:
        def __init__(self):
            self.show_calls = 0
            self.hide_calls = 0

        def show(self):
            self.show_calls += 1

        def hide(self):
            self.hide_calls += 1

        def raise_(self):
            pass

    plugin = FloatingLyricsPlugin.__new__(FloatingLyricsPlugin)
    api = APIStub()
    plugin.context = type("ContextStub", (), {"api": api})()
    plugin.settings = {
        "enabled": True,
        "remember_position": False,
        "hide_on_pause": True,
    }
    plugin.floating = FloatingStub()
    plugin.refresh = lambda: None
    plugin.window = object()

    plugin.toggle_window(False)
    plugin._show_after_main_window()
    plugin.on_playback_state_changed("playing")

    assert plugin.settings["enabled"] is False
    assert api.saved["enabled"] is False
    assert api.menu_checked is False
    assert plugin.floating.hide_calls == 1
    assert plugin.floating.show_calls == 0


@pytest.mark.parametrize("translation_lines", [[], [1]])
def test_hidden_multiline_lyrics_defer_reading_and_layout_until_shown(
    qapp, monkeypatch, translation_lines
):
    from lyrics.line_breaks import decode_line_breaks

    reads = []

    def cached_readings(text):
        reads.append(text)
        return [{"text": decode_line_breaks(text)[0], "reading": None}]

    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", cached_readings)
    api = SimpleNamespace(
        set_setting=lambda *args: None,
        set_menu_action_checked=lambda *args: None,
    )
    plugin = FloatingLyricsPlugin()
    plugin.context = SimpleNamespace(api=api)
    plugin.settings = dict(FloatingLyricsPlugin.DEFAULTS, remember_position=False)
    plugin.floating = FloatingLyricsWindow(plugin.settings)
    plugin.floating.visibility_changed = plugin._set_menu_checked
    first = SimpleNamespace(text="First\nFirst translation", translation_lines=translation_lines)
    latest = SimpleNamespace(text="Latest\nLatest translation", translation_lines=translation_lines)
    plugin.window = SimpleNamespace(lyric_lines=[first, latest], current_line=0, show_ruby=True)
    try:
        for position in range(100):
            plugin.on_position_changed(position)
        plugin.on_track_changed("song.mp3")
        assert reads == []
        assert plugin.floating.lyric_widget.lyric == ""
        assert plugin.floating.lyric_widget._prepared_layout == []

        # Opening while paused must display the current lyric and settings
        # without waiting for another positionChanged signal.
        plugin.window.current_line = 1
        plugin.window.show_ruby = False
        plugin.toggle_window(True)
        qapp.processEvents()
        assert plugin.floating.lyric_widget.lyric == latest.text
        assert plugin.floating.lyric_widget.translation_lines == translation_lines
        assert not plugin.floating.lyric_widget.show_ruby
        assert reads

        plugin.floating.hide()
        count = len(reads)
        plugin.window.current_line = 0
        plugin.on_position_changed(200)
        assert len(reads) == count
        plugin.floating.show()
        qapp.processEvents()
        assert plugin.floating.lyric_widget.lyric == first.text
    finally:
        plugin.on_unload(None)
        qapp.processEvents()
@pytest.mark.parametrize("format", ["objects", "tuples", "dicts", "strings"])
def test_context_tracks_current_line_and_clears_at_song_boundaries(qapp, format):
    texts = ["Previous", "Current", "Next"]
    entries = {
        "objects": [SimpleNamespace(text=text) for text in texts],
        "tuples": list(enumerate(texts)),
        "dicts": [{"text": text} for text in texts],
        "strings": texts,
    }[format]
    plugin = FloatingLyricsPlugin()
    plugin.floating = FloatingLyricsWindow(dict(plugin.DEFAULTS))
    plugin.window = SimpleNamespace(current_line=1, show_ruby=False)
    setattr(plugin.window, "lyric_lines" if format == "objects" else "lyrics", entries)
    try:
        plugin.floating.show()
        plugin.refresh()
        qapp.processEvents()
        floating = plugin.floating
        assert floating.previous_label.text() == "Previous"
        assert floating.lyric_widget.lyric == "Current"
        assert floating.next_label.text() == "Next"
        assert floating.previous_label.geometry().bottom() < floating.lyric_widget.y()
        assert floating.lyric_widget.geometry().bottom() < floating.next_label.y()

        plugin.window.current_line = 0
        plugin.refresh()
        assert floating.previous_label.text() == ""
        assert floating.next_label.text() == "Current"
        plugin.window.current_line = 2
        plugin.refresh()
        assert floating.previous_label.text() == "Current"
        assert floating.next_label.text() == ""
        plugin.window.current_line = -1
        plugin.refresh()
        assert floating.previous_label.text() == ""
        assert floating.next_label.text() == "Previous"
        entries.clear()
        plugin.refresh()
        assert floating.previous_label.text() == floating.next_label.text() == ""
    finally:
        plugin.floating.close()
        plugin.floating.deleteLater()
        qapp.processEvents()


def test_long_current_line_pans_both_ways_and_resets_for_resize_and_new_line(qapp):
    floating = FloatingLyricsWindow(dict(FloatingLyricsPlugin.DEFAULTS, window_width=350))
    try:
        floating.set_furigana_enabled(False)
        floating.set_text("A long lyric that needs to scroll from beginning to end " * 3)
        floating.show()
        qapp.processEvents()
        widget = floating.lyric_widget
        assert not widget.wrap_text
        assert widget._pan_active
        assert floating.width() == 350
        prepared = widget._prepare_segments()
        positions = [s["x"] for s in prepared]
        assert len({s["base_y"] for s in prepared}) == 1
        assert min(positions) + widget._pan_offset == pytest.approx(0)
        widget._pan_pause.stop()
        for _ in range(10000):
            widget._advance_pan()
            if widget._pan_direction == -1:
                break
        assert widget._pan_direction == -1
        assert widget._pan_offset == widget._pan_end
        assert max(s["x"] + s["width"] for s in prepared) + widget._pan_offset == pytest.approx(widget.width())
        for _ in range(10000):
            widget._advance_pan()
            if widget._pan_direction == 1:
                break
        assert widget._pan_offset == widget._pan_start
        assert [s["x"] for s in widget._prepare_segments()] == positions

        floating.set_text("Hi")
        qapp.processEvents()
        assert not widget._pan_active
        assert widget._pan_offset == 0
        floating.resize(60, floating.height())
        qapp.processEvents()
        floating.grab()
        assert widget._pan_active
        floating.resize(1000, floating.height())
        qapp.processEvents()
        floating.grab()
        assert not widget._pan_active
        assert not widget._pan_timer.isActive()
        floating.resize(60, floating.height())
        qapp.processEvents()
        floating.grab()
        floating.hide()
        assert not widget._pan_timer.isActive()
        assert not widget._pan_pause.isActive()
    finally:
        floating.close()
        floating.deleteLater()
        qapp.processEvents()
