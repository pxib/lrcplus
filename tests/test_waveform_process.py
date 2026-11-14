"""Waveform decoding must not open a console in the Windows GUI build."""

import importlib
import subprocess

import pytest


@pytest.mark.parametrize("module_name", ["lyrics.waveform", "lyrics.waveform_worker"])
def test_waveform_decoder_runs_without_console(qapp, monkeypatch, module_name):
    module = importlib.import_module(module_name)
    launches = []
    # Use a sentinel on other platforms too, so a missing flag fails everywhere.
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", no_window, raising=False)
    monkeypatch.setattr(module, "find_ffmpeg", lambda: "ffmpeg")

    class Decoder:
        returncode = 0

        def __init__(self, command, **kwargs):
            launches.append((command, kwargs))
            kwargs["stdout"].write(b"\x00\x40" * 2000)

        def communicate(self, timeout=None):
            return None, b""

    monkeypatch.setattr(module.subprocess, "Popen", Decoder)
    worker = module._WaveformWorker("song.mp3")
    loaded, errors = [], []
    worker.loaded.connect(lambda peaks, duration: loaded.append((peaks, duration)))
    worker.failed.connect(errors.append)
    worker.run()

    assert not errors
    assert len(launches) == 1
    assert launches[0][1]["creationflags"] == no_window
    assert launches[0][1]["stderr"] == subprocess.PIPE
    assert len(loaded) == 1
    peaks, duration = loaded[0]
    assert duration == 1000
    assert peaks == pytest.approx([0.5] * 1400)
