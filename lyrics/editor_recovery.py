"""Locate recovery copies for drafts that have never been saved."""

import hashlib
import json
import os
from pathlib import Path
from uuid import uuid4

from core.settings import get_app_data_dir


def _draft_key(target):
    absolute = os.path.normcase(os.path.abspath(target))
    return hashlib.sha256(os.fsencode(absolute)).hexdigest()


def new_draft_recovery_path(target):
    return get_app_data_dir() / "lyric-drafts" / f"{_draft_key(target)}-{uuid4().hex}.json"


def recoverable_drafts(target=None):
    """Return (target, recovery path) pairs, newest first."""
    pattern = "*.json" if target is None else f"{_draft_key(target)}-*.json"
    drafts = []
    for path in (get_app_data_dir() / "lyric-drafts").glob(pattern):
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            draft_target = Path(payload["draft_target"])
            if (draft_target.is_absolute() and path.name.startswith(f"{_draft_key(draft_target)}-")
                    and payload.get("format") == "LRCPlusRecovery"
                    and payload.get("version") == 1
                    and isinstance(payload.get("structured"), str)):
                drafts.append((path.stat().st_mtime_ns, path.name, draft_target, path))
        except (OSError, ValueError, TypeError, KeyError):
            continue
    return [(target, path) for _, _, target, path in sorted(drafts, reverse=True)]
