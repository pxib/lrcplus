from dataclasses import replace
import threading
from types import SimpleNamespace

import pytest
from PySide6.QtCore import QBuffer, QIODevice
from PySide6.QtGui import QColor, QImage
from PySide6.QtTest import QTest

import ui.lyrics_card_artwork as artwork
from ui.lyrics_card_artwork import AlbumCoverResult, CardAlbumArt, load_album_cover
from ui.lyrics_card_renderer import CARD_SIZES, CARD_TEMPLATES, CardStyle, render_lyric_card


def cover_image():
    image = QImage(160, 80, QImage.Format.Format_RGB32)
    image.fill(QColor("#12ed45"))
    return image


def cover_bytes():
    buffer = QBuffer()
    buffer.open(QIODevice.OpenModeFlag.WriteOnly)
    cover_image().save(buffer, "PNG")
    return bytes(buffer.data())


def wait_until(predicate):
    for _ in range(200):
        if predicate():
            return
        QTest.qWait(10)
    assert predicate()


def test_resolved_rpc_cover_downloads_without_starting_search_or_upload(tmp_path, monkeypatch):
    calls = []
    class Response:
        def __enter__(self):
            return self
        def __exit__(self, *_):
            pass
        def raise_for_status(self):
            pass
        def iter_content(self, _):
            yield cover_bytes()
    monkeypatch.setattr(artwork.requests, "get", lambda url, **kwargs: (calls.append(url) or Response()))
    monkeypatch.setattr(artwork, "extract_embedded_cover", lambda *_: pytest.fail("Did not need embedded fallback"))
    result = load_album_cover(tmp_path / "song.mp3", lambda path: {"url": "https://archive.example/cover.png"})
    assert calls == ["https://archive.example/cover.png"]
    assert not result.image.isNull()
    assert "Discord" in result.message


@pytest.mark.parametrize("online_failure", [False, True])
def test_embedded_cover_works_without_plugin_and_when_remote_cover_fails(tmp_path, monkeypatch, online_failure):
    monkeypatch.setattr(artwork, "extract_embedded_cover", lambda *_: cover_bytes())
    def download(*_):
        raise artwork.requests.Timeout("Expired cover URL")
    monkeypatch.setattr(artwork, "_download_cover", download)
    provider = (lambda _: {"url": "https://example.test/cover.png"}) if online_failure else None
    result = load_album_cover(tmp_path / "song.mp3", provider)
    assert not result.image.isNull()
    assert "embedded" in result.message


def test_pending_artwork_and_missing_covers_are_distinct(tmp_path, monkeypatch):
    monkeypatch.setattr(artwork, "extract_embedded_cover", lambda *_: None)
    pending = load_album_cover(tmp_path / "song.mp3", lambda _: {"pending": True})
    assert pending.pending
    assert pending.image.isNull()
    missing = load_album_cover(tmp_path / "song.mp3")
    assert not missing.pending
    assert "No album cover" in missing.message


def test_album_art_loads_off_ui_thread_and_discards_old_track_results(qapp, tmp_path, monkeypatch):
    first, second = tmp_path / "first.mp3", tmp_path / "second.mp3"
    first.touch()
    second.touch()
    release = threading.Event()
    entered = threading.Event()
    threads = []
    def load(path, provider):
        threads.append(threading.current_thread())
        if path == first:
            entered.set()
            release.wait(3)
        return AlbumCoverResult(cover_image(), path.name)
    monkeypatch.setattr(artwork, "load_album_cover", load)
    loader = CardAlbumArt()
    loader.configure(first, None, True)
    wait_until(entered.is_set)
    loader.configure(second, None, True)
    wait_until(lambda: loader.message == second.name)
    release.set()
    QTest.qWait(50)
    assert loader.message == second.name
    assert all(thread is not threading.current_thread() for thread in threads)
    loader.shutdown()


