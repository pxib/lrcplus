from types import SimpleNamespace

import pytest
from PySide6.QtCore import QPoint, Qt
from PySide6.QtGui import QColor, QImage, QPainter
from PySide6.QtWidgets import QTabWidget

from core.settings import (
    KARAOKE_DEFAULTS, KARAOKE_SWEEP_STYLES, apply_karaoke_settings,
    get_karaoke_settings, save_karaoke_settings,
)
from lyrics.furigana_widget import FuriganaWidget
from lyrics.karaoke_effects import sweep_effect_pen, text_color_pen
from plugins.core.karaoke_screen.plugin import KaraokeScreenPlugin
from ui.settings_dialog import SettingsDialog


def test_karaoke_category_round_trips_all_properties_and_cancel(qapp):
    values = dict(KARAOKE_DEFAULTS, sweep_style="glow", sweep_easing="ease_out",
                  softness=21, glow_intensity=130, glow_radius=4, shimmer_width=22,
                  sweep_delay_ms=210, sweep_delay_percent=25, sweep_fps=120)
    save_karaoke_settings(values)
    dialog = SettingsDialog([], {}, karaoke_settings=get_karaoke_settings())
    try:
        tabs = dialog.findChild(QTabWidget)
        karaoke = tabs.widget(next(i for i in range(tabs.count()) if tabs.tabText(i) == "Karaoke"))
        plugins = tabs.widget(next(i for i in range(tabs.count()) if tabs.tabText(i) == "Plugins"))
        assert karaoke.isAncestorOf(dialog.karaoke_sweep_fps)
        assert not plugins.isAncestorOf(dialog.karaoke_sweep_fps)
        assert dialog.get_karaoke_settings() == values
        controls = dialog.karaoke_controls
        for style in KARAOKE_SWEEP_STYLES:
            controls["sweep_style"].setCurrentIndex(controls["sweep_style"].findData(style))
            assert controls["glow_radius"].isHidden() == (style != "glow")
            assert controls["softness"].isHidden() == (style != "soft")
            assert controls["shimmer_width"].isHidden() == (style != "shimmer")
            saved = dialog.get_karaoke_settings()
            save_karaoke_settings(saved)
            assert get_karaoke_settings() == saved
        controls["sweep_style"].setCurrentIndex(controls["sweep_style"].findData("rainbow"))
        controls["glow_radius"].setValue(8)
        before_cancel = get_karaoke_settings()
        dialog.reject()
        assert get_karaoke_settings() == before_cancel
    finally:
        dialog.deleteLater()


@pytest.mark.parametrize("style", ["rainbow", "pulse", "spotlight"])
@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
@pytest.mark.parametrize("romaji", [False, True])
def test_new_effects_render_text_and_readings_and_seek_cleanly(qapp, style, direction, romaji):
    row = FuriganaWidget()
    try:
        row.resize(900, 300)
        row.set_direction(direction)
        row.set_romaji_visible(romaji)
        row.set_colors("#777777", "#35a47e")
        row.set_segments("你好", [
            {"text": "你好", "reading": "nǐ hǎo", "start": 0, "end": 4,
             "timing_group": 0},
        ])
        row.karaoke_line_end = 4

        def render(effect, position):
            apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_style=effect,
                                            sweep_delay_ms=0, sweep_delay_percent=0))
            row.set_karaoke_position(position)
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            row.render(image)
            return image

        start = render(style, -1)
        assert start == render("classic", -1)
        active = render(style, 1.5)
        assert active != start
        assert active != render("classic", 1.5)
        render(style, 5)
        assert render(style, 1.5) == active
        assert render(style, -1) == start
    finally:
        row.deleteLater()


def test_fullscreen_receives_all_properties_and_fps_without_repainting_unchanged(qapp, monkeypatch):
    row = FuriganaWidget()
    try:
        save_karaoke_settings(dict(KARAOKE_DEFAULTS, sweep_style="soft", softness=30,
                                  sweep_easing="ease_out", sweep_fps=30))
        intervals = []
        plugin = KaraokeScreenPlugin()
        plugin.window = None
        plugin.screen = SimpleNamespace(current_lyric=row, next_lyric=None)
        plugin.screen_timer = SimpleNamespace(setInterval=intervals.append)
        plugin.on_karaoke_settings_changed()
        assert row.karaoke_sweep_style == "soft"
        assert row.karaoke_softness == 30
        assert row.karaoke_sweep_easing == "ease_out"
        assert intervals == [33]
        updates = []
        monkeypatch.setattr(row, "update", lambda: updates.append(True))
        plugin._apply_sweep_style()
        assert updates == []
    finally:
        row.deleteLater()


def test_editor_preview_uses_saved_properties_without_properties_button(make_lyrics_editor):
    save_karaoke_settings(dict(KARAOKE_DEFAULTS, sweep_style="pulse", sweep_easing="ease_in"))
    editor = make_lyrics_editor()
    assert not hasattr(editor, "karaoke_sweep_style_button")
    assert editor.karaoke_renderer_preview.karaoke_sweep_style == "pulse"
    assert editor.karaoke_renderer_preview.karaoke_sweep_easing == "ease_in"


