"""Private, restart-based environments for optional plugin dependencies.

Installation resolves all selected packages together into a new directory. The
active pointer is replaced only after version and import checks succeed. Loaded
libraries are never overwritten, and app-provided libraries keep precedence.
"""
from __future__ import annotations

import hashlib
import importlib
import importlib.machinery
import importlib.metadata
import json
import os
import re
import shutil
import subprocess
import sys
import sysconfig
import tempfile
import urllib.parse
import urllib.request
import uuid
import zipfile
from contextlib import contextmanager
from dataclasses import dataclass
from pathlib import Path

from packaging.requirements import InvalidRequirement, Requirement
from packaging.utils import canonicalize_name
from packaging.version import InvalidVersion, Version

from core.settings import get_app_data_dir


class DependencyError(RuntimeError):
    pass


_runtime_dll_handles = {}


def runtime_tag():
    return f"{sys.implementation.cache_tag}-{sysconfig.get_platform()}"


def dependency_root():
    return get_app_data_dir() / "dependencies" / runtime_tag()


def private_python_path():
    executable = f"python{sys.version_info.major}.{sys.version_info.minor}t.exe" if sysconfig.get_config_var("Py_GIL_DISABLED") else "python.exe"
    return get_app_data_dir() / "runtime" / runtime_tag() / executable


def _read_state(root):
    try:
        state = json.loads((root / "active.json").read_text(encoding="utf-8"))
        environment = state.get("environment")
        roots = state.get("requirements", {})
        if environment is not None and not re.fullmatch(r"[0-9a-f]{32}", environment):
            raise ValueError("invalid environment name")
        if not isinstance(roots, dict) or not all(
            isinstance(key, str) and isinstance(value, list)
            and all(isinstance(item, str) for item in value)
            for key, value in roots.items()
        ):
            raise ValueError("invalid requirements")
        return {"environment": environment, "requirements": roots}
    except FileNotFoundError:
        return {"environment": None, "requirements": {}}
    except (OSError, ValueError, TypeError, AttributeError) as error:
        raise DependencyError(f"Cannot read the dependency registry: {error}") from error


def _site_path(root, state):
    name = state["environment"]
    return root / "environments" / name / "site-packages" if name else None


def activate_runtime_libraries():
    """Supply stdlib modules omitted by the frozen app's static analysis."""
    if not getattr(sys, "frozen", False):
        return
    runtime = get_app_data_dir() / "runtime" / runtime_tag()
    for folder in (runtime / "Lib", runtime / "DLLs", runtime):
        if folder.is_dir() and str(folder) not in sys.path:
            sys.path.append(str(folder))
    if hasattr(os, "add_dll_directory"):
        for folder in (runtime, runtime / "DLLs"):
            if folder.is_dir() and str(folder) not in _runtime_dll_handles:
                _runtime_dll_handles[str(folder)] = os.add_dll_directory(str(folder))


def activate_dependencies():
    """Called before application/plugin imports; performs no downloads or writes."""
    try:
        site = _site_path(dependency_root(), _read_state(dependency_root()))
    except DependencyError as error:
        print(f"[Dependencies] {error}")
        return
    if site is not None and site.is_dir():
        activate_runtime_libraries()
        if str(site) not in sys.path:
            sys.path.append(str(site))
        # Source-mode workers inherit the exact environment used by this run,
        # even if an installation changes active.json while the app is open.
        os.environ["LRCPLUS_DEPENDENCY_SITE"] = str(site)
        if getattr(sys, "frozen", False):
            os.environ["LRCPLUS_DEPENDENCY_PYTHON"] = str(private_python_path())
        importlib.invalidate_caches()
    else:
        os.environ.pop("LRCPLUS_DEPENDENCY_SITE", None)
        os.environ.pop("LRCPLUS_DEPENDENCY_PYTHON", None)


@dataclass(frozen=True)
class Dependency:
    requirement: str
    import_name: str
    requires: tuple[str, ...] = ()

    @property
    def name(self):
        return canonicalize_name(Requirement(self.requirement).name)


def parse_dependencies(value):
    if not isinstance(value, list):
        raise DependencyError("Manifest dependencies must be a list.")
    result = []
    for entry in value:
        if not isinstance(entry, dict):
            raise DependencyError("Each dependency needs requirement and import_name fields.")
        try:
            requirement = Requirement(entry["requirement"])
            name = entry["import_name"]
            if requirement.url or requirement.marker:
                raise ValueError("use a package name with optional extras and version constraints")
            if not isinstance(name, str) or not name.isidentifier():
                raise ValueError("import_name must be a top-level module name")
            support = entry.get("requires", [])
            if not isinstance(support, list) or not all(isinstance(item, str) for item in support):
                raise ValueError("requires must be a list of supporting package requirements")
            support = tuple(str(Requirement(item)) for item in support)
            if any(Requirement(item).url or Requirement(item).extras or Requirement(item).marker for item in support):
                raise ValueError("supporting requirements must use package names and version constraints")
        except (KeyError, TypeError, ValueError, InvalidRequirement) as error:
            raise DependencyError(f"Invalid plugin dependency: {error}") from error
        result.append(Dependency(str(requirement), name, support))
    return result


