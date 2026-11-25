import hashlib
import json
import sys
import zipfile
from io import BytesIO
from pathlib import Path

import pytest
from PySide6.QtCore import Qt

import core.dependencies as dependencies
from core.dependencies import DependencyError, DependencyManager, parse_dependencies
from core.plugin_manager import PluginManager


def _plugin(name, requirement=None, module=None):
    return {
        "plugin_id": name, "name": name,
        "dependencies": [{"requirement": requirement or name, "import_name": module or name.replace("-", "_")}],
    }


def _wheel(folder, name, *, version="1.0", requires=(), extras=(), code="VALUE = 42\n"):
    module = name.replace("-", "_")
    metadata = f"{module}-{version}.dist-info"
    files = {
        f"{module}/__init__.py": code,
        f"{metadata}/METADATA": "Metadata-Version: 2.1\n" + f"Name: {name}\nVersion: {version}\n"
        + "".join(f"Provides-Extra: {item}\n" for item in extras)
        + "".join(f"Requires-Dist: {item}\n" for item in requires) + "\n",
        f"{metadata}/WHEEL": "Wheel-Version: 1.0\nGenerator: test\nRoot-Is-Purelib: true\nTag: py3-none-any\n",
    }
    files[f"{metadata}/RECORD"] = "".join(f"{path},,\n" for path in [*files, f"{metadata}/RECORD"])
    path = folder / f"{module}-{version}-py3-none-any.whl"
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
    _wheel(wheels, "fugashi", version="1.5.2", code="import unidic_lite\n")
    _wheel(wheels, "unidic-lite", version="1.0.8")
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


@pytest.mark.exhaustive
def test_explicit_supporting_requirements_are_installed(offline_manager):
    plugin = _plugin("lp-test-undeclared")
    plugin["dependencies"][0]["requires"] = ["lp-test-support>=1,<2"]
    offline_manager.configure([plugin])
    state = offline_manager.install("lp-test-undeclared")
    assert "lp-test-support<2,>=1" in state["requirements"]["lp-test-undeclared"]
    offline_manager.check("lp-test-undeclared")


@pytest.mark.exhaustive
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


def test_yt_dlp_build_channels_switch_and_survive_dependency_rebuilds(offline_manager, monkeypatch):
    manager = offline_manager
    manager.configure([_plugin("yt-dlp", "yt-dlp[default]>=2026.3.3"), _plugin("lp-test-alpha")])
    wheels = manager.root.parent / "wheels"
    stable = _wheel(wheels, "yt-dlp", version="2026.4.1", extras=["default"],
                    requires=["lp-test-support; extra == 'default'"], code="import lp_test_support\n")
    nightly = _wheel(wheels, "yt-dlp", version="2026.3.5.dev1", extras=["default"],
                     requires=["lp-test-support; extra == 'default'"], code="import lp_test_support\n")
    files = [
        {"filename": stable.name},
        {"filename": nightly.name},
        {"filename": "yt_dlp-2026.5.1.dev1-py3-none-any.whl", "requires-python": ">=99"},
        {"filename": "yt_dlp-2026.6.1.dev1-py3-none-any.whl", "yanked": "bad build"},
        {"filename": "yt_dlp-2026.3.3.dev1-py3-none-any.whl"},
    ]

    def package_index(request, *, timeout):
        assert request.full_url == "https://pypi.org/simple/yt-dlp/"
        return BytesIO(json.dumps({"files": files}).encode())

    monkeypatch.setattr(dependencies.urllib.request, "urlopen", package_index)
    manager.install("yt-dlp")
    assert manager.status("yt-dlp")["channel"] == "stable"
    manager.install("yt-dlp", channel="nightly")
    assert manager.status("yt-dlp")["text"] == "Installed (Nightly 2026.3.5.dev1) — restart required"
    manager.install("lp-test-alpha")
    assert manager.status("yt-dlp")["channel"] == "nightly"
    manager.check("yt-dlp")

    nightly_state = manager.state()
    files.remove({"filename": nightly.name})
    with pytest.raises(DependencyError, match="No compatible yt-dlp nightly build"):
        manager.install("yt-dlp", channel="nightly")
    assert manager.state() == nightly_state

    manager.install("yt-dlp", channel="stable")
    assert manager.status("yt-dlp")["version"] == "2026.4.1"
    assert manager.status("lp-test-alpha")["state"] == "available"


@pytest.mark.exhaustive
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
    [{"requirement": "x", "import_name": "x", "optional": "true"}],
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


