"""Keep the card editor usable when its settings pane is narrow or scrolled."""

import pytest
from PySide6.QtCore import QPoint, QRect
from PySide6.QtGui import QFont, QImage
from PySide6.QtWidgets import QStyle, QStyleOptionButton


def open_card(editor, qapp):
    editor.resize(1100, 700)
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.controls_splitter.setSizes([300, 500])
    qapp.processEvents()
    card.refresh()
    return card


def test_separate_typography_and_story_export_round_trip_through_presets(editor, qapp, monkeypatch, tmp_path):
    from PySide6.QtGui import QColor, QFontDatabase
    import ui.lyrics_share_card as card_module
    from ui.lyrics_card_presets import save_card_preset
    card = open_card(editor, qapp)
    for key, color, size in (("title", "#ff1234", 48), ("credit", "#12ff34", 24)):
        group, font, custom, _ = card.detail_typography[key]
        group.setChecked(True)
        font.setCurrentFont(QFont(QFontDatabase.families()[0 if key == "title" else -1]))
        getattr(card, key + "_font_size").setValue(size)
        custom.setChecked(True)
        monkeypatch.setattr(card_module.QColorDialog, "getColor", lambda *args, value=color: QColor(value))
        card.choose_detail_color(key)
    card.size_combo.setCurrentIndex(next(index for index in range(card.size_combo.count())
                                        if tuple(card.size_combo.itemData(index)) == (1080, 1920)))
    style = card.current_style()
    assert style.title_font_size == 48 and style.credit_font_size == 24
    assert style.title_color == "#ff1234" and style.credit_color == "#12ff34"
    assert (style.width, style.height) == (1080, 1920)
    save_card_preset("Typography", style)
    card._reload_presets()
    for group, _, _, _ in card.detail_typography.values():
        group.setChecked(False)
    assert card.current_style().title_font_size == 0
    assert card.current_style().credit_color == ""
    card.size_combo.setCurrentIndex(0)
    card.preset_combo.setCurrentText("Typography")
    assert card.current_style() == style
    card.refresh()
    target = tmp_path / "story.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *args: (str(target), ""))
    card.export_image()
    exported = QImage(str(target))
    assert (exported.width(), exported.height()) == (1080, 1920)
    assert exported.convertToFormat(card._image.format()) == card._image
    # Older presets clear the new overrides and recover template defaults.
    save_card_preset("Original", card_module.CardStyle())
    card._reload_presets()
    card.preset_combo.setCurrentText("Original")
    assert not any(group.isChecked() for group, _, _, _ in card.detail_typography.values())
    assert card.current_style().title_font_family == ""


def test_expanded_settings_fit_narrow_pane_and_leave_export_visible(editor, qapp, tmp_path):
    card = open_card(editor, qapp)
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(0xff234567)
    path = tmp_path / "background.png"
    assert image.save(str(path))
    card.image_path_edit.setText(str(path))
    card.background_mode.setCurrentText("Image")
    for group in (card.custom_colors, card.show_album_cover, card.stroke_enabled,
                  card.shadow_enabled, card.watermark_enabled, card.cover_shadow_enabled):
        group.setChecked(True)
    for group, _, _, _ in card.detail_typography.values():
        group.setChecked(True)
    card.refresh()

    for scroll in card.control_scrolls.values():
        card.options_tabs.setCurrentWidget(scroll)
        qapp.processEvents()
        assert scroll.widget().width() <= scroll.viewport().width()
        scroll.verticalScrollBar().setValue(scroll.verticalScrollBar().maximum())
        qapp.processEvents()
        for button in (card.copy_button, card.save_button):
            assert button.isVisible() and button.isEnabled()
            assert card.rect().contains(QRect(button.mapTo(card, QPoint()), button.size()))
        assert card.preview.isVisible() and not card.preview.image.isNull()

    card.options_tabs.setCurrentWidget(card.control_scrolls["Text"])
    qapp.processEvents()
    width = card.control_scrolls["Text"].viewport().width()
    for field in (card.title_edit, card.credit_edit, card.font_combo,
                  *(font for _, font, _, _ in card.detail_typography.values())):
        assert field.width() >= width - 60


def test_optional_effects_collapse_without_losing_values(editor, qapp):
    card = open_card(editor, qapp)
    card.options_tabs.setCurrentWidget(card.control_scrolls["Effects"])
    qapp.processEvents()
    assert not card.stroke_width.isVisible()
    card.stroke_enabled.setChecked(True)
    card.stroke_width.setValue(7)
    qapp.processEvents()
    assert card.stroke_width.isVisible() and card.stroke_width.isEnabled()
    card.stroke_enabled.setChecked(False)
    qapp.processEvents()
    assert not card.stroke_width.isVisible()
    assert not card.current_style().stroke_enabled
    card.stroke_enabled.setChecked(True)
    qapp.processEvents()
    assert card.stroke_width.isVisible()
    assert card.current_style().stroke_width == 7


@pytest.mark.parametrize("font_size,indicator_size", [(8, 20), (14, 28)])
def test_collapsed_effect_headers_fit_themed_checkbox_indicators(editor, qapp, font_size, indicator_size):
    card = open_card(editor, qapp)
    card.options_tabs.setCurrentWidget(card.control_scrolls["Effects"])
    headers = [group.checkbox for group in (card.stroke_enabled, card.shadow_enabled, card.watermark_enabled)]
    for header in headers:
        font = QFont(header.font())
        font.setPointSize(font_size)
        header.setFont(font)
        header.setStyleSheet(f"QCheckBox::indicator {{ width: {indicator_size}px; height: {indicator_size}px; border: 1px solid gray; border-radius: 4px; }}")
    qapp.processEvents()
    for header in headers:
        option = QStyleOptionButton()
        header.initStyleOption(option)
        indicator = header.style().subElementRect(QStyle.SubElement.SE_CheckBoxIndicator, option, header)
        assert indicator.height() >= indicator_size
        assert header.rect().contains(indicator)
        assert header.rect().contains(header.style().subElementRect(QStyle.SubElement.SE_CheckBoxContents, option, header))


def test_artwork_reveals_only_controls_for_the_selected_background(editor, qapp):
    card = open_card(editor, qapp)
    card.options_tabs.setCurrentWidget(card.control_scrolls["Artwork"])
    qapp.processEvents()
    assert not card.cover_controls.isVisible()
    assert card.gradient_controls.isVisible()
    assert not card.image_controls.isVisible()
    card.show_album_cover.setChecked(True)
    card.background_mode.setCurrentText("Image")
    qapp.processEvents()
    assert card.cover_controls.isVisible()
    assert card.cover_size.isEnabled()
    assert card.image_controls.isVisible()
    assert not card.gradient_controls.isVisible()
    card.background_mode.setCurrentText("Solid")
    qapp.processEvents()
    assert not card.gradient_controls.isVisible()
    assert not card.image_controls.isVisible()
