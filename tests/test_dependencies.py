import hashlib
import json
import sys
import zipfile
from pathlib import Path

import pytest

import core.dependencies as dependencies
from core.dependencies import DependencyError, DependencyManager, parse_dependencies
from core.plugin_manager import PluginManager


def _plugin(name, requirement=None, module=None):
    return {
        "plugin_id": name, "name": name,
        "dependencies": [{"requirement": requirement or name, "import_name": module or name.replace("-", "_")}],
    }


def _wheel(folder, name, *, requires=(), extras=(), code="VALUE = 42\n"):
    module = name.replace("-", "_")
    metadata = f"{module}-1.0.dist-info"
    files = {
        f"{module}/__init__.py": code,
        f"{metadata}/METADATA": "Metadata-Version: 2.1\n" + f"Name: {name}\nVersion: 1.0\n"
        + "".join(f"Provides-Extra: {item}\n" for item in extras)
        + "".join(f"Requires-Dist: {item}\n" for item in requires) + "\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{metadata}/RECORD"] = "".join(f"{path},,\n" for path in [*files, f"{metadata}/RECORD"])
    path = folder / f"{module}-1.0-py3-none-any.whl"
    with zipfile.ZipFile(path, "w") as archive:
        for key, value in files.items():
            archive.writestr(key, value)
    return path


@pytest.fixture
def offline_manager(tmp_path, monkeypatch):
    wheels = tmp_path / "wheels"
    wheels.mkdir()
    for name in ("lp-test-alpha", "lp-test-beta"):
        _wheel(wheels, name, requires=["lp-test-shared>=1,<2"])
    _wheel(wheels, "lp-test-shared")
    _wheel(wheels, "lp-test-broken", code="raise RuntimeError('broken native library')\n")
    _wheel(wheels, "lp-test-conflict", requires=["lp-test-shared>=2"])
    _wheel(wheels, "lp-test-support")
    _wheel(wheels, "lp-test-undeclared", code="import lp_test_support\n")
    _wheel(wheels, "lp-test-extra", requires=["lp-test-support>=1; extra == 'default'"],
           extras=["default"], code="import lp_test_support\n")
    manager = DependencyManager(tmp_path / "dependencies")
    manager.configure([_plugin(name) for name in (
        "lp-test-alpha", "lp-test-beta", "lp-test-broken", "lp-test-conflict",
    )])
    original_run = dependencies._run

    def offline_run(command, **kwargs):
        command = list(command)
        if "--index-url" in command:
            offset = command.index("--index-url")
            command[offset:offset + 2] = ["--no-index", "--find-links", wheels]
        return original_run(command, **kwargs)

    monkeypatch.setattr(dependencies, "_run", offline_run)
    return manager


def test_explicit_supporting_requirements_are_installed(offline_manager):
    plugin = _plugin("lp-test-undeclared")
    plugin["dependencies"][0]["requires"] = ["lp-test-support>=1,<2"]
    offline_manager.configure([plugin])
    state = offline_manager.install("lp-test-undeclared")
    assert "lp-test-support<2,>=1" in state["requirements"]["lp-test-undeclared"]
    offline_manager.check("lp-test-undeclared")


def test_optional_extras_install_without_putting_extras_in_pip_constraints(offline_manager):
    plugin = _plugin("lp-test-extra", requirement="lp-test-extra[default]>=1")
    offline_manager.configure([plugin])
    state = offline_manager.install("lp-test-extra")
    site = dependencies._site_path(offline_manager.root, state)
    assert (site / "lp_test_support").is_dir()
    assert state["requirements"]["lp-test-extra"] == ["lp-test-extra[default]>=1"]
    offline_manager.check("lp-test-extra")


def test_install_remove_preserves_shared_dependencies_and_requires_restart(offline_manager):
    manager = offline_manager
    before = list(sys.path)
    first = manager.install("lp-test-alpha")
    assert manager.status("lp-test-alpha")["state"] == "available"
    assert "restart required" in manager.status("lp-test-alpha")["text"]
    assert sys.path == before
    second = manager.install("lp-test-beta")
    assert set(second["requirements"]) == {"lp-test-alpha", "lp-test-beta"}
    assert first["environment"] != second["environment"]
    third = manager.remove("lp-test-alpha")
    site = dependencies._site_path(manager.root, third)
    assert not (site / "lp_test_alpha").exists()
    assert (site / "lp_test_beta").exists()
    assert (site / "lp_test_shared").exists()
    manager.check("lp-test-beta")
    manager.remove("lp-test-beta")
    assert manager.state() == {"environment": None, "requirements": {}}
    assert manager.status("lp-test-beta")["state"] == "missing"


