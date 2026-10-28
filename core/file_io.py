"""Atomic writes for user-authored files."""

import os
from pathlib import Path
import tempfile
import time


def _atomic_write_bytes(path: Path, contents: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = None
    try:
        with tempfile.NamedTemporaryFile(dir=path.parent, prefix=f".{path.name}.",
                                         suffix=".tmp", delete=False) as handle:
            temporary = Path(handle.name)
            handle.write(contents)
            handle.flush()
            os.fsync(handle.fileno())
        for attempt in range(5):
            try:
                os.replace(temporary, path)
                break
            except PermissionError:
                if attempt == 4:
                    raise
                time.sleep(0.05 * (attempt + 1))
    finally:
        if temporary is not None:
            temporary.unlink(missing_ok=True)


def atomic_write_text(path, contents: str, *, backup: bool = False) -> None:
    """Replace UTF-8 text only after writing it fully; optionally keep a .bak."""
    path = Path(path)
    encoded = contents.encode("utf-8")
    if backup and path.exists():
        _atomic_write_bytes(path.with_name(path.name + ".bak"), path.read_bytes())
    _atomic_write_bytes(path, encoded)
