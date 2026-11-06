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


@pytest.mark.parametrize("mode", ["next_track", "repeat_track", "repeat_all", "shuffle", "priority", "duplicate"])
def test_player_handoff_preserves_queue_rules_and_sink(qapp, monkeypatch, tmp_path, mode):
    from core.settings import save_playback_settings
    from ui.main_window import Player
    from types import SimpleNamespace

    monkeypatch.setattr("core.gapless.shutil.which", lambda name: "ffmpeg")
    save_playback_settings({"play_mode": "playlist", "gapless_playback": True})
    window = Player()
    player = window.media_player
    player._decoder_factory = Decoder
    player._sink_factory = Sink
    device = SimpleNamespace(preferredFormat=audio_format, isFormatSupported=lambda fmt: True)
    monkeypatch.setattr(window.audio_output, "device", lambda: device)
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
    try:
        window.load_audio(window.playlist[window.current_index])
        player.play()
        player._pump()
        sink = player._sink
        assert sink is not None
        sink.time = 100000
        player._pump()
        assert window.current_audio_path == expected
        assert player._sink is sink
        assert Path(player.source().toLocalFile()) == expected
        if mode == "duplicate":
            assert window.current_index == 1
        if mode == "priority":
            assert not window._priority_queue_indices
    finally:
        window.close()
        window.deleteLater()
        qapp.processEvents()


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
