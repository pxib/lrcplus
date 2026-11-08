"""Reading setup previews locally and applies only explicitly accepted edits."""

import pytest
from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QWidget

from core import settings
from ui.onboarding import FINISH_PAGE, LYRICS_PAGE, READING_PAGE, STEPS, OnboardingDialog, load_dialogue


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
    assert wizard.debug_step.itemText(READING_PAGE) == "Reading preferences"
    assert wizard.debug_step.itemText(LYRICS_PAGE) == "Lyrics"
    assert wizard.debug_step.itemText(FINISH_PAGE) == "Ready"


@pytest.mark.parametrize("mode", ["none", "furigana", "romaji", "all_romaji"])
def test_reading_preview_uses_supplied_readings_without_workers(wizard, monkeypatch, qapp, mode):
    def no_generation(*args, **kwargs):
        pytest.fail("Reading preview must not generate readings")

    monkeypatch.setattr("lyrics.furigana_widget.build_furigana_segments", no_generation)
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(48)
    panel.reading_mode.setCurrentIndex(panel.reading_mode.findData(mode))
    qapp.processEvents()
    assert panel.preview.base_font_size == 48
    assert panel.preview.lyric == "歌"
    assert panel.preview.show_ruby is (mode == "furigana")
    assert panel.preview.show_romaji is (mode == "romaji")
    assert panel.preview.all_romaji is (mode == "all_romaji")
    assert panel.preview._furigana_future is None
    assert not panel.preview._reading_timer.isActive()
    assert not settings.SETTINGS_PATH.exists()


def test_apply_updates_player_and_global_preferences(wizard):
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(44)
    panel.reading_mode.setCurrentIndex(panel.reading_mode.findData("romaji"))
    refreshed = []
    wizard.player.refresh_lyrics_settings_for_current_file = lambda: refreshed.append(True)
    assert not settings.SETTINGS_PATH.exists()
    wizard.next_page()
    values = settings.get_global_lyrics_settings()
    assert wizard.page == LYRICS_PAGE
    assert values["font_size"] == 44
    assert values["show_romaji"]
    assert not values["show_ruby"]
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
    panel.reading_mode.setCurrentIndex(panel.reading_mode.findData("all_romaji"))
    wizard.skip_reading.click()
    assert wizard.page == LYRICS_PAGE
    assert settings.load_all_settings() == before
    wizard.previous_page()
    assert wizard.page == READING_PAGE
    assert panel.font_size.value() == 36
    assert panel.reading_mode.currentData() == "furigana"
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


def test_preview_reflows_at_largest_font_and_modes(wizard, qapp):
    wizard.set_page(READING_PAGE)
    panel = wizard.reading_preferences
    panel.font_size.setValue(96)
    for index in range(panel.reading_mode.count()):
        panel.reading_mode.setCurrentIndex(index)
        qapp.processEvents()
        assert panel.preview.height() >= panel.preview.sizeHint().height()
        for piece in panel.preview._prepare_segments():
            assert piece["x"] >= 0
            assert piece["x"] + piece["width"] <= panel.preview.width()
        assert panel.geometry().bottom() < wizard.text_speed_combo.geometry().top()
        assert wizard.height() == wizard._height_target
