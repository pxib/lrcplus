"""Register the Rhythm Games actions before loading the gameplay code."""

from pathlib import Path

from PySide6.QtGui import QKeySequence, QShortcut


class RhythmGamesPlugin:
    def on_load(self, context):
        self.api = context.api
        self.window = self.dialog = self.secret = None
        context.on("app_ready", self.on_app_ready)
        self.api.add_menu_action("Plugins", "Rhythm Games…", self.open_game)

    def on_app_ready(self, window):
        self.window = window
        self.secret = QShortcut(QKeySequence("Ctrl+Shift+G"), window)
        self.secret.setAutoRepeat(False)
        self.secret.activated.connect(self._open_from_lyrics)

    def _open_from_lyrics(self):
        if self.window.workspace_stack.currentWidget() is self.window.lyrics_page:
            self.open_game()

    def open_game(self):
        if self.dialog is not None or self.window is None:
            return
        from plugins.community.rhythm_games.game import RhythmDialog, lyric_passages

        path = self.api.get_current_track()
        passages = ()
        has_audio = bool(path and Path(path).is_file())
        duration_ms = self.api.get_duration() if has_audio else 0
        if has_audio and not getattr(self.window, "plain_lyrics_mode", False):
            passages = tuple(lyric_passages(
                getattr(self.window, "lyric_lines", ()),
                getattr(self.window, "lyrics_offset_seconds", 0) * 1000,
                duration_ms,
            ))
        self.dialog = RhythmDialog(self.window, self.api, passages,
                                   audio_only=has_audio and not passages, duration_ms=duration_ms)
        self.dialog.finished.connect(self._close_game)
        if has_audio:
            self.dialog.analyze_track()
        self.dialog.open()

    def _close_game(self, _result):
        dialog, self.dialog = self.dialog, None
        dialog.deleteLater()

    def on_unload(self, context):
        if self.dialog is not None:
            self.dialog.reject()
        if self.secret is not None:
            self.secret.setEnabled(False)
            self.secret.deleteLater()
        self.window = None


def __getattr__(name):
    if name.startswith("__"):
        raise AttributeError(name)
    from plugins.community.rhythm_games import game

    return getattr(game, name)


PLUGIN_CLASS = RhythmGamesPlugin
