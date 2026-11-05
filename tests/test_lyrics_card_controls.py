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


def test_expanded_settings_fit_narrow_pane_and_leave_export_visible(editor, qapp, tmp_path):
    card = open_card(editor, qapp)
    image = QImage(64, 64, QImage.Format.Format_RGB32)
    image.fill(0xff234567)
    path = tmp_path / "background.png"
    assert image.save(str(path))
    card.image_path_edit.setText(str(path))
    card.background_mode.setCurrentText("Image")
    for group in (card.custom_colors, card.show_album_cover, card.stroke_enabled,
                  card.shadow_enabled, card.watermark_enabled):
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
    for field in (card.title_edit, card.credit_edit, card.font_combo):
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
