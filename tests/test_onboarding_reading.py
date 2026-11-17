"""Reading setup previews locally and applies only explicitly accepted edits."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core import settings
from ui.onboarding import FINISH_PAGE, LYRICS_PAGE, READING_PAGE, STEPS, OnboardingDialog, load_dialogue


def select_layers(panel, enabled):
    for checked in (True, False):
        for index in range(panel.layer_order.count()):
            item = panel.layer_order.item(index)
            if (item.data(Qt.ItemDataRole.UserRole) in enabled) == checked:
                item.setCheckState(Qt.CheckState.Checked if checked else Qt.CheckState.Unchecked)


@pytest.fixture
def wizard(qapp):
    player = QWidget()
    player.library_folders = []
    dialog = OnboardingDialog(player)
    dialog.show()
    dialog.auto_read_check.setChecked(False)
    dialog.reduced_motion_check.setChecked(True)
    yield dialog
    dialog.close()
    qapp.processEvents()


def test_reading_step_is_between_appearance_and_lyrics(wizard):
    assert STEPS[READING_PAGE - 1][0] == "thirdstepappearance"
    assert STEPS[READING_PAGE + 1][0] == "fourthsteplyrics"
    assert load_dialogue("readingpreferences")[-1][1].endswith(r"\e")
    assert wizard.reading_preferences is None
    wizard.set_page(2)
    wizard.next_page()
    assert wizard.page == READING_PAGE
    assert wizard.skip_reading.isVisible()
    assert not wizard.reading_container.isVisible()
    wizard.show_debug()
    assert wizard.debug_step.itemText(READING_PAGE) == "Lyric layers"
    assert wizard.debug_step.itemText(LYRICS_PAGE) == "Lyrics"
    assert wizard.debug_step.itemText(FINISH_PAGE) == "Ready"


@pytest.mark.parametrize("layers", [
    ["lyrics"], ["furigana"], ["romaji"], ["translation"],
    ["lyrics", "romaji", "translation", "furigana"],
])
def test_reading_preview_uses_supplied_readings_without_workers(wizard, monkeypatch, qapp, layers):
    def no_generation(*args, **kwargs):
        pytest.fail("Reading preview must not generate readings")

    monkeypatch.setattr("lyrics.furigana_widget.build_furigana_segments", no_generation)
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(48)
    select_layers(panel, layers)
    qapp.processEvents()
    assert panel.preview.base_font_size == 48
    assert panel.preview.lyric == "歌♪\nSong"
    assert panel.preview.show_ruby is ("furigana" in layers)
    assert panel.preview.show_romaji is ("romaji" in layers)
    assert not panel.preview.all_romaji
    assert {p["display_layer"] for p in panel.preview._prepare_segments()} == set(layers)
    assert panel.preview._furigana_future is None
    assert not panel.preview._reading_timer.isActive()
    assert not settings.SETTINGS_PATH.exists()


def test_apply_updates_player_and_global_preferences(wizard):
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(44)
    select_layers(panel, ["lyrics", "romaji", "furigana"])
    panel.layer_order.setCurrentRow(3)
    panel.layer_order.move_current(-1)
    refreshed = []
    wizard.player.refresh_lyrics_settings_for_current_file = lambda: refreshed.append(True)
    assert not settings.SETTINGS_PATH.exists()
    wizard.next_page()
    values = settings.get_global_lyrics_settings()
    assert wizard.page == LYRICS_PAGE
    assert values["font_size"] == 44
    assert values["show_romaji"]
    assert values["show_ruby"] and values["show_lyrics"]
    assert not values["show_translation"]
    assert values["lyric_layer_order"] == ["lyrics", "romaji", "furigana", "translation"]
    assert not values["all_romaji"]
    assert wizard.player.global_lyrics_settings == values
    assert refreshed == [True]
    assert not wizard.reading_container.isVisible()
    assert not wizard.skip_reading.isVisible()


def test_skip_discards_draft_and_back_reloads_current_preferences(wizard):
    settings.save_global_lyrics_settings({"font_size": 36, "show_ruby": True})
    before = settings.load_all_settings()
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(64)
    select_layers(panel, ["romaji"])
    panel.layer_order.setCurrentRow(0)
    panel.layer_order.move_current(1)
    wizard.skip_reading.click()
    assert wizard.page == LYRICS_PAGE
    assert settings.load_all_settings() == before
    wizard.previous_page()
    assert wizard.page == READING_PAGE
    assert panel.font_size.value() == 36
    assert panel.layer_controls["furigana"].isChecked()
    assert panel.layer_controls["lyrics"].isChecked()
    assert not panel.layer_controls["romaji"].isChecked()
    assert panel.layer_order.value() == settings.get_global_lyrics_settings()["lyric_layer_order"]
    assert not panel.changes()


def test_skip_works_during_intro_and_cancels_old_dialogue(wizard):
    wizard.set_page(READING_PAGE)
    wizard.show_dialogue()
    assert wizard.typing_timer.isActive()
    wizard.skip_reading.click()
    assert wizard.page == LYRICS_PAGE
    assert wizard.full_text == "Okay. Music, a little personality, and now the thing in the name: lyrics."
    assert not wizard.reading_container.isVisible()
    assert not settings.SETTINGS_PATH.exists()


def test_space_can_skip_reading_while_intro_is_typing(wizard, qapp):
    wizard.set_page(READING_PAGE)
    wizard.show_dialogue()
    wizard.activateWindow()
    wizard.skip_reading.setFocus()
    qapp.processEvents()
    QTest.keyClick(wizard.skip_reading, Qt.Key.Key_Space)
    assert wizard.page == LYRICS_PAGE
    assert not settings.SETTINGS_PATH.exists()


def test_apply_preserves_unedited_settings_and_file_overrides(wizard):
    settings.save_all_settings({
        "lyrics_global": {"font_size": 32, "show_ruby": True},
        "lyrics_local": {"example.mp3": {"font_size": 55}},
        "plugins": {"example": {"enabled": True}},
    })
    wizard.set_page(READING_PAGE)
    wizard.reading_preferences.font_size.setValue(46)
    # Simulate another settings surface changing unrelated values while previewing.
    latest = settings.get_global_lyrics_settings()
    latest.update({"lyrics_color": "#445566", "all_romaji": True, "show_ruby": False})
    settings.save_global_lyrics_settings(latest)
    wizard.next_page()
    payload = settings.load_all_settings()
    assert payload["lyrics_global"]["font_size"] == 46
    assert payload["lyrics_global"]["lyrics_color"] == "#445566"
    assert payload["lyrics_global"]["all_romaji"]
    assert payload["lyrics_local"]["example.mp3"]["font_size"] == 55
    assert payload["plugins"]["example"]["enabled"]


def test_unchanged_reading_preferences_do_not_write_settings(wizard, monkeypatch):
    wizard.set_page(READING_PAGE)
    writes = []
    monkeypatch.setattr(settings, "save_global_lyrics_settings", lambda values: writes.append(values))
    wizard.next_page()
    assert wizard.page == LYRICS_PAGE
    assert writes == []
    assert not settings.SETTINGS_PATH.exists()


def test_layer_edit_preserves_concurrent_edits_to_other_layers(wizard):
    wizard.set_page(READING_PAGE)
    wizard.reading_preferences.layer_controls["romaji"].setChecked(True)
    latest = settings.get_global_lyrics_settings()
    latest.update(show_translation=False, show_ruby=False, lyrics_color="#445566")
    settings.save_global_lyrics_settings(latest)
    wizard.next_page()
    result = settings.get_global_lyrics_settings()
    assert result["show_romaji"] and result["show_lyrics"]
    assert not result["show_translation"] and not result["show_ruby"]
    assert result["lyrics_color"] == "#445566"


def test_editing_legacy_replacement_mode_keeps_its_effective_romaji_layer(wizard):
    settings.save_all_settings({"lyrics_global": {
        "lyric_layer_order": ["lyrics", "romaji", "translation", "furigana"],
        "all_romaji": True, "show_romaji": False, "show_lyrics": True, "show_ruby": False,
    }})
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    assert not panel.changes()
    assert panel.layer_controls["romaji"].isChecked()
    assert not panel.layer_controls["lyrics"].isChecked()
    panel.layer_controls["lyrics"].setChecked(True)
    wizard.next_page()
    result = settings.get_global_lyrics_settings()
    assert result["show_lyrics"] and result["show_romaji"]
    assert not result["all_romaji"]


def test_preview_scrolls_at_largest_font_without_pushing_controls_offscreen(wizard, qapp):
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(96)
    for layers in (["lyrics"], ["romaji"], ["translation"], ["furigana"],
                   ["lyrics", "romaji", "translation", "furigana"]):
        select_layers(panel, layers)
        qapp.processEvents()
        assert panel.preview.height() >= panel.preview.sizeHint().height()
        for piece in panel.preview._prepare_segments():
            assert piece["x"] >= 0
            assert piece["x"] + piece["width"] <= panel.preview.width()
        assert panel.geometry().bottom() < wizard.text_speed_combo.geometry().top()
        assert wizard.height() == wizard._height_target
    assert panel.preview_scroll.verticalScrollBar().maximum() > 0


def test_preview_reorders_live_and_keeps_one_layer_enabled(wizard):
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    select_layers(panel, ["lyrics", "romaji", "translation", "furigana"])
    panel.layer_order.setCurrentRow(3)
    panel.layer_order.move_current(-1)
    pieces = panel.preview._prepare_segments()
    assert list(dict.fromkeys(p["display_layer"] for p in pieces)) == panel.layer_order.value()
    assert "".join(p["text"] for p in pieces).count("♪") == 1
    romaji = next(p for p in pieces if p["display_layer"] == "romaji")
    assert panel.preview._paint_font(panel.preview.base_font, romaji).pointSize() == panel.initial["ruby_font_size"]
    select_layers(panel, ["romaji"])
    for index in range(panel.layer_order.count()):
        panel.layer_order.item(index).setCheckState(Qt.CheckState.Unchecked)
    assert panel.layer_controls["romaji"].isChecked()
    romaji = next(p for p in panel.preview._prepare_segments() if p["display_layer"] == "romaji")
    assert panel.preview._paint_font(panel.preview.base_font, romaji).pointSize() == panel.font_size.value()
