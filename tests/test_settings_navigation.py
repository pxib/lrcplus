from PySide6.QtCore import Qt
from PySide6.QtTest import QTest

from ui.settings_dialog import SettingsDialog


def test_settings_categories_support_keyboard_and_programmatic_navigation(qapp, monkeypatch):
    refreshes = []
    monkeypatch.setattr(
        "ui.dependency_settings.DependencyManagerWidget.refresh",
        lambda widget: refreshes.append(widget),
    )
    dialog = SettingsDialog([], {}, appearance_settings={
        "background_blur": 18,
        "empty_timestamp_placeholder": "Next in {countdown}",
    })
    try:
        dialog.show()
        qapp.processEvents()
        categories = dialog.category_list
        pages = dialog.category_pages
        assert pages.currentIndex() == categories.currentRow() == 0
        categories.setFocus()
        QTest.keyClick(categories, Qt.Key.Key_Down)
        assert pages.currentWidget().isAncestorOf(dialog.theme_color_button)
        QTest.keyClick(categories, Qt.Key.Key_Down)
        assert pages.currentWidget().isAncestorOf(dialog.background_blur_slider)
        assert not pages.currentWidget().isAncestorOf(dialog.theme_color_button)
        assert refreshes == []

        lyrics_index = next(i for i in range(pages.count()) if pages.tabText(i) == "Lyrics")
        pages.setCurrentIndex(lyrics_index)
        assert categories.currentRow() == lyrics_index
        assert pages.currentWidget().isAncestorOf(dialog.empty_timestamp_placeholder_edit)
        assert dialog.scope_tabs.widget(0).isAncestorOf(dialog.empty_timestamp_placeholder_edit)
        dialog.empty_timestamp_placeholder_edit.setText("Waiting {countdown}")
        appearance = dialog.get_appearance_settings()
        assert appearance["empty_timestamp_placeholder"] == "Waiting {countdown}"
        assert appearance["background_blur"] == 18

        dependency_index = pages.indexOf(dialog.dependencies_tab)
        categories.setCurrentRow(dependency_index)
        assert refreshes == [dialog.dependencies_tab]
        categories.setCurrentRow(0)
        categories.setCurrentRow(dependency_index)
        assert refreshes == [dialog.dependencies_tab]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_lyric_sections_keep_global_and_local_values_when_switching(qapp):
    from PySide6.QtWidgets import QTabWidget
    from core.settings import get_global_lyrics_settings
    from ui.settings_dialog import LYRIC_SECTIONS, LOCAL_LYRIC_KEYS, LYRIC_KEYS

    values = get_global_lyrics_settings()
    overrides = {"font_size": 40, "show_romaji": True, "show_ruby": False,
                 "scroll_animation_speed": 500}
    dialog = SettingsDialog([], values, local_overrides=overrides, current_file="song.mp3")
    try:
        dialog.show()
        dialog.category_pages.setCurrentWidget(dialog.scope_tabs.parentWidget())
        qapp.processEvents()
        assert set(dialog.global_controls) >= set(LYRIC_KEYS)
        assert set(dialog.local_controls) == set(LOCAL_LYRIC_KEYS)
        assert dialog.get_global_lyrics_settings() == values
        for scope, controls in ((0, dialog.global_controls), (1, dialog.local_controls)):
            dialog.scope_tabs.setCurrentIndex(scope)
            tabs = dialog.scope_tabs.widget(scope).findChild(QTabWidget, "lyricsSections")
            assert [tabs.tabText(i) for i in range(tabs.count())] == list(LYRIC_SECTIONS)
            for index, keys in enumerate(LYRIC_SECTIONS.values()):
                tabs.setCurrentIndex(index)
                qapp.processEvents()
                for key in keys:
                    if key not in controls:
                        continue
                    editor = controls[key] if scope == 0 else controls[key][1]
                    assert tabs.currentWidget().isAncestorOf(editor), key
        assert dialog.get_local_lyrics_overrides() == overrides
        # A local edit changes only the enabled override, even after leaving its section.
        enabled, editor = dialog.local_controls["font_size"]
        editor.setValue(44)
        tabs.setCurrentIndex(0)
        assert dialog.get_local_lyrics_overrides() == dict(overrides, font_size=44)
        enabled.setChecked(False)
        assert "font_size" not in dialog.get_local_lyrics_overrides()
        assert dialog.get_global_lyrics_settings() == values
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
