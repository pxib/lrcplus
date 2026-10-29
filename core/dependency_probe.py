"""Import checks run in a child process, including in frozen releases."""
from __future__ import annotations

import contextlib
import argparse
import importlib
import io
import json
import sys
from pathlib import Path


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    request = json.loads(Path(args.input).read_text(encoding="utf-8"))
    if getattr(sys, "frozen", False):
        from core.dependencies import activate_runtime_libraries
        activate_runtime_libraries()
    # Mirror the app's precedence: bundled/system libraries win over downloads.
    for path in request.get("paths", []):
        if path and path not in sys.path:
            sys.path.append(path)
    site = request.get("site")
    if site and site not in sys.path:
        sys.path.append(site)
    result = {}
    for name in request["imports"]:
        try:
            # Third-party diagnostics must not corrupt the response protocol.
            with contextlib.redirect_stdout(io.StringIO()), contextlib.redirect_stderr(io.StringIO()):
                importlib.import_module(name)
            result[name] = None
        except BaseException as error:
            result[name] = f"{type(error).__name__}: {error}"
    # A windowed PyInstaller executable has no Python stdin/stdout streams.
    Path(args.output).write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
