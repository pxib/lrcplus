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

from core.plugin_manager import PluginManager
from core.radio_browser import Station, StationPage
from core.radio_history import load_station_history, remember_station
from core.settings import get_playback_settings, save_playback_settings, save_statistics_settings
from ui.main_window import Player
from ui.player_dialogs import PitchChangerDialog


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
            if self.path in {"/live.wav", "/icy.wav"}:
                self.send_response(200)
                self.send_header("Content-Type", "audio/wav")
                icy = self.path == "/icy.wav" and self.headers.get("Icy-MetaData") == "1"
                if icy:
                    self.send_header("icy-metaint", "32000")
                self.end_headers()
                try:
                    header = live_header
                    while not stop_live.is_set():
                        song = self.server.icy_title
                        if icy:
                            # Distinguish the new song in decoded audio as well as metadata.
                            sample_byte = b"\x10" if song.endswith(" - Next Song") else b"\0"
                            self.wfile.write(header + sample_byte * (32000 - len(header)))
                            title = f"StreamTitle='{song}';".encode()
                            size = (len(title) + 15) // 16
                            self.wfile.write(bytes([size]) + title.ljust(size * 16, b"\0"))
                        else:
                            self.wfile.write(header or payload[44:])
                        header = b""
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
    server.icy_title = "Test Artist - First Song"
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
        time.sleep(0.001)
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
        assert not window.statusBar().isHidden()
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
        assert load_station_history() == []

        original.write_bytes(b"")
        monkeypatch.setattr(window, "update_background_for_audio", lambda *_: None)
        window.load_audio(original)
        assert window._stream_url is None
        assert player.source().isLocalFile()
        assert window.seek_slider.isEnabled()
        assert not window.network_source_label.text()
        assert window.statusBar().isHidden()
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


