import pytest
from PySide6.QtCore import QRectF
from PySide6.QtGui import QColor, QFontMetrics, QImage, QPainter
from PySide6.QtWidgets import QColorDialog, QGroupBox

from core.fonts import library_font
from core.lyrics_settings import LyricShadow, lyric_text_effects
from core.settings import (
    get_effective_lyrics_settings, get_global_lyrics_settings, get_karaoke_settings,
    save_global_lyrics_settings, save_local_lyrics_overrides,
)
from lyrics.furigana_widget import FuriganaWidget
from lyrics.models import LyricLine, LyricSegment
from lyrics.scrolling_widget import ScrollingLyricsWidget
from ui.settings_dialog import SettingsDialog


@pytest.mark.parametrize("all_romaji", [False, True])
@pytest.mark.parametrize("alignment", ["left", "center", "right"])
@pytest.mark.parametrize("quoted", [False, True])
def test_wrapped_lyrics_keep_natural_proportions_and_row_alignment(
        qapp, monkeypatch, all_romaji, alignment, quoted):
    row = FuriganaWidget()
    try:
        row.base_font = library_font(row.base_font)
        row.base_font.setPixelSize(32)
        row.resize(760, 400)
        row.set_ruby_visible(False)
        row.set_all_romaji_visible(all_romaji)
        row.set_alignment(alignment)
        text = "I won't spark it, I'll put it in my pocket and save it like rocket fuel"
        row.set_segments(text, [{"text": text, "start": 1, "end": 4}],
                         is_blockquote=quoted)
        # Exercise switching from a previously prepared unwrapped layout, too.
        row._prepare_segments()
        row.set_text_wrapping(True, -1)
        draws = []
        original = QPainter.drawText

        def draw(painter, x, y, text):
            metrics = QFontMetrics(painter.font())
            transform = painter.worldTransform()
            bounds = QRectF(x, y - metrics.ascent(),
                            metrics.horizontalAdvance(text), metrics.height())
            draws.append((text, transform.mapRect(bounds), transform))
            return original(painter, x, y, text)

        monkeypatch.setattr(QPainter, "drawText", draw)
        image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        row.render(image)

        assert "".join(text for text, _, _ in draws) == row.lyric
        baselines = {bounds.top() for _, bounds, _ in draws}
        assert len(baselines) > 1
        for _, _, transform in draws:
            assert transform.m11() == pytest.approx(1)
            assert transform.m22() == pytest.approx(1)
        for baseline in baselines:
            bounds = [rect for _, rect, _ in draws if rect.top() == baseline]
            left = min(rect.left() for rect in bounds)
            right = max(rect.right() for rect in bounds)
            assert left >= row._quote_indent() - 1
            assert right <= row.width() + 1
            if alignment == "left":
                assert left == pytest.approx(row._quote_indent(), abs=1)
            elif alignment == "right":
                assert right == pytest.approx(row.width(), abs=1)
            else:
                assert (left + right) / 2 == pytest.approx(
                    (row._quote_indent() + row.width()) / 2, abs=1)
    finally:
        row.deleteLater()


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


def test_text_effects_settings_follow_lyrics_scope(qapp, tmp_path):
    path = tmp_path / "song.mp3"
    dialog = SettingsDialog([], get_global_lyrics_settings(), current_file=str(path))
    try:
        dialog.global_controls["lyrics_stroke_width"].setValue(2)
        dialog.global_controls["lyrics_shadow"].setChecked(True)
        dialog.global_controls["lyrics_shadow_opacity"].setValue(40)
        dialog.global_controls["lyrics_shadow_offset_x"].setValue(-3)
        dialog.global_controls["lyrics_shadow_blur"].setValue(4)
        save_global_lyrics_settings(dialog.get_global_lyrics_settings())
        override, width = dialog.local_controls["lyrics_stroke_width"]
        override.setChecked(True)
        width.setValue(0)
        override, offset = dialog.local_controls["lyrics_shadow_offset_x"]
        override.setChecked(True)
        offset.setValue(6)
        save_local_lyrics_overrides(path, dialog.get_local_lyrics_overrides())
        assert get_effective_lyrics_settings(path)["lyrics_stroke_width"] == 0
        _, _, enabled, style, _ = lyric_text_effects(get_effective_lyrics_settings(path))
        assert enabled is True
        assert style == LyricShadow(opacity=40, offset_x=6, blur=4)
        assert get_global_lyrics_settings()["lyrics_shadow_offset_x"] == -3
        assert get_global_lyrics_settings()["lyrics_stroke_width"] == 2
    finally:
        dialog.deleteLater()