def test_pending_resolution_retries_after_hidden_card_is_shown(qapp, tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.touch()
    calls = []
    def load(*_):
        calls.append(True)
        return AlbumCoverResult(pending=True) if len(calls) == 1 else AlbumCoverResult(cover_image(), "Ready")
    monkeypatch.setattr(artwork, "load_album_cover", load)
    loader = CardAlbumArt()
    loader._retry_timer.setInterval(10)
    loader.pause()
    loader.configure(path, None, True)
    assert not calls
    loader.resume()
    wait_until(lambda: not loader.image.isNull())
    assert len(calls) == 2
    loader.shutdown()


def test_shutdown_discards_late_cover_results(qapp, tmp_path, monkeypatch):
    path = tmp_path / "song.mp3"
    path.touch()
    release, finished = threading.Event(), threading.Event()
    def load(*_):
        release.wait(3)
        finished.set()
        return AlbumCoverResult(cover_image(), "Late cover")
    monkeypatch.setattr(artwork, "load_album_cover", load)
    loader = CardAlbumArt()
    loader.configure(path, None, True)
    loader.shutdown()
    release.set()
    wait_until(finished.is_set)
    QTest.qWait(30)
    assert loader.image.isNull()
    assert not loader.loading


@pytest.mark.parametrize("template", CARD_TEMPLATES)
@pytest.mark.parametrize("size", CARD_SIZES.values())
def test_cover_preserves_aspect_ratio_and_stays_separate_from_lyrics(qapp, template, size):
    import numpy as np
    style = CardStyle(width=size[0], height=size[1], template=template, show_album_cover=True,
                      cover_size=240, padding=160, vertical_position=100, line_spacing=160,
                      title="Night Notes", credit="Artist",
                      colors=("#000000", "#000000", "#ff00ff", "#ffffff", "#ffffff", "#888888"))
    image = render_lyric_card("A lyric line\nSecond lyric\nThird lyric", style, cover_image())
    rgba = image.convertToFormat(QImage.Format.Format_RGBA8888)
    pixels = np.frombuffer(rgba.constBits(), dtype=np.uint8).reshape(size[1], size[0], 4)
    cy, cx = np.where((pixels[:, :, 0] == 18) & (pixels[:, :, 1] == 237) & (pixels[:, :, 2] == 69))
    ly, lx = np.where((pixels[:, :, 0] == 255) & (pixels[:, :, 1] == 0) & (pixels[:, :, 2] == 255))
    assert len(cx) and len(lx)
    assert abs((cx.max() - cx.min() + 1) / (cy.max() - cy.min() + 1) - 2) < .02
    assert cx.min() >= 0 and cx.max() < style.width
    assert cy.min() >= style.padding and cy.max() < style.height - style.padding
    assert cx.max() < lx.min() or cy.max() < ly.min() or cy.min() > ly.max()
    assert render_lyric_card("A lyric line", style) == render_lyric_card("A lyric line", replace(style, show_album_cover=False))


def test_card_uses_editor_song_and_exports_loaded_cover(editor, tmp_path, monkeypatch):
    from ui import lyrics_share_card as card_module
    attached, originally_playing, now_playing = (tmp_path / name for name in ("attached.mp3", "original.mp3", "next.mp3"))
    for path in (attached, originally_playing, now_playing):
        path.touch()
    editor.attached_song_path = str(attached)
    editor._editing_song_path = str(originally_playing)
    editor.player = SimpleNamespace(current_audio_path=now_playing)
    calls = []
    def load(path, provider):
        calls.append(path)
        return AlbumCoverResult(cover_image(), "Ready")
    monkeypatch.setattr(artwork, "load_album_cover", load)
    editor.show()
    editor.tabs.setCurrentWidget(editor.share_page)
    editor.share_tabs.setCurrentWidget(editor.share_card)
    editor.select_all_share_lines()
    card = editor.share_card
    card.show_album_cover.setChecked(True)
    wait_until(lambda: not card.album_art.image.isNull())
    card.refresh()
    assert calls == [attached]
    assert card.save_button.isEnabled()
    output = tmp_path / "card.png"
    monkeypatch.setattr(card_module.QFileDialog, "getSaveFileName", lambda *_: (str(output), ""))
    card.export_image()
    assert QImage(str(output)).convertToFormat(card._image.format()) == card._image
    assert card._image != render_lyric_card("Hello\nWorld", replace(card.current_style(), show_album_cover=False))


def test_cover_preference_is_saved_without_reusing_the_previous_song_cover():
    from ui.lyrics_card_presets import style_from_preset
    style = style_from_preset({"show_album_cover": True, "cover_size": 220, "cover_path": "previous-song.png"})
    assert style.show_album_cover
    assert style.cover_size == 220
    assert not hasattr(style, "cover_path")
