"""Small project-wide debug logging helper."""
from __future__ import annotations
import os
import sys

def debug_enabled() -> bool:
    return "--debug" in sys.argv or os.environ.get("PYAS_DEBUG", "").lower() in {"1", "true", "yes", "on"}

def debug_print(*args, **kwargs) -> None:
    if debug_enabled():
        print(*args, **kwargs)
