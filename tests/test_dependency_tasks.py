import subprocess
import sys
import threading
import time

import pytest

from core.dependency_tasks import DependencyCancelled, cancellation_scope, diagnostic_scope, run_process


def test_cancellation_stops_running_installer(monkeypatch):
    processes = []
    popen = subprocess.Popen

    def record(*args, **kwargs):
        process = popen(*args, **kwargs)
        processes.append(process)
        return process

    monkeypatch.setattr(subprocess, "Popen", record)
    cancelled = threading.Event()
    timer = threading.Timer(0.3, cancelled.set)
    timer.start()
    start = time.monotonic()
    try:
        with cancellation_scope(cancelled.is_set):
            with pytest.raises(DependencyCancelled):
                run_process([sys.executable, "-c", "import time; time.sleep(30)"],
                            capture_output=True, timeout=60)
        assert time.monotonic() - start < 5
        assert processes[0].poll() is not None
    finally:
        timer.cancel()


def test_cancellable_process_preserves_output_and_exit_code():
    with cancellation_scope(lambda: False):
        result = run_process([sys.executable, "-c", "print(input()); raise SystemExit(3)"],
                             input="hello\n", capture_output=True, text=True, timeout=5)
    assert result.returncode == 3
    assert result.stdout.strip() == "hello"


def test_diagnostics_stream_output_before_process_exits(tmp_path):
    marker = tmp_path / "output-was-received"
    logs = []

    def log(message):
        logs.append(message)
        if message == "[stdout] Downloading test wheel":
            marker.write_text("received", encoding="utf-8")

    code = (
        "import pathlib, sys, time\n"
        "print('Downloading test wheel', flush=True)\n"
        "while not pathlib.Path(sys.argv[1]).exists(): time.sleep(0.01)\n"
        "print('Test warning', file=sys.stderr, flush=True)\n"
    )
    with diagnostic_scope(log), cancellation_scope(lambda: False):
        result = run_process([sys.executable, "-u", "-c", code, str(marker)],
                             capture_output=True, text=True, timeout=5)
    assert marker.exists()
    assert result.stdout == "Downloading test wheel\n"
    assert result.stderr == "Test warning\n"
    assert "[stderr] Test warning" in logs
    assert any(line.startswith("Command:") for line in logs)
    assert any(line.startswith("Process exited 0 after") for line in logs)


def test_cancellation_keeps_output_already_streamed():
    cancelled = threading.Event()
    logs = []

    def log(message):
        logs.append(message)
        if message == "[stdout] First download chunk":
            cancelled.set()

    with diagnostic_scope(log), cancellation_scope(cancelled.is_set):
        with pytest.raises(DependencyCancelled):
            run_process([sys.executable, "-u", "-c",
                         "import time; print('First download chunk', flush=True); time.sleep(30)"],
                        capture_output=True, text=True, timeout=5)
    assert "[stdout] First download chunk" in logs
    assert any(line.startswith("Process stopped after") for line in logs)
