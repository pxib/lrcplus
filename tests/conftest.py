"""Keep UI tests headless and all preferences inside pytest's temporary folder."""

import os

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")

import pytest

collect_ignore = ["test_simple.py", "test_repetition.py"]


@pytest.fixture(autouse=True)
def isolated_settings(tmp_path, monkeypatch):
    import core.settings as settings

    destination = tmp_path / "preferences" / "settings.json"
    monkeypatch.setenv("LRCPLUS_SETTINGS_PATH", str(destination))
    monkeypatch.setenv("LRCPLUS_DATA_DIR", str(tmp_path / "app-data"))
    monkeypatch.setattr(settings, "SETTINGS_PATH", destination)
    monkeypatch.setattr(settings, "LEGACY_SETTINGS_PATH", tmp_path / "legacy.json")


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    app = QApplication.instance() or QApplication([])
    yield app


@pytest.fixture
def fake_midi():
    """Exercise game audio controls without playing sound during UI tests."""
    class Midi:
        def __init__(self):
            self.calls = []
            self.current_mode = "stopped"
            self.fail_play = False

        def play(self, restart=False):
            self.calls.append(("play", restart))
            if self.fail_play:
                raise OSError("MIDI output unavailable")
            self.current_mode = "playing"

        def pause(self):
            self.calls.append(("pause",))
            self.current_mode = "paused"

        def mode(self):
            return self.current_mode

        def close(self):
            self.calls.append(("close",))
            self.current_mode = "stopped"

    return Midi()


@pytest.fixture
def fake_sound_effects():
    from types import SimpleNamespace
    from PySide6.QtMultimedia import QSoundEffect

    voices, played = [], []

    class Effect:
        def __init__(self, parent):
            self.playing = False
            self.current_status = QSoundEffect.Status.Ready
            self.stops = 0
            voices.append(self)

        def setLoopCount(self, count):
            self.loops = count

        def setVolume(self, volume):
            self.volume = volume

        def setSource(self, url):
            self.source = url

        def status(self):
            return self.current_status

        def isPlaying(self):
            return self.playing

        def play(self):
            from pathlib import Path
            self.playing = True
            played.append(Path(self.source.toLocalFile()).stem)

        def stop(self):
            self.playing = False
            self.stops += 1

    return SimpleNamespace(factory=Effect, voices=voices, played=played)


@pytest.fixture
def make_lyrics_editor(tmp_path, qapp, monkeypatch):
    from ui.lrc_editor import LrcEditorDialog

    monkeypatch.setattr(LrcEditorDialog, "_schedule_auto_reading_generation", lambda self: None)
    monkeypatch.setattr(LrcEditorDialog, "_refresh_ruby_preview", lambda *args: None)
    monkeypatch.setattr(LrcEditorDialog, "_start_background_reading_generation", lambda *args, **kwargs: None)
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    dialogs = []

    def make(*, karaoke=True, path=None, populate=True):
        from lyrics.models import LyricLine, LyricSegment

        class Editor(LrcEditorDialog):
            def _build_ui(self):
                self.karaoke_enabled = karaoke
                super()._build_ui()

        dialog = Editor(path or tmp_path / f"song-{len(dialogs)}.lrcx")
        if populate:
            dialog.lines = [
                LyricLine(2, "Hello", [LyricSegment(2.2, "Hello", end=3.5)], end=4, has_karaoke=True),
                LyricLine(5, "World", [LyricSegment(5, "World")]),
            ]
            dialog._populate_lines(preserve_line_index=0)
            dialog._reset_history_baseline()
        dialogs.append(dialog)
        return dialog

    yield make
    for dialog in dialogs:
        dialog.document_dirty = False
        dialog.close()
        dialog.deleteLater()
    qapp.processEvents()


@pytest.fixture
def editor(make_lyrics_editor, tmp_path):
    return make_lyrics_editor(path=tmp_path / "song.lrcx")
