"""Continuous pitch processing with a shared stereo analysis history."""

import ctypes
from ctypes.util import find_library
from functools import lru_cache
import logging
from pathlib import Path
import sys
import weakref

import numpy as np


BLOCK_FRAMES = 512
_FLOAT_POINTER = ctypes.POINTER(ctypes.c_float)
_CHANNEL_POINTERS = ctypes.POINTER(_FLOAT_POINTER)
_LOG = logging.getLogger(__name__)


def _library_candidates():
    # The Windows aubio-ledfx wheel already ships Rubber Band and its DLL
    # dependencies. Importing aubio registers that wheel's DLL directory.
    try:
        import aubio
    except ImportError:
        pass
    else:
        root = Path(aubio.__file__).resolve().parent.parent
        for directory in (root / "aubio_ledfx.libs", root / "aubio.libs",
                          Path(aubio.__file__).resolve().parent / ".dylibs"):
            if directory.is_dir():
                for path in sorted(directory.glob("*rubberband*")):
                    if path.is_file():
                        yield str(path)
    # PyInstaller preserves the wheel's relative library directory.
    frozen_root = getattr(sys, "_MEIPASS", None)
    if frozen_root:
        yield from (str(path) for path in
                    (Path(frozen_root) / "aubio_ledfx.libs").glob("*rubberband*.dll"))
    for name in ("rubberband", "rubberband-3", "librubberband"):
        path = find_library(name)
        if path:
            yield path


@lru_cache(maxsize=1)
def _load_rubberband():
    # Types match rubberband/rubberband-c.h (the stable C ABI).
    signatures = {
        "new": (ctypes.c_void_p, [ctypes.c_uint, ctypes.c_uint, ctypes.c_int,
                                  ctypes.c_double, ctypes.c_double]),
        "set_pitch_scale": (None, [ctypes.c_void_p, ctypes.c_double]),
        "delete": (None, [ctypes.c_void_p]),
        "get_engine_version": (ctypes.c_int, [ctypes.c_void_p]),
        "get_preferred_start_pad": (ctypes.c_uint, [ctypes.c_void_p]),
        "get_start_delay": (ctypes.c_uint, [ctypes.c_void_p]),
        "get_samples_required": (ctypes.c_uint, [ctypes.c_void_p]),
        "set_max_process_size": (None, [ctypes.c_void_p, ctypes.c_uint]),
        "process": (None, [ctypes.c_void_p, _CHANNEL_POINTERS,
                           ctypes.c_uint, ctypes.c_int]),
        "available": (ctypes.c_int, [ctypes.c_void_p]),
        "retrieve": (ctypes.c_uint, [ctypes.c_void_p, _CHANNEL_POINTERS, ctypes.c_uint]),
    }
    for candidate in _library_candidates():
        try:
            library = ctypes.CDLL(candidate)
            for name, (result_type, arguments) in signatures.items():
                function = getattr(library, "rubberband_" + name)
                function.restype = result_type
                function.argtypes = arguments
            return library
        except (OSError, AttributeError):
            continue
    raise OSError("Rubber Band 3 shared library was not found.")


def _pointers(planar):
    return (_FLOAT_POINTER * len(planar))(
        *(channel.ctypes.data_as(_FLOAT_POINTER) for channel in planar)
    )


