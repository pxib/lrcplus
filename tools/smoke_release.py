"""Launch and exercise a Windows release ZIP with an isolated profile."""

import json
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


def smoke_release(archive, architecture, work):
    app = extract_update_archive(archive, work / "install",
                                 expected_version=APP_VERSION, architecture=architecture)
    fixtures = work / "fixtures"
    fixtures.mkdir()
    write_fixture(fixtures / "sample.wav")
    settings = work / "settings.json"
    settings.write_text(json.dumps({
        "updates": {"automatic_check": False},
        "appearance": {"artwork_source": "album_only"},
        "disabled_plugins": ["discord_rpc"],
    }), encoding="utf-8")
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
    except (OSError, RuntimeError, subprocess.TimeoutExpired):
        for diagnostic in (report, data / "logs" / "crash.log"):
            if diagnostic.is_file():
                print(diagnostic.read_text(encoding="utf-8", errors="replace"), file=sys.stderr)
        raise


def main():
    if sys.platform != "win32":
        raise SystemExit("The release smoke test requires Windows.")
    architecture = windows_architecture()
    archive = PROJECT_ROOT / "dist" / "release" / f"LRCPlus-v{APP_VERSION}-Windows-{architecture}.zip"
    with TemporaryDirectory(prefix="lrcplus-smoke-") as work:
        smoke_release(archive, architecture, Path(work))


if __name__ == "__main__":
    main()
