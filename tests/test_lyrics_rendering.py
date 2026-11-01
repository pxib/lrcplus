import pytest
from PySide6.QtCore import QRectF
from PySide6.QtWidgets import QGroupBox

from core.settings import (
    get_effective_lyrics_settings, get_global_lyrics_settings, get_karaoke_settings,
    save_global_lyrics_settings, save_local_lyrics_overrides,
)
from lyrics.furigana_widget import FuriganaWidget
from lyrics.models import LyricLine, LyricSegment
from lyrics.scrolling_widget import ScrollingLyricsWidget
from ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("old_choice", [False, True])
def test_focus_migrates_from_karaoke_and_new_lyrics_choice_takes_priority(old_choice):
    payload = {"karaoke": {"focus_transition": old_choice}}
    assert get_global_lyrics_settings(payload)["focus_transition"] is old_choice
    payload["lyrics_global"] = {"focus_transition": not old_choice}
    assert get_global_lyrics_settings(payload)["focus_transition"] is not old_choice
    assert "focus_transition" not in get_karaoke_settings(payload)


def test_rendering_category_supports_global_and_file_specific_choices(qapp, tmp_path):
    path = tmp_path / "song.mp3"
    dialog = SettingsDialog([], get_global_lyrics_settings(), current_file=str(path))
    try:
        checkbox = dialog.global_controls["focus_transition"]
        assert checkbox.parentWidget().title() == "Rendering"
        assert isinstance(checkbox.parentWidget(), QGroupBox)
        assert "focus_transition" not in dialog.karaoke_controls
        checkbox.setChecked(False)
        save_global_lyrics_settings(dialog.get_global_lyrics_settings())
        assert get_effective_lyrics_settings(path)["focus_transition"] is False
        override, editor = dialog.local_controls["focus_transition"]
        override.setChecked(True)
        editor.setChecked(True)
        save_local_lyrics_overrides(path, dialog.get_local_lyrics_overrides())
        assert get_effective_lyrics_settings(path)["focus_transition"] is True
        assert get_effective_lyrics_settings(tmp_path / "another.mp3")["focus_transition"] is False
    finally:
        dialog.deleteLater()


def test_static_karaoke_rows_keep_clocks_synced_without_repaints_and_seek_correctly(qapp, monkeypatch):
    row = FuriganaWidget()
    row.resize(600, 180)
    try:
        row.set_segments("Timing", [{"text": "Timing", "start": 1, "end": 3}])
        row._prepare_segments()
        updates = []
        monkeypatch.setattr(row, "update", lambda: updates.append(row.karaoke_position))
        for position in (0, 0.5, 0.9, 1.5, 3, 4, 5, 2, -1, -2):
            row.set_karaoke_position(position)
            assert row.karaoke_position == position
        assert updates == [0, 1.5, 3, 2, -1]
        row.set_karaoke_position(None)
        assert updates[-1] is None
        row.segments[0]["instant"] = True
        row._prepare_segments()
        row.set_karaoke_position(0)
        updates.clear()
        row.set_karaoke_position(1)
        assert updates == [1]
        row.set_karaoke_position(4)
        row._prepare_segments()
        updates.clear()
        row.segments[0]["end"] = 6
        row.set_karaoke_position(4.5)
        assert updates == [4.5]
    finally:
        row.deleteLater()


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_focus_clock_ticks_repaint_only_active_row(qapp, monkeypatch, direction):
    paints = []
    original = FuriganaWidget.paintEvent

    def paint(row, event):
        paints.append(row._lyric_index)
        return original(row, event)

    monkeypatch.setattr(FuriganaWidget, "paintEvent", paint)
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": "reading"}])
    monkeypatch.setattr("lyrics.scrolling_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": "reading"}])
    view = ScrollingLyricsWidget()
    view.resize(1000, 650)
    view.set_direction(direction)
    view.set_focus_transition(True)
    view.show()
    qapp.processEvents()
    view.set_lyrics([LyricLine(i * 4, f"Lyric line {i}",
                    [LyricSegment(i * 4, f"Lyric line {i}", end=i * 4 + 3.8)],
                    end=i * 4 + 4, has_karaoke=True) for i in range(50)])
    qapp.processEvents()
    view.set_current_line(5, animate=False)
    qapp.processEvents()
    try:
        for row in view.rows:
            row._focus_animation.setCurrentTime(240)
        for position in (20.05, 20.1, 20.15):
            view.set_karaoke_position(position)
            qapp.processEvents()
        paints.clear()
        for position in (20.2, 20.3, 20.4):
            view.set_karaoke_position(position)
            qapp.processEvents()
        assert paints == [5, 5, 5]
        for row in view.rows:
            assert row.karaoke_position == 20.4
            assert row._focus_effect.boundingRectFor(QRectF(row.rect())) == QRectF(row.rect())
        view.set_current_line(0, animate=False)
        view.set_karaoke_position(0.5)
        qapp.processEvents()
        assert view._mounted_rows[0].karaoke_position == 0.5
    finally:
        view.close()
        view.deleteLater()
        qapp.processEvents()


def test_plain_lyrics_focus_works_without_karaoke(qapp):
    view = ScrollingLyricsWidget()
    try:
        view.resize(800, 400)
        view.set_focus_transition(True)
        view.show()
        view.set_lyrics([(0, "Plain lyrics"), (2, "Another line")])
        view.set_current_line(0, animate=False)
        qapp.processEvents()
        row = view._mounted_rows[0]
        row._focus_animation.setCurrentTime(240)
        assert row.focus_transition and row.focus_progress == 1
        assert not row.graphicsEffect().isEnabled()
        assert view._mounted_rows[1].graphicsEffect().isEnabled()
        view.set_focus_transition(False)
        assert all(not row.graphicsEffect().isEnabled() for row in view.rows)
    finally:
        view.close()
        view.deleteLater()
        qapp.processEvents()
