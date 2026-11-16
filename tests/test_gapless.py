from pathlib import Path
import shutil
import struct
import time
import wave

import pytest
from PySide6.QtCore import QUrl
from PySide6.QtMultimedia import QAudioFormat, QAudioOutput, QMediaPlayer

from core.gapless import GaplessMediaPlayer, TrackDecoder
from core.replaygain import ReplayGain


def audio_format():
    fmt = QAudioFormat()
    fmt.setSampleRate(48000)
    fmt.setChannelCount(1)
    fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
    return fmt


class Decoder:
    def __init__(self, executable, path, fmt, position=0):
        self.path = str(path)
        self.position = position
        self.duration = 100
        self.tags = ReplayGain()
        self.error = None
        self.closed = False
        value = 0.25 if Path(self.path).name == "first.wav" else -0.5
        self.data = struct.pack("<4800f", *([value] * 4800))

    def take(self, count):
        data, self.data = self.data[:count], self.data[count:]
        return data

    @property
    def exhausted(self):
        return not self.data

    def close(self):
        self.closed = True


class Sink:
    instances = []

    def __init__(self, device, fmt, parent):
        self.fmt = fmt
        self.written = bytearray()
        self.time = 0
        self.free = 1000000
        self.max_write = 1000000
        self.suspended = False
        self.stopped = False
        self.instances.append(self)

    def setBufferSize(self, count): pass
    def start(self): return self
    def bytesFree(self): return self.free
    def processedUSecs(self): return self.time
    def setVolume(self, value): pass
    def resume(self): self.suspended = False
    def suspend(self): self.suspended = True
    def stop(self): self.stopped = True
    def reset(self): pass
    def deleteLater(self): pass

    def write(self, data):
        count = min(len(data), self.max_write)
        self.written.extend(data[:count])
        return count


@pytest.fixture
def transport(qapp):
    Sink.instances = []
    player = GaplessMediaPlayer(sink_factory=Sink, decoder_factory=Decoder)
    output = QAudioOutput()
    player.setAudioOutput(output)
    # Inject the stream format independently of a physical sound device.
    player._gapless_active = player._gapless_enabled = True
    player._url = QUrl.fromLocalFile("first.wav")
    player._format = audio_format()
    player._sink_device = output.device()
    player._decoder = Decoder(None, "first.wav", player._format)
    player._state = QMediaPlayer.PlaybackState.PlayingState
    player.next_path = lambda: "second.wav"
    try:
        yield player
    finally:
        player.stop()
        player.deleteLater()
        output.deleteLater()
        qapp.processEvents()


