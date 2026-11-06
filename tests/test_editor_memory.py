"""Regressions for native image buffers retained by editor UI objects."""

from PySide6.QtCore import QEvent
from PySide6.QtWidgets import QApplication


def test_card_controls_are_created_only_when_requested(editor, qapp):
    assert editor._share_card is None
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    qapp.processEvents()
    assert editor._share_card is None  # Sharing text does not need image controls.
    editor.share_tabs.setCurrentWidget(editor._share_card_page)
    card = editor._share_card
    assert card is not None
    assert editor.share_tabs.currentWidget() is card
    assert editor.share_card is card
    editor.share_tabs.setCurrentIndex(0)
    editor.share_tabs.setCurrentIndex(1)
    assert editor._share_card is card


def test_closing_editor_does_not_create_card_controls(editor):
    editor.document_dirty = False
    editor.close()
    assert editor._share_card is None


def test_card_viewers_release_native_images_after_each_close(editor, monkeypatch):
    from ui.player_dialogs import CoverPreviewDialog

    card = editor.share_card
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(card)
    editor.select_all_share_lines()
    monkeypatch.setattr(CoverPreviewDialog, "exec", lambda self: 0)
    for _ in range(5):
        card.open_image_viewer()
        QApplication.sendPostedEvents(None, QEvent.Type.DeferredDelete)
        assert card.findChildren(CoverPreviewDialog) == []
    assert not card._image.isNull()


def test_card_shutdown_releases_render_and_preview(editor):
    card = editor.share_card
    editor.select_all_share_lines()
    card.refresh()
    assert not card._image.isNull()
    card.shutdown()
    assert card._image.isNull()
    assert card.preview.image.isNull()
    assert not card._refresh_timer.isActive()
