"""Optional Cantonese reading provider powered by PyCantonese.

PyCantonese is deliberately isolated in a helper process.  Some versions of
its tokenizer stack can terminate the Python interpreter instead of raising a
normal exception.  Keeping that native-risky work outside LyricsPlus means a
bad lyric/provider call cannot bring down the UI process.
"""
from __future__ import annotations

import importlib.util
import json
import os
import subprocess
import sys
import tempfile
from functools import lru_cache
from pathlib import Path


# PyCantonese's wordseg dependency currently emits this third-party
# deprecation warning. Keep it from drowning the application's own log.
os.environ.setdefault(
    "PYTHONWARNINGS",
    "ignore:pkg_resources is deprecated as an API:UserWarning:wordseg",
)


def _worker_path():
    return Path(__file__).with_name("worker.py")


def _isolated_convert(texts, style, timeout=120.0):
    values = [str(text or "") for text in texts]
    if not values:
        return []

    worker = _worker_path()
    if not worker.exists():
        raise RuntimeError("Cantonese reading worker is missing")

    env = os.environ.copy()
    project_root = str(worker.parents[2])
    old_pythonpath = env.get("PYTHONPATH", "")
    env["PYTHONPATH"] = (
        project_root + os.pathsep + old_pythonpath
        if old_pythonpath
        else project_root
    )

    # Do not use stdout as the IPC channel. In particular, Python/wordseg
    # startup diagnostics can contaminate stdout even when the child exits 0.
    # Temporary files also let us distinguish a real JSON response from an
    # empty/partial response caused by a child crash.
    input_path = output_path = None
    try:
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as handle:
            json.dump({"texts": values, "style": str(style)}, handle, ensure_ascii=False)
            input_path = handle.name
        with tempfile.NamedTemporaryFile(
            mode="w", encoding="utf-8", suffix=".json", delete=False
        ) as handle:
            output_path = handle.name

        try:
            completed = subprocess.run(
                [
                    sys.executable,
                    "-u",
                    str(worker),
                    "--input",
                    input_path,
                    "--output",
                    output_path,
                ],
                stdin=subprocess.DEVNULL,
                stdout=subprocess.DEVNULL,
                stderr=subprocess.PIPE,
                timeout=timeout,
                cwd=project_root,
                env=env,
                check=False,
            )
        except (OSError, subprocess.SubprocessError) as error:
            raise RuntimeError(f"Could not start Cantonese worker: {error}") from error

        if completed.returncode != 0:
            stderr = completed.stderr.decode("utf-8", errors="replace").strip()
            detail = f" (worker exited {completed.returncode})"
            if stderr:
                detail += f": {stderr[-1000:]}"
            raise RuntimeError("Cantonese worker failed" + detail)

        try:
            with open(output_path, "r", encoding="utf-8") as handle:
                result = json.load(handle)
        except (OSError, UnicodeDecodeError, json.JSONDecodeError) as error:
            raise RuntimeError("Cantonese worker returned no valid result") from error

        if not isinstance(result, list) or len(result) != len(values):
            raise RuntimeError("Cantonese worker returned an invalid result shape")
        return result
    finally:
        for path in (input_path, output_path, output_path + ".tmp" if output_path else None):
            if path:
                try:
                    os.unlink(path)
                except OSError:
                    pass


class CantoneseReadingsPlugin:
    name = "Cantonese Readings"
    version = "2.3.1"
    description = "Plugin API v2 Cantonese readings: Jyutping and Yale via PyCantonese."

    def on_load(self, context):
        # Do not import PyCantonese in the application process. Merely checking
        # the module spec is enough to decide whether the optional plugin can
        # advertise its providers; all actual imports happen in worker.py.
        if importlib.util.find_spec("pycantonese") is None:
            context.api.log("PyCantonese unavailable")
            return

        @lru_cache(maxsize=4096)
        def build_cached(text, style):
            return tuple(_isolated_convert([str(text or "")], style, timeout=60.0)[0])

        def build(text, style):
            return [
                {"text": a, "reading": b}
                for a, b in build_cached(str(text or ""), style)
            ]

        def build_many(texts, style):
            values = [str(text or "") for text in texts]
            if not values:
                return []
            try:
                converted = _isolated_convert(values, style)
                return [
                    [{"text": a, "reading": b} for a, b in line]
                    for line in converted
                ]
            except Exception as error:
                # If a whole batch fails, isolate each line. This also makes a
                # single pathological lyric unable to block the remaining ones.
                print(f"[Cantonese] Isolated batch failed: {error!r}")
                output = []
                for text in values:
                    try:
                        converted = _isolated_convert([text], style, timeout=60.0)[0]
                        output.append([
                            {"text": a, "reading": b} for a, b in converted
                        ])
                    except Exception as line_error:
                        print(f"[Cantonese] Line failed: {text!r}: {line_error!r}")
                        output.append([])
                return output

        context.api.register_reading_provider(
            "jyutping", "Jyutping", lambda text: build(text, "jyutping"),
            batch_generator=lambda texts: build_many(texts, "jyutping"),
            description="Context-aware Cantonese Jyutping generated by PyCantonese.",
        )
        context.api.register_reading_provider(
            "yale", "Yale", lambda text: build(text, "yale"),
            batch_generator=lambda texts: build_many(texts, "yale"),
            description="Cantonese Yale romanization generated from PyCantonese.",
        )
        context.api.log("Cantonese Jyutping and Yale providers registered through Plugin API v2")


PLUGIN_CLASS = CantoneseReadingsPlugin
