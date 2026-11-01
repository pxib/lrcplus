import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QDialog, QPushButton

from plugins.community.floating_lyrics.plugin import FloatingLyricsPlugin, FloatingLyricsWindow


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
