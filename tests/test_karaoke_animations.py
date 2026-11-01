import pytest
from PySide6.QtCore import QAbstractAnimation, Qt
from PySide6.QtGui import QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QCheckBox, QGraphicsBlurEffect, QGraphicsDropShadowEffect, QVBoxLayout, QWidget

from core.settings import KARAOKE_DEFAULTS, apply_karaoke_settings, get_karaoke_settings, get_global_lyrics_settings, save_global_lyrics_settings
from lyrics.furigana_widget import FuriganaWidget
from lyrics.karaoke_effects import word_lift_amount
from ui.settings_dialog import SettingsDialog


def test_animation_toggles_round_trip_independently(qapp):
    assert get_karaoke_settings()["line_entrance"] is True
    assert get_karaoke_settings()["word_lift"] is True
    dialog = SettingsDialog([], {}, karaoke_settings=dict(KARAOKE_DEFAULTS))
    try:
        entrance = dialog.karaoke_controls["line_entrance"]
        lift = dialog.karaoke_controls["word_lift"]
        focus = dialog.global_controls["focus_transition"]
        assert focus.isChecked()
        assert isinstance(entrance, QCheckBox)
        assert entrance.isChecked() and lift.isChecked()
        entrance.setChecked(False)
        assert dialog.get_karaoke_settings()["line_entrance"] is False
        assert dialog.get_karaoke_settings()["word_lift"] is True
        lift.setChecked(False)
        assert dialog.get_karaoke_settings()["word_lift"] is False
        assert dialog.get_global_lyrics_settings()["focus_transition"] is True
        focus.setChecked(False)
        save_global_lyrics_settings(dialog.get_global_lyrics_settings())
        assert get_global_lyrics_settings()["focus_transition"] is False
    finally:
        dialog.deleteLater()


@pytest.fixture
def motion_row(qapp):
    row = FuriganaWidget()
    row.resize(800, 180)
    row.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
    row.set_segments("歌", [{"text": "歌", "reading": "うた", "start": 1,
                            "end": 3, "timing_group": 0}])
    apply_karaoke_settings(row, KARAOKE_DEFAULTS)
    yield row
    row.close()
    row.deleteLater()
    qapp.processEvents()


def test_line_entrance_fades_and_settles_without_layout_changes(motion_row, qapp):
    row = motion_row
    row.show()
    qapp.processEvents()
    row.set_current_line(True)
    animation = row._line_entrance_animation
    assert animation.state() == QAbstractAnimation.State.Running
    assert row.line_entrance_progress == 0
    layout = row._prepare_segments()

    def frame():
        image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        row.render(image)
        return image

    first = frame()
    animation.setCurrentTime(130)
    assert 0 < row.line_entrance_progress < 1
    assert frame() != first
    animation.setCurrentTime(260)
    assert row.line_entrance_progress == 1
    assert animation.state() == QAbstractAnimation.State.Stopped
    assert row._prepare_segments() == layout
    assert frame() != first


def test_line_entrance_stops_when_hidden_and_when_disabled(motion_row, qapp):
    row = motion_row
    row.show()
    row.set_current_line(True)
    row.hide()
    assert row._line_entrance_animation.state() == QAbstractAnimation.State.Stopped
    assert row.line_entrance_progress == 1
    row.show()
    apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, line_entrance=False))
    row.start_line_entrance()
    assert row._line_entrance_animation.state() == QAbstractAnimation.State.Stopped
    assert row.line_entrance_progress == 1


def test_word_lift_speed_is_fixed_and_pauses_do_not_advance_it():
    for duration in (1, 10):
        assert word_lift_amount(0.06, 0, duration) == pytest.approx(0.75)
        assert word_lift_amount(duration - 0.08, 0, duration) == pytest.approx(0.75)
        assert word_lift_amount(duration / 2, 0, duration) == 1
    assert word_lift_amount(0.02, 0, 0.04) == 1
    assert word_lift_amount(0, 0, 0.04) == 0
    assert word_lift_amount(0.04, 0, 0.04) == 0
    assert word_lift_amount(2, 2, 2) == 0


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_word_and_readings_move_together_without_duplicate_drawing(
        motion_row, monkeypatch, direction):
    row = motion_row
    row.set_direction(direction)
    draws = []
    original = QPainter.drawText

    def draw(painter, *args):
        draws.append((args, painter.worldTransform().dy()))
        return original(painter, *args)

    monkeypatch.setattr(QPainter, "drawText", draw)

    def render(position):
        row.set_karaoke_position(position)
        draws.clear()
        image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        row.render(image)
        return list(draws)

    resting = render(0)
    layout = row._prepare_segments()
    lifted = render(2)
    assert len(resting) == len(lifted) >= 2
    amount = -QFontMetrics(row.base_font).height() * 0.1
    for (rest_args, rest_y), (lift_args, lift_y) in zip(resting, lifted):
        assert rest_args == lift_args
        assert lift_y - rest_y == pytest.approx(amount)
    assert row._prepare_segments() == layout
    assert render(3) == resting
    assert render(0) == resting
    apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, word_lift=False))
    assert all(y == 0 for _, y in render(2))


def test_word_lift_honors_gaps_and_implicit_ends(motion_row):
    row = motion_row
    piece = {"start": 1, "end": 2, "_next_timed_start": 4}
    row.set_karaoke_position(3)
    assert row._word_lift_offset(piece) == 0
    row.set_karaoke_position(1.5)
    assert row._word_lift_offset(piece) < 0
    assert row._word_lift_offset(dict(piece, instant=True)) == 0
    row.set_karaoke_position(3)
    assert row._word_lift_offset(dict(piece, end=None)) < 0


