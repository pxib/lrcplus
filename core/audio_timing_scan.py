"""Resolve timing against nearby subtitles or local vocal analysis."""

import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
from tempfile import TemporaryDirectory

import numpy as np

from core.dependency_tasks import cancellation_scope, check_cancelled, run_process
from lyrics.audio_timing_drift import VOCAL_FRAME_SECONDS, estimate_audio_timing, sung_intervals
from lyrics.timing_drift import estimate_timing_correction, load_timing_reference


_cached_envelope = None
_CAPTION_VARIANT = re.compile(r"[a-z]{2,3}(?:[-_][a-z0-9]{2,8})*(?:\.(?:manual|auto))?")


def _worker_pythons():
    if getattr(sys, "frozen", False):
        private_python = os.environ.get("LRCPLUS_DEPENDENCY_PYTHON")
        return [private_python] if private_python else []
    return list(dict.fromkeys((sys.executable, getattr(sys, "_base_executable", sys.executable))))


def _vocal_envelope(audio):
    global _cached_envelope
    stat = audio.stat()
    key = audio, stat.st_size, stat.st_mtime_ns
    cached = _cached_envelope
    if cached is not None and cached[0] == key:
        return cached[1]
    ffmpeg = shutil.which("ffmpeg")
    if ffmpeg is None:
        raise ValueError("Install FFmpeg in Settings → Dependencies to analyze vocal timing.")
    worker = Path(__file__).with_name("vocal_envelope_worker.py")
    with TemporaryDirectory(prefix="lrcplus-vocal-timing-") as folder:
        output = Path(folder) / "envelope.npy"
        for python in _worker_pythons():
            result = run_process(
                [python, "-I", str(worker), ffmpeg, str(audio), str(output)],
                capture_output=True, encoding="utf-8", errors="replace", timeout=900,
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            if result.returncode == 2:
                continue
            if result.returncode:
                raise ValueError(result.stderr.strip().splitlines()[-1] if result.stderr.strip()
                                 else "Vocal analysis could not finish.")
            envelope = np.load(output, allow_pickle=False)
            _cached_envelope = key, envelope
            return envelope
    raise ValueError("Install Demucs in Settings → Dependencies and restart LRCPlus to analyze vocal timing.")


def correct_audio_timing(audio, lines, offset_ms, duration_ms, cancelled):
    """Return corrected lines and offset, or None for an already aligned song."""
    with cancellation_scope(cancelled.is_set):
        audio = Path(audio).resolve(strict=True)
        stem = audio.stem.casefold()
        captions = sorted(
            (path for path in audio.parent.iterdir() if path.suffix.lower() in {".srt", ".vtt"}
             and _same_recording_caption(path.stem.casefold(), stem)),
            key=lambda path: (path.stem.lower().endswith(".auto"), path.name.casefold()),
        )
        caption_results = []
        for path in captions:
            check_cancelled()
            try:
                ref, ref_offset = load_timing_reference(path)
                correction = estimate_timing_correction(lines, ref, offset_ms=offset_ms,
                                                        reference_offset_ms=ref_offset)
                if correction is not None:
                    correction.apply(lines, offset_ms=offset_ms, duration_ms=duration_ms)
                caption_results.append(correction)
            except (OSError, ValueError, OverflowError):
                continue
        if caption_results:
            # Different caption files can correspond to different lyric languages
            # or versions. Use them automatically only when their fits agree.
            start = min(line.start for line in lines)
            end = max(line.start for line in lines)
            first = caption_results[0]
            if all(abs(_playback_time(first, t, offset_ms) - _playback_time(other, t, offset_ms)) < .45
                   for other in caption_results[1:] for t in (start, end)):
                return None if first is None else first.apply(lines, offset_ms=offset_ms, duration_ms=duration_ms)
        intervals = sung_intervals(lines)
        envelope = _vocal_envelope(audio)
        check_cancelled()
        correction = estimate_audio_timing(
            intervals, envelope, offset_ms=offset_ms, check_cancelled=check_cancelled,
        )
        duration_ms = duration_ms or round(len(envelope) * VOCAL_FRAME_SECONDS * 1000)
        return None if correction is None else correction.apply(lines, offset_ms=offset_ms, duration_ms=duration_ms)


def _playback_time(correction, timestamp, offset_ms):
    return timestamp + offset_ms / 1000 if correction is None else correction.audio_time(timestamp)


def _same_recording_caption(caption_stem, audio_stem):
    if caption_stem == audio_stem:
        return True
    suffix = caption_stem.removeprefix(audio_stem + ".")
    return suffix != caption_stem and _CAPTION_VARIANT.fullmatch(suffix) is not None
