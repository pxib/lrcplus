"""Exercise the updater end-to-end with a synthetic release in temporary folders."""
from __future__ import annotations

import hashlib
import io
import json
import logging
import os
from pathlib import Path
import platform
import re
import shutil
import sys
import tempfile
import zipfile
import urllib.request

PROJECT_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PROJECT_ROOT))

from core.app_info import APP_VERSION
from core import updater as updater_module
from core.updater import (
    ReleaseAsset,
    download_asset,
    extract_update_archive,
    parse_version,
)
import updater_helper


class _Response:
    def __init__(self, content: bytes):
        self._content = io.BytesIO(content)
        self.headers = {"Content-Length": str(len(content))}

    def __enter__(self):
        return self

    def __exit__(self, *_args):
        return None

    def geturl(self):
        return "https://release-assets.githubusercontent.com/fake-release.zip"

    def read(self, size=-1):
        return self._content.read(size)


class _Process:
    def poll(self):
        return None


def _next_fake_version() -> str:
    match = re.fullmatch(r"(\d{4}-\d{2}-\d{2})\.(\d+)", APP_VERSION)
    if match:
        return f"{match.group(1)}.{int(match.group(2)) + 1}"
    version = parse_version(APP_VERSION)
    release = list(version.release)
    release[-1] += 1
    return ".".join(str(part) for part in release)


def _architecture() -> str:
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise RuntimeError(f"Unsupported test architecture: {platform.machine()}")


def _fake_archive(version: str, architecture: str) -> bytes:
    manifest = json.dumps({"version": version, "architecture": architecture})
    files = {
        "LRCPlus/LRCPlus.exe": b"FAKE NEW LRCPLUS EXECUTABLE",
        "LRCPlus/LRCPlusUpdater.exe": b"FAKE STANDALONE UPDATER",
        "LRCPlus/lrcplus-update.json": manifest.encode("utf-8"),
        "LRCPlus/_internal/fake-runtime.dll": b"FAKE RUNTIME",
        "LRCPlus/plugins/core/fake_builtin/plugin.py": b"BUILTIN_PLUGIN = True",
    }
    output = io.BytesIO()
    with zipfile.ZipFile(output, "w", zipfile.ZIP_DEFLATED) as archive:
        for name, content in files.items():
            archive.writestr(name, content)
    return output.getvalue()


