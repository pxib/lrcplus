"""Measure ReplayGain from local audio with FFmpeg's EBU R128 filter."""

from dataclasses import dataclass
from itertools import chain
from math import isfinite, log10
from pathlib import Path
import re
import subprocess

from core.playlists import path_key
from core.replaygain import ReplayGain
from core.replaygain_cache import file_revision, save_scans


_TARGET_LUFS = -18.0
_MOMENTARY = re.compile(r"^lavfi\.r128\.M=(-?\d+(?:\.\d+)?)$", re.MULTILINE)
_PEAK = re.compile(r"^\[Parsed_astats_\d+ @ [^\n]+\] Peak level dB: (-?\d+(?:\.\d+)?|-inf)$", re.MULTILINE)


@dataclass(frozen=True)
class ScanResult:
    path: Path
    gain: ReplayGain | None = None
    status: str = ""


def replaygain_from_blocks(blocks):
    """Apply the absolute and relative BS.1770 gates to 400 ms block energies."""
    energies = [10 ** (value / 10) for value in blocks if value >= -70]
    if not energies:
        return None
    relative_gate = sum(energies) / (10 * len(energies))
    gated = [energy for energy in energies if energy >= relative_gate]
    return round(_TARGET_LUFS - 10 * log10(sum(gated) / len(gated)), 2)


def _measure(executable, path, cancelled):
    command = [
        executable, "-hide_banner", "-nostdin", "-nostats", "-loglevel", "info", "-xerror",
        "-protocol_whitelist", "file,pipe", "-i", str(path), "-map", "0:a:0", "-vn", "-sn", "-dn",
        "-af", "astats=measure_perchannel=none:measure_overall=Peak_level,"
               "ebur128=metadata=1,ametadata=mode=print:key=lavfi.r128.M:file=-",
        "-f", "null", "-",
    ]
    with subprocess.Popen(
        command, stdin=subprocess.DEVNULL, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
        text=True, encoding="utf-8", errors="replace",
        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
    ) as process:
        try:
            while not cancelled.is_set():
                try:
                    output, log = process.communicate(timeout=0.15)
                    break
                except subprocess.TimeoutExpired:
                    continue
            else:
                return None
        finally:
            if process.poll() is None:
                process.kill()
                process.communicate()
    peak = _PEAK.search(log)
    if process.returncode or peak is None:
        raise ValueError("FFmpeg could not measure this file.")
    peak_db = float(peak[1])
    return tuple(float(value) for value in _MOMENTARY.findall(output)), (10 ** (peak_db / 20) if isfinite(peak_db) else 0.0)


def scan_replaygain(executable, groups, album, cancelled, progress):
    results, rows = [], []
    completed = 0
    for group in groups:
        measured = []
        for path in group:
            if cancelled.is_set():
                return None
            path = Path(path)
            progress.put((completed, path.name))
            try:
                path = path.resolve()
                if not path.is_file():
                    raise OSError("File unavailable")
                revision = file_revision(path)
                measurement = _measure(executable, path, cancelled)
                if measurement is None:
                    return None
                if revision != file_revision(path):
                    raise OSError("File changed during scan")
            except (OSError, ValueError) as error:
                results.append(ScanResult(path, status=str(error)))
            else:
                measured.append((path, revision, *measurement))
            completed += 1
        album_gain = (replaygain_from_blocks(chain.from_iterable(row[2] for row in measured))
                      if album and len(measured) == len(group) else None)
        album_peak = max(row[3] for row in measured) if album_gain is not None else None
        for path, revision, blocks, peak in measured:
            track_gain = replaygain_from_blocks(blocks)
            tags = ReplayGain(track_gain, peak, album_gain, album_peak)
            if track_gain is None and album_gain is None:
                results.append(ScanResult(path, status="No measurable loudness"))
                continue
            status = "Album incomplete" if album and album_gain is None else ""
            results.append(ScanResult(path, tags, status))
            rows.append((path_key(path), *revision, track_gain, peak, album_gain, album_peak))
    if cancelled.is_set():
        return None
    if rows and not save_scans(rows, cancelled):
        return None
    return results