@pytest.mark.parametrize("name, message", [
    ("lp-test-broken", "Import check failed"),
    ("lp-test-conflict", "Dependency helper exited"),
])
def test_failed_install_preserves_previous_environment(offline_manager, name, message):
    manager = offline_manager
    previous = manager.install("lp-test-alpha")
    existing = set((manager.root / "environments").iterdir())
    with pytest.raises(DependencyError, match=message):
        manager.install(name)
    assert manager.state() == previous
    assert set((manager.root / "environments").iterdir()) == existing
    manager.check("lp-test-alpha")


def test_activation_uses_private_directory_after_app_libraries(tmp_path, monkeypatch):
    monkeypatch.setattr(sys, "path", list(sys.path))
    root = dependencies.dependency_root()
    name = "a" * 32
    site = root / "environments" / name / "site-packages"
    site.mkdir(parents=True)
    (root / "active.json").write_text(json.dumps({"environment": name, "requirements": {}}))
    dependencies.activate_dependencies()
    assert sys.path[-1] == str(site)
    assert __import__("os").environ["LRCPLUS_DEPENDENCY_SITE"] == str(site)


def test_app_provided_libraries_are_not_replaced(tmp_path):
    manager = DependencyManager(tmp_path / "dependencies")
    manager.configure([_plugin("packaging", "packaging>=24", "packaging")])
    assert manager.status("packaging")["source"] == "app"
    with pytest.raises(DependencyError, match="provided by the app"):
        manager.install("packaging")
    assert not (manager.root / "active.json").exists()


def test_frozen_runtime_supplies_missing_standard_library_modules(tmp_path, monkeypatch):
    runtime = dependencies.get_app_data_dir() / "runtime" / dependencies.runtime_tag()
    (runtime / "Lib").mkdir(parents=True)
    (runtime / "DLLs").mkdir()
    monkeypatch.setattr(sys, "path", list(sys.path))
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setattr(dependencies, "_runtime_dll_handles", {})
    if hasattr(dependencies.os, "add_dll_directory"):
        monkeypatch.setattr(dependencies.os, "add_dll_directory", lambda path: object())
    previous = list(sys.path)
    dependencies.activate_runtime_libraries()
    assert sys.path[:len(previous)] == previous
    assert str(runtime / "Lib") in sys.path
    assert str(runtime / "DLLs") in sys.path


@pytest.mark.parametrize("value", [
    {}, ["koroman"], [{"requirement": "koroman"}],
    [{"requirement": "x @ https://example.com/x.whl", "import_name": "x"}],
    [{"requirement": "x", "import_name": "x.y"}],
])
def test_invalid_dependency_declarations_are_rejected(value):
    with pytest.raises(DependencyError):
        parse_dependencies(value)


def test_missing_dependencies_block_only_enabled_plugins(tmp_path):
    root = tmp_path / "plugins"
    for name in ("enabled", "disabled"):
        plugin = root / "community" / name
        plugin.mkdir(parents=True)
        (plugin / "plugin.py").write_text("raise AssertionError('must not be imported')\n")
        metadata = _plugin(name, "lp-does-not-exist>=1", "lp_does_not_exist")
        (plugin / "manifest.json").write_text(json.dumps(metadata))
    manager = PluginManager(disabled_plugins={"disabled"})
    manager.discover_and_load(root)
    assert set(manager.dependency_errors) == {"enabled"}
    assert "Missing" in manager.dependency_errors["enabled"]
    assert not manager.plugins


def test_incompatible_dependency_versions_are_identified(tmp_path):
    manager = DependencyManager(tmp_path / "dependencies")
    manager.configure([_plugin("packaging", "packaging<1", "packaging")])
    assert manager.status("packaging")["state"] == "incompatible"
    assert "Incompatible version" in manager.plugin_problems(_plugin("packaging", "packaging<1", "packaging"))[0]


def test_probe_contains_native_crashes(tmp_path):
    (tmp_path / "lp_crash.py").write_text("import os; os._exit(23)\n")
    with pytest.raises(DependencyError, match="exited 23"):
        dependencies.probe_imports(["lp_crash"], tmp_path, [])


def test_corrupt_or_escaping_registry_does_not_crash_player(tmp_path):
    root = dependencies.dependency_root()
    root.mkdir(parents=True)
    (root / "active.json").write_text('{"environment":"../../outside"}')
    dependencies.activate_dependencies()
    manager = PluginManager()
    manager.discover_and_load(tmp_path / "empty")
    with pytest.raises(DependencyError, match="invalid environment"):
        manager.dependency_manager.state()


