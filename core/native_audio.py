"""Locate the Rust audio helper and load the in-process DSP library."""

from functools import lru_cache
from pathlib import Path
import sys


def audio_probe_executable():
    if sys.platform != "win32":
        return None
    if getattr(sys, "frozen", False):
        directory = Path(sys.executable).parent
    else:
        directory = Path(__file__).resolve().parents[1] / "build" / "audio-probe" / "release"
    executable = directory / "lrcplus-audio-probe.exe"
    return executable if executable.is_file() else None


@lru_cache(maxsize=1)
def _load_library():
    if sys.platform != "win32":
        return None
    from ctypes import CDLL

    if getattr(sys, "frozen", False):
        directory = Path(sys._MEIPASS)
    else:
        directory = Path(__file__).resolve().parents[1] / "build" / "audio-dsp" / "release"
    try:
        return CDLL(str(directory / "lrcplus_audio_dsp_v1.dll"))
    except OSError:
        return None


def audio_kernel(name, argument_types, result_type=None):
    function = getattr(_load_library(), name, None)
    if function is not None:
        function.argtypes = argument_types
        function.restype = result_type
    return function
