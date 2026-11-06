"""Check cold startup and the first DSP call in fresh Python processes."""

from pathlib import Path
import subprocess
import sys


def run_fresh(script):
    result = subprocess.run(
        [sys.executable, "-c", script], cwd=Path(__file__).resolve().parents[1],
        capture_output=True, encoding="utf-8", errors="replace", timeout=20,
    )
    assert result.returncode == 0, result.stdout + result.stderr


def test_library_and_neutral_audio_controls_do_not_load_numpy():
    run_fresh("""
import sys
import main
from PySide6.QtTest import QTest
assert 'numpy' not in sys.modules
app = main.QApplication([])
window = main.Player()
try:
    window.show()
    window.handle_pitch_change('Percent', 100)
    window.handle_pitch_link_change(True)
    window.handle_speed_change(100)
    window._flush_pcm_output()
    QTest.qWait(100)  # Include the audio thread's idle/reset timer callbacks.
    assert 'numpy' not in sys.modules
    assert 'core.pitch_stream' not in sys.modules
finally:
    window.close()
    app.processEvents()
""")


def test_first_gain_and_resample_calls_load_numpy_and_preserve_output():
    run_fresh("""
import struct, sys
from core.audio_dsp import PcmPitchProcessor, PcmRateProcessor, apply_pcm_gain_to_bytes, apply_pcm_rate_to_bytes
from PySide6.QtMultimedia import QAudioFormat
fmt = QAudioFormat.SampleFormat.Float
source = struct.pack('<200f', *([0.25] * 200))
pitch, rate = PcmPitchProcessor(), PcmRateProcessor()
pitch.reset()
rate.reset()
assert pitch.process(source, fmt, 1, 48000, 0) == source
assert apply_pcm_rate_to_bytes(source, fmt, 1, 1) == source
assert 'numpy' not in sys.modules
gained = apply_pcm_gain_to_bytes(source, fmt, 2)
assert 'numpy' in sys.modules
assert all(value == 0.5 for (value,) in struct.iter_unpack('<f', gained))
resampled = apply_pcm_rate_to_bytes(source, fmt, 1, 1.25)
assert len(resampled) == 160 * 4
assert all(abs(value - 0.25) < 1e-6 for (value,) in struct.iter_unpack('<f', resampled))
""")


def test_audio_thread_imports_numpy_only_when_first_processed_buffer_needs_it():
    run_fresh("""
import struct, sys, time
from core.pcm_playback import PcmPlayback
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication
app = QApplication([])
written = []
class Sink:
    def __init__(self, device, fmt, parent): self.fmt = fmt
    def format(self): return self.fmt
    def setVolume(self, value): pass
    def setBufferSize(self, value): pass
    def start(self): return self
    def bytesFree(self): return 1000000
    def write(self, data): written.append(data); return len(data)
    def reset(self): pass
    def stop(self): pass
    def deleteLater(self): pass
    def suspend(self): pass
    def resume(self): pass
def wait(predicate):
    deadline = time.monotonic() + 5
    while not predicate() and time.monotonic() < deadline: time.sleep(0.001)
    assert predicate()
fmt = QAudioFormat()
fmt.setSampleRate(48000)
fmt.setChannelCount(1)
fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
source = struct.pack('<4096f', *([0.25] * 4096))
worker = PcmPlayback(sink_factory=Sink)
thread = worker._thread
try:
    worker.configure(None, 1, 0, 1, enabled=True, playing=True)
    worker.receive_buffer(QAudioBuffer(source, fmt))
    wait(lambda: len(written) == 1)
    assert written[0] == source
    assert 'numpy' not in sys.modules
    worker.configure(None, 1, 0, 2, enabled=True, playing=True)
    worker.receive_buffer(QAudioBuffer(source, fmt))
    wait(lambda: len(written) == 2)
    assert 'numpy' in sys.modules
    assert all(value == 0.5 for (value,) in struct.iter_unpack('<f', written[1]))
finally:
    worker.close()
    assert thread.wait(3000)
""")


def test_first_dsp_import_failure_reports_error_and_stops_processed_input():
    run_fresh("""
import builtins, struct, time
from core.pcm_playback import PcmPlayback
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication
app = QApplication([])
errors = []
original_import = builtins.__import__
def unavailable(name, *args, **kwargs):
    if name == 'numpy': raise ImportError('NumPy unavailable for test')
    return original_import(name, *args, **kwargs)
builtins.__import__ = unavailable
def unexpected_sink(*args): raise AssertionError('Failed processing must not start a sink')
worker = PcmPlayback(sink_factory=unexpected_sink)
thread = worker._thread
worker.failed.connect(errors.append)
fmt = QAudioFormat()
fmt.setSampleRate(48000)
fmt.setChannelCount(1)
fmt.setSampleFormat(QAudioFormat.SampleFormat.Float)
try:
    worker.configure(None, 1, 0, 2, enabled=True, playing=True)
    worker.receive_buffer(QAudioBuffer(struct.pack('<4096f', *([0.25] * 4096)), fmt))
    deadline = time.monotonic() + 5
    while not errors and time.monotonic() < deadline:
        app.processEvents()
        time.sleep(0.001)
    assert errors == ['NumPy unavailable for test']
    assert not worker._controls.enabled
finally:
    worker.close()
    assert thread.wait(3000)
""")