def test_runtime_download_hash_is_verified(tmp_path, monkeypatch):
    import io

    class Response(io.BytesIO):
        def geturl(self):
            return "https://www.python.org/ftp/python/runtime.zip"

    monkeypatch.setattr(dependencies.urllib.request, "urlopen", lambda *args, **kwargs: Response(b"download"))
    destination = tmp_path / "runtime.zip"
    with pytest.raises(DependencyError, match="SHA-256"):
        dependencies._download("https://www.python.org/ftp/python/runtime.zip", destination, "0" * 64)
    dependencies._download(
        "https://www.python.org/ftp/python/runtime.zip", destination,
        hashlib.sha256(b"download").hexdigest(),
    )
    assert destination.read_bytes() == b"download"


def test_runtime_archive_cannot_escape_destination(tmp_path):
    archive = tmp_path / "unsafe.zip"
    with zipfile.ZipFile(archive, "w") as bundle:
        bundle.writestr("../outside.txt", "bad")
    with pytest.raises(DependencyError, match="invalid path"):
        dependencies._extract_runtime(archive, tmp_path / "runtime")
    assert not (tmp_path / "outside.txt").exists()


@pytest.mark.parametrize("flag, payload, expected", [
    ("--lrcplus-dependency-probe", {"imports": ["json"], "paths": []}, {"json": None}),
])
def test_application_dispatches_helpers_before_gui_startup(tmp_path, flag, payload, expected):
    import subprocess

    source = tmp_path / "request.json"
    target = tmp_path / "response.json"
    source.write_text(json.dumps(payload), encoding="utf-8")
    result = subprocess.run(
        [sys.executable, str(Path(__file__).resolve().parents[1] / "main.py"), flag,
         "--input", str(source), "--output", str(target)],
        capture_output=True, timeout=30,
    )
    assert result.returncode == 0, result.stderr
    assert json.loads(target.read_text(encoding="utf-8")) == expected


def test_frozen_cantonese_uses_private_python_for_its_worker(tmp_path, monkeypatch):
    import importlib.util
    import subprocess

    plugin_path = Path(__file__).resolve().parents[1] / "plugins/community/cantonese_readings/plugin.py"
    spec = importlib.util.spec_from_file_location("lp_cantonese_test", plugin_path)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    python = tmp_path / "python.exe"
    python.touch()
    monkeypatch.setattr(sys, "frozen", True, raising=False)
    monkeypatch.setenv("LRCPLUS_DEPENDENCY_PYTHON", str(python))
    monkeypatch.setattr(module.tempfile, "tempdir", str(tmp_path))

    def run(command, **kwargs):
        assert command[:3] == [str(python), "-I", "-u"]
        output = Path(command[command.index("--output") + 1])
        output.write_text(json.dumps([[["text", "reading"]]]))
        return subprocess.CompletedProcess(command, 0, stderr=b"")

    monkeypatch.setattr(module.subprocess, "run", run)
    assert module._isolated_convert(["text"], "jyutping") == [[["text", "reading"]]]


def test_parallel_installation_is_rejected(tmp_path):
    root = tmp_path / "dependencies"
    with dependencies._installation_lock(root):
        with pytest.raises(DependencyError, match="already running"):
            with dependencies._installation_lock(root):
                pytest.fail("Second installer acquired the same lock")


def test_settings_dependency_tab_and_busy_close_guard(qapp, tmp_path):
    from ui.settings_dialog import SettingsDialog

    manager = PluginManager()
    manager.dependency_manager.configure([_plugin("lp-test-missing")])
    dialog = SettingsDialog([], {}, plugin_manager=manager)
    try:
        widget = dialog.dependencies_tab
        assert widget.tree.topLevelItemCount() == 1
        assert widget.tree.topLevelItem(0).text(2) == "Missing"
        widget._job = object()
        dialog.accept()
        assert dialog.result() == 0
        assert "wait" in widget.status.text()
        widget._job = None
        dialog.accept()
        assert dialog.result() == 1
    finally:
        dialog.deleteLater()
        qapp.processEvents()


def test_dependency_job_reports_import_failure_and_restores_controls(qapp, monkeypatch):
    import time
    from ui.dependency_settings import DependencyManagerWidget

    manager = PluginManager()
    manager.dependency_manager.configure([_plugin("packaging", "packaging>=24", "packaging")])

    def fail(_name):
        raise DependencyError("broken DLL")

    monkeypatch.setattr(manager.dependency_manager, "check", fail)
    widget = DependencyManagerWidget(manager)
    try:
        widget._start("check", "packaging")
        assert widget.busy
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        assert "Import failed" in widget.tree.topLevelItem(0).text(2)
        assert "broken DLL" in widget.details.toPlainText()
        assert widget._buttons["check"].isEnabled()
        assert not widget._buttons["install"].isEnabled()
    finally:
        widget.deleteLater()
        qapp.processEvents()
