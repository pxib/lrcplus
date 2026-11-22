"""Validation shared by direct stream playback and station directories."""

from PySide6.QtCore import QUrl


def parse_stream_url(text):
    url = QUrl(text.strip(), QUrl.ParsingMode.StrictMode)
    if (not url.isValid() or url.scheme() not in ("http", "https")
            or not url.host() or url.userInfo()):
        raise ValueError("Enter a direct HTTP or HTTPS audio URL without embedded credentials.")
    return url
