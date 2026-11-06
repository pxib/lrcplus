"""Disposable, GUI-free pypinyin worker; responses use UTF-8 JSON files."""

import argparse
import json
import os
from pathlib import Path
import sys


def convert_runs(runs):
    # This is the only pypinyin import in the app. This module itself must stay
    # cheap to import for frozen worker dispatch and packaging analysis.
    from pypinyin import Style, pinyin

    return [
        [str(values[0]) if values else None
         for values in pinyin(text, style=Style.TONE, heteronym=False, strict=False)]
        for text in runs
    ]


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    if getattr(sys, "frozen", False):
        from core.dependencies import activate_runtime_libraries
        activate_runtime_libraries()
    # Mirror the running app's optional dependency environment while preserving
    # precedence for bundled libraries. Source workers use isolated Python mode.
    site = os.environ.get("LRCPLUS_DEPENDENCY_SITE")
    if site and site not in sys.path:
        sys.path.append(site)
    try:
        request = json.loads(Path(args.input).read_text(encoding="utf-8"))
        runs = request["runs"]
        if not isinstance(runs, list) or any(not isinstance(text, str) for text in runs):
            raise ValueError("Expected a list of Han text runs")
        result = convert_runs(runs)
    except Exception as error:
        # A frozen, windowed executable may have no Python stdout/stderr.
        Path(args.output).write_text(json.dumps({"error": f"{type(error).__name__}: {error}"}), encoding="utf-8")
        return 1
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
