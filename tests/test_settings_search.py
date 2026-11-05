from PySide6.QtCore import Qt
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QScrollArea

from core.settings import get_global_lyrics_settings
from ui.settings_dialog import SettingsDialog


def matches(search):
    return [search.entries[search.results.item(i).data(Qt.ItemDataRole.UserRole)]
            for i in range(search.results.count())]


def test_search_finds_aliases_scopes_and_tooltips_without_changing_settings(qapp, monkeypatch):
    refreshes = []
    monkeypatch.setattr("ui.dependency_settings.DependencyManagerWidget.refresh",
                        lambda widget: refreshes.append(widget))
    values = get_global_lyrics_settings()
    dialog = SettingsDialog([], values, current_file="song.mp3")
    try:
        search = dialog.settings_search
        search.search_edit.setText("furigana position")
        entries = matches(search)
        assert len(entries) == 2
        assert {entry.path for entry in entries} == {
            "Lyrics → Global → Readings", "Lyrics → Local (File) → Readings",
        }
        search.search_edit.setText("windows audio")
        assert dialog.output_device_combo in [entry.widget for entry in matches(search)]
        search.search_edit.setText("font local")
        assert matches(search)
        assert all("Local (File)" in entry.path for entry in matches(search))
        assert dialog.get_global_lyrics_settings() == values
        assert dialog.get_local_lyrics_overrides() == {}
        assert refreshes == []
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_search_keyboard_opens_nested_control_and_scrolls_without_accepting(qapp):
    dialog = SettingsDialog([], get_global_lyrics_settings(), current_file="song.mp3")
    try:
        dialog.show()
        qapp.processEvents()
        search = dialog.settings_search
        search.search_edit.setFocus()
        QTest.keyClicks(search.search_edit, "local scroll easing")
        assert search.results.count() == 1
        target = matches(search)[0].widget
        QTest.keyClick(search.search_edit, Qt.Key.Key_Down)
        assert search.results.hasFocus()
        QTest.keyClick(search.results, Qt.Key.Key_Return)
        qapp.processEvents()
        assert search.search_edit.text() == ""
        assert search.stack.currentIndex() == 0
        assert dialog.isVisible() and dialog.result() == 0
        assert dialog.scope_tabs.currentIndex() == 1
        assert target.isVisible()
        assert target.property("settingsSearchHighlight") is True
        assert dialog.local_controls["scroll_animation_easing"][0].hasFocus()
        scroll = target.parentWidget()
        while not isinstance(scroll, QScrollArea):
            scroll = scroll.parentWidget()
        assert scroll.viewport().rect().intersects(target.rect().translated(target.mapTo(scroll.viewport(), target.rect().topLeft())))
        search._clear_highlight()
        assert target.property("settingsSearchHighlight") is False
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_empty_results_escape_and_clear_restore_current_page(qapp):
    dialog = SettingsDialog([], {})
    try:
        dialog.show()
        search = dialog.settings_search
        dialog.category_list.setCurrentRow(2)
        page = dialog.category_pages.currentWidget()
        search.search_edit.setFocus()
        QTest.keyClick(search.search_edit, Qt.Key.Key_Return)
        assert dialog.isVisible() and dialog.result() == 0
        search.search_edit.setText("no-such-preference-xyz")
        assert search.results.count() == 0
        assert "No settings found" in search.summary.text()
        QTest.keyClick(search.search_edit, Qt.Key.Key_Return)
        assert dialog.isVisible() and dialog.result() == 0
        QTest.keyClick(search.search_edit, Qt.Key.Key_Escape)
        assert search.stack.currentIndex() == 0
        assert dialog.category_pages.currentWidget() is page
        assert dialog.isVisible()
        search.search_edit.setText("font")
        search.search_edit.clear()
        assert search.stack.currentIndex() == 0
        search.search_edit.setText("font")
        dialog.category_list.setCurrentRow(0)
        assert search.search_edit.text() == ""
        assert search.stack.currentIndex() == 0
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_hidden_effect_result_explains_requirement_without_changing_effect(qapp):
    dialog = SettingsDialog([], {})
    try:
        dialog.show()
        qapp.processEvents()
        search = dialog.settings_search
        before = dialog.get_karaoke_settings()
        search.search_edit.setText("glow spread")
        assert search.results.count() == 1
        search.activate(search.results.item(0))
        qapp.processEvents()
        assert "Glow sweep effect" in search.notice.text()
        assert search.notice.isVisible()
        assert search._highlighted is dialog.karaoke_controls["sweep_style"]
        assert dialog.get_karaoke_settings() == before
        search.search_edit.setText("font local")
        assert search.results.count() == 0
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_enter_in_search_field_opens_result_without_applying_settings(qapp):
    dialog = SettingsDialog([], get_global_lyrics_settings())
    try:
        dialog.show()
        qapp.processEvents()
        search = dialog.settings_search
        search.search_edit.setFocus()
        search.search_edit.setText("accent color")
        assert search.results.count() == 1
        QTest.keyClick(search.search_edit, Qt.Key.Key_Return)
        qapp.processEvents()
        assert dialog.isVisible() and dialog.result() == 0
        assert dialog.theme_color_button.isVisible()
        assert dialog.theme_color_button.hasFocus()
        QTest.keyClick(dialog.theme_color_button, Qt.Key.Key_F, Qt.KeyboardModifier.ControlModifier)
        qapp.processEvents()
        assert search.search_edit.hasFocus()
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()