def test_scrolling_rows_restore_both_animations_after_remount(qapp, monkeypatch):
    from lyrics.models import LyricLine, LyricSegment
    from lyrics.scrolling_widget import ScrollingLyricsWidget
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    widget = ScrollingLyricsWidget()
    try:
        widget.resize(800, 300)
        widget.show()
        qapp.processEvents()
        widget.set_focus_transition(True)
        apply_karaoke_settings(widget, KARAOKE_DEFAULTS)
        widget.set_lyrics([LyricLine(i * 3, str(i),
            [LyricSegment(i * 3, str(i), end=i * 3 + 2)], end=i * 3 + 3,
            has_karaoke=True) for i in range(500)])
        qapp.processEvents()
        widget.set_current_line(400, animate=False)
        widget.set_karaoke_position(1201)
        row = widget._mounted_rows[400]
        assert row.karaoke_line_entrance and row.karaoke_word_lift
        assert row.focus_transition
        assert row._word_lift_offset(row._prepare_segments()[0]) < 0
        widget.set_current_line(0, animate=False)
        assert 400 not in widget._mounted_rows
        widget.set_current_line(400, animate=False)
        widget.set_karaoke_position(1201)
        assert widget._mounted_rows[400].karaoke_word_lift
        assert widget._mounted_rows[400].karaoke_line_entrance
        assert widget._mounted_rows[400].focus_transition
    finally:
        widget.close()
        widget.deleteLater()


def test_focus_transition_blurs_and_dims_then_becomes_fully_sharp(qapp, monkeypatch):
    parent = QWidget()
    row = FuriganaWidget()
    row.set_segments("Focus transition", [{"text": "Focus transition", "reading": "readings"}])
    QVBoxLayout(parent).addWidget(row)
    parent.resize(700, 180)
    row.set_focus_transition(True)
    apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, line_entrance=False, word_lift=False))
    parent.show()
    qapp.processEvents()
    try:
        assert isinstance(row.graphicsEffect(), QGraphicsBlurEffect)
        effect = row.graphicsEffect()
        assert effect.isEnabled() and effect.blurRadius() > 0
        assert row.focus_progress == 0
        prepared = row._prepare_segments()
        draws = []
        original = QPainter.drawText

        def draw(painter, *args):
            draws.append(painter.opacity())
            return original(painter, *args)

        monkeypatch.setattr(QPainter, "drawText", draw)

        def frame():
            image = QImage(parent.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            draws.clear()
            row.update()
            parent.render(image)
            return image, list(draws)

        blurred, dim_opacities = frame()
        row.set_current_line(True)
        assert row._focus_animation.state() == QAbstractAnimation.State.Running
        row._focus_animation.setCurrentTime(120)
        assert 0 < row.focus_progress < 1
        partial, _ = frame()
        row._focus_animation.setCurrentTime(240)
        assert row.focus_progress == 1
        assert not effect.isEnabled()
        focused, bright_opacities = frame()
        assert blurred != partial != focused
        assert dim_opacities and bright_opacities
        assert max(dim_opacities) < max(bright_opacities)
        assert row._prepare_segments() == prepared
        row.set_current_line(False)
        row._focus_animation.setCurrentTime(240)
        assert row.focus_progress == 0 and effect.isEnabled()
        row.hide()
        assert row._focus_animation.state() == QAbstractAnimation.State.Stopped
        row.set_focus_transition(False)
        assert row.focus_progress == 1 and not effect.isEnabled()
    finally:
        parent.close()
        parent.deleteLater()
        qapp.processEvents()


def test_fullscreen_focus_preserves_outline_and_tracks_both_lyric_rows(qapp):
    from types import SimpleNamespace
    from lyrics.models import LyricLine, LyricSegment
    from plugins.core.karaoke_screen.plugin import KaraokeScreenPlugin
    from plugins.core.karaoke_screen.screen import KaraokeScreen
    window = QWidget()
    screen = KaraokeScreen(window)
    try:
        window.current_line = 0
        window.lyric_lines = [LyricLine(i * 10, f"Line {i}",
            [LyricSegment(i * 10, f"Line {i}", end=i * 10 + 9)],
            end=i * 10 + 10, has_karaoke=True) for i in range(2)]
        plugin = KaraokeScreenPlugin()
        plugin.window = window
        plugin.screen = screen
        plugin._screen_line_index = None
        plugin._screen_next_line_index = None
        plugin._screen_next_text = None
        plugin.context = SimpleNamespace(api=SimpleNamespace(
            get_current_lyrics=lambda: [{"text": line.text, "start_ms": int(line.start * 1000)}
                                       for line in window.lyric_lines],
            get_position=lambda: window.current_line * 10000,
            get_duration=lambda: 20000,
            get_current_track=lambda: "song.mp3",
        ))
        screen.show()
        qapp.processEvents()
        plugin.refresh_karaoke_screen()
        assert screen.current_lyric.is_current_line
        assert not screen.next_lyric.is_current_line
        for row in (screen.current_lyric, screen.next_lyric):
            assert isinstance(row.graphicsEffect(), QGraphicsBlurEffect)
            assert isinstance(row.parentWidget().graphicsEffect(), QGraphicsDropShadowEffect)
        window.current_line = 1
        plugin.refresh_karaoke_screen()
        assert not screen.current_lyric.is_current_line
        assert screen.next_lyric.is_current_line
        screen.next_lyric._focus_animation.setCurrentTime(240)
        assert not screen.next_lyric.graphicsEffect().isEnabled()
    finally:
        screen.close()
        window.deleteLater()
        qapp.processEvents()