def test_continuity_and_audible_track_boundary_use_single_sink(transport):
    player = transport
    sources = []

    def advance(status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            sources.append(player._sink.processedUSecs())
            player.stop()  # load_audio's stop must preserve this handoff.
            player.setSource(QUrl.fromLocalFile("second.wav"))
            player.next_path = lambda: None
            player.play()

    player.mediaStatusChanged.connect(advance)
    player._pump()
    sink = player._sink
    assert sources == []  # UI stays on the first track while its tail is audible.
    assert player.position() == 0
    assert len(sink.written) == 2 * 4800 * 4
    expected = struct.pack("<9600f", *([0.25] * 4800 + [-0.5] * 4800))
    assert bytes(sink.written) == expected
    sink.time = 100000
    player._pump()
    assert sources == [100000]
    assert player.source().toLocalFile() == "second.wav"
    assert player.position() == 0
    assert player._sink is sink
    assert len(Sink.instances) == 1


def test_partial_writes_do_not_drop_or_duplicate_samples(transport):
    player = transport
    player.next_path = lambda: None
    player._pump()
    # Start again with a sink that accepts only a few frames per write.
    player.setPosition(0)
    player._sink = Sink(None, player._format, player)
    player._io = player._sink
    player._sink.max_write = 12
    for _ in range(1700):
        player._pump()
    assert bytes(player._sink.written) == struct.pack("<4800f", *([0.25] * 4800))


def test_pause_resume_seek_and_stop_clean_up(transport):
    player = transport
    player._pump()
    first, successor, sink = player._decoder, player._next_decoder, player._sink
    player.pause()
    assert sink.suspended
    assert player.playbackState() == QMediaPlayer.PlaybackState.PausedState
    player.play()
    assert not sink.suspended
    player.setPosition(42)
    assert first.closed and successor.closed and sink.stopped
    assert player.position() == 42
    assert player._decoder.position == 42
    player.stop()
    assert player._decoder is None and player._next_decoder is None
    assert player.position() == 0


def test_queue_change_discards_prefed_successor(transport):
    player = transport
    player._pump()
    old = player._next_decoder
    sink = player._sink
    player.next_path = lambda: "replacement.wav"
    player.invalidate_preload()
    assert old.closed and sink.stopped
    assert player._next_decoder is None
    player._pump()
    assert player._next_decoder.path == "replacement.wav"


def test_per_track_replaygain_is_applied_once(transport):
    player = transport
    player._decoder.tags = ReplayGain(track_gain=-6)
    player._rg_settings = ("track", 0, True)
    player._volume = 0.5
    player._pump()
    samples = [x[0] for x in struct.iter_unpack("<f", player._sink.written)]
    assert samples[:4800] == pytest.approx([0.25 * 0.5 * 10 ** (-6 / 20)] * 4800)
    assert samples[4800:] == [-0.25] * 4800


def test_final_track_tail_finishes_before_end_notification(transport):
    player = transport
    player.next_path = lambda: None
    events = []
    player.mediaStatusChanged.connect(events.append)
    player._pump()
    sink = player._sink
    sink.time = 99999
    player._pump()
    assert QMediaPlayer.MediaStatus.EndOfMedia not in events
    assert player.isPlaying()
    sink.time = 100000
    player._pump()
    assert events[-1] == QMediaPlayer.MediaStatus.EndOfMedia
    assert not player.isPlaying()
    assert sink.stopped
    assert player._decoder is None


def test_decoder_error_returns_to_native_and_reports_reason(transport):
    player = transport
    errors = []
    player.gaplessFallback.connect(errors.append)
    decoder = player._decoder
    decoder.error = "test decode failure"
    player._pump()
    assert not player._gapless_active
    assert decoder.closed
    assert errors == ["test decode failure"]


def test_missing_ffmpeg_retains_native_transport(qapp, monkeypatch):
    monkeypatch.setattr("core.gapless.shutil.which", lambda name: None)
    player = GaplessMediaPlayer()
    try:
        assert not player.configure_gapless(True, 1, ("off", 0, True))
        assert not player._gapless_enabled
        assert not player._gapless_active
    finally:
        player.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("mode", ["next_track", "repeat_track", "repeat_all", "shuffle", "priority", "duplicate", "counted", "counted_crossfade", "remembered_ab"])
def test_player_handoff_preserves_queue_rules_and_sink(qapp, monkeypatch, tmp_path, mode):
    from core.settings import save_playback_settings
    from ui.main_window import Player
    from types import SimpleNamespace

    monkeypatch.setattr("core.gapless.shutil.which", lambda name: "ffmpeg")
    # Exercise the same device-free environment as the hosted CI runner.
    monkeypatch.setattr("ui.audio_devices.QMediaDevices.audioOutputs", lambda: [])
    save_playback_settings({"play_mode": "playlist", "gapless_playback": True,
                            "crossfade_seconds": 2 if mode == "counted_crossfade" else 0})
    window = Player()
    player = window.media_player
    player._decoder_factory = Decoder
    player._sink_factory = Sink
    device = SimpleNamespace(preferredFormat=audio_format, isFormatSupported=lambda fmt: True)
    monkeypatch.setattr(window.audio_output, "device", lambda: device)
    # The fake sink supplies output even though the host has no audio device.
    monkeypatch.setattr(window, "_audio_output_unavailable", False)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    monkeypatch.setattr(window, "update_background_for_audio", lambda *args: None)
    paths = [tmp_path / name for name in ("first.wav", "second.wav", "third.wav")]
    for path in paths:
        path.touch()
    window.playlist = paths
    window.current_index = 0
    expected = paths[1]
    if mode == "repeat_track":
        window.playback_settings["after_playback"] = "repeat_track"
        expected = paths[0]
    elif mode == "repeat_all":
        window.current_index = 2
        window.playback_settings["after_playback"] = "repeat_all"
        expected = paths[0]
    elif mode == "shuffle":
        window.shuffle_enabled = True
        monkeypatch.setattr("core.audio_controller.random.choice", lambda candidates: candidates[-1])
        expected = paths[2]
    elif mode == "priority":
        window._priority_queue_indices = [2]
        expected = paths[2]
    elif mode == "duplicate":
        window.playlist = [paths[0], paths[0], paths[1]]
        expected = paths[0]
    elif mode == "remembered_ab":
        from core.playlists import path_key
        window._ab_loop_memory[path_key(paths[1])] = (10, 50)
    try:
        window.load_audio(window.playlist[window.current_index])
        if mode.startswith("counted"):
            expected = paths[0]
            window._start_counted_repeat(3)
        player.play()
        player._pump()
        if mode.startswith("counted"):
            assert player._next_decoder is None
        sink = player._sink
        assert sink is not None
        sink.time = 100000
        player._pump()
        assert window.current_audio_path == expected
        if mode.startswith("counted"):
            assert window._repeat_remaining == 2
        else:
            assert player._sink is sink
        assert Path(player.source().toLocalFile()) == expected
        if mode == "remembered_ab":
            assert (window._ab_start, window._ab_end) == (10, 50)
            assert window._ab_timer.isActive()
            assert player._next_decoder is None
        if mode == "duplicate":
            assert window.current_index == 1
        if mode == "priority":
            assert not window._priority_queue_indices
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.fixture
def sleep_window(transport, qapp, monkeypatch):
    from ui.main_window import Player

    monkeypatch.setattr(Player, "_automatic_update_check", lambda self: None)
    window = Player()
    original = window.media_player
    window.media_player = transport
    window._audio_output_unavailable = False
    window.current_audio_path = Path("first.wav").resolve()
    window.playlist = [window.current_audio_path, Path("second.wav").resolve()]
    window.current_index = 0
    window.playback_settings["play_mode"] = "playlist"
    now = [0.0]
    window.sleep_timer._clock = lambda: now[0]
    transport.before_pump = window.sleep_timer.check
    transport.next_path = window._gapless_next_path
    transport.mediaStatusChanged.connect(window.handle_media_status)
    transport.playbackStateChanged.connect(window._sleep_playback_state_changed)
    transport.mediaStatusChanged.connect(window._statistics_state_changed)
    transport.playbackStateChanged.connect(window._statistics_state_changed)
    transport.seekStarting.connect(window._statistics_sample)
    transport.seekFinished.connect(window._statistics_rebase)
    qapp.processEvents()
    try:
        yield window, transport, now
    finally:
        transport.before_pump = lambda: None
        transport.next_path = lambda: None
        transport.mediaStatusChanged.disconnect(window.handle_media_status)
        transport.playbackStateChanged.disconnect(window._sleep_playback_state_changed)
        transport.mediaStatusChanged.disconnect(window._statistics_state_changed)
        transport.playbackStateChanged.disconnect(window._statistics_state_changed)
        transport.seekStarting.disconnect(window._statistics_sample)
        transport.seekFinished.disconnect(window._statistics_rebase)
        window.media_player = original
        window.close()
        window.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("late", [False, True])
@pytest.mark.parametrize("after", ["repeat_track", "repeat_all", "next_track"])
def test_sleep_finish_blocks_gapless_successor_including_already_buffered_audio(sleep_window, late, after):
    window, player, now = sleep_window
    window.playback_settings["after_playback"] = after
    window.shuffle_enabled = True
    window._priority_queue_indices = [1]
    if late:
        player._pump()
        old_sink = player._sink
        assert player._boundary_us is not None
        assert len(old_sink.written) > 4800 * 4
    window.sleep_timer.finish_current_track()
    if late:
        assert old_sink.stopped
        assert player._next_decoder is None
    player._pump()
    sink = player._sink
    assert bytes(sink.written) == struct.pack("<4800f", *([0.25] * 4800))
    sink.time = 100000
    player._pump()
    assert not player.isPlaying()
    assert window.current_audio_path.name == "first.wav"
    assert not window.sleep_timer.active
    assert window._sleep_expired
    assert window.playback_settings["after_playback"] == after
    assert window.shuffle_enabled
    assert window._priority_queue_indices == [1]


def test_immediate_sleep_expiry_wins_over_a_buffered_gapless_handoff(sleep_window):
    window, player, now = sleep_window
    window.sleep_timer.start(1)
    player._pump()
    sink = player._sink
    assert player._boundary_us is not None
    now[0] = 59.9
    window.sleep_timer.check()
    now[0] = 60
    sink.time = 100000
    player._pump()
    assert sink.suspended
    assert not player.isPlaying()
    assert window.current_audio_path.name == "first.wav"
    assert not window.sleep_timer.active


def test_finish_action_invalidates_buffered_successor_at_countdown_expiry(sleep_window):
    window, player, now = sleep_window
    window.sleep_timer.start(1, "finish_track")
    player._pump()
    old_sink = player._sink
    now[0] = 59.9
    window.sleep_timer.check()
    now[0] = 60
    player._pump()
    assert old_sink.stopped
    assert window.sleep_timer.finishing
    sink = player._sink
    assert bytes(sink.written) == struct.pack("<4800f", *([0.25] * 4800))
    sink.time = 100000
    player._pump()
    assert not player.isPlaying()
    assert window.current_audio_path.name == "first.wav"
    assert not window.sleep_timer.active


@pytest.mark.parametrize("countdown", [True, False])
def test_finish_timer_at_exact_gapless_boundary_pauses_without_seeking_past_eof(sleep_window, countdown):
    window, player, now = sleep_window
    if countdown:
        window.sleep_timer.start(1, "finish_track")
    player._pump()
    sink = player._sink
    decoder = player._decoder
    sink.time = 100000
    if countdown:
        now[0] = 59.9
        window.sleep_timer.check()
        now[0] = 60
        player._pump()
    else:
        window._sleep_at_end_of_song()
    assert sink.suspended
    assert not sink.stopped
    assert player._decoder is decoder
    assert window.current_audio_path.name == "first.wav"
    assert not window.sleep_timer.active
    assert window._sleep_expired


@pytest.mark.skipif(not shutil.which("ffmpeg"), reason="FFmpeg not installed")
def test_real_decoder_bounds_memory_preserves_samples_and_cancels(tmp_path):
    path = tmp_path / "tone.wav"
    count = 200000
    with wave.open(str(path), "wb") as stream:
        stream.setparams((1, 2, 48000, count, "NONE", "not compressed"))
        stream.writeframes(struct.pack("<" + str(count) + "h", *([8192] * count)))
    decoder = TrackDecoder(shutil.which("ffmpeg"), path, audio_format())
    try:
        deadline = time.monotonic() + 5
        while decoder._size < decoder.LIMIT and time.monotonic() < deadline:
            time.sleep(0.005)
        assert decoder._size == decoder.LIMIT
        assert decoder.error is None
        result = bytearray()
        while not decoder.exhausted and time.monotonic() < deadline:
            result.extend(decoder.take(16384))
            time.sleep(0.001)
        assert len(result) == count * 4
        assert all(sample == (0.25,) for sample in struct.iter_unpack("<f", result))
        assert decoder.duration == pytest.approx(count / 48, abs=1)
    finally:
        decoder.close()
        decoder._thread.join(3)
        assert not decoder._thread.is_alive()
    second = TrackDecoder(shutil.which("ffmpeg"), path, audio_format())
    second.close()
    second._thread.join(3)
    assert not second._thread.is_alive()


@pytest.mark.parametrize("repeat", [False, True])
def test_statistics_split_audible_gapless_tracks_and_repeats(sleep_window, monkeypatch, repeat):
    from datetime import datetime, timedelta, timezone

    window, player, now = sleep_window
    # Recording is opt-in in the factory defaults; this test exercises it on.
    window.statistics.set_enabled(True)
    window.statistics._clock = lambda: now[0]
    window.statistics._wall_clock = lambda: datetime.now(timezone.utc).replace(microsecond=0) + timedelta(seconds=now[0])
    for method in ("update_background_for_audio", "_start_lyrics_search", "_configure_pcm_volume", "_load_replaygain"):
        monkeypatch.setattr(window, method, lambda *args: None)
    monkeypatch.setattr(window.waveform_widget, "load_audio", lambda *args: None)
    window.playback_settings["after_playback"] = "repeat_track" if repeat else "next_track"
    player._duration = 100
    window._statistics_track_started()
    player._pump()
    sink = player._sink
    now[0] = .1
    sink.time = 100000
    player._pump()
    assert window.current_audio_path.name == ("first.wav" if repeat else "second.wav")
    now[0] = .15
    sink.time = 150000
    window._statistics_checkpoint()
    result = window.statistics_store.snapshot().result(timeout=5)
    assert result["summary"]["listened_ms"] == 150
    assert result["summary"]["plays"] == 2
    assert result["summary"]["songs"] == (1 if repeat else 2)


@pytest.mark.parametrize("partial", [False, True])
def test_crossfade_mixes_overlap_and_preserves_successor_clock(transport, partial):
    player = transport
    player._crossfade_seconds = 1
    def advance(status):
        if status == QMediaPlayer.MediaStatus.EndOfMedia:
            player.setSource(QUrl.fromLocalFile("second.wav"))
            player.next_path = lambda: None
    player.mediaStatusChanged.connect(advance)
    if partial:
        player._sink = Sink(None, player._format, player)
        player._io = player._sink
        player._sink.max_write = 12
    for _ in range(2600 if partial else 1):
        player._pump()
    sink = player._sink
    samples = [x[0] for x in struct.iter_unpack("<f", sink.written)]
    assert len(samples) == 7200  # 100 ms + 100 ms - 50 ms overlap
    assert samples[:2400] == [0.25] * 2400
    assert samples[2400] == pytest.approx(0.25)
    assert samples[3599] == pytest.approx(-0.125, abs=0.001)
    assert samples[4799:] == pytest.approx([-0.5] * 2401)
    sink.time = 100000
    player._pump()
    assert player.position() == 50  # incoming head was already heard
    assert player._sink is sink


def test_crossfade_queue_edit_discards_mixed_audio(transport):
    player = transport
    player._crossfade_seconds = 1
    player._pump()
    old, sink = player._next_decoder, player._sink
    player.next_path = lambda: None
    player.invalidate_preload()
    assert old.closed and sink.stopped
    assert player._next_offset_frames == 0
    assert player._crossfade_out == b""
    player._pump()
    assert player._next_decoder is None


def test_crossfade_preferences_validation():
    from core.settings import save_playback_settings, get_playback_settings
    for raw, expected in [(5, 5), (99, 10), (-3, 0), ("bad", 0), (None, 0)]:
        save_playback_settings({"crossfade_seconds": raw})
        assert get_playback_settings()["crossfade_seconds"] == expected


def test_crossfade_enables_stream_without_gapless_checkbox(qapp, monkeypatch):
    monkeypatch.setattr("core.gapless.shutil.which", lambda name: "ffmpeg")
    player = GaplessMediaPlayer()
    try:
        player.configure_gapless(False, 1, ("off", 0, True), crossfade_seconds=3)
        assert player._gapless_enabled
        player.configure_gapless(False, 1, ("off", 0, True), compatible=False, crossfade_seconds=3)
        assert not player._gapless_enabled
    finally:
        player.deleteLater()


def test_crossfade_settings_control_round_trip(qapp):
    from ui.settings_dialog import SettingsDialog
    dialog = SettingsDialog([], {}, playback_settings={"crossfade_seconds": 4})
    try:
        assert dialog.crossfade_seconds.value() == 4
        dialog.crossfade_seconds.setValue(7)
        assert dialog.get_playback_settings()["crossfade_seconds"] == 7
    finally:
        dialog.deleteLater()


def test_crossfade_without_successor_preserves_final_track(transport):
    player = transport
    player._crossfade_seconds = 5
    player.next_path = lambda: None
    player._pump()
    assert bytes(player._sink.written) == struct.pack("<4800f", *([0.25] * 4800))
    assert player._next_decoder is None


def test_crossfade_defers_successor_decoder_until_near_end(transport):
    player = transport
    player._crossfade_seconds = 3
    player._decoder.duration = 60000
    player._pump()
    assert player._next_decoder is None
    player._read_frames = 55 * 48000
    player._pump()
    assert player._next_decoder is not None
