"""Update package validation shared by the app and standalone installer.

Keep this module independent of app settings and GUI libraries so the installer
can run with its own small runtime while replacing an installation.
"""
from __future__ import annotations

from datetime import date
import json
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import zipfile

from packaging.version import InvalidVersion, Version


MAX_ASSET_SIZE = 2 * 1024**3
_DATE_VERSION = re.compile(
    r"^v?(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?((?:a|b|rc)\d+|\.post\d+|\.dev\d+|-exp)?$",
    re.IGNORECASE,
)
MAX_ARCHIVE_ENTRIES = 50000
MAX_UNPACKED_SIZE = 4 * 1024**3
MAX_FILE_SIZE = 2 * 1024**3


class UpdateError(RuntimeError):
    """An update could not be safely checked or prepared."""


def parse_version(value: str) -> Version:
    """Parse PEP 440 and YYYY-MM-DD[.N] tags, defaulting the revision to zero."""
    if not isinstance(value, str):
        raise UpdateError("The release version is not text.")
    raw = value.strip()
    date_match = _DATE_VERSION.fullmatch(raw)
    if date_match:
        year, month, day, revision, suffix = date_match.groups()
        try:
            date(int(year), int(month), int(day))
        except ValueError as error:
            raise UpdateError(f"Unsupported release version: {value!r}.") from error
        version_suffix = ".dev0" if suffix and suffix.casefold() == "-exp" else suffix or ""
        raw = f"{year}.{int(month)}.{int(day)}.{int(revision or '0')}{version_suffix}"
    elif raw[:1].lower() == "v":
        raw = raw[1:]
    try:
        return Version(raw)
    except InvalidVersion as error:
        raise UpdateError(f"Unsupported release version: {value!r}.") from error


def extract_update_archive(
    archive: Path,
    staging_parent: Path,
    *,
    expected_version: str,
    architecture: str,
) -> Path:
    """Validate supported release roots and extract into a normalized LRCPlus folder."""
    archive = Path(archive)
    staging_parent = Path(staging_parent).resolve()
    if architecture not in {"x64", "arm64"}:
        raise UpdateError("The update package architecture is unsupported.")
    expected = parse_version(expected_version)
    version_text = expected_version[1:] if expected_version[:1].lower() == "v" else expected_version
    versioned_root = f"LRCPlus-v{version_text}-Windows-{architecture}"
    allowed_roots = {"LRCPlus", versioned_root}
    staging_parent.mkdir(parents=True, exist_ok=True)
    app_root = staging_parent / "LRCPlus"
    if app_root.exists():
        raise UpdateError("The update staging directory is not empty.")

    try:
        with zipfile.ZipFile(archive) as bundle:
            entries = bundle.infolist()
            if not entries or len(entries) > MAX_ARCHIVE_ENTRIES:
                raise UpdateError("The update archive has an invalid number of entries.")
            total_size = sum(item.file_size for item in entries)
            if total_size > MAX_UNPACKED_SIZE:
                raise UpdateError("The update archive exceeds the extraction size limit.")

            seen = set()
            files = set()
            archive_root = None
            for item in entries:
                raw_name = item.filename.replace("\\", "/")
                path = PurePosixPath(raw_name)
                parts = path.parts
                raw_parts = raw_name.rstrip("/").split("/")
                if (not raw_name or "\x00" in raw_name or path.is_absolute()
                        or not parts or parts[0] not in allowed_roots
                        or (archive_root is not None and parts[0] != archive_root)
                        or any(part in {"", ".", ".."} for part in raw_parts)
                        or any(":" in part for part in parts)):
                    raise UpdateError("The update archive contains an unsafe path.")
                archive_root = parts[0]
                key = "/".join(parts).casefold()
                if key in seen:
                    raise UpdateError("The update archive contains duplicate or conflicting paths.")
                seen.add(key)
                mode = item.external_attr >> 16
                file_type = stat.S_IFMT(mode)
                if stat.S_ISLNK(mode) or file_type not in {0, stat.S_IFREG, stat.S_IFDIR}:
                    raise UpdateError("The update archive contains an unsupported file type.")
                if item.file_size > MAX_FILE_SIZE:
                    raise UpdateError("The update archive contains an oversized file.")
                if item.file_size and item.file_size / max(1, item.compress_size) > 1000:
                    raise UpdateError("The update archive contains an excessive compression ratio.")
                if not item.is_dir():
                    files.add("/".join(parts[1:]).casefold())

            required = {"lrcplus.exe"}
            if archive_root == "LRCPlus":
                required.update({"lrcplusupdater.exe", "lrcplus-update.json"})
            if not required.issubset(files):
                raise UpdateError("The update archive is missing required application files.")

            app_root.mkdir()
            root = app_root.resolve()
            for item in entries:
                parts = PurePosixPath(item.filename.replace("\\", "/")).parts[1:]
                if not parts:
                    continue
                target = root.joinpath(*parts)
                if not target.resolve().is_relative_to(root):
                    raise UpdateError("The update archive contains an unsafe path.")
                if item.is_dir():
                    target.mkdir(parents=True, exist_ok=True)
                    continue
                target.parent.mkdir(parents=True, exist_ok=True)
                with bundle.open(item) as source, target.open("xb") as output:
                    shutil.copyfileobj(source, output, length=1024 * 1024)

        manifest_path = app_root / "lrcplus-update.json"
        if manifest_path.exists():
            manifest_data = json.loads(manifest_path.read_text(encoding="utf-8"))
            if (not isinstance(manifest_data, dict)
                    or manifest_data.get("version") != expected_version
                    or manifest_data.get("architecture") != architecture):
                raise UpdateError("The update package version or architecture did not match the release.")
        return app_root
    except UpdateError:
        shutil.rmtree(app_root, ignore_errors=True)
        raise
    except (OSError, ValueError, zipfile.BadZipFile, RuntimeError) as error:
        shutil.rmtree(app_root, ignore_errors=True)
        raise UpdateError("The update archive is corrupt or incompatible.") from error
