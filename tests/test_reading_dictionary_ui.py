from copy import deepcopy

from PySide6.QtCore import QCoreApplication, QEvent, Qt
from PySide6.QtWidgets import QDialog, QFileDialog, QLineEdit, QMessageBox

from core.reading_dictionary import load_dictionaries, new_dictionary, save_dictionaries
from core.settings import get_app_data_dir
from ui.reading_dictionary import ReadingDictionaryDialog
from ui.settings_dialog import SettingsDialog


def sample_data():
    first = new_dictionary("Song readings")
    first["entries"] = [
        {"word": "明日", "reading": "あす", "type": "furigana", "comment": "Preferred reading", "match": "word"},
        {"word": "重慶", "reading": "chóng qìng", "type": "pinyin", "comment": "Place name", "match": "word"},
    ]
    return {"version": 1, "dictionaries": [first, new_dictionary("Other")]}


def test_dictionary_manager_edits_filters_and_cancel_preserves_input(qapp):
    data = sample_data()
    original = deepcopy(data)
    dialog = ReadingDictionaryDialog(data)
    try:
        dialog.table.item(0, 0).setText("あした")
        matching = dialog.table.cellWidget(0, 4)
        assert matching.currentData() == "word"
        matching.setCurrentIndex(matching.findData("substring"))
        assert dialog.data["dictionaries"][0]["entries"][0]["match"] == "substring"
        dialog.dictionary_list.item(0).setCheckState(Qt.CheckState.Unchecked)
        dialog.search.setText("Place")
        assert dialog.table.isRowHidden(0)
        assert not dialog.table.isRowHidden(1)
        dialog.dictionary_list.setCurrentRow(1)
        dialog._move_dictionary(-1)
        assert dialog.data["dictionaries"][0]["name"] == "Other"
        assert dialog.data["dictionaries"][1]["entries"][0]["reading"] == "あした"
        assert not dialog.data["dictionaries"][1]["enabled"]
        dialog.reject()
        assert data == original
    finally:
        dialog.deleteLater()


def test_manager_accept_commits_active_cell_editor_and_rejects_incomplete_rows(qapp, monkeypatch):
    dialog = ReadingDictionaryDialog(sample_data())
    warnings = []
    monkeypatch.setattr(QMessageBox, "warning", lambda *args: warnings.append(args))
    try:
        dialog.show()
        dialog.table.setCurrentCell(0, 0)
        dialog.table.editItem(dialog.table.item(0, 0))
        qapp.processEvents()
        editor = next(widget for widget in dialog.table.findChildren(QLineEdit) if widget.isVisible())
        editor.setText("あした")
        dialog.accept()
        assert dialog.result() == QDialog.DialogCode.Accepted
        assert dialog.data["dictionaries"][0]["entries"][0]["reading"] == "あした"
        dialog.show()
        dialog._add_entry()
        dialog.accept()
        assert warnings
        assert dialog.isVisible()
    finally:
        dialog.reject()
        dialog.deleteLater()


def test_dictionary_changes_are_staged_until_settings_ok(qapp, monkeypatch):
    import ui.reading_dictionary as module

    real_dialog = module.ReadingDictionaryDialog
    original = sample_data()
    save_dictionaries(original)
    managers = []

    def make_manager(data, parent):
        dialog = real_dialog(data, parent)
        managers.append(dialog)
        def edit():
            dialog.table.item(0, 0).setText("あした")
            dialog.accept()
            return dialog.result()
        dialog.exec = edit
        return dialog

    monkeypatch.setattr(module, "ReadingDictionaryDialog", make_manager)
    settings = SettingsDialog([], {})
    windows = [settings]
    try:
        settings._manage_reading_dictionaries()
        assert load_dictionaries()["dictionaries"][0]["entries"][0]["reading"] == "あす"
        settings.reject()
        assert load_dictionaries(reload=True) == original
        settings = SettingsDialog([], {})
        windows.append(settings)
        settings._manage_reading_dictionaries()
        settings.accept()
        assert load_dictionaries(reload=True)["dictionaries"][0]["entries"][0]["reading"] == "あした"
        assert settings.result() == QDialog.DialogCode.Accepted
        settings.settings_search.search_edit.setText("dictionary")
        search = settings.settings_search
        entries = [search.entries[search.results.item(i).data(Qt.ItemDataRole.UserRole)]
                   for i in range(search.results.count())]
        assert any(entry.widget is settings.reading_dictionary_button for entry in entries)
    finally:
        for window in windows:
            window.deleteLater()
        QCoreApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)


def test_import_adds_separate_dictionary_without_overwriting_entries(qapp, tmp_path, monkeypatch):
    path = tmp_path / "Song readings.txt"
    path.write_text("きみ\t君\t名詞\tImported\n", encoding="utf-8")
    monkeypatch.setattr(QFileDialog, "getOpenFileName", lambda *args: (str(path), ""))
    dialog = ReadingDictionaryDialog(sample_data())
    try:
        dialog._import_dictionaries()
        assert len(dialog.data["dictionaries"]) == 3
        assert dialog.data["dictionaries"][-1]["name"] == "Song readings (2)"
        assert dialog.data["dictionaries"][-1]["entries"][0]["word"] == "君"
        assert dialog.data["dictionaries"][0]["entries"][0]["word"] == "明日"
        assert not (get_app_data_dir() / "reading-dictionaries.json").exists()
    finally:
        dialog.deleteLater()
