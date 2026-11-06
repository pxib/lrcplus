"""Compare the same workload without changing the user's plugin preferences."""

import json
from pathlib import Path
import sys

import pytest

from tools import profile_memory


@pytest.mark.skipif(sys.platform != "win32", reason="Profiler uses Windows memory counters")
@pytest.mark.parametrize("disable_karaoke", [False, True])
def test_profiler_comparison_only_changes_the_current_run(
    qapp, tmp_path, monkeypatch, disable_karaoke
):
    import main as application
    from core.settings import get_disabled_plugins, save_all_settings

    save_all_settings({"disabled_plugins": ["unrelated_plugin"]})
    saved_disabled = get_disabled_plugins()
    output = tmp_path / "memory.jsonl"
    arguments = ["profile_memory.py", "--output", str(output)]
    if disable_karaoke:
        arguments.append("--disable-karaoke")
    monkeypatch.setattr(sys, "argv", arguments)
    monkeypatch.setattr(application.Player, "_automatic_update_check", lambda self: None)
    # The launcher replaces this class for the duration of the process.
    monkeypatch.setattr(application, "Player", application.Player)
    windows = []

    def run_application():
        manager = application.PluginManager()
        manager.load_from_file("karaoke", Path("plugins/core/karaoke/plugin.py"))
        window = application.Player(plugin_manager=manager)
        windows.append(window)
        try:
            assert ("karaoke" in manager.plugins) is not disable_karaoke
            assert get_disabled_plugins() == saved_disabled
            manager.set_host_window(window)
            manager.emit("app_ready", window=window)
        finally:
            manager.unload_all()
            window.close()
        return 0

    monkeypatch.setattr(application, "main", run_application)
    try:
        assert profile_memory.main() == 0
        records = [json.loads(line) for line in output.read_text(encoding="utf-8").splitlines()]
        assert ("karaoke" in records[0]["runtime"]["loaded_plugins"]) is not disable_karaoke
        assert get_disabled_plugins() == saved_disabled
    finally:
        for window in windows:
            window.deleteLater()
        qapp.processEvents()
