"""Share provider request pacing across isolated downloader processes."""
from contextlib import contextmanager
import getpass
import hashlib
import os
from pathlib import Path
import tempfile
import time


@contextmanager
def _locked(path):
    with path.open("a+b") as initial:
        if initial.tell() == 0:
            initial.write(b"0")
    with path.open("r+b") as handle:
        if os.name == "nt":
            import msvcrt
            handle.seek(0)
            msvcrt.locking(handle.fileno(), msvcrt.LK_LOCK, 1)
        else:
            import fcntl
            fcntl.flock(handle, fcntl.LOCK_EX)
        try:
            yield handle
        finally:
            handle.seek(0)
            if os.name == "nt":
                msvcrt.locking(handle.fileno(), msvcrt.LK_UNLCK, 1)
            else:
                fcntl.flock(handle, fcntl.LOCK_UN)


def wait_for_provider(name, interval=1.1):
    user = hashlib.sha256(getpass.getuser().encode()).hexdigest()[:16]
    path = Path(tempfile.gettempdir()) / f"lrcplus-{user}-{name}.pace"
    with _locked(path) as handle:
        handle.seek(1)
        try:
            previous = float(handle.read().decode() or "0")
        except ValueError:
            previous = 0
        time.sleep(max(0, min(interval, previous + interval - time.time())))
        handle.seek(1)
        handle.write(str(time.time()).encode())
        handle.truncate()
        handle.flush()
