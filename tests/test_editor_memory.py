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


def test_closing_editor_releases_cached_backgrounds_and_preserves_other_previews(
    make_lyrics_editor, tmp_path,
):
    import weakref
    from PySide6.QtGui import QColor, QImage
    from ui import lyrics_card_renderer as renderer

    first, second = make_lyrics_editor(), make_lyrics_editor()
    first_card, second_card = first.share_card, second.share_card
    background = QImage(80, 60, QImage.Format.Format_RGB32)
    background.fill(QColor("#234567"))
    path = tmp_path / "background.png"
    assert background.save(str(path))
    for editor, card in ((first, first_card), (second, second_card)):
        editor.select_all_share_lines()
        card.image_path_edit.setText(str(path))
        card.background_mode.setCurrentText("Image")
        card.watermark_enabled.setChecked(True)
        card.refresh()
    # Keep only weak references to the cached native images. Closing either
    # workspace should release them even while another preview remains alive.
    stat = path.stat()
    cached_background = weakref.ref(renderer._image_background(
        str(path), stat.st_mtime_ns, stat.st_size, 1080, 1080, 100, 50, 50, 0))
    logo_stat = renderer.LOGO_PATH.stat()
    cached_logo = weakref.ref(renderer._watermark_logo(
        str(renderer.LOGO_PATH), logo_stat.st_mtime_ns, logo_stat.st_size))
    assert cached_background() is not None and cached_logo() is not None
    surviving_preview = QImage(second_card.preview.image)

    first.document_dirty = False
    first.close()
    assert cached_background() is None and cached_logo() is None
    assert second_card.preview.image == surviving_preview
    second_card._render_key = None
    second_card.refresh()
    assert second_card.preview.image == surviving_preview


def test_editor_starts_only_the_waveform_decoder_being_shown(editor, qapp, monkeypatch):
    from lyrics.waveform import WaveformWidget

    started = []
    monkeypatch.setattr("lyrics.waveform.find_ffmpeg", lambda: "ffmpeg")
    monkeypatch.setattr(WaveformWidget, "_start_audio_worker", lambda self: started.append(self))
    editor.lyrics_waveform.load_audio("song.mp3")
    editor.karaoke_waveform.load_audio("song.mp3")
    assert started == []
    editor.show()
    editor.lyrics_transport_stack.setCurrentWidget(editor.lyrics_waveform)
    qapp.processEvents()
    assert started == [editor.lyrics_waveform]
    karaoke_index = next(index for index in range(editor.tabs.count())
                         if editor.tabs.tabText(index) == "Karaoke")
    editor.tabs.setCurrentIndex(karaoke_index)
    editor.karaoke_transport_stack.setCurrentWidget(editor.karaoke_waveform)
    qapp.processEvents()
    assert started == [editor.lyrics_waveform, editor.karaoke_waveform]
