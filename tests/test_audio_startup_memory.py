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


def test_library_startup_defers_statistics_games_and_pillow():
    run_fresh("""
import sys
from pathlib import Path
import main

app = main.QApplication([])
manager = main.PluginManager()
manager.load_from_file("rhythm_games", Path("plugins/community/rhythm_games/plugin.py"))
window = main.Player(plugin_manager=manager)
window.show()
manager.set_host_window(window)
manager.emit("app_ready", window=window)
try:
    app.processEvents()
    assert window._statistics_page is None
    assert "ui.statistics_page" not in sys.modules
    assert "plugins.community.rhythm_games.game" not in sys.modules
    assert "PIL.Image" not in sys.modules
    window.statistics_button.click()
    assert window.workspace_stack.currentWidget() is window.statistics_page
    game = manager.plugins["rhythm_games"].instance
    game.open_game()
    assert game.dialog is not None
finally:
    manager.unload_all()
    window.close()
    app.processEvents()
""")


def test_startup_and_theme_setup_defer_optional_dialogs_and_lyric_renderers():
    run_fresh("""
import sys
import main
from PySide6.QtWidgets import QDialog

assert 'ui.onboarding' not in sys.modules
app = main.QApplication([])
window = main.Player()
try:
    window.show()
    app.processEvents()
    assert 'lyrics.scrolling_widget' not in sys.modules
    assert 'ui.lyrics_search_dialog' not in sys.modules

    from ui.onboarding import OnboardingDialog
    from ui.onboarding_sound import OnboardingSounds

    OnboardingSounds.play = lambda *_: False
    wizard = OnboardingDialog(window)
    try:
        wizard.choose_preset(wizard.preset_combo.findText('Blue'))
        assert window.appearance_settings['theme_color'] == '#4477cc'
        assert 'ui.settings_dialog' not in sys.modules
    finally:
        wizard.done(QDialog.DialogCode.Rejected)

    opened = []
    QDialog.exec = lambda dialog: opened.append(dialog.windowTitle()) or 0
    search_action = next(action for action in window.lyrics_menu.actions()
                         if action.objectName() == 'search_lyrics')
    search_action.trigger()
    assert opened == ['Search Lyrics']
    assert 'ui.lyrics_search_dialog' in sys.modules
    window.lyrics_view_button.click()
    assert 'lyrics.scrolling_widget' in sys.modules
finally:
    window.close()
    app.processEvents()
""")


def test_first_dsp_calls_preserve_output_and_defer_numpy_when_possible():
    run_fresh("""
import struct, sys
from core.audio_dsp import (
    PcmPitchProcessor, PcmRateProcessor, _native_gain, apply_pcm_gain_to_bytes, apply_pcm_rate_to_bytes,
)
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
assert ('numpy' in sys.modules) == (_native_gain() is None)
assert all(value == 0.5 for (value,) in struct.iter_unpack('<f', gained))
resampled = apply_pcm_rate_to_bytes(source, fmt, 1, 1.25)
assert 'numpy' in sys.modules
assert len(resampled) == 160 * 4
assert all(abs(value - 0.25) < 1e-6 for (value,) in struct.iter_unpack('<f', resampled))
""")


def test_audio_thread_imports_numpy_only_when_first_processed_buffer_needs_it():
    run_fresh("""
import struct, sys, time
from core.audio_dsp import _native_gain
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
    assert ('numpy' in sys.modules) == (_native_gain() is None)
    assert all(value == 0.5 for (value,) in struct.iter_unpack('<f', written[1]))
finally:
    worker.close()
    assert thread.wait(3000)
""")


def test_first_dsp_import_failure_reports_error_and_stops_processed_input():
    run_fresh("""
import builtins, struct, time
from core import audio_dsp
from core.pcm_playback import PcmPlayback
from PySide6.QtMultimedia import QAudioBuffer, QAudioFormat
from PySide6.QtWidgets import QApplication
app = QApplication([])
errors = []
original_import = builtins.__import__
audio_dsp._native_gain = lambda: None
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


def test_cold_player_can_switch_tracks_and_pitch_with_waveform_enabled(tmp_path, qapp, monkeypatch):
    import shutil
    import pytest
    from PySide6.QtMultimedia import QMediaDevices
    from core.native_audio import audio_probe_executable

    if not QMediaDevices.audioOutputs() or not (audio_probe_executable() or shutil.which("ffmpeg")):
        pytest.skip("Requires audio output and a waveform decoder")
    monkeypatch.setenv("LRCPLUS_RUST_PLAYBACK", "0")
    # Keep this in a fresh process: NumPy/Rubber Band imports on the audio
    # thread exposed a renderer/GIL deadlock hidden by the warmed-up suite.
    run_fresh(f"""
import faulthandler, time, wave
from pathlib import Path
from PySide6.QtMultimedia import QMediaPlayer
from PySide6.QtTest import QTest
from PySide6.QtWidgets import QApplication
from core.gui_garbage_collector import install_gui_garbage_collector
from ui.main_window import Player

faulthandler.dump_traceback_later(15, exit=True)
app = QApplication([])
install_gui_garbage_collector(app)
directory = Path({str(tmp_path)!r})
paths = [directory / name for name in ('first.wav', 'second.wav')]
for path in paths:
    with wave.open(str(path), 'wb') as audio:
        audio.setnchannels(2)
        audio.setsampwidth(2)
        audio.setframerate(48000)
        audio.writeframes(bytes(48000 * 4 * 30))
window = Player()
for name in ('update_background_for_audio', '_start_lyrics_search', '_notify_queue_changed'):
    setattr(window, name, lambda *args: None)
errors = []
window._pcm_worker.failed.disconnect()
window._pcm_worker.failed.connect(errors.append)
window.volume_slider.setValue(0)
window.show()
window.waveform_widget.show()
app.processEvents()
thread = window._pcm_worker._thread
try:
    assert window.waveform_widget.isVisible()
    for index in range(24):
        window.load_audio(paths[index % 2])
        window.media_player.play()
        QTest.qWait(15 + (index % 5) * 20)
        window.handle_pitch_change('Semitones', (0, 5, -5, 12, -12)[index % 5])
    deadline = time.monotonic() + 3
    while (window.waveform_widget._loading or window.media_player.position() <= 0) and time.monotonic() < deadline:
        QTest.qWait(20)
    assert not errors, errors
    assert window.media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState
    assert window.media_player.position() > 0
    assert not window.waveform_widget._loading
    assert window.waveform_widget._peaks
    assert window.waveform_widget._error is None
finally:
    window.close()
    assert thread.wait(3000)
    window.deleteLater()
    app.processEvents()
faulthandler.cancel_dump_traceback_later()
""")