@pytest.mark.parametrize("style", KARAOKE_SWEEP_STYLES)
@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
@pytest.mark.parametrize("readings", ["none", "ruby", "romaji"])
def test_effects_paint_each_glyph_once_without_increasing_coverage(
        qapp, monkeypatch, style, direction, readings):
    row = FuriganaWidget()
    try:
        row.resize(600, 240)
        row.setAttribute(Qt.WidgetAttribute.WA_TranslucentBackground)
        row.display_opacity = 0.6
        row.set_direction(direction)
        row.set_ruby_visible(readings == "ruby")
        row.set_romaji_visible(readings == "romaji")
        row.set_segments("歌", [{"text": "歌", "reading": "うた",
                                "start": 0, "end": 4, "timing_group": 0}])
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_style=style,
                                        sweep_delay_ms=0, sweep_delay_percent=0,
                                        glow_radius=8, glow_intensity=255,
                                        word_lift=False, line_entrance=False))
        calls = []
        draw_text = QPainter.drawText

        def draw(painter, *args):
            calls.append(args)
            return draw_text(painter, *args)

        monkeypatch.setattr(QPainter, "drawText", draw)

        def render(position):
            row.set_karaoke_position(position)
            calls.clear()
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            row.render(image)
            return bytes(image.constBits())[3::4], list(calls)

        coverage, original_glyphs = render(-1)
        assert any(coverage)
        assert original_glyphs
        for position in (0.001, 0.5, 1.5, 3.9999999, 5):
            active_coverage, active_glyphs = render(position)
            assert active_glyphs == original_glyphs
            assert active_coverage == coverage
    finally:
        row.deleteLater()


@pytest.mark.parametrize("style", ["soft", "glow", "shimmer", "spotlight"])
@pytest.mark.parametrize("vertical", [False, True])
def test_effect_width_depends_on_font_size_instead_of_segment_width(style, vertical):
    def render(extent, font_height):
        pen = sweep_effect_pen(
            style, QColor("#ffffff"), QColor("#ffaa00"),
            100 - extent / 2, extent, 0.5, font_height=font_height,
            vertical=vertical,
        )
        image = QImage(200, 200, QImage.Format.Format_ARGB32_Premultiplied)
        image.fill(0)
        painter = QPainter(image)
        painter.fillRect(image.rect(), pen.brush())
        painter.end()
        return image

    reference = render(50, 48)
    for extent in (1, 12, 100, 500):
        assert render(extent, 48) == reference
    assert render(50, 24) != reference
    assert render(50, 96) != reference


@pytest.mark.parametrize("style", KARAOKE_SWEEP_STYLES)
def test_duration_changes_speed_without_changing_effect_at_same_progress(qapp, style):
    row = FuriganaWidget()
    try:
        row.resize(600, 180)
        row.set_ruby_visible(False)
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_style=style,
                                        sweep_delay_ms=0, sweep_delay_percent=0,
                                        word_lift=False, line_entrance=False))
        frames = []
        for duration in (0.25, 1, 8):
            row.set_segments("Consistent softness", [{"text": "Consistent softness",
                             "start": 0, "end": duration, "timing_group": 0}])
            row.set_karaoke_position(duration / 2)
            image = QImage(row.size(), QImage.Format.Format_ARGB32_Premultiplied)
            image.fill(0)
            row.render(image)
            frames.append(image)
        assert frames[0] == frames[1] == frames[2]
    finally:
        row.deleteLater()


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
@pytest.mark.parametrize("romaji", [False, True])
@pytest.mark.parametrize("dpr", [1, 2])
def test_plain_and_karaoke_glyph_smoothing_match_on_opaque_background(
        qapp, direction, romaji, dpr):
    row = FuriganaWidget()
    try:
        row.resize(600, 240)
        row.set_direction(direction)
        row.set_romaji_visible(romaji)
        row.set_colors("#e8ad49", "#e8ad49")
        apply_karaoke_settings(row, dict(KARAOKE_DEFAULTS, sweep_style="soft",
                                        sweep_delay_ms=0, sweep_delay_percent=0,
                                        word_lift=False, line_entrance=False))
        row.set_segments("歌", [{"text": "歌", "reading": "うた",
                                "start": 0, "end": 4, "timing_group": 0}])

        def render(position):
            row.set_karaoke_position(position)
            image = QImage(row.width() * dpr, row.height() * dpr,
                           QImage.Format.Format_RGB32)
            image.setDevicePixelRatio(dpr)
            image.fill(QColor("#263a38"))
            painter = QPainter(image)
            row.render(painter, QPoint())
            painter.end()
            return image

        plain = render(None)
        for position in (-1, 0.5, 1.5, 5):
            assert render(position) == plain
    finally:
        row.deleteLater()


def test_plain_and_gradient_brushes_blend_glyph_edges_identically(qapp):
    color = QColor("#e8ad49")

    def render(pen):
        image = QImage(400, 100, QImage.Format.Format_RGB32)
        image.fill(QColor("#263a38"))
        painter = QPainter(image)
        painter.setRenderHint(QPainter.RenderHint.TextAntialiasing)
        painter.setPen(pen)
        painter.drawText(10, 60, "Consistent softness")
        painter.end()
        return image

    plain = render(text_color_pen(color))
    for progress in (0, 0.1, 0.5, 0.9, 1):
        assert render(sweep_effect_pen("soft", color, color, 0, 350, progress,
                                       font_height=48)) == plain
