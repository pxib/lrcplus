"""GitHub release discovery and update metadata validation."""
from __future__ import annotations

from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import json
import logging
import os
import platform
import re
import shutil
import stat
import sys
import tempfile
import threading
import urllib.error
import urllib.parse
import urllib.request
import zipfile
from pathlib import Path, PurePosixPath
from logging.handlers import RotatingFileHandler

from packaging.version import InvalidVersion, Version

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL


GITHUB_REPOSITORY = "pxib/lrcplus"
API_ROOT = f"https://api.github.com/repos/{GITHUB_REPOSITORY}"
MAX_ASSET_SIZE = 2 * 1024**3
_DATE_VERSION = re.compile(
    r"^v?(\d{4})-(\d{2})-(\d{2})(?:\.(\d+))?((?:a|b|rc)\d+|\.post\d+|\.dev\d+|-exp)?$",
    re.IGNORECASE,
)
_SHA256 = re.compile(r"^sha256:([0-9a-f]{64})$", re.IGNORECASE)
MAX_ARCHIVE_ENTRIES = 50000
MAX_UNPACKED_SIZE = 4 * 1024**3
MAX_FILE_SIZE = 2 * 1024**3
_ALLOWED_DOWNLOAD_HOSTS = {
    "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
}
_APP_LOGGER = None


class UpdateError(RuntimeError):
    """An update could not be safely checked or prepared."""


@dataclass(frozen=True)
class ReleaseAsset:
    name: str
    url: str
    size: int
    sha256: str


@dataclass(frozen=True)
class Release:
    tag: str
    version: Version
    name: str
    published_at: str
    body: str
    prerelease: bool
    html_url: str
    asset: ReleaseAsset

    @property
    def package_version(self) -> str:
        return package_version_from_tag(self.tag)


def package_version_from_tag(tag: str) -> str:
    if not isinstance(tag, str):
        raise UpdateError("The release version is not text.")
    version = tag[1:] if tag[:1].lower() == "v" else tag
    return version[:-4] if version.casefold().endswith("-exp") else version


def build_handoff_request(
    install_dir: Path,
    release: Release,
    architecture: str,
    process_id: int,
    *,
    current_version: str = APP_VERSION,
) -> dict:
    """Build the exact validated request consumed by the standalone helper."""
    version = release.package_version
    target = parse_version(release.tag)
    current = parse_version(current_version)
    if target <= current:
        raise UpdateError("The update version must be newer than the installed version.")
    if architecture not in {"x64", "arm64"}:
        raise UpdateError("The update architecture is unsupported.")
    if isinstance(process_id, bool) or not isinstance(process_id, int) or process_id <= 0:
        raise UpdateError("The LRCPlus process could not be identified.")
    expected_asset = f"LRCPlus-v{version}-Windows-{architecture}.zip"
    if release.asset.name != expected_asset:
        raise UpdateError("The selected release asset does not match the update version and architecture.")
    return {
        "install_dir": str(Path(install_dir).resolve()),
        "version": version,
        "current_version": current_version,
        "architecture": architecture,
        "asset_name": release.asset.name,
        "sha256": release.asset.sha256,
        "size": release.asset.size,
        "process_id": process_id,
    }


def find_updater_executable(staged_root: Path, install_root: Path) -> Path:
    """Use the staged updater when present, otherwise the trusted installed one."""
    for root in (Path(staged_root), Path(install_root)):
        candidate = root / "LRCPlusUpdater.exe"
        if candidate.is_file() and not candidate.is_symlink():
            return candidate
    raise UpdateError("The update package and current installation both lack LRCPlusUpdater.exe.")


def log_update_event(message: str) -> None:
    global _APP_LOGGER
    try:
        if _APP_LOGGER is None:
            from core.settings import get_app_data_dir
            log_dir = get_app_data_dir() / "logs"
            log_dir.mkdir(parents=True, exist_ok=True)
            logger = logging.getLogger("lrcplus.update-check")
            if not logger.handlers:
                handler = RotatingFileHandler(
                    log_dir / "update-check.log", maxBytes=1024 * 1024,
                    backupCount=2, encoding="utf-8",
                )
                handler.setFormatter(logging.Formatter("%(asctime)s %(levelname)s %(message)s"))
                logger.addHandler(handler)
                logger.setLevel(logging.INFO)
            _APP_LOGGER = logger
        _APP_LOGGER.info("%s", message)
    except OSError:
        logging.getLogger("lrcplus.update-check").warning("%s", message)


def should_check_automatically(update_settings: dict, now: datetime | None = None) -> bool:
    if not isinstance(update_settings, dict) or not update_settings.get("automatic_check", True):
        return False
    last_attempt = str(update_settings.get("last_attempt", "") or "")
    if not last_attempt:
        return True
    try:
        previous = datetime.fromisoformat(last_attempt.replace("Z", "+00:00"))
    except ValueError:
        return True
    if previous.tzinfo is None:
        previous = previous.replace(tzinfo=timezone.utc)
    current = now or datetime.now(timezone.utc)
    if current.tzinfo is None:
        current = current.replace(tzinfo=timezone.utc)
    return current - previous >= timedelta(hours=24)


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