class RubberBandPitchStream:
    """R3/Finer engine, with one continuous history for all audio channels."""

    def __init__(self, channels, sample_rate, semitones):
        self._library = library = _load_rubberband()
        self.channels = channels
        # RealTime | ThreadingNever | PitchHighConsistency | ChannelsTogether |
        # EngineFiner | WindowShort for responsive live pitch changes.
        options = 0x00000001 | 0x00010000 | 0x04000000 | 0x10000000 | 0x20000000 | 0x00100000
        self._handle = library.rubberband_new(
            sample_rate, channels, options, 1.0, 2.0 ** (semitones / 12.0),
        )
        if not self._handle:
            raise RuntimeError("Could not create the Rubber Band pitch processor.")
        self._finalizer = weakref.finalize(self, library.rubberband_delete, self._handle)
        if library.rubberband_get_engine_version(self._handle) != 3:
            self.close()
            raise OSError("The installed Rubber Band library does not support R3.")
        library.rubberband_set_max_process_size(self._handle, BLOCK_FRAMES)
        pad = library.rubberband_get_preferred_start_pad(self._handle)
        self._trim = library.rubberband_get_start_delay(self._handle)
        required = library.rubberband_get_samples_required(self._handle)
        # The silent input below already supplies `pad` frames of analysis
        # context. Count only the remaining context and trimmed output delay,
        # plus four blocks for variable output during live pitch changes and
        # partial decoder callbacks.
        self.latency_frames = max(
            BLOCK_FRAMES, required + self._trim - pad + 4 * BLOCK_FRAMES,
        )
        for start in range(0, pad, BLOCK_FRAMES):
            self.process(np.zeros((min(BLOCK_FRAMES, pad - start), channels), np.float32))

    def set_semitones(self, semitones):
        self._library.rubberband_set_pitch_scale(self._handle, 2.0 ** (semitones / 12.0))

    def process(self, frames):
        planar = np.ascontiguousarray(frames.T, dtype=np.float32)
        library = self._library
        library.rubberband_process(self._handle, _pointers(planar), len(frames), 0)
        available = library.rubberband_available(self._handle)
        if available <= 0:
            return np.empty((0, self.channels), np.float32)
        output = np.empty((self.channels, available), np.float32)
        count = library.rubberband_retrieve(self._handle, _pointers(output), available)
        trim = min(self._trim, count)
        self._trim -= trim
        return output[:, trim:count].T.copy()

    def close(self):
        self._finalizer()
        self._handle = None


@lru_cache(maxsize=4)
def _pedalboard_streaming_supported(effect_type):
    # Some builds re-prepare the priming delay on every process(reset=False)
    # call, silently losing all input supplied in short callbacks. Check the
    # installed implementation once instead of selecting a silent fallback.
    # Upstream: https://github.com/spotify/pedalboard/pull/486
    effect = effect_type(semitones=5)
    tone = (.25 * np.sin(2 * np.pi * 440 * np.arange(BLOCK_FRAMES) / 48000)).astype(np.float32)
    for _ in range(150):
        output = effect(tone, 48000, buffer_size=BLOCK_FRAMES, reset=False)
        if np.any(np.abs(output) > .001):
            return True
    return False


class PedalboardPitchStream:
    """Continuous fallback for installations without a Rubber Band shared library."""

    def __init__(self, channels, sample_rate, semitones):
        from pedalboard import PitchShift

        if not _pedalboard_streaming_supported(PitchShift):
            raise OSError("This Pedalboard build cannot stream pitch-shifted audio. "
                          "Install a Rubber Band 3 library to enable pitch processing.")
        self._effect = PitchShift(semitones=semitones)
        self._sample_rate = sample_rate
        # Pedalboard's wrapper primes a full second of silence internally.
        self.latency_frames = int(sample_rate * 1.25) + 2 * BLOCK_FRAMES

    def set_semitones(self, semitones):
        self._effect.semitones = semitones

    def process(self, frames):
        return self._effect(
            np.ascontiguousarray(frames.T), self._sample_rate,
            buffer_size=BLOCK_FRAMES, reset=False,
        ).T.copy()

    def close(self):
        self._effect = None


def create_pitch_stream(channels, sample_rate, semitones):
    try:
        return RubberBandPitchStream(channels, sample_rate, semitones)
    except OSError as error:
        _LOG.warning("%s Using continuous Pedalboard processing with higher latency.", error)
        return PedalboardPitchStream(channels, sample_rate, semitones)
