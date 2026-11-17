"""Load alkana only in a disposable process, releasing its dictionary on exit."""
from __future__ import annotations

import argparse
import importlib.util
import json
import os
from pathlib import Path
import sys


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--input", required=True)
    parser.add_argument("--output", required=True)
    args = parser.parse_args()
    site = os.environ.get("LRCPLUS_DEPENDENCY_SITE")
    if site and site not in sys.path:
        sys.path.append(site)
    # -I excludes the script directory; load the sibling without importing
    # the application, Qt, or its audio dependencies into this helper.
    spec = importlib.util.spec_from_file_location("english_katakana", Path(__file__).with_name("plugin.py"))
    plugin = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(plugin)
    from alkana import get_kana

    values = json.loads(Path(args.input).read_text(encoding="utf-8"))
    result = [plugin.build_segments(text, get_kana) for text in values]
    Path(args.output).write_text(json.dumps(result, ensure_ascii=False), encoding="utf-8")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
