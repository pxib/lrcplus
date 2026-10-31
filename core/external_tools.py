"""Verified, per-user executable installs for optional media tools."""
from __future__ import annotations

import hashlib
import json
import os
from pathlib import Path, PurePosixPath
import re
import shutil
import stat
import subprocess
import sysconfig
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile

from packaging.version import Version

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.dependencies import DependencyError, _installation_lock
from core.settings import get_app_data_dir


TOOLS = {
    "ffmpeg": {
        "label": "FFmpeg + ffprobe", "used_by": "URL audio import and waveforms",
        "repo": "yt-dlp/FFmpeg-Builds", "executables": ("ffmpeg", "ffprobe"),
        "assets": {"win-amd64": "ffmpeg-master-latest-win64-gpl.zip"},
    },
    "deno": {
        "label": "Deno (JavaScript runtime)", "used_by": "YouTube audio import",
        "repo": "denoland/deno", "executables": ("deno",),
        "assets": {"win-amd64": "deno-x86_64-pc-windows-msvc.zip",
                   "win-arm64": "deno-aarch64-pc-windows-msvc.zip"},
    },
}
_active_paths = set()


def _request(url):
    return urllib.request.urlopen(urllib.request.Request(url, headers={
        "User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})",
        "Accept": "application/vnd.github+json" if url.startswith("https://api.github.com/") else "*/*",
    }), timeout=30)


def _release(repo):
    with _request(f"https://api.github.com/repos/{repo}/releases/latest") as response:
        data = response.read(4 * 1024 * 1024 + 1)
    if len(data) > 4 * 1024 * 1024:
        raise DependencyError("The tool release information is too large.")
    result = json.loads(data)
    if not isinstance(result, dict) or not isinstance(result.get("assets"), list):
        raise DependencyError("The tool publisher returned invalid release information.")
    return result


def _download(url, destination, digest, log):
    count, last_progress = 0, -1
    checksum = hashlib.sha256()
    with _request(url) as response, destination.open("wb") as output:
        final = urllib.parse.urlsplit(response.geturl())
        if final.scheme != "https" or final.hostname not in {
            "github.com", "release-assets.githubusercontent.com", "objects.githubusercontent.com",
        }:
            raise DependencyError("The tool download redirected to an unexpected location.")
        for chunk in iter(lambda: response.read(1024 * 1024), b""):
            count += len(chunk)
            if count > 1024 * 1024 * 1024:
                raise DependencyError("The tool download exceeds the size limit.")
            checksum.update(chunk)
            output.write(chunk)
            progress = count // (8 * 1024 * 1024)
            if progress > last_progress:
                log(f"Downloaded {count // (1024 * 1024)} MB…")
                last_progress = progress
    if checksum.hexdigest().lower() != digest.lower():
        raise DependencyError("SHA-256 verification failed. The existing installation was kept.")


def _extract(archive, target):
    root = target.resolve()
    with zipfile.ZipFile(archive) as bundle:
        entries = bundle.infolist()
        if len(entries) > 10000 or sum(item.file_size for item in entries) > 2 * 1024 ** 3:
            raise DependencyError("The tool archive exceeds the extraction limits.")
        for item in entries:
            path = PurePosixPath(item.filename.replace("\\", "/"))
            if (path.is_absolute() or ".." in path.parts or ":" in item.filename
                    or not (root / item.filename).resolve().is_relative_to(root)
                    or stat.S_ISLNK(item.external_attr >> 16)):
                raise DependencyError("The tool archive contains an invalid path.")
        bundle.extractall(target)


