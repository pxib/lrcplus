"""Fit lyrics to a vocal envelope in a disposable, GUI-free process."""

from dataclasses import asdict
import json
from pathlib import Path
import sys


def main():
    request, envelope, response = map(Path, sys.argv[1:])
    try:
        import numpy as np

        from lyrics.audio_timing_drift import VOCAL_FRAME_SECONDS, estimate_audio_timing

        data = json.loads(request.read_text(encoding="utf-8"))
        levels = np.load(envelope, allow_pickle=False)
        correction = estimate_audio_timing(data["intervals"], levels, offset_ms=data["offset_ms"])
        result = {
            "correction": asdict(correction) if correction is not None else None,
            "duration_ms": round(len(levels) * VOCAL_FRAME_SECONDS * 1000),
        }
    except Exception as error:
        response.write_text(json.dumps({"error": str(error)}), encoding="utf-8")
        return 1
    response.write_text(json.dumps(result), encoding="utf-8")
    return 0


if __name__ == "__main__":
    sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
    raise SystemExit(main())
