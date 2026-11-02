"""Independent, standard-library update installer for frozen Windows builds."""
from __future__ import annotations

import ctypes
import hashlib
import json
import logging
from logging.handlers import RotatingFileHandler
import os
from pathlib import Path
import re
import shutil
import subprocess
import sys
import time
import uuid

from core.updater import MAX_ASSET_SIZE, UpdateError, extract_update_archive, parse_version


APP_FOLDER = "LRCPlus"
TRANSACTION_ID_LENGTH = 32
INSTALL_PHASES = {"prepared", "backup_moved", "installed", "launching"}
KNOWN_APP_PATHS = {
    "lrcplus.exe", "lrcplusupdater.exe", "assets", "core", "lyrics", "ui",
    "plugins", "lib", "include", "scripts", "share", "_internal",
    "base_library.zip",
}


def _app_data_root() -> Path:
    override = os.environ.get("LRCPLUS_DATA_DIR")
    if override:
        return Path(override).expanduser().resolve()
    root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local")
    return (root / APP_FOLDER).resolve()


def _transactions_root() -> Path:
    return _app_data_root() / "updates" / "transactions"


def _logger() -> logging.Logger:
    logger = logging.getLogger("lrcplus.updater")
    if logger.handlers:
        return logger
    folder = _app_data_root() / "logs"
    folder.mkdir(parents=True, exist_ok=True)
    handler = RotatingFileHandler(
        folder / "updater.log", maxBytes=2 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
    logger.addHandler(handler)
    logger.setLevel(logging.INFO)
    return logger


def _write_json(path: Path, payload: dict) -> None:
    temp = path.with_name(path.name + ".tmp")
    with temp.open("w", encoding="utf-8") as stream:
        stream.write(json.dumps(payload, indent=2))
        stream.flush()
        os.fsync(stream.fileno())
    os.replace(temp, path)


def _read_request(request_path: Path) -> dict:
    request_path = Path(request_path)
    if request_path.is_symlink():
        raise UpdateError("The update request path is invalid.")
    request_path = request_path.resolve(strict=True)
    tx_root = _transactions_root()
    if request_path.parent.is_symlink():
        raise UpdateError("The update transaction path is invalid.")
    if (request_path.name != "request.json"
            or request_path.parent.parent.resolve() != tx_root.resolve()
            or len(request_path.parent.name) != TRANSACTION_ID_LENGTH
            or any(char not in "0123456789abcdef" for char in request_path.parent.name)):
        raise UpdateError("The update request is not in a trusted transaction directory.")
    if request_path.stat().st_size > 64 * 1024:
        raise UpdateError("The update request is too large.")
    try:
        request = json.loads(request_path.read_text(encoding="utf-8"))
    except (OSError, ValueError, UnicodeError) as error:
        raise UpdateError("The update request is invalid.") from error
    if not isinstance(request, dict):
        raise UpdateError("The update request is invalid.")

    transaction = request_path.parent.resolve()
    archive_path = transaction / "release.zip"
    if archive_path.is_symlink():
        raise UpdateError("The update archive path is invalid.")
    archive = archive_path.resolve()
    install_value = request.get("install_dir")
    if not isinstance(install_value, str):
        raise UpdateError("The installation location is invalid.")
    install_candidate = Path(install_value).expanduser()
    if install_candidate.is_symlink():
        raise UpdateError("The installation location is invalid.")
    install_dir = install_candidate.resolve()
    if (install_dir.name.casefold() != APP_FOLDER.casefold()
            or not (install_dir / "LRCPlus.exe").is_file()
            or (install_dir / "LRCPlus.exe").is_symlink()):
        raise UpdateError("The LRCPlus installation could not be verified.")
    version = request.get("version")
    parse_version(version)
    current_version = request.get("current_version")
    parse_version(current_version)
    architecture = request.get("architecture")
    if architecture not in {"x64", "arm64"}:
        raise UpdateError("The update architecture is unsupported.")
    asset_name = request.get("asset_name")
    expected_asset = f"LRCPlus-v{version}-Windows-{architecture}.zip"
    if asset_name != expected_asset:
        raise UpdateError("The selected release asset does not match the update manifest.")
    digest = request.get("sha256")
    if not isinstance(digest, str) or re.fullmatch(r"[0-9a-f]{64}", digest.casefold()) is None:
        raise UpdateError("The update checksum is invalid.")
    size = request.get("size")
    if not isinstance(size, int) or size <= 0 or size > MAX_ASSET_SIZE:
        raise UpdateError("The update size is invalid.")
    process_id = request.get("process_id")
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise UpdateError("The LRCPlus process could not be identified.")
    return {
        "transaction": transaction,
        "archive": archive,
        "install_dir": install_dir,
        "version": version,
        "current_version": current_version,
        "architecture": architecture,
        "asset_name": asset_name,
        "sha256": digest.casefold(),
        "size": size,
        "process_id": process_id,
        "transaction_id": transaction.name,
    }


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as stream:
        for chunk in iter(lambda: stream.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def _process_image_path(process_id: int) -> str:
    if os.name != "nt":
        raise UpdateError("The standalone updater is supported on Windows only.")
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.QueryFullProcessImageNameW.argtypes = [
        wintypes.HANDLE, wintypes.DWORD, wintypes.LPWSTR, ctypes.POINTER(wintypes.DWORD),
    ]
    kernel32.QueryFullProcessImageNameW.restype = wintypes.BOOL
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    process = kernel32.OpenProcess(0x1000 | 0x00100000, False, process_id)
    if not process:
        if ctypes.get_last_error() in {87, 1168}:
            return ""
        raise UpdateError("LRCPlus could not be safely identified for shutdown.")
    try:
        size = wintypes.DWORD(32768)
        buffer = ctypes.create_unicode_buffer(size.value)
        if not kernel32.QueryFullProcessImageNameW(process, 0, buffer, ctypes.byref(size)):
            raise UpdateError("LRCPlus could not be safely identified for shutdown.")
        return buffer.value
    finally:
        kernel32.CloseHandle(process)


def _wait_for_application(process_id: int, expected_exe: Path) -> None:
    if os.name != "nt":
        raise UpdateError("The standalone updater is supported on Windows only.")
    from ctypes import wintypes
    kernel32 = ctypes.WinDLL("kernel32", use_last_error=True)
    kernel32.OpenProcess.argtypes = [wintypes.DWORD, wintypes.BOOL, wintypes.DWORD]
    kernel32.OpenProcess.restype = wintypes.HANDLE
    kernel32.WaitForSingleObject.argtypes = [wintypes.HANDLE, wintypes.DWORD]
    kernel32.WaitForSingleObject.restype = wintypes.DWORD
    kernel32.CloseHandle.argtypes = [wintypes.HANDLE]
    kernel32.CloseHandle.restype = wintypes.BOOL
    expected = str(expected_exe.resolve()).casefold()
    while True:
        image = _process_image_path(process_id)
        if not image:
            return
        if str(Path(image).resolve()).casefold() != expected:
            raise UpdateError("The process selected for shutdown is not this LRCPlus installation.")
        process = kernel32.OpenProcess(0x00100000 | 0x001000, False, process_id)
        if not process:
            if ctypes.get_last_error() in {87, 1168}:
                return
            raise UpdateError("Could not wait for LRCPlus to close safely.")
        try:
            result = kernel32.WaitForSingleObject(process, 500)
            if result == 0:
                return
            if result not in {0x102, 0xFFFFFFFF}:
                raise UpdateError("Could not wait for LRCPlus to close safely.")
        finally:
            kernel32.CloseHandle(process)


def _copy_preserved_files(old_root: Path, new_root: Path) -> None:
    for child in old_root.iterdir():
        if child.is_symlink():
            continue
        target = new_root / child.name
        key = child.name.casefold()
        if key == "plugins" and child.is_dir() and target.is_dir():
            for category in child.iterdir():
                if category.is_symlink():
                    continue
                category_target = target / category.name
                if category.is_dir() and category_target.is_dir():
                    for plugin in category.iterdir():
                        if plugin.is_symlink():
                            continue
                        plugin_target = category_target / plugin.name
                        if not plugin_target.exists():
                            shutil.copytree(plugin, plugin_target, symlinks=True) if plugin.is_dir() else shutil.copy2(plugin, plugin_target)
                elif not category_target.exists():
                    shutil.copytree(category, category_target, symlinks=True) if category.is_dir() else shutil.copy2(category, category_target)
            continue
        if key in KNOWN_APP_PATHS or target.exists():
            continue
        if child.is_dir():
            shutil.copytree(child, target, symlinks=True)
        elif child.is_file():
            shutil.copy2(child, target)


def _journal(transaction: Path, install_dir: Path, version: str, phase: str) -> dict:
    return {
        "transaction_id": transaction.name,
        "install_dir": str(install_dir),
        "version": version,
        "phase": phase,
    }


def _validate_transaction_path(transaction: Path) -> Path:
    transaction = Path(transaction).resolve(strict=True)
    if (transaction.parent != _transactions_root().resolve()
            or len(transaction.name) != TRANSACTION_ID_LENGTH
            or any(char not in "0123456789abcdef" for char in transaction.name)):
        raise UpdateError("The recovery transaction is not in a trusted location.")
    return transaction


def _schedule_recovery(transaction: Path) -> None:
    import winreg

    transaction = _validate_transaction_path(transaction)
    helper_path = Path(sys.executable).resolve() if getattr(sys, "frozen", False) else Path(__file__).resolve()
    command = ([str(helper_path), "--recover", str(transaction)] if getattr(sys, "frozen", False)
               else [sys.executable, str(helper_path), "--recover", str(transaction)])
    registry_path = r"Software\Microsoft\Windows\CurrentVersion\RunOnce"
    value_name = f"!LRCPlusUpdateRecovery-{transaction.name}"
    with winreg.CreateKeyEx(winreg.HKEY_CURRENT_USER, registry_path, 0, winreg.KEY_SET_VALUE) as key:
        winreg.SetValueEx(key, value_name, 0, winreg.REG_SZ, subprocess.list2cmdline(command))


def _remove_recovery(transaction_id: str) -> None:
    if len(transaction_id) != TRANSACTION_ID_LENGTH:
        return
    try:
        import winreg
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Microsoft\Windows\CurrentVersion\RunOnce",
            0,
            winreg.KEY_SET_VALUE,
        ) as key:
            winreg.DeleteValue(key, f"!LRCPlusUpdateRecovery-{transaction_id}")
    except (ImportError, OSError):
        pass


def _restore_transaction(transaction: Path, journal: dict, logger: logging.Logger) -> bool:
    transaction_id = transaction.name
    install_dir = Path(journal["install_dir"]).resolve()
    if install_dir.name.casefold() != APP_FOLDER.casefold():
        logger.error("Refusing recovery for invalid installation path")
        return False
    backup = install_dir.with_name(f"{install_dir.name}.backup-{transaction_id}")
    failed = install_dir.with_name(f"{install_dir.name}.failed-{transaction_id}")
    if not backup.is_dir():
        return install_dir.is_dir()
    try:
        if install_dir.exists():
            if failed.exists():
                shutil.rmtree(failed)
            os.replace(install_dir, failed)
        os.replace(backup, install_dir)
        if failed.exists():
            shutil.rmtree(failed, ignore_errors=True)
        _write_json(transaction / "journal.json", {**journal, "phase": "rolled_back"})
        logger.warning("Restored the previous installation for transaction %s", transaction_id)
        return (install_dir / "LRCPlus.exe").is_file()
    except OSError:
        logger.exception("Rollback failed for transaction %s", transaction_id)
        return False


def recover_incomplete_transactions(skip_transaction: str | None = None) -> None:
    logger = _logger()
    root = _transactions_root()
    if not root.is_dir():
        return
    for transaction in root.iterdir():
        if (not transaction.is_dir() or transaction.name == skip_transaction
                or len(transaction.name) != TRANSACTION_ID_LENGTH):
            continue
        journal_path = transaction / "journal.json"
        try:
            journal = json.loads(journal_path.read_text(encoding="utf-8"))
        except (OSError, ValueError, UnicodeError):
            continue
        if not isinstance(journal, dict) or journal.get("phase") not in INSTALL_PHASES:
            continue
        _recover_transaction(transaction, journal, logger)


def _recover_transaction(transaction: Path, journal: dict, logger: logging.Logger) -> bool:
    transaction = _validate_transaction_path(transaction)
    transaction_id = transaction.name
    if (transaction / "health.json").is_file():
        _write_json(transaction / "journal.json", {**journal, "phase": "healthy"})
        _remove_recovery(transaction_id)
        return True
    restored = _restore_transaction(transaction, journal, logger)
    if restored:
        _remove_recovery(transaction_id)
    return restored


def _wait_for_health(transaction: Path, process: subprocess.Popen, timeout: float = 60) -> bool:
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        if (transaction / "health.json").is_file():
            return True
        if process.poll() is not None:
            return False
        time.sleep(0.25)
    return (transaction / "health.json").is_file()


def install_update(request_path: Path, *, wait_for_process: bool = True) -> Path:
    logger = _logger()
    request = _read_request(Path(request_path))
    transaction = request["transaction"]
    install_dir = request["install_dir"]
    transaction_id = request["transaction_id"]
    version = request["version"]
    recover_incomplete_transactions(skip_transaction=transaction_id)
    logger.info(
        "Starting update from %s to %s using %s",
        request["current_version"], version, request["asset_name"],
    )

    if not request["archive"].is_file() or request["archive"].stat().st_size != request["size"]:
        raise UpdateError("The downloaded update package is missing or incomplete.")
    if _sha256(request["archive"]) != request["sha256"]:
        raise UpdateError("The update package failed independent SHA-256 verification.")

    verified_parent = transaction / "verified"
    verified_root = extract_update_archive(
        request["archive"], verified_parent,
        expected_version=version, architecture=request["architecture"],
    )
    _copy_preserved_files(install_dir, verified_root)

    unpacked_size = sum(path.stat().st_size for path in verified_root.rglob("*") if path.is_file())
    install_parent = install_dir.parent
    if shutil.disk_usage(install_parent).free < unpacked_size:
        raise UpdateError("There is not enough free disk space to install the update safely.")
    stage_root = install_parent / f".{APP_FOLDER}-staging-{transaction_id}"
    backup_root = install_parent / f"{APP_FOLDER}.backup-{transaction_id}"
    if stage_root.exists() or backup_root.exists():
        raise UpdateError("A conflicting update transaction already exists.")
    shutil.copytree(verified_root, stage_root)
    if not (stage_root / "LRCPlus.exe").is_file() or not (stage_root / "LRCPlusUpdater.exe").is_file():
        shutil.rmtree(stage_root, ignore_errors=True)
        raise UpdateError("The staged application failed validation.")

    journal = _journal(transaction, install_dir, version, "prepared")
    _write_json(transaction / "journal.json", journal)
    try:
        if wait_for_process:
            _wait_for_application(request["process_id"], install_dir / "LRCPlus.exe")
        _schedule_recovery(transaction)
        os.replace(install_dir, backup_root)
        logger.info("Created installation backup at %s", backup_root)
        journal = _journal(transaction, install_dir, version, "backup_moved")
        _write_json(transaction / "journal.json", journal)
        os.replace(stage_root, install_dir)
        logger.info("Installed staged release %s", version)
        journal = _journal(transaction, install_dir, version, "installed")
        _write_json(transaction / "journal.json", journal)
        new_exe = install_dir / "LRCPlus.exe"
        if not new_exe.is_file() or not (install_dir / "lrcplus-update.json").is_file():
            raise UpdateError("The installed application failed its file checks.")

        journal = _journal(transaction, install_dir, version, "launching")
        _write_json(transaction / "journal.json", journal)
        flags = getattr(subprocess, "DETACHED_PROCESS", 0) | getattr(subprocess, "CREATE_NEW_PROCESS_GROUP", 0)
        process = subprocess.Popen(
            [str(new_exe), f"--lrcplus-update-health={transaction_id}"],
            cwd=install_dir,
            close_fds=True,
            creationflags=flags,
        )
        if not _wait_for_health(transaction, process):
            raise UpdateError("LRCPlus did not complete its startup check. The previous version will be restored.")
        _write_json(transaction / "journal.json", _journal(transaction, install_dir, version, "healthy"))
        _remove_recovery(transaction_id)
        logger.info("Update to %s passed its startup check; backup retained at %s", version, backup_root)
        return install_dir
    except Exception:
        logger.exception("Installation failed for transaction %s", transaction_id)
        if backup_root.exists():
            failed_root = install_dir.with_name(f"{APP_FOLDER}.failed-{transaction_id}")
            try:
                if install_dir.exists():
                    if failed_root.exists():
                        shutil.rmtree(failed_root)
                    os.replace(install_dir, failed_root)
                os.replace(backup_root, install_dir)
                shutil.rmtree(failed_root, ignore_errors=True)
                journal = _journal(transaction, install_dir, version, "rolled_back")
                _write_json(transaction / "journal.json", journal)
                if not (install_dir / "LRCPlus.exe").is_file():
                    raise UpdateError("The previous installation could not be verified after rollback.")
                _remove_recovery(transaction_id)
            except OSError as rollback_error:
                logger.exception("Rollback failed; backup retained at %s", backup_root)
                raise UpdateError(
                    f"Installation failed and automatic rollback could not finish. The backup remains at {backup_root}."
                ) from rollback_error
        raise
    finally:
        shutil.rmtree(stage_root, ignore_errors=True)


def _show_error(message: str) -> None:
    if os.name == "nt":
        ctypes.windll.user32.MessageBoxW(None, message, "LRCPlus Update", 0x10)


def main(argv=None) -> int:
    args = list(sys.argv[1:] if argv is None else argv)
    if len(args) == 2 and args[0] == "--recover":
        try:
            transaction = _validate_transaction_path(Path(args[1]))
            journal = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
            if not isinstance(journal, dict) or journal.get("phase") not in INSTALL_PHASES:
                return 0
            if not _recover_transaction(transaction, journal, _logger()):
                _show_error("LRCPlus could not restore the previous version automatically. The backup has been preserved.")
                return 1
            return 0
        except Exception as error:
            _logger().exception("Recovery failed")
            _show_error(f"LRCPlus update recovery failed. The previous installation may still be available in the backup.\n\n{error}")
            return 1
    if len(args) != 2 or args[0] != "--request":
        _show_error("The update request could not be opened.")
        return 2
    try:
        install_update(Path(args[1]))
    except Exception as error:
        logger = _logger()
        logger.exception("Updater stopped with an error")
        _show_error(f"LRCPlus could not install the update. The previous version was preserved when possible.\n\n{error}")
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