def _distributions(paths):
    found = {}
    for distribution in importlib.metadata.distributions(path=paths):
        name = distribution.metadata.get("Name")
        if name:
            found.setdefault(canonicalize_name(name), distribution.version)
    return found


def _has_module(name, paths):
    try:
        return importlib.machinery.PathFinder.find_spec(name, paths) is not None
    except (ImportError, OSError, ValueError):
        return False


def _run(command, *, payload=None, timeout=900):
    env = os.environ.copy()
    # Do not let another Python installation redirect the private interpreter.
    for key in ("PYTHONHOME", "PYTHONPATH", "PYTHONUSERBASE"):
        env.pop(key, None)
    env["PYTHONIOENCODING"] = "utf-8"
    env["PYTHONNOUSERSITE"] = "1"
    try:
        completed = subprocess.run(
            [str(part) for part in command], input=payload,
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            encoding="utf-8", errors="replace", timeout=timeout,
            env=env, creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
    except (OSError, subprocess.TimeoutExpired) as error:
        raise DependencyError(f"Could not run the dependency helper: {error}") from error
    if completed.returncode:
        detail = completed.stdout[-6000:].strip()
        raise DependencyError(f"Dependency helper exited {completed.returncode}.\n{detail}")
    return completed.stdout


def probe_imports(imports, site, paths, *, python=None):
    if python is None and getattr(sys, "frozen", False):
        command = [sys.executable, "--lyricsplus-dependency-probe"]
    else:
        command = [python or sys.executable, "-I", Path(__file__).with_name("dependency_probe.py")]
    cache = get_app_data_dir() / "cache" / "checks"
    cache.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="import-", dir=cache) as directory:
        request = Path(directory) / "request.json"
        response = Path(directory) / "response.json"
        request.write_text(json.dumps({
            "imports": sorted(set(imports)), "site": str(site) if site else None,
            "paths": list(paths),
        }), encoding="utf-8")
        _run(command + ["--input", request, "--output", response], timeout=120)
        try:
            result = json.loads(response.read_text(encoding="utf-8"))
            if isinstance(result, dict) and set(result) == set(imports):
                return result
        except (OSError, ValueError):
            pass
    raise DependencyError("The dependency check returned no valid result.")


def _official_url(url):
    parsed = urllib.parse.urlsplit(url)
    if parsed.scheme != "https" or parsed.hostname != "www.python.org" or not parsed.path.startswith("/ftp/python/"):
        raise DependencyError("The Python runtime URL is not an official Python download.")
    return url


def _download(url, destination, expected_hash=None):
    digest = hashlib.sha256()
    with urllib.request.urlopen(_official_url(url), timeout=30) as response, destination.open("wb") as handle:
        _official_url(response.geturl())
        while chunk := response.read(1024 * 1024):
            digest.update(chunk)
            handle.write(chunk)
    if expected_hash and digest.hexdigest().lower() != expected_hash.lower():
        raise DependencyError("The Python runtime download failed its SHA-256 check.")


def _runtime_entry():
    suffix = {"win-amd64": "64", "win32": "32", "win-arm64": "arm64"}.get(sysconfig.get_platform())
    if not suffix:
        raise DependencyError("Automatic private Python setup is supported by Windows releases only.")
    threaded = "t" if sysconfig.get_config_var("Py_GIL_DISABLED") else ""
    tag = f"{sys.version_info.major}.{sys.version_info.minor}{threaded}-{suffix}"
    url = "https://www.python.org/ftp/python/index-windows.json"
    for _ in range(5):
        with urllib.request.urlopen(_official_url(url), timeout=30) as response:
            _official_url(response.geturl())
            index = json.load(response)
        matches = []
        for entry in index.get("versions", []):
            if entry.get("company") != "PythonCore" or entry.get("tag") != tag:
                continue
            try:
                version = Version(entry["sort-version"])
            except (KeyError, InvalidVersion):
                continue
            checksum = entry.get("hash", {}).get("sha256", "")
            if not version.is_prerelease and re.fullmatch(r"[0-9a-fA-F]{64}", checksum):
                matches.append((version, entry))
        if matches:
            return max(matches, key=lambda item: item[0])[1]
        if not index.get("next"):
            break
        url = urllib.parse.urljoin(url, index["next"])
    raise DependencyError(f"No verified private Python runtime is available for {tag}.")


