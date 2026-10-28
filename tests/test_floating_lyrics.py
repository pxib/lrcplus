import pytest

from plugins.community.floating_lyrics.plugin import FloatingLyricsPlugin, FloatingLyricsWindow


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