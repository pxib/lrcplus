"""Read Mandarin pronunciations without retaining dictionaries in the player."""

import json
from pathlib import Path
import subprocess
import sys
import tempfile
import threading


_WORKER_LOCK = threading.Lock()


def read_pinyin_runs(runs, timeout=30):
    """Convert complete Han runs in one short-lived, serialized worker."""
    if not runs:
        return []
    # Several lyric previews can request readings concurrently. Only one
    # dictionary process should occupy memory at a time.
    with _WORKER_LOCK, tempfile.TemporaryDirectory(prefix="lrcplus-pinyin-") as folder:
        request = Path(folder) / "input.json"
        response = Path(folder) / "output.json"
        request.write_text(json.dumps({"runs": runs}, ensure_ascii=False), encoding="utf-8")
        if getattr(sys, "frozen", False):
            command = [sys.executable, "--lrcplus-pinyin-worker"]
        else:
            worker = Path(__file__).resolve().parents[1] / "core" / "pinyin_worker.py"
            command = [sys.executable, "-I", "-u", str(worker)]
        completed = subprocess.run(
            command + ["--input", str(request), "--output", str(response)],
            stdin=subprocess.DEVNULL, stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
            timeout=timeout, check=False,
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0),
        )
        result = json.loads(response.read_text(encoding="utf-8")) if response.is_file() else None
        if completed.returncode != 0:
            detail = result.get("error", "") if isinstance(result, dict) else ""
            raise RuntimeError(f"Pinyin worker exited {completed.returncode}: {detail}")
        if not isinstance(result, list) or len(result) != len(runs):
            raise ValueError("Pinyin worker returned an invalid result shape")
        for text, readings in zip(runs, result):
            if (not isinstance(readings, list) or len(readings) != len(text)
                    or any(value is not None and not isinstance(value, str) for value in readings)):
                raise ValueError("Pinyin worker returned invalid character readings")
        return result