def _run_scenario(root: Path, *, fail_launch: bool) -> None:
    version = _next_fake_version()
    architecture = _architecture()
    package = _fake_archive(version, architecture)
    transaction_id = "a" * 31 + ("2" if fail_launch else "1")
    data_root = Path(os.environ["LRCPLUS_DATA_DIR"])
    transaction = data_root / "updates" / "transactions" / transaction_id
    transaction.mkdir(parents=True)

    install_dir = root / ("install-failure" if fail_launch else "install-success") / "LRCPlus"
    (install_dir / "plugins" / "community" / "test_user_plugin").mkdir(parents=True)
    (install_dir / "LRCPlus.exe").write_bytes(b"FAKE OLD LRCPLUS EXECUTABLE")
    (install_dir / "lrc_player_settings.json").write_text(
        '{"playback": {"volume": 37}}', encoding="utf-8"
    )
    (install_dir / "user-file.txt").write_text("preserve me", encoding="utf-8")
    (install_dir / "plugins" / "community" / "test_user_plugin" / "plugin.py").write_text(
        "USER_PLUGIN = True", encoding="utf-8"
    )

    asset_name = f"LRCPlus-v{version}-Windows-{architecture}.zip"
    asset = ReleaseAsset(
        asset_name,
        f"https://github.com/pxib/lrcplus/releases/download/{version}/{asset_name}",
        len(package),
        hashlib.sha256(package).hexdigest(),
    )
    original_download = urllib.request.urlopen
    urllib.request.urlopen = lambda *_args, **_kwargs: _Response(package)
    try:
        archive_path = download_asset(asset, transaction / "release.zip")
    finally:
        urllib.request.urlopen = original_download

    extract_update_archive(
        archive_path,
        transaction / "preflight",
        expected_version=version,
        architecture=architecture,
    )
    request_path = transaction / "request.json"
    request_path.write_text(json.dumps({
        "install_dir": str(install_dir),
        "version": version,
        "current_version": APP_VERSION,
        "architecture": architecture,
        "asset_name": asset_name,
        "sha256": asset.sha256,
        "size": asset.size,
        "process_id": os.getpid(),
    }), encoding="utf-8")

    old_schedule = updater_helper._schedule_recovery
    old_remove = updater_helper._remove_recovery
    old_popen = updater_helper.subprocess.Popen
    updater_helper._schedule_recovery = lambda _transaction: None
    updater_helper._remove_recovery = lambda _transaction_id: None

    def launch(_args, **_kwargs):
        if fail_launch:
            raise OSError("Synthetic launch failure")
        (transaction / "health.json").write_text("{}", encoding="utf-8")
        return _Process()

    updater_helper.subprocess.Popen = launch
    try:
        if fail_launch:
            try:
                updater_helper.install_update(request_path, wait_for_process=False)
            except OSError as error:
                if "Synthetic launch failure" not in str(error):
                    raise
            else:
                raise AssertionError("The synthetic launch failure was not exercised.")
            if (install_dir / "LRCPlus.exe").read_bytes() != b"FAKE OLD LRCPLUS EXECUTABLE":
                raise AssertionError("Rollback did not restore the previous executable.")
            print("PASS: simulated launch failure restored the previous installation")
        else:
            updater_helper.install_update(request_path, wait_for_process=False)
            if (install_dir / "LRCPlus.exe").read_bytes() != b"FAKE NEW LRCPLUS EXECUTABLE":
                raise AssertionError("The synthetic release was not installed.")
            if (install_dir / "lrc_player_settings.json").read_text(encoding="utf-8") != '{"playback": {"volume": 37}}':
                raise AssertionError("User settings were not preserved.")
            if (install_dir / "user-file.txt").read_text(encoding="utf-8") != "preserve me":
                raise AssertionError("Unknown user data was not preserved.")
            if not (install_dir / "plugins" / "community" / "test_user_plugin" / "plugin.py").is_file():
                raise AssertionError("The user plugin was not preserved.")
            backup = install_dir.with_name(f"LRCPlus.backup-{transaction_id}")
            if not (backup / "LRCPlus.exe").is_file():
                raise AssertionError("The previous release backup was not retained.")
            print(f"PASS: synthetic {APP_VERSION} -> {version} update installed in a temporary folder")
            print("PASS: settings, unknown user files, and custom plugins were preserved")
            print("PASS: previous installation backup was retained")
    finally:
        updater_helper.subprocess.Popen = old_popen
        updater_helper._schedule_recovery = old_schedule
        updater_helper._remove_recovery = old_remove


def _close_test_logs() -> None:
    for name in ("lrcplus.update-check", "lrcplus.updater"):
        logger = logging.getLogger(name)
        for handler in logger.handlers[:]:
            logger.removeHandler(handler)
            handler.close()
    updater_module._APP_LOGGER = None


def main() -> int:
    old_data_dir = os.environ.get("LRCPLUS_DATA_DIR")
    with tempfile.TemporaryDirectory(prefix="lrcplus-fake-update-") as temporary:
        root = Path(temporary)
        os.environ["LRCPLUS_DATA_DIR"] = str(root / "app-data")
        try:
            _run_scenario(root, fail_launch=False)
            _run_scenario(root, fail_launch=True)
        finally:
            _close_test_logs()
            if old_data_dir is None:
                os.environ.pop("LRCPLUS_DATA_DIR", None)
            else:
                os.environ["LRCPLUS_DATA_DIR"] = old_data_dir
    print("All fake updater scenarios passed. Temporary data was removed.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