def test_karaoke_stroke_color_can_be_picked_and_reset_per_file(qapp, tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    dialog = SettingsDialog([], get_global_lyrics_settings(), current_file=str(path))
    try:
        key = "karaoke_stroke_color"
        global_color = dialog.global_controls[key]
        assert global_color.text() == "Use text stroke color"
        monkeypatch.setattr(QColorDialog, "getColor", lambda *_: QColor("#389fee"))
        global_color.menu().actions()[0].trigger()
        save_global_lyrics_settings(dialog.get_global_lyrics_settings())
        assert get_global_lyrics_settings()[key] == "#389fee"
        assert get_effective_lyrics_settings(path)[key] == "#389fee"

        override, local_color = dialog.local_controls[key]
        override.setChecked(True)
        local_color.menu().actions()[0].trigger()
        local_color.menu().actions()[1].trigger()
        save_local_lyrics_overrides(path, dialog.get_local_lyrics_overrides())
        assert get_effective_lyrics_settings(path)[key] == ""
    finally:
        dialog.deleteLater()


def test_scrolling_lyrics_paint_text_effects_on_existing_and_new_rows(qapp):
    view = ScrollingLyricsWidget()

    def render(row):
        row.resize(210, 80)
        image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        row.render(image)
        return image

    try:
        view.set_focus_transition(False)
        view.set_highlight_current(False)
        view.set_lyrics([(0, "Lyrics")])
        row = view.rows[0]
        plain = render(row)

        view.set_text_effects(0, "#ff0000", True)
        shadow = render(row)
        assert shadow != plain

        view.set_text_effects(2, "#ff0000", True)
        decorated = render(row)
        pixels = {decorated.pixel(x, y) for x in range(decorated.width()) for y in range(decorated.height())}
        assert QColor("#ff0000").rgba() in pixels
        assert QColor("#ffffff").rgba() in pixels
        assert decorated != shadow

        view.set_lyrics([(0, "Lyrics"), (3, "Another line")])
        assert view.rows[1].lyrics_shadow
        assert view.rows[1].lyrics_stroke_pen.color() == QColor("#ff0000")
    finally:
        view.deleteLater()


def test_custom_shadow_blurs_and_keeps_its_color(qapp):
    from PySide6.QtCore import Qt
    from PySide6.QtWidgets import QWidget

    row = FuriganaWidget()
    row.resize(280, 120)
    row.set_focus_transition(False)
    row.set_lyric("Custom shadow", generate=False)

    def red_shadow_pixels(blur, opacity):
        row.set_text_effects(0, "#000000", True, LyricShadow("#ff0000", opacity, -8, 10, blur))
        image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(Qt.GlobalColor.transparent)
        row.render(image, renderFlags=QWidget.RenderFlag.DrawChildren)
        return {
            (x, y): pixel.alpha()
            for y in range(image.height()) for x in range(image.width())
            if (pixel := image.pixelColor(x, y)).red() > pixel.green()
        }

    try:
        sharp = red_shadow_pixels(0, 100)
        blurred = red_shadow_pixels(3, 100)
        faded = red_shadow_pixels(3, 30)
        assert sharp and blurred
        assert set(blurred) - set(sharp)
        assert max(faded.values()) < max(blurred.values())
        assert red_shadow_pixels(3, 0) == {}
    finally:
        row.deleteLater()


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
