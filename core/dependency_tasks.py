"""Cooperative cancellation for dependency workers and their subprocesses."""
from contextlib import contextmanager
import os
import signal
import subprocess
import threading
import time


class DependencyCancelled(Exception):
    pass


_worker = threading.local()


@contextmanager
def diagnostic_scope(log):
    previous = getattr(_worker, "log", None)
    _worker.log = log
    try:
        yield
    finally:
        _worker.log = previous


def diagnostics_enabled():
    return getattr(_worker, "log", None) is not None


def diagnostic(message):
    log = getattr(_worker, "log", None)
    if log is not None:
        log(message)


@contextmanager
def cancellation_scope(requested):
    previous = getattr(_worker, "requested", None)
    _worker.requested = requested
    try:
        check_cancelled()
        yield
    finally:
        _worker.requested = previous


def check_cancelled():
    requested = getattr(_worker, "requested", None)
    if requested is not None and requested():
        raise DependencyCancelled("Dependency operation cancelled.")


def run_process(command, **kwargs):
    """Match subprocess.run, stopping the worker's process tree on cancellation."""
    log = getattr(_worker, "log", None)
    if getattr(_worker, "requested", None) is None and log is None:
        return subprocess.run(command, **kwargs)
    check_cancelled()
    timeout = kwargs.pop("timeout", None)
    payload = kwargs.pop("input", None)
    if payload is not None:
        kwargs["stdin"] = subprocess.PIPE
    if kwargs.pop("capture_output", False):
        kwargs["stdout"] = kwargs["stderr"] = subprocess.PIPE
    if os.name != "nt":
        kwargs["start_new_session"] = True
    deadline = time.monotonic() + timeout if timeout is not None else None
    started = time.monotonic()
    diagnostic("Command: " + subprocess.list2cmdline([str(part) for part in command]))
    diagnostic(f"Process timeout: {timeout} seconds")
    with subprocess.Popen(command, **kwargs) as process:
        readers = []
        outputs = [[], []]
        streaming = log is not None and payload is None
        if streaming:
            def read_output(stream, parts, channel):
                for line in iter(stream.readline, "" if process.text_mode else b""):
                    parts.append(line)
                    text = line if isinstance(line, str) else line.decode("utf-8", errors="replace")
                    log(f"[{channel}] {text.rstrip()}")

            for stream, parts, channel in ((process.stdout, outputs[0], "stdout"),
                                           (process.stderr, outputs[1], "stderr")):
                if stream is not None:
                    reader = threading.Thread(target=read_output, args=(stream, parts, channel), daemon=True)
                    readers.append(reader)
                    reader.start()
        try:
            while True:
                check_cancelled()
                if deadline is not None and time.monotonic() >= deadline:
                    raise subprocess.TimeoutExpired(command, timeout)
                try:
                    if streaming:
                        process.wait(timeout=0.1)
                        for reader in readers:
                            reader.join(timeout=0.1)
                        if any(reader.is_alive() for reader in readers):
                            continue
                        empty = "" if process.text_mode else b""
                        stdout = empty.join(outputs[0]) if process.stdout is not None else None
                        stderr = empty.join(outputs[1]) if process.stderr is not None else None
                    else:
                        stdout, stderr = process.communicate(input=payload, timeout=0.1)
                    diagnostic(f"Process exited {process.returncode} after {time.monotonic() - started:.2f}s")
                    return subprocess.CompletedProcess(command, process.returncode, stdout, stderr)
                except subprocess.TimeoutExpired:
                    payload = None
        except BaseException:
            if os.name == "nt":
                # pip may launch build helpers; stop those along with pip.
                try:
                    subprocess.run(
                        ["taskkill", "/PID", str(process.pid), "/T", "/F"],
                        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
                        creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0), timeout=10,
                    )
                except (OSError, subprocess.TimeoutExpired):
                    pass
            else:
                try:
                    os.killpg(process.pid, signal.SIGKILL)
                except ProcessLookupError:
                    pass
            if process.poll() is None:
                process.kill()
            if streaming:
                process.wait()
                for reader in readers:
                    reader.join()
            else:
                process.communicate()
            diagnostic(f"Process stopped after {time.monotonic() - started:.2f}s; exit code {process.returncode}")
            raise
