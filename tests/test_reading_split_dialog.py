import pytest

from ui.reading_split_dialog import SplitReadingDialog, reading_split_points


@pytest.mark.parametrize("text,expected", [
    ("連帯", [1]), ("女子力", [1, 2]), ("心", []),
    ("e\u0301心", [2]), ("👩‍💻心", [3]), ("心\ufe0f", []),
])
def test_split_points_keep_unicode_characters_together(text, expected):
    assert reading_split_points(text) == expected


def test_suggest_readings_and_refresh_labels_when_boundary_changes(qapp):
    dialog = SplitReadingDialog("女子力", "じょしりょく")
    try:
        assert dialog.split_position == 1
        assert dialog.left_label.text() == "女:"
        assert dialog.right_label.text() == "子力:"
        assert dialog.left_reading.text() == "じょ"
        assert dialog.right_reading.text() == "しりょく"
        dialog.boundary.setCurrentIndex(1)
        assert dialog.split_position == 2
        assert dialog.left_label.text() == "女子:"
        assert dialog.right_label.text() == "力:"
        assert dialog.left_reading.text() == "じょし"
        assert dialog.right_reading.text() == "りょく"
    finally:
        dialog.deleteLater()


def test_suggestions_keep_okurigana_outside_existing_reading(qapp):
    dialog = SplitReadingDialog("描き", "エガ", reading_range=(0, 1))
    try:
        assert dialog.left_reading.text() == "エガ"
        assert dialog.right_reading.text() == ""
    finally:
        dialog.deleteLater()
