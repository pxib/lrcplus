"""Launch and exercise a Windows release ZIP with an isolated profile."""

import argparse
import json
from math import isclose
import os
from pathlib import Path
import struct
import subprocess
import sys
from tempfile import TemporaryDirectory
import wave

from build_release import PROJECT_ROOT, windows_architecture
from core.app_info import APP_VERSION
from core.update_package import extract_update_archive


def write_fixture(path):
    with wave.open(str(path), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", ""))
        period = struct.pack("<100h", *([1500] * 50 + [-1500] * 50))
        audio.writeframes(period * 2205)


def write_seek_fixtures(directory):
    with wave.open(str(directory / "seek-profile.wav"), "wb") as audio:
        audio.setparams((1, 2, 44100, 0, "NONE", ""))
        audio.writeframes(bytes(44100 * 2 * 30))
    rows = []
    for row in range(165):
        timestamp = f"00:{row // 6:02d}.{row % 6 * 166:03d}"
        rows.append(f"[{timestamp}]<{timestamp}>Timed lyric {row}\n")
    (directory / "seek-profile.lrcx").write_text("".join(rows), encoding="utf-8")


def smoke_release(archive, architecture, work, *, profile_seeks=False):
    app = extract_update_archive(archive, work / "install",
                                 expected_version=APP_VERSION, architecture=architecture)
    fixtures = work / "fixtures"
    fixtures.mkdir()
    sample = fixtures / "sample.wav"
    write_fixture(sample)
    if profile_seeks:
        write_seek_fixtures(fixtures)
    waveform = subprocess.run(
        [str(app / "lrcplus-audio-probe.exe"), str(sample), "--waveform", "200"],
        cwd=app, capture_output=True, check=True, timeout=30,
        creationflags=subprocess.CREATE_NO_WINDOW,
    ).stdout.split()
    if (waveform[:2] != [b"waveform-v1", b"5000"] or len(waveform) != 202
            or any(not isclose(float(peak), 1500 / 32768, abs_tol=1e-6) for peak in waveform[2:])):
        raise RuntimeError("The packaged Rust waveform decoder returned unexpected results.")
    print("Packaged Rust waveform decoder passed.")
    settings = work / "settings.json"
    profile = {
        "updates": {"automatic_check": False},
        "appearance": {"artwork_source": "album_only"},
        "disabled_plugins": ["discord_rpc"],
    }
    if profile_seeks:
        profile["lyrics_global"] = {"scrolling_lyrics": True}
    settings.write_text(json.dumps(profile), encoding="utf-8")
    data = work / "app-data"
    env = {
        key: value for key, value in os.environ.items()
        if not key.upper().startswith(("PYTHON", "QT_", "QML", "LRCPLUS_"))
    }
    env.update(LRCPLUS_SETTINGS_PATH=str(settings), LRCPLUS_DATA_DIR=str(data),
               QT_QPA_PLATFORM="offscreen", QT_MEDIA_BACKEND="ffmpeg")

    report = fixtures / "result.txt"
    try:
        result = subprocess.run(
            [str(app / "LRCPlus.exe"), f"--lrcplus-release-smoke={fixtures}"],
            cwd=app, env=env, timeout=90,
        )
        if result.returncode or not report.is_file():
            raise RuntimeError(f"LRCPlus smoke test failed (exit code {result.returncode}).")
        print(report.read_text(encoding="utf-8").strip())
        if profile_seeks:
            print((fixtures / "seek-memory.json").read_text(encoding="utf-8"))
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        for diagnostic in (report, data / "logs" / "crash.log"):
            if diagnostic.is_file():
                print(diagnostic.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
        raise


def main():
    if sys.platform != "win32":
        raise SystemExit("The release smoke test requires Windows.")
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--profile-seeks", action="store_true",
                        help="Measure app and Rust helper memory across 160 seeks.")
    args = parser.parse_args()
    architecture = windows_architecture()
    archive = PROJECT_ROOT / "dist" / "release" / f"LRCPlus-v{APP_VERSION}-Windows-{architecture}.zip"
    with TemporaryDirectory(prefix="lrcplus-smoke-") as work:
        smoke_release(archive, architecture, Path(work), profile_seeks=args.profile_seeks)


if __name__ == "__main__":
    main()
