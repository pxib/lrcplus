"""Keep fatal Python/native fault traces available in windowed releases."""

import faulthandler
import sys
from datetime import datetime, timezone

from core.settings import get_app_data_dir


_crash_file = None


def install_crash_logging():
    global _crash_file
    if _crash_file is not None:
        return
    try:
        directory = get_app_data_dir() / "logs"
        directory.mkdir(parents=True, exist_ok=True)
        path = directory / "crash.log"
        # Keep the previous trace across restarts without growing indefinitely.
        if path.exists() and path.stat().st_size > 2 * 1024 * 1024:
            path.replace(directory / "crash.previous.log")
        output = path.open("a", encoding="utf-8", buffering=1)
        output.write(f"\nSession {datetime.now(timezone.utc).isoformat()}\nPython {sys.version}\n")
        try:
            faulthandler.enable(file=output, all_threads=True)
        except (OSError, RuntimeError):
            output.close()
            return
        # faulthandler keeps the descriptor, so retain its owner until exit.
        _crash_file = output
    except OSError:
        # Diagnostics must not prevent startup in a read-only environment.
        return
