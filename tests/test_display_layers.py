from itertools import permutations

import pytest
from PySide6.QtCore import Qt

from core.lyrics_settings import LYRIC_LAYER_ORDER, normalize_lyrics_settings
from lyrics.furigana_widget import FuriganaWidget
from ui.settings_dialog import SettingsDialog


def lyric_widget():
    widget = FuriganaWidget()
    widget.resize(700, 700)
    widget.set_lyric("歌\nSong", generate=False, translation_lines=[1])
    widget.segments = [
        {"text": "歌", "reading": "うた", "start": 1, "end": 3, "timing_group": 0},
        {"text": "\nSong", "reading": None, "start": 1, "instant": True},
    ]
    return widget


@pytest.mark.parametrize("floating", [False, True])
@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
@pytest.mark.parametrize("text", ["Hello world", "We're alive!", "Café déjà vu"])
def test_latin_lyrics_have_no_romaji_layer(qapp, floating, direction, text):
    if floating:
        from plugins.community.floating_lyrics.plugin import SplitRenderFuriganaWidget
        widget = SplitRenderFuriganaWidget()
    else:
        widget = FuriganaWidget()
    try:
        widget.resize(700, 500)
        widget.set_direction(direction)
        widget.set_lyric(text, generate=False)
        widget.segments = [{"text": text, "reading": "ハロー", "start": 1, "end": 3}]
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, False, False)
        pieces = widget._prepare_segments()
        assert {p["display_layer"] for p in pieces} == {"lyrics"}
        assert "".join(p["text"] for p in pieces) == text
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, False, True, False, False)
        assert widget._prepare_segments() == []

        # Suppression applies to this lyric, without changing the user's toggle.
        widget.set_lyric("歌", generate=False)
        widget.segments = [{"text": "歌", "reading": "うた"}]
        assert "".join(p["text"] for p in widget._prepare_segments()) == "uta"
    finally:
        widget.close()
        widget.deleteLater()


def test_english_row_is_not_duplicated_in_mixed_lyrics(qapp):
    widget = lyric_widget()
    try:
        widget.set_lyric("Hello world\n歌\nSong", generate=False, translation_lines=[2])
        widget.segments = [
            {"text": "Hello ", "reading": None, "start": 1, "end": 2},
            {"text": "world", "reading": None, "start": 2, "end": 3},
            {"text": "\n", "reading": None},
            {"text": "歌", "reading": "うた", "start": 3, "end": 4},
            {"text": "\nSong", "reading": None, "instant": True},
        ]
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, True, False)
        pieces = widget._prepare_segments()
        assert "".join(p["text"] for p in pieces if p["display_layer"] == "romaji") == "uta"
        assert "".join(p["text"] for p in pieces if p["display_layer"] == "translation") == "Song"
        english = [p for p in pieces if p["text"] in {"Hello ", "world"}]
        assert [(p["start"], p["end"]) for p in english] == [(1, 2), (2, 3)]
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("order", list(permutations(LYRIC_LAYER_ORDER)))
def test_order_controls_text_and_baselines_without_changing_timing(qapp, order):
    widget = lyric_widget()
    try:
        widget.set_lyric_layers(order, True, True, True, True)
        prepared = widget._prepare_segments()
        assert [p["display_layer"] for p in prepared] == [layer for layer in order if layer != "furigana"]
        assert [p["base_y"] for p in prepared] == sorted(p["base_y"] for p in prepared)
        assert {p["display_layer"]: p["text"] for p in prepared} == {
            "lyrics": "歌", "romaji": "uta", "translation": "Song",
        }
        lyric = next(p for p in prepared if p["display_layer"] == "lyrics")
        assert lyric["_display_reading"] == "\u3046\u305f"
        assert (lyric["reading_y"] < lyric["base_y"]) == (order.index("furigana") < order.index("lyrics"))
        assert widget.segments[0]["text"] == "\u6b4c"
        widget.karaoke_position = 2
        progress = [widget._segment_progress(p, prepared) for p in prepared if p["display_layer"] != "translation"]
        assert len(set(progress)) == 1
        assert 0 < progress[0] < 1
        assert widget.sizeHint().height() > max(p["base_y"] for p in prepared)
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("layer", LYRIC_LAYER_ORDER)
def test_layers_can_render_alone_and_all_can_be_hidden(qapp, layer):
    widget = lyric_widget()
    try:
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, *(key == layer for key in LYRIC_LAYER_ORDER))
        assert {p["display_layer"] for p in widget._prepare_segments()} == {layer}
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, False, False, False, False)
        assert widget._prepare_segments() == []
        assert widget.sizeHint().height() == 1
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("floating", [False, True])
@pytest.mark.parametrize("note", ["♪", "♫", "♬", "♩"])
def test_music_notes_stay_in_lyrics_without_duplicate_reading_rows(qapp, floating, note):
    if floating:
        from plugins.community.floating_lyrics.plugin import SplitRenderFuriganaWidget
        widget = SplitRenderFuriganaWidget()
    else:
        widget = FuriganaWidget()
    try:
        widget.resize(700, 500)
        widget.set_lyric(f"歌{note}", generate=False)
        widget.segments = [{"text": "歌", "reading": "うた"}, {"text": note, "reading": None}]
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, False, True)
        pieces = widget._prepare_segments()
        assert "".join(p["text"] for p in pieces).count(note) == 1
        assert note in "".join(p["text"] for p in pieces if p["display_layer"] == "lyrics")
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, False, True, False, False)
        assert note in "".join(p["text"] for p in widget._prepare_segments())

        widget.set_lyric(note, generate=False)
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, False, True)
        pieces = widget._prepare_segments()
        assert [p["display_layer"] for p in pieces] == ["lyrics"]
        assert pieces[0]["text"] == note
    finally:
        widget.close()
        widget.deleteLater()