def _extract_runtime(archive, destination):
    with zipfile.ZipFile(archive) as bundle:
        root = destination.resolve()
        for entry in bundle.infolist():
            if not (root / entry.filename).resolve().is_relative_to(root):
                raise DependencyError("The runtime archive contains an invalid path.")
        bundle.extractall(destination)


def ensure_installer(log):
    if not getattr(sys, "frozen", False):
        _run([sys.executable, "-I", "-m", "pip", "--version"], timeout=30)
        return Path(sys.executable)
    python = private_python_path()
    runtime = python.parent
    executable = python.name
    if python.is_file():
        _run([python, "-I", "-m", "pip", "--version"], timeout=30)
        return python
    log("Downloading a private Python runtime. This is needed only once.")
    entry = _runtime_entry()
    parent = runtime.parent
    parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="python-", dir=parent) as work:
        work = Path(work)
        archive = work / "python.zip"
        _download(entry["url"], archive, entry["hash"]["sha256"])
        staged = work / "runtime"
        _extract_runtime(archive, staged)
        _run([staged / executable, "-I", "-m", "pip", "--version"], timeout=30)
        staged.rename(runtime)
    log("Private Python runtime is ready.")
    return python


@contextmanager
def _installation_lock(root):
    root.mkdir(parents=True, exist_ok=True)
    with (root / "install.lock").open("a+b") as handle:
        try:
            if os.fstat(handle.fileno()).st_size == 0:
                handle.write(b"0")
                handle.flush()
            handle.seek(0)
            if sys.platform == "win32":
                import msvcrt
                msvcrt.locking(handle.fileno(), msvcrt.LK_NBLCK, 1)
            else:
                import fcntl
                fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except OSError as error:
            raise DependencyError("Another dependency installation is already running.") from error
        try:
            yield
        finally:
            handle.seek(0)
            if sys.platform == "win32":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle.fileno(), fcntl.LOCK_UN)


