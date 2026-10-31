"""LRCLIB searches with bounded retries and server-directed backoff."""
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
import math
from threading import Event

import requests

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL


class LRCLibError(RuntimeError):
    def __init__(self, message, retry_after=0):
        super().__init__(message)
        self.retry_after = retry_after


def _retry_after(value):
    try:
        seconds = float(value)
        return max(0.0, seconds) if math.isfinite(seconds) else None
    except (TypeError, ValueError):
        try:
            date = parsedate_to_datetime(value)
            if date.tzinfo is None:
                date = date.replace(tzinfo=timezone.utc)
            return max(0.0, (date - datetime.now(timezone.utc)).total_seconds())
        except (TypeError, ValueError, OverflowError):
            return None


def search(query, cancel=None):
    cancel = cancel if cancel is not None else Event()
    for attempt in range(3):
        if cancel.is_set():
            return []
        try:
            response = requests.get(
                "https://lrclib.net/api/search", params={"q": query},
                headers={
                    "User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})",
                    "Lrclib-App-Id": "zTywhWzql6kFvrFMcWui-xuV0qYCVO3FzKQKPwZGf7U",
                }, timeout=15,
            )
        except (requests.Timeout, requests.ConnectionError) as error:
            if attempt == 2:
                raise LRCLibError("Could not reach LRCLIB. Check your connection and try again later.") from error
            if cancel.wait(2 ** attempt):
                return []
            continue
        try:
            if response.status_code in {429, 500, 502, 503, 504}:
                delay = _retry_after(response.headers.get("Retry-After"))
                delay = max(2 ** attempt, delay if delay is not None else 0)
                message = ("LRCLIB is rate limiting searches (HTTP 429)."
                           if response.status_code == 429 else
                           f"LRCLIB is temporarily busy or unavailable (HTTP {response.status_code}).")
                # Do not occupy a worker indefinitely or retry before a long
                # server-directed wait. The dialog enforces the remaining delay.
                if attempt == 2 or delay > 10:
                    raise LRCLibError(f"{message} Please try again in {int(delay + 0.999)} seconds.", delay)
            else:
                response.raise_for_status()
                try:
                    payload = response.json()
                except ValueError as error:
                    raise LRCLibError("LRCLIB returned an invalid response. Please try again later.") from error
                if not isinstance(payload, list) or any(not isinstance(item, dict) for item in payload):
                    raise LRCLibError("LRCLIB returned unexpected search results. Please try again later.")
                return payload
        finally:
            response.close()
        if cancel.wait(delay):
            return []
    return []