def test_vertical_layers_use_separate_columns(qapp):
    widget = lyric_widget()
    try:
        widget.set_direction("vertical")
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, True, True)
        pieces = widget._prepare_segments()
        bounds = []
        for layer in ("lyrics", "romaji", "translation"):
            row = [p for p in pieces if p["display_layer"] == layer]
            bounds.append((min(p["x"] for p in row), max(p["x"] + p["width"] for p in row)))
        assert all(left[1] < right[0] for left, right in zip(bounds, bounds[1:]))
        assert widget.sizeHint().width() >= bounds[-1][1]
    finally:
        widget.close()
        widget.deleteLater()


@pytest.mark.parametrize("floating", [False, True])
@pytest.mark.parametrize("above", [False, True])
def test_furigana_stays_on_its_word_with_romaji_and_wrapping(qapp, floating, above):
    from PySide6.QtGui import QFontMetrics
    from core.fonts import library_font

    if floating:
        from plugins.community.floating_lyrics.plugin import SplitRenderFuriganaWidget
        widget = SplitRenderFuriganaWidget()
    else:
        widget = FuriganaWidget()
    try:
        widget.resize(180 if not floating else 900, 600)
        widget.set_font(library_font(qapp.font()).family(), 32, 14)
        widget.set_lyric("未来へ未来へ未来へ", generate=False)
        widget.segments = [piece for _ in range(3) for piece in (
            {"text": "未来", "reading": "みらい"}, {"text": "へ", "reading": None},
        )]
        order = ["furigana", "lyrics", "romaji", "translation"] if above else list(LYRIC_LAYER_ORDER)
        widget.set_lyric_layers(order, True, True, False, True)
        pieces = widget._prepare_segments()
        lyrics = [p for p in pieces if p["display_layer"] == "lyrics"]
        annotated = [p for p in lyrics if p["reading"]]
        assert len(annotated) == 3
        for piece in annotated:
            assert piece["_display_reading"] == "みらい"
            assert piece["_display_reading_width"] == QFontMetrics(widget.reading_font).horizontalAdvance("みらい")
            assert (piece["reading_y"] < piece["base_y"]) == above
            assert piece["reading_y"] == piece["romaji_y"]
        assert all(not p["_display_reading"] for p in lyrics if p["text"] == "へ")
        if not floating:
            assert len({p["base_y"] for p in lyrics}) > 1
            base_metrics = QFontMetrics(widget.base_font)
            reading_metrics = QFontMetrics(widget.reading_font)
            for previous, following in zip(annotated, annotated[1:]):
                if above:
                    assert previous["base_y"] + base_metrics.descent() <= following["reading_y"] - reading_metrics.ascent()
                else:
                    assert previous["reading_y"] + reading_metrics.descent() <= following["base_y"] - base_metrics.ascent()
        assert widget.sizeHint().height() > max(
            max(p["base_y"], p.get("reading_y") or 0) for p in pieces
        )
        widget.show()
        qapp.processEvents()
        assert not widget.grab().isNull()
    finally:
        widget.close()
        widget.deleteLater()


def test_layer_settings_restore_both_readings_after_legacy_setters(qapp):
    widget = lyric_widget()
    try:
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, True, True)
        widget.set_ruby_visible(True)
        widget.set_romaji_visible(True)
        widget.set_all_romaji_visible(False)
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, True, True)
        assert widget.show_ruby and widget.show_romaji
        assert {p["display_layer"] for p in widget._prepare_segments()} == {"lyrics", "romaji", "translation"}
    finally:
        widget.close()
        widget.deleteLater()


def test_long_reading_rows_wrap_inside_the_widget(qapp):
    widget = lyric_widget()
    try:
        widget.resize(160, 100)
        widget.segments = [{"text": "歌" * 20, "reading": "うた" * 20}]
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, True, True, False, True)
        pieces = widget._prepare_segments()
        assert all(p["x"] >= 0 and p["x"] + p["width"] <= widget.width() for p in pieces)
        assert widget.sizeHint().height() > 100
        for layer in ("lyrics", "romaji"):
            assert len({p["base_y"] for p in pieces if p["display_layer"] == layer}) > 1
    finally:
        widget.close()
        widget.deleteLater()