def test_live_network_stream_disables_seeking_and_speed_changes(qapp, stream_server, monkeypatch, tmp_path):
    server, url = stream_server
    save_playback_settings({"speed_percent": 125, "pitch_linked_to_speed": True,
                            "resume_live_from_buffer": True})
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
        assert window.playback_speed == player.playbackRate() == 1.0
        wait_for(lambda: any(buffers), player)
        assert window.playback_speed == player.playbackRate() == 1.0
        window.handle_speed_change(150)
        window.handle_pitch_link_change(False)
        assert window.playback_speed == player.playbackRate() == 1.0
        assert window.playback_settings["speed_percent"] == 125
        assert window.pitch_linked_to_speed

        def check_pitch_dialog(dialog):
            assert dialog.speed_label.text() == "100%"
            assert not dialog.speed_slider.isEnabled()
            assert not dialog.link_pitch_checkbox.isEnabled()
            assert not dialog.link_pitch_checkbox.isChecked()
            assert dialog.slider.isEnabled()

        monkeypatch.setattr(PitchChangerDialog, "exec", check_pitch_dialog)
        window.open_pitch_options()
        assert window.duration_label.text() == "Live"
        assert window.network_source_label.text() == "127.0.0.1"
        assert window.network_source_label.width() > 0
        assert window.statusBar().isHidden()
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
        window.toggle_playback()
        assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        changes = QSignalSpy(player.sourceChanged)
        window.toggle_playback()
        assert player.isPlaying()
        assert changes.count() == 0
        window.stop_playback()
        assert player.source().isEmpty()
        assert label.cursor().shape() == Qt.CursorShape.PointingHandCursor
        assert label.toolTip() == "Click to show elapsed time"
        QTest.mouseClick(label, Qt.MouseButton.LeftButton)
        assert clicks.count() == 2
        audio_path = tmp_path / "local.wav"
        with wave.open(str(audio_path), "wb") as recording:
            recording.setparams((1, 2, 48000, 0, "NONE", "not compressed"))
            recording.writeframes(b"\0" * 48000 * 2)
        monkeypatch.setattr(window, "update_background_for_audio", lambda *_: None)
        window.load_audio(audio_path)
        assert window.playback_speed == player.playbackRate() == 1.25
        assert window._pitch_follows_speed()

        server.available = True
        monkeypatch.setattr(window, "_sleep_before_track_load", lambda: False)
        window.load_stream(url)
        wait_for(lambda: player.duration() > 0, player)
        assert window.playback_speed == player.playbackRate() == 1.25
        assert window._pitch_follows_speed()
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("keep_buffer", [False, True])
def test_live_lyrics_follow_resume_mode(qapp, stream_server, monkeypatch, keep_buffer):
    server, url = stream_server
    manager = PluginManager()
    searches = []
    manager.register_service("ytdlp_import.search", lambda query: searches.append(query) or True, "test")
    monkeypatch.setattr("ui.internet_radio.search_stations", lambda *_: StationPage((), False))
    window = Player(plugin_manager=manager)
    player = window.media_player
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    output = QAudioBufferOutput(window)
    player.setAudioBufferOutput(output)
    requests = []
    errors = []
    window._stream_lyrics._monitor.failed.connect(errors.append)

    def lookup(title, cancelled, *, artist):
        requests.append((artist, title))
        if title == "Second Song":
            return []
        if title == "First Song":
            return [{"trackName": title, "artistName": artist, "syncedLyrics": "[00:01.00]Lyrics for First Song"}]
        return [
            {"trackName": title, "artistName": artist, "plainLyrics": f"Lyrics for {title}"},
            {"trackName": title, "artistName": artist, "plainLyrics": f"Lyrics, for\n{title}!"},
        ]

    monkeypatch.setattr("core.stream_lyrics.lrclib.search", lookup)
    try:
        window.open_network_playback()
        dialog = window._network_playback_dialog
        dialog.tabs.setCurrentWidget(dialog.radio)
        download = dialog.radio.download_button
        option = dialog.auto_lyrics_checkbox
        assert not option.isChecked()
        assert not dialog.keep_live_buffer_checkbox.isChecked()
        option.click()
        if keep_buffer:
            dialog.keep_live_buffer_checkbox.click()
        assert window.playback_settings["radio_auto_lyrics"]
        window.load_stream(url.replace("/audio.wav", "/icy.wav"))
        wait_for(lambda: player.position() > 0, player)
        assert window._is_live_stream(), (player.duration(), player.source().toString(), player.isPlaying())
        assert window._stream_lyrics._monitor_cancel is not None
        wait_for(lambda: bool(window.lyric_lines) or bool(errors), player)
        assert not errors
        assert window.lyric_lines[0].text == "Lyrics for First Song"
        download.click()
        assert searches == ["Test Artist First Song"]
        status = window.stream_lyrics_status_label
        assert status.text() == "Test Artist — First Song · Plain lyrics"
        assert window.plain_lyrics_mode and window.current_lyrics_path is None
        assert window.current_line == -1
        seeks = QSignalSpy(player.seekStarting)
        window.jump_to_lyric_item(window.lyrics_list_widget.item(0))
        assert seeks.count() == 0
        assert get_playback_settings()["radio_auto_lyrics"]
        assert get_playback_settings()["resume_live_from_buffer"] == keep_buffer

        server.icy_title = "Test Artist - Second Song"
        wait_for(lambda: status.text() == "No confident lyrics match for Test Artist — Second Song.", player)
        assert not window.lyric_lines
        assert requests == [("Test Artist", "First Song"), ("Test Artist", "Second Song")]

        server.icy_title = "Test Artist - First Song"
        wait_for(lambda: status.text() == "Test Artist — First Song · Plain lyrics", player)
        window.toggle_playback()
        assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        paused_position = player.position()
        server.icy_title = "Test Artist - Third Song"
        QTest.qWait(400)
        assert window.lyric_lines[0].text == "Lyrics for First Song"
        assert status.text() == "Test Artist — First Song · Plain lyrics"
        download.click()
        assert searches == ["Test Artist First Song"]

        changes = QSignalSpy(player.sourceChanged)
        player.play()
        if keep_buffer:
            assert changes.count() == 0
            assert not window.go_live_button.isHidden()
            assert window.duration_label.text() == "Delayed"
            assert "Choose Go Live" in status.text()
            assert not window.lyric_lines
            wait_for(lambda: player.position() > paused_position, player)
            assert window._stream_lyrics._monitor_cancel is None
            assert requests == [("Test Artist", "First Song"), ("Test Artist", "Second Song")]
            download.click()
            assert "Choose Go Live" in dialog.radio.status.text()
            assert searches == ["Test Artist First Song"]
            window.go_live_button.click()
        assert changes.count() == 2
        assert changes.at(0)[0].isEmpty()
        assert changes.at(1)[0] == window._stream_url
        assert window.go_live_button.isHidden()
        assert not window.lyric_lines
        wait_for(lambda: status.text() == "Test Artist — Third Song · Plain lyrics", player)
        assert window.lyric_lines[0].text == "Lyrics for Third Song"
        download.click()
        assert searches == ["Test Artist First Song", "Test Artist Third Song"]
        option.click()
        assert not window.lyric_lines
        assert status.isHidden()
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("tracking", [False, True])
def test_radio_history_and_statistics_follow_actual_playback(qapp, stream_server, monkeypatch, tracking):
    server, url = stream_server
    save_statistics_settings({"enabled": tracking})
    window = Player()
    player = window.media_player
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    output = QAudioBufferOutput(window)
    player.setAudioBufferOutput(output)
    station = Station("Test Station", url, "Vietnam", station_id="test-station")
    try:
        window.open_network_playback()
        history = window._network_playback_dialog.history
        window.play_radio_station(station)
        assert window.network_source_label.text() == station.name
        wait_for(lambda: window._stream_error, player)
        assert not load_station_history()
        assert history.results.topLevelItemCount() == 0

        server.available = True
        window.toggle_playback()
        wait_for(lambda: history.results.topLevelItemCount() == 1, player)
        assert history.results.topLevelItem(0).text(0) == station.name
        wait_for(lambda: player.source().isEmpty(), player)
        result = window.statistics_store.snapshot().result(timeout=5)
        assert result["songs"] == []
        assert bool(result["stations"]) is tracking
        if tracking:
            assert result["stations"][0]["listened_ms"] > 0
            assert result["stations"][0]["name"] == station.name
            window.statistics_page._apply(result)
            assert window.statistics_page.tables["stations"].item(0, 0).text() == station.name

        refreshed = Station("Updated Station", url + "?new", station_id=station.station_id)
        remember_station(refreshed)
        assert [entry[0] for entry in load_station_history()] == [refreshed]
        window.station_history_changed.emit()
        window._network_playback_dialog.tabs.setCurrentWidget(history)
        history.play_button.click()
        assert player.source().toString() == refreshed.url
    finally:
        window.close()
        window.deleteLater()


