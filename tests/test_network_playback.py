from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from io import BytesIO
from threading import Event, Thread
import time
import wave

import pytest
from PySide6.QtCore import Qt
from PySide6.QtGui import QColor
from PySide6.QtMultimedia import QAudioBufferOutput, QMediaPlayer
from PySide6.QtTest import QSignalSpy, QTest

from core.settings import save_playback_settings
from ui.main_window import Player


@pytest.fixture
def stream_server():
    audio = BytesIO()
    with wave.open(audio, "wb") as recording:
        recording.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
        recording.writeframes(b"\0" * 48000 * 2)
    payload = audio.getvalue()
    # WAV sent to a pipe uses unknown RIFF and data lengths.
    live_header = payload[:4] + b"\xff" * 4 + payload[8:40] + b"\xff" * 4
    stop_live = Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            if self.path == "/live.wav":
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                self.end_headers()
                try:
                    self.wfile.write(live_header)
                    while not stop_live.is_set():
                        self.wfile.write(payload[44:])
                        stop_live.wait(0.1)
                except ConnectionError:
                    pass
                return
            if not self.server.available:
                self.send_error(404)
                return
            self.send_response(200)
            self.send_header("Content-Type", "audio/wav")
            self.send_header("Content-Length", str(len(payload)))
            self.end_headers()
            self.wfile.write(payload)

        def log_message(self, *_args):
            pass

    try:
        server = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
    except PermissionError:
        pytest.skip("Loopback server is unavailable")
    server.available = False
    thread = Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield server, f"http://127.0.0.1:{server.server_port}/audio.wav"
    finally:
        stop_live.set()
        server.shutdown()
        server.server_close()
        thread.join()


def wait_for(predicate, player):
    deadline = time.monotonic() + 6
    while not predicate() and time.monotonic() < deadline:
        QTest.qWait(10)
    assert predicate(), player.errorString()


def test_network_stream_can_retry_decode_and_reconnect(qapp, stream_server, monkeypatch, tmp_path):
    server, url = stream_server
    save_playback_settings({"play_mode": "playlist", "after_playback": "repeat_track"})
    window = Player()
    player = window.media_player
    # Observe decoded audio directly, including on hosts without an output device.
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    output = QAudioBufferOutput(window)
    player.setAudioBufferOutput(output)
    buffers = []
    output.audioBufferReceived.connect(lambda buffer: buffers.append(buffer.byteCount()))
    original = tmp_path / "queued.wav"
    window.playlist = [original]
    monkeypatch.setattr(window, "_load_playback_index", lambda *_: pytest.fail("Stream advanced the local queue"))
    try:
        action = next(action for action in window.open_menu.actions() if "Network Playback" in action.text())
        action.trigger()
        dialog = window._network_playback_dialog
        dialog.url.setText("file:///track.mp3")
        dialog.play_button.click()
        assert dialog.isVisible()
        assert "HTTP or HTTPS" in dialog.url_status.text()
        dialog.url.setText(url)
        QTest.keyClick(dialog.url, Qt.Key.Key_Return)
        assert window._network_playback_dialog is None
        wait_for(lambda: window._stream_error, player)
        assert "Press Play to retry" in window.statusBar().currentMessage()
        server.available = True
        window.toggle_playback()
        wait_for(lambda: any(buffers), player)
        assert not window._stream_error
        assert window.current_audio_path is None
        assert window.playlist == [original]
        assert not window.waveform_widget.isVisible()
        assert not window.previous_button.isEnabled()
        window.clear_queue()
        window.enqueue_tracks([original])
        assert player.source() == window._stream_url
        assert window.playlist == [original]

        window.stop_playback()
        assert player.source().isEmpty()
        assert not window.seek_slider.isEnabled()
        buffers.clear()
        window.toggle_playback()
        wait_for(lambda: any(buffers), player)
        wait_for(lambda: player.source().isEmpty(), player)
        assert "Network stream ended" in window.statusBar().currentMessage()

        original.write_bytes(b"")
        monkeypatch.setattr(window, "update_background_for_audio", lambda *_: None)
        window.load_audio(original)
        assert window._stream_url is None
        assert player.source().isLocalFile()
        assert window.seek_slider.isEnabled()
    finally:
        window.close()
        window.deleteLater()


def test_network_stream_url_requires_http_and_an_explicit_host(qapp):
    window = Player()
    try:
        for url in ("track.mp3", "file:///track.mp3", "https:///track.mp3",
                    "https://user:password@example.org/radio", "https://example.org/a b"):
            with pytest.raises(ValueError):
                window.load_stream(url)
        assert window.media_player.mediaStatus() == QMediaPlayer.MediaStatus.NoMedia
    finally:
        window.close()
        window.deleteLater()


def test_live_network_stream_disables_seeking(qapp, stream_server, monkeypatch):
    _, url = stream_server
    window = Player()
    player = window.media_player
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    output = QAudioBufferOutput(window)
    player.setAudioBufferOutput(output)
    buffers = []
    output.audioBufferReceived.connect(lambda buffer: buffers.append(buffer.byteCount()))
    try:
        window.show()
        label = window.current_time_label
        clicks = QSignalSpy(label.clicked)
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert clicks.count() == 1
        assert window.show_remaining_time
        window.load_stream(url.replace("/audio.wav", "/live.wav"))
        wait_for(lambda: any(buffers), player)
        assert window.duration_label.text() == "Live"
        assert not window.seek_slider.isEnabled()
        assert not window.forward_button.isEnabled()
        assert not window.rewind_button.isEnabled()
        assert not window._sleep_audio_playing()
        image = window.seek_slider.grab().toImage()
        center = image.height() // 2
        red = QColor("#ef4444")
        assert image.pixelColor(image.width() // 2, center) == red
        # Both the middle of the bar and the old handle area use a thin line.
        handle_x = round(8 * image.devicePixelRatio())
        above = center - round(5 * image.devicePixelRatio())
        assert image.pixelColor(handle_x, center) == red
        assert image.pixelColor(handle_x, above) == image.pixelColor(image.width() // 2, above)
        assert label.cursor().shape() == Qt.CursorShape.ArrowCursor
        assert label.toolTip() == ""
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert clicks.count() == 1
        assert window.show_remaining_time
        assert not label.text().startswith("-")
        window.stop_playback()
        assert player.source().isEmpty()
        assert label.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert label.toolTip() == "Click to show elapsed time"
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert clicks.count() == 2
    finally:
        window.close()
        window.deleteLater()