class DependencyManager:
    def __init__(self, root=None):
        self.root = Path(root) if root else dependency_root()
        # Downloads may satisfy optional packages but cannot replace host modules.
        self.base_paths = [
            path for path in sys.path
            if path and not Path(path).resolve().is_relative_to(self.root.resolve())
        ]
        self.base_versions = _distributions(self.base_paths)
        self.catalog = {}
        self.catalog_errors = {}
        try:
            self.start_environment = self.state()["environment"]
        except DependencyError:
            # A damaged optional registry must not prevent the player starting.
            self.start_environment = None

    def state(self):
        return _read_state(self.root)

    def configure(self, plugins):
        self.catalog = {}
        self.catalog_errors = {}
        for plugin in plugins:
            try:
                dependencies = parse_dependencies(plugin.get("dependencies", []))
            except DependencyError as error:
                self.catalog_errors[plugin["plugin_id"]] = str(error)
                continue
            for dependency in dependencies:
                entry = self.catalog.setdefault(dependency.name, {"dependencies": [], "plugins": []})
                entry["dependencies"].append(dependency)
                entry["plugins"].append(plugin["name"])

    def status(self, name, *, state=None):
        name = canonicalize_name(name)
        state = self.state() if state is None else state
        dependencies = self.catalog[name]["dependencies"]
        base_module = any(_has_module(item.import_name, self.base_paths) for item in dependencies)
        site = _site_path(self.root, state)
        managed_versions = _distributions([str(site)]) if site and site.is_dir() else {}
        # If the application already provides this import, it remains authoritative.
        version = self.base_versions.get(name) if base_module else managed_versions.get(name)
        source = "Provided by app" if base_module else "Installed"
        paths = self.base_paths if base_module else [str(site)] if site else []
        present = all(_has_module(item.import_name, paths) for item in dependencies)
        if not present:
            return {"state": "missing", "text": "Missing", "version": version, "source": "app" if base_module else "managed"}
        if version is None:
            return {"state": "incompatible", "text": "Version could not be verified", "version": None, "source": "app" if base_module else "managed"}
        try:
            compatible = all(Requirement(item.requirement).specifier.contains(version) for item in dependencies)
        except InvalidVersion:
            compatible = False
        if not compatible:
            return {"state": "incompatible", "text": f"Incompatible version ({version})", "version": version, "source": "app" if base_module else "managed"}
        pending = state["environment"] != self.start_environment
        text = f"{source} ({version})" + (" — restart required" if pending and not base_module else "")
        return {"state": "available", "text": text, "version": version, "source": "app" if base_module else "managed"}

    def plugin_problems(self, plugin):
        if plugin["plugin_id"] in self.catalog_errors:
            return [self.catalog_errors[plugin["plugin_id"]]]
        return [
            f"{dependency.requirement}: {self.status(dependency.name)['text']}"
            for dependency in parse_dependencies(plugin.get("dependencies", []))
            if self.status(dependency.name)["state"] != "available"
        ]

    def check(self, name):
        dependencies = self.catalog[canonicalize_name(name)]["dependencies"]
        result = probe_imports(
            [item.import_name for item in dependencies],
            _site_path(self.root, self.state()), self.base_paths,
        )
        errors = [f"{module}: {error}" for module, error in result.items() if error]
        if errors:
            raise DependencyError("\n".join(errors))

    def install(self, name, log=lambda message: None):
        return self._change(canonicalize_name(name), False, log)

    def remove(self, name, log=lambda message: None):
        return self._change(canonicalize_name(name), True, log)

    def _change(self, name, remove, log):
        if name not in self.catalog:
            raise DependencyError("This dependency is not declared by an installed plugin.")
        with _installation_lock(self.root):
            state = self.state()
            requirements = dict(state["requirements"])
            if remove:
                requirements.pop(name, None)
            else:
                if any(_has_module(item.import_name, self.base_paths) for item in self.catalog[name]["dependencies"]):
                    raise DependencyError("This library is provided by the app or its Python environment. Update the app/environment to repair it.")
                requirements[name] = sorted({value for item in self.catalog[name]["dependencies"] for value in (item.requirement, *item.requires)})
            # Refresh constraints for previously selected packages after plugin updates.
            for key in requirements.keys() & self.catalog.keys():
                requirements[key] = sorted({value for item in self.catalog[key]["dependencies"] for value in (item.requirement, *item.requires)})
            environment = uuid.uuid4().hex if requirements else None
            directory = self.root / "environments" / environment if environment else None
            try:
                if directory:
                    directory.mkdir(parents=True)
                    target = directory / "site-packages"
                    constraints = directory / "constraints.txt"
                    # Protect app-provided libraries, including transitively required ones.
                    pins = [f"{key}=={value}" for key, value in sorted(self.base_versions.items())]
                    for entry in self.catalog.values():
                        for item in entry["dependencies"]:
                            for value in (item.requirement, *item.requires):
                                constraint = Requirement(value)
                                # pip accepts extras in install requests, but
                                # constraints must name distributions alone.
                                constraint.extras = set()
                                pins.append(str(constraint))
                    constraints.write_text("\n".join(pins) + "\n", encoding="utf-8")
                    python = ensure_installer(log)
                    log("Resolving and installing dependencies. This can take a few minutes.")
                    output = _run([
                        python, "-I", "-m", "pip", "--isolated", "--disable-pip-version-check",
                        "install", "--index-url", "https://pypi.org/simple",
                        "--python-version", ".".join(map(str, sys.version_info[:3])),
                        "--only-binary", ":all:", "--ignore-installed", "--no-compile",
                        "--no-warn-script-location", "--target", target,
                        "--cache-dir", get_app_data_dir() / "cache" / "downloads" / "pip",
                        "--constraint", constraints,
                        *sorted({item for values in requirements.values() for item in values}),
                    ])
                    log(output.strip())
                    log("Checking installed versions and imports…")
                    staged_state = {"environment": environment, "requirements": requirements}
                    imports = []
                    for key in requirements.keys() & self.catalog.keys():
                        status = self.status(key, state=staged_state)
                        if status["state"] != "available":
                            raise DependencyError(f"{key}: {status['text']}")
                        imports.extend(item.import_name for item in self.catalog[key]["dependencies"])
                    failures = probe_imports(imports, target, self.base_paths, python=python)
                    if any(failures.values()):
                        raise DependencyError("Import check failed:\n" + "\n".join(
                            f"{key}: {error}" for key, error in failures.items() if error
                        ))
                updated = {"environment": environment, "requirements": requirements}
                pending = self.root / f"active-{uuid.uuid4().hex}.tmp"
                try:
                    pending.write_text(json.dumps(updated, indent=2) + "\n", encoding="utf-8")
                    pending.replace(self.root / "active.json")
                finally:
                    pending.unlink(missing_ok=True)
            except Exception:
                if directory:
                    # Only this operation's new, never-activated environment is removed.
                    if directory.resolve().is_relative_to((self.root / "environments").resolve()):
                        shutil.rmtree(directory, ignore_errors=True)
                raise
            log("Dependencies updated. Restart LyricsPlus to apply the changes.")
            return updated