@pytest.mark.parametrize("keep_buffer", [False, True])
def test_radio_can_search_yt_dlp_from_metadata_without_auto_lyrics(qapp, stream_server, monkeypatch, keep_buffer):
    server, url = stream_server
    save_playback_settings({"resume_live_from_buffer": keep_buffer})
    manager = PluginManager()
    searches = []
    manager.register_service("ytdlp_import.search", lambda query: searches.append(query) or True, "test")
    monkeypatch.setattr("ui.internet_radio.search_stations", lambda *_: StationPage((), False))
    window = Player(plugin_manager=manager)
    player = window.media_player
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    output = QAudioBufferOutput(window)
    player.setAudioBufferOutput(output)
    decoded = []
    output.audioBufferReceived.connect(
        lambda buffer: decoded.append(bytes(buffer.constData()[:2])) if buffer.byteCount() else None
    )
    try:
        window.load_stream(url.replace("/audio.wav", "/icy.wav"))
        wait_for(lambda: player.position() > 0, player)
        window.open_network_playback()
        dialog = window._network_playback_dialog
        assert not dialog.auto_lyrics_checkbox.isChecked()
        dialog.tabs.setCurrentWidget(dialog.radio)
        # The button uses the playing stream even when the radio page has no selection.
        dialog.radio.download_button.click()
        wait_for(lambda: bool(searches), player)
        assert searches == ["Test Artist First Song"]
        assert dialog.radio.download_button.isEnabled()
        assert window._stream_lyrics._monitor_cancel is None

        window.toggle_playback()
        assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
        server.icy_title = "Test Artist - Next Song"
        decoded.clear()
        changes = QSignalSpy(player.sourceChanged)
        window.toggle_playback()
        if keep_buffer:
            assert changes.count() == 0
            wait_for(lambda: bool(decoded), player)
            assert decoded[0] == b"\0\0"
            dialog.radio.download_button.click()
            assert "Choose Go Live" in dialog.radio.status.text()
            assert searches == ["Test Artist First Song"]
            decoded.clear()
            window.go_live_button.click()
        assert changes.count() == 2
        wait_for(lambda: player.position() > 0, player)
        # A buffer queued before the reconnect can still reach the observer.
        decoded.clear()
        wait_for(lambda: bool(decoded), player)
        assert all(sample == b"\x10\x10" for sample in decoded)
        dialog.radio.download_button.click()
        wait_for(lambda: len(searches) == 2, player)
        assert searches[-1] == "Test Artist Next Song"
    finally:
        window.close()
        window.deleteLater()
