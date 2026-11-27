"""Waveform decoding must not open a console in the Windows GUI build."""

import subprocess

import pytest

from lyrics import waveform_worker


@pytest.mark.parametrize("native", ["unavailable", "failed", "invalid", "success"])
def test_waveform_decoder_runs_without_console(qapp, monkeypatch, native):
    launches = []
    # Use a sentinel on other platforms too, so a missing flag fails everywhere.
    no_window = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    monkeypatch.setattr(subprocess, "CREATE_NO_WINDOW", no_window, raising=False)
    monkeypatch.setattr(waveform_worker, "audio_probe_executable",
                        lambda: None if native == "unavailable" else "probe")
    monkeypatch.setattr(waveform_worker, "find_ffmpeg", lambda: "ffmpeg")

    class Decoder:
        returncode = 0

        def __init__(self, command, **kwargs):
            launches.append((command, kwargs))
            self.is_native = command[0] == "probe"
            if self.is_native:
                self.returncode = int(native == "failed")
                assert kwargs["stdout"] == subprocess.PIPE
            else:
                kwargs["stdout"].write(b"\x00\x40" * 2000)

        def communicate(self, timeout=None):
            if self.is_native:
                if native == "invalid":
                    return b"waveform-v1 1000 NaN", b""
                return b"waveform-v1 1000" + b" 0.5" * 1400, b""
            return None, b""

    monkeypatch.setattr(subprocess, "Popen", Decoder)
    worker = waveform_worker._WaveformWorker("song.mp3")
    loaded, errors = [], []
    worker.loaded.connect(lambda peaks, duration: loaded.append((peaks, duration)))
    worker.failed.connect(errors.append)
    worker.run()

    assert not errors
    programs = {
        "unavailable": ["ffmpeg"], "failed": ["probe", "ffmpeg"],
        "invalid": ["probe", "ffmpeg"], "success": ["probe"],
    }
    assert [command[0] for command, _ in launches] == programs[native]
    for _, options in launches:
        assert options["creationflags"] == no_window
        assert options["stderr"] == subprocess.PIPE
    assert len(loaded) == 1
    peaks, duration = loaded[0]
    assert duration == 1000
    assert peaks == pytest.approx([0.5] * 1400)