def test_settings_only_refresh_dependencies_on_first_selection(qapp, monkeypatch):
    from PySide6.QtWidgets import QTabWidget
    from ui.dependency_settings import DependencyManagerWidget
    from ui.settings_dialog import SettingsDialog

    refreshes = []
    original_refresh = DependencyManagerWidget.refresh

    def refresh(widget):
        refreshes.append(widget)
        original_refresh(widget)

    monkeypatch.setattr(DependencyManagerWidget, "refresh", refresh)
    dialog = SettingsDialog([], {})
    try:
        widget = dialog.dependencies_tab
        tabs = next(tab for tab in dialog.findChildren(QTabWidget)
                    if tab.indexOf(widget) >= 0)
        assert refreshes == []
        assert widget.tree.topLevelItemCount() == 0
        # Painting Settings must not start checks for the hidden tab either.
        dialog.show()
        qapp.processEvents()
        assert refreshes == []
        tabs.setCurrentWidget(widget)
        assert refreshes == [widget]
        assert widget.tree.topLevelItemCount() > 0
        tabs.setCurrentIndex(0)
        tabs.setCurrentWidget(widget)
        assert refreshes == [widget]
    finally:
        dialog.close()
        dialog.deleteLater()
        qapp.processEvents()


def test_settings_dependency_tab_and_busy_close_guard(qapp, tmp_path):
    from ui.settings_dialog import SettingsDialog

    manager = PluginManager()
    manager.dependency_manager.configure([_plugin("lp-test-missing")])
    dialog = SettingsDialog([], {}, plugin_manager=manager)
    try:
        widget = dialog.dependencies_tab
        from PySide6.QtWidgets import QTabWidget
        tabs = next(tab for tab in dialog.findChildren(QTabWidget)
                    if tab.indexOf(widget) >= 0)
        tabs.setCurrentWidget(widget)
        missing = next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount())
                       if widget.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "lp-test-missing")
        assert missing.text(2) == "Missing"
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
        item = next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount())
                    if widget.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "packaging")
        widget.tree.setCurrentItem(item)
        widget._start("check", "packaging")
        assert widget.busy
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        item = widget.tree.currentItem()
        assert "Import failed" in item.text(2)
        assert "broken DLL" in widget.details.toPlainText()
        assert widget._buttons["check"].isEnabled()
        assert not widget._buttons["install"].isEnabled()
    finally:
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.exhaustive
def test_mecab_engine_and_dictionary_install_together(offline_manager):
    manager = offline_manager
    manager.base_paths = []
    manager.base_versions = {}
    state = manager.install("fugashi")
    site = dependencies._site_path(manager.root, state)
    assert (site / "fugashi" / "__init__.py").is_file()
    assert (site / "unidic_lite" / "__init__.py").is_file()
    assert manager.status("fugashi")["state"] == "available"
    assert manager.status("unidic-lite")["state"] == "available"
    assert "MeCab" in manager.catalog["fugashi"]["plugins"][0]
    manager.remove("fugashi")
    assert manager.status("fugashi")["state"] == "missing"
    assert manager.status("unidic-lite")["state"] == "missing"


@pytest.mark.parametrize("version", [None, "0.1"])
def test_app_version_mismatch_still_allows_check_and_explains_repair(qapp, version):
    from ui.dependency_settings import DependencyManagerWidget

    manager = PluginManager()
    manager.dependency_manager.configure([_plugin("packaging", "packaging>=24", "packaging")])
    manager.dependency_manager.base_versions["packaging"] = version
    widget = DependencyManagerWidget(manager)
    try:
        item = next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount())
                    if widget.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "packaging")
        widget.tree.setCurrentItem(item)
        assert widget._buttons["check"].isEnabled()
        assert not widget._buttons["install"].isEnabled()
        assert "Update LRCPlus" in widget.action_hint.text()
        widget._succeeded("check", "packaging")
        widget.refresh()
        assert "Import check passed" in widget.tree.currentItem().text(2)
        assert "Incompatible" in widget.tree.currentItem().text(2) or "verified" in widget.tree.currentItem().text(2)
    finally:
        widget.deleteLater()
        qapp.processEvents()


def test_checked_dependencies_install_as_queue_and_continue_after_failure(qapp, monkeypatch):
    import time
    from ui.dependency_settings import DependencyManagerWidget

    manager = PluginManager()
    manager.dependency_manager.configure([
        _plugin("lp-test-alpha"), _plugin("lp-test-beta"),
        _plugin("yt-dlp"),
        _plugin("packaging", "packaging>=24", "packaging"),
    ])
    calls = []

    def install(name, log, *, channel=None):
        calls.append(("library", name, channel))
        if name == "lp-test-alpha":
            raise DependencyError("test install failure")
        log("test install completed")

    monkeypatch.setattr(manager.dependency_manager, "install", install)
    widget = DependencyManagerWidget(manager)
    monkeypatch.setattr(widget.tools, "supported", lambda name: name == "ffmpeg")
    monkeypatch.setattr(widget.tools, "install", lambda name, log: calls.append(("tool", name)))
    try:
        widget.select_all.click()
        # Includes app-provided packaging and an unsupported tool: both must be skipped.
        assert len(widget._checked_items()) == widget.tree.topLevelItemCount()
        widget.refresh()
        assert len(widget._checked_items()) == widget.tree.topLevelItemCount()
        assert not widget.yt_dlp_channel.isHidden()
        assert widget.yt_dlp_channel.currentData() == "stable"
        widget.yt_dlp_channel.setCurrentIndex(widget.yt_dlp_channel.findData("nightly"))
        widget._buttons["install"].click()
        assert widget.busy
        assert not widget.tree.isEnabled()
        assert not widget.select_all.isEnabled()
        assert not widget.yt_dlp_channel.isEnabled()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        assert ("library", "lp-test-alpha", None) in calls
        assert ("library", "lp-test-beta", None) in calls
        assert ("library", "yt-dlp", "nightly") in calls
        assert ("tool", "ffmpeg") in calls
        assert ("library", "packaging", None) not in calls
        assert ("tool", "deno") not in calls
        assert "1 failed" in widget.status.text()
        assert "lp-test-alpha: test install failure" in widget.details.toPlainText()
        assert widget.tree.isEnabled()
        assert widget._buttons["install"].isEnabled()
        widget.clear_selection.click()
        assert widget._checked_items() == []
        yt_dlp = next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount())
                      if widget.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "yt-dlp")
        widget.tree.setCurrentItem(yt_dlp)
        assert not widget.yt_dlp_channel.isHidden()
        widget.yt_dlp_channel.setCurrentIndex(widget.yt_dlp_channel.findData("stable"))
        widget._buttons["install"].click()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        assert calls[-1] == ("library", "yt-dlp", "stable")
    finally:
        if widget._job is not None:
            widget._job.wait()
            qapp.processEvents()
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.parametrize("disable", [False, True])
def test_dependency_notice_dont_show_again_survives_changed_errors(qapp, monkeypatch, disable):
    from types import SimpleNamespace
    from PySide6.QtWidgets import QMessageBox
    from ui import dependency_settings as module

    shown = []

    def dismiss(notice):
        shown.append(notice)
        notice.checkBox().setChecked(disable)
        next(button for button in notice.buttons() if button.text() == "Later").click()
        return notice.result()

    monkeypatch.setattr(QMessageBox, "exec", dismiss)
    manager = SimpleNamespace(dependency_errors={"first": "missing library"})
    module.show_dependency_notice(manager, None)
    assert module.load_all_settings()["dependency_notice_disabled"] is disable
    manager.dependency_errors = {"second": "another missing library"}
    module.show_dependency_notice(manager, None)
    assert len(shown) == (1 if disable else 2)

    widget = module.DependencyManagerWidget(None)
    try:
        assert widget.hide_notices.isChecked() is disable
        widget.hide_notices.setChecked(not disable)
        assert module.load_all_settings()["dependency_notice_disabled"] is (not disable)
        manager.dependency_errors = {"third": "new missing library"}
        module.show_dependency_notice(manager, None)
        assert len(shown) == 2
    finally:
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.exhaustive
def test_cancelled_library_install_rolls_back_before_activation(offline_manager, monkeypatch):
    from core.dependency_tasks import DependencyCancelled, cancellation_scope

    manager = offline_manager
    original = manager.install("lp-test-alpha")
    existing = set((manager.root / "environments").iterdir())
    probe = dependencies.probe_imports
    cancelled = False

    def cancel_after_probe(*args, **kwargs):
        nonlocal cancelled
        result = probe(*args, **kwargs)
        cancelled = True
        return result

    monkeypatch.setattr(dependencies, "probe_imports", cancel_after_probe)
    with cancellation_scope(lambda: cancelled):
        with pytest.raises(DependencyCancelled):
            manager.install("lp-test-beta")
    assert manager.state() == original
    assert set((manager.root / "environments").iterdir()) == existing


def test_cancel_button_stops_current_install_and_remaining_batch(qapp, monkeypatch):
    import threading
    import time
    from core.dependency_tasks import check_cancelled
    from ui.dependency_settings import DependencyManagerWidget

    manager = PluginManager()
    manager.dependency_manager.configure([_plugin("lp-test-alpha"), _plugin("lp-test-beta")])
    calls = []
    started = threading.Event()

    def install(name, log):
        calls.append(name)
        started.set()
        while True:
            check_cancelled()
            time.sleep(0.005)

    monkeypatch.setattr(manager.dependency_manager, "install", install)
    widget = DependencyManagerWidget(manager)
    try:
        for i in range(widget.tree.topLevelItemCount()):
            item = widget.tree.topLevelItem(i)
            if item.data(0, Qt.ItemDataRole.UserRole) in {"lp-test-alpha", "lp-test-beta"}:
                item.setCheckState(0, Qt.CheckState.Checked)
        widget._buttons["install"].click()
        assert started.wait(2)
        assert widget.cancel_button.isEnabled()
        widget.cancel_button.click()
        assert widget.busy
        assert "Cancelling" in widget.status.text()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        assert calls == ["lp-test-alpha"]
        assert "Cancelled" in widget.status.text()
        assert not widget.cancel_button.isEnabled()
        assert widget.tree.isEnabled()
        assert widget._buttons["install"].isEnabled()
        # A new batch can run after cancellation.
        monkeypatch.setattr(manager.dependency_manager, "install", lambda name, log: calls.append(name))
        widget._buttons["install"].click()
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(0.005)
        assert not widget.busy
        assert calls == ["lp-test-alpha", "lp-test-alpha", "lp-test-beta"]
        assert "2 succeeded" in widget.status.text()
    finally:
        if widget._job is not None:
            widget._job.requestInterruption()
            widget._job.wait()
            qapp.processEvents()
        widget.deleteLater()
        qapp.processEvents()


@pytest.mark.exhaustive
def test_verbose_library_install_reports_resolution_checks_and_activation(offline_manager):
    from core.dependency_tasks import diagnostic_scope

    logs = []
    with diagnostic_scope(logs.append):
        offline_manager.install("lp-test-alpha")
    text = "\n".join(logs)
    assert "--verbose" in text
    assert "Installation target:" in text
    assert "Constraints file:" in text
    assert "[stdout]" in text
    assert "Successfully installed" in text
    assert "Process exited 0" in text
    assert "Installed version check lp-test-alpha:" in text
    assert "Import probe lp_test_alpha: passed" in text
    assert "Activated environment:" in text


def test_dependency_job_verbose_failure_contains_traceback(qapp):
    from types import SimpleNamespace
    from ui.dependency_settings import _DependencyJob

    def fail(name):
        raise DependencyError("test diagnostic failure")

    job = _DependencyJob(SimpleNamespace(check=fail), "check", "test")
    logs, errors = [], []
    job.diagnostic_message.connect(logs.append)
    job.failed.connect(errors.append)
    job.run()
    assert errors == ["test diagnostic failure"]
    text = "\n".join(logs)
    assert "Python:" in text
    assert "Platform:" in text
    assert "Traceback (most recent call last)" in text
    assert "DependencyError: test diagnostic failure" in text
    assert "Operation failed after" in text


def test_verbose_log_burst_batches_worker_notifications(qapp, monkeypatch):
    from ui import dependency_settings as module

    monkeypatch.setattr(module.time, "monotonic", lambda: 10.0)
    job = module._DependencyJob(None, "check", "test")
    batches = []
    job.diagnostic_message.connect(batches.append)
    for index in range(10000):
        job._buffer_diagnostic(f"Installer output {index}")
    job._flush_diagnostics()
    assert len(batches) == 2
    assert batches[0] == "Installer output 0"
    assert len(batches[1].splitlines()) == 5000
    assert batches[1].splitlines()[0] == "Installer output 5000"
    assert batches[1].splitlines()[-1] == "Installer output 9999"


def test_hidden_verbose_logs_defer_rendering_and_retain_latest_lines(qapp):
    from ui.dependency_settings import DependencyManagerWidget

    widget = DependencyManagerWidget(None)
    try:
        for index in range(10000):
            widget._log(f"Installer output {index}")
        assert widget.details.toPlainText() == ""
        assert not widget._log_timer.isActive()
        widget.details_button.setChecked(True)
        lines = widget.details.toPlainText().splitlines()
        assert len(lines) == 5000
        assert lines[0].endswith("Installer output 5000")
        assert lines[-1].endswith("Installer output 9999")

        before = widget.details.toPlainText()
        widget._log("New live output")
        assert widget.details.toPlainText() == before
        assert widget._log_timer.isActive()
        widget._flush_logs()
        assert widget.details.toPlainText().endswith("New live output")
        assert len(widget.details.toPlainText().splitlines()) == 5000

        widget.details_button.setChecked(False)
        widget._log("Old operation output")
        widget._clear_logs()
        widget._log("Next operation output")
        widget.details_button.setChecked(True)
        assert "Old operation output" not in widget.details.toPlainText()
        assert "Next operation output" in widget.details.toPlainText()
    finally:
        widget.deleteLater()
        qapp.processEvents()