class ExternalToolManager:
    def __init__(self, root=None):
        self.root = Path(root) if root is not None else get_app_data_dir() / "tools"

    def supported(self, name):
        return sysconfig.get_platform() in TOOLS[name]["assets"]

    def _folder(self, name):
        if name not in TOOLS:
            raise DependencyError("Unknown external tool.")
        folder = self.root / name
        if not folder.resolve().is_relative_to(self.root.resolve()):
            raise DependencyError("The tool installation path is outside LRCPlus storage.")
        return folder

    def _active(self, name):
        folder = self._folder(name)
        try:
            data = json.loads((folder / "active.json").read_text(encoding="utf-8"))
            installation = data["installation"]
            if not isinstance(installation, str) or not re.fullmatch(r"[0-9a-f]{32}", installation):
                raise ValueError("invalid installation identifier")
            version_root = folder / "versions" / installation
            binary = version_root / data["bin"]
            if (not isinstance(data["version"], str)
                    or not version_root.resolve().is_relative_to(folder.resolve())
                    or not binary.resolve().is_relative_to(version_root.resolve())):
                raise ValueError("invalid tool location")
            return data, binary
        except FileNotFoundError:
            return None, None
        except (OSError, ValueError, KeyError, TypeError) as error:
            raise DependencyError(f"Cannot read the {name} installation: {error}") from error

    def _external_path(self):
        root = self.root.resolve()
        return os.pathsep.join(entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                               if entry and not Path(entry).resolve().is_relative_to(root))

    def paths(self, name):
        _data, binary = self._active(name)
        if binary is not None:
            # A broken managed pair must be repaired rather than silently mixing
            # it with an unrelated ffprobe installation.
            return {exe: str(binary / f"{exe}.exe") if (binary / f"{exe}.exe").is_file() else None
                    for exe in TOOLS[name]["executables"]}
        return {exe: shutil.which(exe, path=self._external_path()) for exe in TOOLS[name]["executables"]}

    def status(self, name):
        folder = self._folder(name)
        managed = (folder / "active.json").exists() or (folder / "versions").exists()
        try:
            data, _binary = self._active(name)
            paths = self.paths(name)
            missing = [exe for exe, path in paths.items() if not path]
            if missing:
                text = "Missing: " + ", ".join(missing)
            else:
                text = f"Installed by LRCPlus ({data['version']})" if data else "Found on PATH"
            return {"state": "missing" if missing else "available", "text": text,
                    "managed": managed, "paths": paths}
        except DependencyError as error:
            return {"state": "broken", "text": str(error), "managed": managed, "paths": {}}

    def _check_paths(self, name, paths):
        output = []
        for executable in TOOLS[name]["executables"]:
            path = paths.get(executable)
            if not path:
                raise DependencyError(f"{executable} was not found. Select Install / Repair to install it for LRCPlus.")
            argument = "--version" if name == "deno" else "-version"
            result = subprocess.run(
                [path, argument], capture_output=True, timeout=15, text=True,
                encoding="utf-8", errors="replace",
                creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
            )
            lines = (result.stdout + result.stderr).splitlines()
            first = lines[0] if lines else ""
            if result.returncode or not first.lower().startswith(executable + " "):
                raise DependencyError(f"{executable} could not run correctly. Use Install / Repair.")
            if name == "deno":
                match = re.match(r"deno (\d+\.\d+\.\d+)", first)
                if match is None or Version(match[1]) < Version("2.3.0"):
                    raise DependencyError("Deno 2.3 or newer is required. Use Install / Repair to update it.")
            output.append(first + f"\n  {path}")
        return "\n".join(output)

    def check(self, name):
        return self._check_paths(name, self.paths(name))

    def install(self, name, log=lambda message: None):
        folder = self._folder(name)
        if not self.supported(name):
            raise DependencyError("Automatic installation is unavailable for this platform. Install the tool on PATH instead.")
        with _installation_lock(folder):
            log(f"Finding the latest {TOOLS[name]['label']} release…")
            release = _release(TOOLS[name]["repo"])
            asset_name = TOOLS[name]["assets"][sysconfig.get_platform()]
            asset = next((item for item in release["assets"] if item.get("name") == asset_name), None)
            digest = asset.get("digest", "") if asset else ""
            if not isinstance(digest, str) or not re.fullmatch(r"sha256:[0-9a-fA-F]{64}", digest):
                raise DependencyError("The publisher did not provide a verified SHA-256 download for this platform.")
            url = asset.get("browser_download_url", "")
            prefix = f"https://github.com/{TOOLS[name]['repo']}/releases/download/"
            if not isinstance(url, str) or not url.startswith(prefix):
                raise DependencyError("The publisher returned an unexpected download URL.")
            installation = uuid.uuid4().hex
            versions = folder / "versions"
            if not versions.resolve().is_relative_to(folder.resolve()):
                raise DependencyError("The tool storage path is outside LRCPlus storage.")
            versions.mkdir(parents=True, exist_ok=True)
            destination = versions / installation
            pending = folder / f"active-{installation}.tmp"
            try:
                with tempfile.TemporaryDirectory(prefix="download-", dir=folder) as temporary:
                    archive = Path(temporary) / "tool.zip"
                    log(f"Downloading {asset_name}. SHA-256 verification runs before installation.")
                    _download(url, archive, digest.removeprefix("sha256:"), log)
                    _extract(archive, destination)
                matches = list(destination.rglob(TOOLS[name]["executables"][0] + ".exe"))
                if len(matches) != 1:
                    raise DependencyError("The tool archive does not contain the expected executable.")
                binary = matches[0].parent
                paths = {exe: str(binary / f"{exe}.exe") for exe in TOOLS[name]["executables"]}
                log("Verifying installed executables…")
                log(self._check_paths(name, paths))
                data = {"installation": installation, "bin": binary.relative_to(destination).as_posix(),
                        "version": str(release.get("tag_name", "latest")), "sha256": digest[7:], "url": url}
                pending.write_text(json.dumps(data, indent=2), encoding="utf-8")
                pending.replace(folder / "active.json")
            except Exception:
                if destination.exists():
                    shutil.rmtree(destination, ignore_errors=True)
                raise
            finally:
                pending.unlink(missing_ok=True)
            self.activate()
            log(f"{TOOLS[name]['label']} is ready. New imports can use it immediately.")

    def remove(self, name, log=lambda message: None):
        folder = self._folder(name)
        versions = folder / "versions"
        if not versions.resolve().is_relative_to(folder.resolve()):
            raise DependencyError("The tool storage path is outside LRCPlus storage.")
        with _installation_lock(folder):
            (folder / "active.json").unlink(missing_ok=True)
            self.activate()
            try:
                if versions.exists():
                    shutil.rmtree(versions)
            except OSError:
                log("The managed tool is deactivated. Close running imports and use Remove again to delete files still in use.")
            else:
                log("Removed the LRCPlus installation. Tools installed elsewhere were kept.")

    def activate(self):
        """Change only this process's PATH, inherited by new importer workers."""
        global _active_paths
        additions = []
        for name in TOOLS:
            try:
                _data, binary = self._active(name)
                if binary is not None and all(self.paths(name).values()):
                    additions.append(str(binary))
            except DependencyError:
                continue
        existing = [entry for entry in os.environ.get("PATH", "").split(os.pathsep)
                    if entry and entry not in _active_paths and entry not in additions]
        os.environ["PATH"] = os.pathsep.join(additions + existing)
        _active_paths = set(additions)


def activate_tools():
    ExternalToolManager().activate()