def test_settings_order_and_independent_toggles_survive_round_trip(qapp):
    values = normalize_lyrics_settings({})
    dialog = SettingsDialog([], values)
    try:
        controls = dialog.global_controls
        order = controls["lyric_layer_order"]
        order.setCurrentRow(3)
        order.move_current(-1)
        order.item(1).setCheckState(Qt.CheckState.Checked)
        assert controls["show_romaji"].isChecked()
        assert controls["show_ruby"].isChecked()
        result = normalize_lyrics_settings(dialog.get_global_lyrics_settings())
        assert result["lyric_layer_order"] == ["lyrics", "romaji", "furigana", "translation"]
        assert result["show_romaji"] and result["show_ruby"]
        order.setValue(list(reversed(LYRIC_LAYER_ORDER)))
        assert order.item(0).checkState() == Qt.CheckState.Checked
    finally:
        dialog.close()
        dialog.deleteLater()


def test_legacy_romaji_replacement_migrates_and_invalid_order_is_repaired():
    values = normalize_lyrics_settings({"all_romaji": True})
    assert values["show_romaji"] and not values["show_lyrics"]
    assert not values["show_ruby"]
    values = normalize_lyrics_settings({"lyric_layer_order": ["romaji", "bad", "romaji"],
                                       "show_ruby": True, "show_romaji": True})
    assert values["lyric_layer_order"] == ["romaji", "lyrics", "translation", "furigana"]
    assert values["show_ruby"] and values["show_romaji"]


def test_settings_keep_the_last_visible_layer_checked(qapp):
    dialog = SettingsDialog([], normalize_lyrics_settings({}))
    try:
        order = dialog.global_controls["lyric_layer_order"]
        for index in range(order.count()):
            order.item(index).setCheckState(Qt.CheckState.Unchecked)
        assert order.item(3).checkState() == Qt.CheckState.Checked
        assert dialog.global_controls["show_ruby"].isChecked()
        order.item(0).setCheckState(Qt.CheckState.Checked)
        order.item(3).setCheckState(Qt.CheckState.Unchecked)
        assert dialog.global_controls["show_lyrics"].isChecked()
        assert not dialog.global_controls["show_ruby"].isChecked()
    finally:
        dialog.close()
        dialog.deleteLater()


def test_empty_saved_layers_fall_back_to_lyrics_including_file_overrides(tmp_path):
    from core.lyrics_settings import file_settings_key, get_effective_lyrics_settings

    hidden = dict.fromkeys(("show_lyrics", "show_romaji", "show_translation", "show_ruby"), False)
    assert normalize_lyrics_settings(hidden)["show_lyrics"]
    song = tmp_path / "song.mp3"
    values = get_effective_lyrics_settings(song, {
        "lyrics_local": {file_settings_key(song): hidden},
    })
    assert values["show_lyrics"]


def test_local_layer_toggles_restore_lyrics_if_the_last_layer_is_disabled(qapp):
    values = normalize_lyrics_settings({"show_translation": False, "show_ruby": False})
    dialog = SettingsDialog([], values, current_file="song.mp3")
    try:
        override, editor = dialog.local_controls["show_lyrics"]
        override.click()
        editor.click()
        assert override.isChecked() and editor.isChecked()
    finally:
        dialog.close()
        dialog.deleteLater()


@pytest.mark.parametrize("direction", ["horizontal", "vertical"])
def test_scrolling_rows_keep_layers_after_seek_and_recreation(qapp, monkeypatch, direction):
    from lyrics.models import LyricLine
    from lyrics.scrolling_widget import ScrollingLyricsWidget

    tokens = [{"text": "歌", "reading": "うた"}, {"text": "\nSong", "reading": None}]
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments", lambda _: tokens)
    monkeypatch.setattr("lyrics.scrolling_widget.get_cached_furigana_segments", lambda _: tokens)
    widget = ScrollingLyricsWidget()
    widget.resize(700, 500)
    widget.set_direction(direction)
    widget.set_lyric_layers(list(reversed(LYRIC_LAYER_ORDER)), True, True, True, True)
    try:
        widget.show()
        widget.set_lyrics([LyricLine(i * 5, "歌\nSong", translation_lines=[1]) for i in range(100)])
        qapp.processEvents()
        widget.set_current_line(90, animate=False)
        qapp.processEvents()
        assert 90 in widget._mounted_rows
        for row in widget.rows:
            pieces = row._prepare_segments()
            assert {p["display_layer"] for p in pieces} == {"lyrics", "romaji", "translation"}
            if direction == "horizontal":
                assert row.height() >= row.sizeHint().height()
            else:
                assert row.width() >= row.sizeHint().width()
        widget.set_lyric_layers(LYRIC_LAYER_ORDER, False, True, False, False)
        qapp.processEvents()
        assert all({p["display_layer"] for p in row._prepare_segments()} == {"romaji"}
                   for row in widget.rows)
    finally:
        widget.clear()
        widget.close()
        widget.deleteLater()