def _github_json(url: str):
    request = urllib.request.Request(url, headers={
        "Accept": "application/vnd.github+json",
        "User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    try:
        with urllib.request.urlopen(request, timeout=20) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname != "api.github.com":
                raise UpdateError("The update API redirected to an untrusted location.")
            data = response.read(4 * 1024 * 1024 + 1)
    except urllib.error.HTTPError as error:
        if error.code in {403, 429}:
            raise UpdateError("GitHub rate-limited the update check. Try again later.") from error
        if error.code == 404:
            if url.rstrip("/").endswith("/releases/latest"):
                raise UpdateError(
                    "No stable LRCPlus release is available. Select the Prerelease channel to check experimental builds."
                ) from error
            raise UpdateError("No published LRCPlus releases were found.") from error
        if 500 <= error.code <= 599:
            raise UpdateError("GitHub is temporarily unavailable. Try again later.") from error
        raise UpdateError(f"GitHub rejected the update check (HTTP {error.code}).") from error
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        raise UpdateError("Could not connect to GitHub to check for updates.") from error
    if len(data) > 4 * 1024 * 1024:
        raise UpdateError("GitHub returned an unexpectedly large update response.")
    try:
        return json.loads(data)
    except (json.JSONDecodeError, UnicodeDecodeError) as error:
        raise UpdateError("GitHub returned invalid release information.") from error


def _target_architecture() -> str:
    if sys.platform != "win32":
        raise UpdateError("Automatic updates are currently supported on Windows only.")
    machine = platform.machine().casefold()
    if machine in {"amd64", "x86_64", "x64"}:
        return "x64"
    if machine in {"arm64", "aarch64"}:
        return "arm64"
    raise UpdateError(f"Automatic updates are not available for {platform.machine()}.")


def _select_asset(release: dict, architecture: str) -> ReleaseAsset:
    assets = release.get("assets")
    if not isinstance(assets, list):
        raise UpdateError("The release has no valid application assets.")
    tag = release.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("GitHub returned a release without a valid tag.")
    parse_version(tag)
    tag_text = package_version_from_tag(tag)
    expected_name = f"LRCPlus-v{tag_text}-Windows-{architecture}.zip"
    matches = []
    for item in assets:
        if not isinstance(item, dict) or item.get("name") != expected_name:
            continue
        matches.append(item)
    if len(matches) != 1:
        raise UpdateError(f"The release must contain exactly one {expected_name} asset.")
    item = matches[0]
    size = item.get("size")
    digest = item.get("digest")
    url = item.get("browser_download_url")
    parsed_url = urllib.parse.urlsplit(url) if isinstance(url, str) else None
    digest_match = _SHA256.fullmatch(digest) if isinstance(digest, str) else None
    if (isinstance(size, bool) or not isinstance(size, int) or size <= 0 or size > MAX_ASSET_SIZE
            or parsed_url is None or parsed_url.scheme != "https"
            or parsed_url.hostname != "github.com" or parsed_url.port is not None
            or parsed_url.username is not None or parsed_url.password is not None
            or not parsed_url.path.startswith(f"/{GITHUB_REPOSITORY}/releases/download/")
            or digest_match is None):
        raise UpdateError("The release asset is missing a safe URL, size, or SHA-256 digest.")
    return ReleaseAsset(item["name"], url, size, digest_match.group(1).lower())


def _to_release(data: dict, architecture: str) -> Release:
    if not isinstance(data, dict):
        raise UpdateError("GitHub returned an invalid release record.")
    if not isinstance(data.get("draft", False), bool):
        raise UpdateError("GitHub returned an invalid draft status.")
    if data.get("draft") is True:
        raise UpdateError("Draft releases cannot be installed.")
    if not isinstance(data.get("prerelease", False), bool):
        raise UpdateError("GitHub returned an invalid prerelease status.")
    tag = data.get("tag_name")
    if not isinstance(tag, str):
        raise UpdateError("GitHub returned a release without a valid tag.")
    version = parse_version(tag)
    name = data.get("name")
    body = data.get("body")
    published_at = data.get("published_at")
    html_url = data.get("html_url")
    if (not isinstance(name, str) or not isinstance(body, str)
            or not isinstance(published_at, str) or not isinstance(html_url, str)
            or urllib.parse.urlsplit(html_url).scheme != "https"
            or urllib.parse.urlsplit(html_url).hostname != "github.com"
            or urllib.parse.urlsplit(html_url).username is not None
            or urllib.parse.urlsplit(html_url).password is not None):
        raise UpdateError("The release metadata is incomplete or invalid.")
    return Release(
        tag=tag,
        version=version,
        name=name,
        published_at=published_at,
        body=body,
        prerelease=bool(data.get("prerelease")),
        html_url=html_url,
        asset=_select_asset(data, architecture),
    )


def get_latest_release(channel: str, architecture: str | None = None) -> Release:
    """Find the newest compatible published release for the selected channel."""
    if channel not in {"stable", "prerelease"}:
        raise UpdateError("The selected update channel is invalid.")
    architecture = architecture or _target_architecture()
    if architecture not in {"x64", "arm64"}:
        raise UpdateError("The selected Windows architecture is unsupported.")
    if channel == "stable":
        data = _github_json(f"{API_ROOT}/releases/latest")
        if not isinstance(data, dict) or data.get("prerelease") is True:
            raise UpdateError("GitHub did not return a stable release.")
        return _to_release(data, architecture)

    data = _github_json(f"{API_ROOT}/releases?per_page=100")
    if not isinstance(data, list):
        raise UpdateError("GitHub returned an invalid release list.")
    releases = []
    for record in data:
        if not isinstance(record, dict) or record.get("draft") is True:
            continue
        try:
            releases.append(_to_release(record, architecture))
        except UpdateError:
            continue
    if not releases:
        raise UpdateError("No compatible published LRCPlus releases were found.")
    return max(releases, key=lambda release: release.version)


def find_update(installed_version: str, channel: str, architecture: str | None = None) -> Release | None:
    current = parse_version(installed_version)
    release = get_latest_release(channel, architecture)
    return release if release.version > current else None


def download_asset(
    asset: ReleaseAsset,
    destination: Path,
    *,
    progress=None,
    cancel_event: threading.Event | None = None,
) -> Path:
    """Stream and verify a release asset, publishing it only when complete."""
    destination = Path(destination)
    destination.parent.mkdir(parents=True, exist_ok=True)
    partial = destination.with_name(destination.name + ".part")
    request = urllib.request.Request(asset.url, headers={
        "Accept": "application/octet-stream",
        "User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})",
    })
    digest = hashlib.sha256()
    downloaded = 0
    log_update_event(f"Download started: {asset.name} ({asset.size} bytes)")
    try:
        with urllib.request.urlopen(request, timeout=30) as response:
            final_url = urllib.parse.urlsplit(response.geturl())
            if final_url.scheme != "https" or final_url.hostname not in _ALLOWED_DOWNLOAD_HOSTS:
                raise UpdateError("The update download redirected to an untrusted host.")
            raw_length = response.headers.get("Content-Length")
            try:
                total = int(raw_length) if raw_length is not None else None
            except (TypeError, ValueError):
                total = None
            if total is not None and (total <= 0 or total != asset.size):
                raise UpdateError("GitHub reported an unexpected update size.")
            with partial.open("wb") as output:
                while True:
                    if cancel_event is not None and cancel_event.is_set():
                        raise UpdateError("The update download was cancelled.")
                    chunk = response.read(1024 * 1024)
                    if not chunk:
                        break
                    downloaded += len(chunk)
                    if downloaded > asset.size or downloaded > MAX_ASSET_SIZE:
                        raise UpdateError("The update download exceeded its expected size.")
                    digest.update(chunk)
                    output.write(chunk)
                    if progress is not None:
                        progress(downloaded, total)
                output.flush()
                os.fsync(output.fileno())
        if downloaded != asset.size:
            raise UpdateError("The update download was incomplete.")
        if digest.hexdigest() != asset.sha256:
            raise UpdateError("SHA-256 verification failed. No files were installed.")
        os.replace(partial, destination)
        log_update_event(f"Download verified: {asset.name} ({downloaded} bytes)")
        return destination
    except UpdateError:
        partial.unlink(missing_ok=True)
        raise
    except (urllib.error.URLError, TimeoutError, OSError) as error:
        partial.unlink(missing_ok=True)
        raise UpdateError("The update download failed. Check your connection and try again.") from error
    except Exception as error:
        partial.unlink(missing_ok=True)
        raise UpdateError("The update download failed unexpectedly. No files were installed.") from error


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


def acknowledge_update_health(transaction_id: str) -> bool:
    """Mark a newly launched release healthy after its main window is shown."""
    if (not isinstance(transaction_id, str) or len(transaction_id) != 32
            or any(char not in "0123456789abcdef" for char in transaction_id)):
        return False
    override = os.environ.get("LRCPLUS_DATA_DIR")
    if override:
        data_root = Path(override).expanduser().resolve()
    elif sys.platform == "win32":
        data_root = Path(os.environ.get("LOCALAPPDATA") or Path.home() / "AppData" / "Local") / "LRCPlus"
    elif sys.platform == "darwin":
        data_root = Path.home() / "Library" / "Application Support" / "LRCPlus"
    else:
        data_root = Path(os.environ.get("XDG_DATA_HOME") or Path.home() / ".local" / "share") / "LRCPlus"
    transaction = data_root / "updates" / "transactions" / transaction_id
    try:
        journal = json.loads((transaction / "journal.json").read_text(encoding="utf-8"))
        if not isinstance(journal, dict) or journal.get("phase") != "launching":
            return False
        installed_root = Path(sys.executable).resolve().parent if getattr(sys, "frozen", False) else Path(__file__).resolve().parents[1]
        if Path(journal.get("install_dir", "")).resolve() != installed_root:
            return False
        marker = transaction / "health.json"
        temp = transaction / "health.json.tmp"
        temp.write_text(json.dumps({"version": journal.get("version")}), encoding="utf-8")
        os.replace(temp, marker)
        return True
    except (OSError, ValueError, TypeError):
        return False
