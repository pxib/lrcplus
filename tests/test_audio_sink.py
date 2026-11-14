"""Disposing a paused output must discard audio without resuming it.

Set LRCPLUS_TEST_AUDIO_DEVICE to an output device description to opt into the
Windows native-sink check. It submits silence and runs in a bounded subprocess
so a native reset deadlock fails the test instead of hanging the test runner.
"""

import os
from pathlib import Path
import subprocess
import sys
import textwrap

import pytest

from core.audio_sink import dispose_audio_sink


class BufferedSink:
    def __init__(self, *, paused):
        self.paused = paused
        self.buffered = b"already submitted to the device"
        self.audible = bytearray()
        self.deleted = False

    def setVolume(self, volume):
        # Updating the stream volume cannot change samples already buffered
        # by the device while playback was suspended.
        pass

    def resume(self):
        self.paused = False
        self.audible.extend(self.buffered)
        self.buffered = b""

    def reset(self):
        self.buffered = b""

    def stop(self):
        self.audible.extend(self.buffered)
        self.buffered = b""

    def deleteLater(self):
        self.deleted = True


@pytest.mark.parametrize("paused", [False, True])
def test_dispose_discards_device_buffer_without_playing_it(paused):
    sink = BufferedSink(paused=paused)
    dispose_audio_sink(sink)
    assert not sink.audible
    assert not sink.buffered
    assert sink.paused is paused
    assert sink.deleted


@pytest.mark.skipif(sys.platform != "win32" or not os.getenv("LRCPLUS_TEST_AUDIO_DEVICE"),
                    reason="Requires an explicitly selected Windows audio output")
def test_native_windows_shutdown_without_resuming():
    script = textwrap.dedent("""
        import sys
        import time
        from PySide6.QtCore import QCoreApplication
        from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat, QAudioSink, QMediaDevices
        from core.audio_sink import dispose_audio_sink
        from core.pcm_playback import PcmPlayback

        app = QCoreApplication([])
        device = next(d for d in QMediaDevices.audioOutputs() if d.description() == sys.argv[1])
        fmt = device.preferredFormat()
        fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
        silence = bytes(fmt.bytesForDuration(100000))

        def wait_for(predicate):
            deadline = time.monotonic() + 3
            while not predicate() and time.monotonic() < deadline:
                time.sleep(.001)
            assert predicate()

        class Sink(QAudioSink):
            def resume(self):
                raise AssertionError("Disposing a sink must never resume it")

        # Exercise immediate reset on both active and suspended streams.
        for iteration in range(20):
            sink = Sink(device, fmt)
            io = sink.start()
            assert io is not None, sink.error()
            io.write(silence[:sink.bytesFree()])
            if iteration % 2:
                sink.suspend()
            dispose_audio_sink(sink)
            app.processEvents()

        # Exercise the real processed-output thread with queued device data.
        for iteration in range(20):
            worker = PcmPlayback(sink_factory=Sink)
            worker.configure(device, 1, 0, 1.1, enabled=True, playing=True)
            worker.receive_buffer(QAudioBuffer(silence, fmt))
            wait_for(lambda: worker._engine._written_bytes > 0)
            if iteration % 2:
                worker.set_playing(False)
                wait_for(lambda: not worker._engine._playing)
            thread = worker._thread
            worker.close()
            assert thread.wait(3000), "Native reset blocked shutdown"
            app.processEvents()
    """)
    result = subprocess.run(
        [sys.executable, "-c", script, os.environ["LRCPLUS_TEST_AUDIO_DEVICE"]],
        cwd=Path(__file__).resolve().parents[1],
        capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr
