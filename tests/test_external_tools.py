import hashlib
import io
import json
import os
from pathlib import Path
from types import SimpleNamespace
import zipfile

import pytest
from PySide6.QtCore import Qt

from core import external_tools as tools
from core.dependencies import DependencyError


def archive_files(files):
    buffer = io.BytesIO()
    with zipfile.ZipFile(buffer, "w") as archive:
        for name, value in files.items():
            archive.writestr(name, value)
    return buffer.getvalue()


@pytest.fixture
def manager(tmp_path, monkeypatch):
    monkeypatch.setenv("PATH", os.environ.get("PATH", ""))
    monkeypatch.setattr(tools, "_active_paths", set())
    monkeypatch.setattr(tools.sysconfig, "get_platform", lambda: "win-amd64")
    return tools.ExternalToolManager(tmp_path / "tools")


@pytest.fixture
def publisher(monkeypatch):
    data = {"blob": archive_files({"build/bin/ffmpeg.exe": b"good", "build/bin/ffprobe.exe": b"good",
                                    "build/LICENSE.txt": b"license", "deno.exe": b"good"}),
            "runs": []}

    class Response(io.BytesIO):
        def geturl(self):
            return "https://release-assets.githubusercontent.com/test.zip"

    monkeypatch.setattr(tools, "_request", lambda url: Response(data["blob"]))

    def release(repo):
        name = next(key for key, tool in tools.TOOLS.items() if tool["repo"] == repo)
        return {"tag_name": "v2.9.8", "assets": [{
            "name": tools.TOOLS[name]["assets"]["win-amd64"],
            "digest": "sha256:" + hashlib.sha256(data["blob"]).hexdigest(),
            "browser_download_url": f"https://github.com/{repo}/releases/download/v2.9.8/tool.zip",
        }]}

    def run(command, **kwargs):
        data["runs"].append(command)
        executable = Path(command[0])
        bad = executable.read_bytes() == b"broken"
        line = "deno 2.9.8" if executable.stem == "deno" else executable.stem + " version test"
        return SimpleNamespace(returncode=1 if bad else 0, stdout=line, stderr="")

    monkeypatch.setattr(tools, "_release", release)
    monkeypatch.setattr(tools.subprocess, "run", run)
    return data


def test_ffmpeg_pair_installs_checks_and_is_discovered_by_importer(manager, publisher):
    from plugins.community.ytdlp_import.worker import tool_paths
    manager.install("ffmpeg")
    status = manager.status("ffmpeg")
    assert status["state"] == "available"
    assert status["managed"]
    assert set(status["paths"]) == {"ffmpeg", "ffprobe"}
    assert "ffmpeg version test" in manager.check("ffmpeg")
    assert Path(tool_paths({})["ffmpeg"]) == Path(status["paths"]["ffmpeg"])
    assert Path(tool_paths({})["ffprobe"]) == Path(status["paths"]["ffprobe"])
    assert Path(status["paths"]["ffmpeg"]).parent.parent.joinpath("LICENSE.txt").is_file()


def test_deno_install_is_automatically_available_to_new_imports(manager, publisher):
    from plugins.community.ytdlp_import.worker import tool_paths
    manager.install("deno")
    path = manager.paths("deno")["deno"]
    assert Path(tool_paths({})["js_runtimes"]["deno"]["path"]) == Path(path)
    assert "deno 2.9.8" in manager.check("deno")


def test_failed_repair_keeps_existing_install_and_process_path(manager, publisher):
    manager.install("ffmpeg")
    original = manager.paths("ffmpeg")
    process_path = os.environ["PATH"]
    publisher["blob"] = archive_files({"bin/ffmpeg.exe": b"broken", "bin/ffprobe.exe": b"good"})
    with pytest.raises(DependencyError, match="could not run"):
        manager.install("ffmpeg")
    assert manager.paths("ffmpeg") == original
    assert os.environ["PATH"] == process_path
    assert len(list((manager.root / "ffmpeg" / "versions").iterdir())) == 1


def test_successful_update_replaces_active_paths_and_survives_restart(manager, publisher, monkeypatch):
    manager.install("ffmpeg")
    old = Path(manager.paths("ffmpeg")["ffmpeg"]).parent
    manager.install("ffmpeg")
    current = Path(manager.paths("ffmpeg")["ffmpeg"]).parent
    assert current != old
    assert str(current) in os.environ["PATH"].split(os.pathsep)
    assert str(old) not in os.environ["PATH"].split(os.pathsep)
    monkeypatch.setenv("PATH", "")
    monkeypatch.setattr(tools, "_active_paths", set())
    tools.ExternalToolManager(manager.root).activate()
    assert os.environ["PATH"] == str(current)


def test_missing_ffprobe_does_not_publish_half_an_installation(manager, publisher):
    publisher["blob"] = archive_files({"bin/ffmpeg.exe": b"good"})
    with pytest.raises((DependencyError, FileNotFoundError)):
        manager.install("ffmpeg")
    assert not (manager.root / "ffmpeg" / "active.json").exists()


def test_download_hash_failure_prevents_extraction_and_execution(manager, publisher, monkeypatch):
    original_release = tools._release

    def release(repo):
        value = original_release(repo)
        value["assets"][0]["digest"] = "sha256:" + "0" * 64
        return value

    monkeypatch.setattr(tools, "_release", release)
    with pytest.raises(DependencyError, match="SHA-256 verification failed"):
        manager.install("ffmpeg")
    assert publisher["runs"] == []
    assert not (manager.root / "ffmpeg" / "active.json").exists()


def test_remove_preserves_tools_installed_elsewhere(manager, publisher, tmp_path, monkeypatch):
    system = tmp_path / "system-bin"
    system.mkdir()
    for name in ("ffmpeg", "ffprobe"):
        (system / (name + ".exe")).write_bytes(b"system")
    monkeypatch.setenv("PATH", str(system))
    manager.install("ffmpeg")
    manager.remove("ffmpeg")
    assert manager.status("ffmpeg")["text"] == "Found on PATH"
    assert not manager.status("ffmpeg")["managed"]
    assert Path(manager.paths("ffmpeg")["ffmpeg"]) == system / "ffmpeg.exe"
    assert (system / "ffmpeg.exe").read_bytes() == b"system"
    assert os.environ["PATH"] == str(system)


@pytest.mark.parametrize("entry", ["../escape.exe", "/escape.exe", "C:/escape.exe", "..\\escape.exe"])
def test_archive_paths_cannot_escape_installation(tmp_path, entry):
    archive = tmp_path / "unsafe.zip"
    archive.write_bytes(archive_files({entry: b"bad"}))
    with pytest.raises(DependencyError, match="invalid path"):
        tools._extract(archive, tmp_path / "extracted")


def test_corrupt_pointer_is_reported_and_can_be_removed(manager):
    folder = manager.root / "ffmpeg"
    folder.mkdir(parents=True)
    (folder / "active.json").write_text(json.dumps({"installation": "../../outside"}))
    assert manager.status("ffmpeg")["state"] == "broken"
    manager.remove("ffmpeg")
    assert not (folder / "active.json").exists()


def test_old_deno_is_rejected(manager, tmp_path, monkeypatch):
    monkeypatch.setattr(tools.subprocess, "run", lambda *args, **kwargs:
                        SimpleNamespace(returncode=0, stdout="deno 2.2.0\n", stderr=""))
    with pytest.raises(DependencyError, match="2.3 or newer"):
        manager._check_paths("deno", {"deno": str(tmp_path / "deno.exe")})


def test_tool_rows_and_threaded_install_work_without_plugin_manager(qapp, manager, publisher, monkeypatch):
    import time
    from ui import dependency_settings

    monkeypatch.setattr(dependency_settings, "ExternalToolManager", lambda: manager)
    widget = dependency_settings.DependencyManagerWidget(None)
    try:
        row = next(widget.tree.topLevelItem(i) for i in range(widget.tree.topLevelItemCount())
                   if widget.tree.topLevelItem(i).data(0, Qt.ItemDataRole.UserRole) == "ffmpeg")
        widget.tree.setCurrentItem(row)
        assert widget._buttons["install"].isEnabled()
        assert not widget._buttons["remove"].isEnabled()
        widget._buttons["install"].click()
        assert widget.busy
        deadline = time.monotonic() + 5
        while widget.busy and time.monotonic() < deadline:
            qapp.processEvents()
            time.sleep(.005)
        assert not widget.busy
        assert widget._buttons["remove"].isEnabled()
        assert "New imports" in widget.status.text()
        assert "FFmpeg" in widget.tree.currentItem().text(0)
    finally:
        widget.deleteLater()
        qapp.processEvents()
