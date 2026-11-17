"""Exercise reproducible cases through the production lyric renderer."""

import pytest

from tools.renderer_playground import PRESETS, RendererPlayground


@pytest.fixture
def playground(qapp, monkeypatch):
    # Keep automatic reading providers out of deterministic authored-ruby cases.
    monkeypatch.setattr("lyrics.furigana_widget.get_cached_furigana_segments",
                        lambda text: [{"text": text, "reading": None}])
    window = RendererPlayground()
    yield window
    window.close()
    window.deleteLater()


def test_authored_ruby_and_scrubbing_reach_renderer(playground):
    row = playground.row
    assert row.lyric == "君の声が届く"
    assert any(segment.get("reading") == "きみ" for segment in row.segments)
    playground.position.setValue(3.25)
    assert row.karaoke_position == 3.25
    assert playground.slider.value() == 3250
    playground.slider.setValue(4500)
    assert row.karaoke_position == 4.5


def test_saved_case_restores_geometry_ruby_and_time(playground):
    playground.controls["width"].setValue(420)
    playground.controls["direction"].setCurrentText("vertical")
    playground.position.setValue(2.5)
    case = playground.case_data()
    playground.use_preset("Wrapping")
    playground.controls["width"].setValue(900)
    playground.restore_case(case)
    assert playground.case_data() == case
    assert playground.row.width() == 420
    assert playground.row.direction == "vertical"
    assert playground.row.karaoke_position == 2.5


def test_simultaneous_rows_and_plain_lrc_remain_independent(playground):
    playground.use_preset("Repeated timestamps")
    assert len(playground.lines) == 3
    playground.line_choice.setCurrentIndex(1)
    playground.position.setValue(2)
    assert playground.row.lyric == "Second simultaneous row"
    assert playground.row.karaoke_position is None
    playground.source.setPlainText("invalid source")
    playground.parse_source()
    assert playground.row.lyric == ""
    assert playground.line_choice.count() == 0


@pytest.mark.parametrize("preset", PRESETS)
def test_presets_render_to_exportable_image(playground, preset, tmp_path):
    playground.use_preset(preset)
    image = playground.render_image()
    assert not image.isNull()
    assert image.size() == playground.row.size()
    # Ensure the production renderer draws content over the background.
    background = image.pixelColor(image.width() // 2, image.height() - 2)
    assert any(image.pixelColor(x, y) != background
               for y in range(2, image.height() - 2, 3)
               for x in range(2, image.width() - 2, 3))
    assert image.save(str(tmp_path / "preview.png"), "PNG")
