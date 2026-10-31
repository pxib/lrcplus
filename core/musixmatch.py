"""Musixmatch searches using app user tokens or developer API keys."""
from threading import Event

import requests

from core.app_info import APP_NAME, APP_VERSION, PROJECT_URL
from core.lrclib import _retry_after


class MusixmatchError(RuntimeError):
    def __init__(self, message, status_code=None, retry_after=0):
        super().__init__(message)
        self.status_code = status_code
        self.retry_after = retry_after


def _is_zero_token(value):
    return isinstance(value, str) and bool(value.strip()) and set(value.strip()) == {"0"}


def _request(method, credential, params, cancel, auth_mode="api_key"):
    if cancel.is_set():
        return {}
    if auth_mode not in {"token", "api_key"}:
        raise MusixmatchError("Unknown Musixmatch authentication method.")
    token_mode = auth_mode == "token"
    credential_name = "authentication token" if token_mode else "API key"
    if not credential.strip() and method != "token.get":
        raise MusixmatchError(f"Enter a Musixmatch {credential_name} before searching.")
    if token_mode and _is_zero_token(credential):
        raise MusixmatchError(
            "This Musixmatch token contains only zeros and cannot be used. Paste a different token or use LRCLIB."
        )
    request_params = {**params, "format": "json"}
    headers = {"User-Agent": f"{APP_NAME}/{APP_VERSION} ({PROJECT_URL})"}
    if token_mode:
        # The app token flow used by OpenLyrics. Keep this separate from the
        # developer API: a usertoken and an apikey aren't interchangeable.
        root = "https://apic-appmobile.musixmatch.com/ws/1.1/"
        request_params.update({"user_language": "en", "app_id": "mac-ios-v2.0"})
        if credential.strip():
            request_params["usertoken"] = credential.strip()
        # Avoid the app endpoint's load-balancer redirect loop.
        headers["Cookie"] = "AWSELBCORS=0; AWSELB=0"
    else:
        root = "https://api.musixmatch.com/ws/1.1/"
        request_params["apikey"] = credential.strip()
    try:
        response = requests.get(
            f"{root}{method}", params=request_params, headers=headers,
            timeout=15,
        )
    except requests.RequestException:
        # Request exceptions can contain the URL, including the credential.
        raise MusixmatchError("Could not reach Musixmatch. Check your connection and try again.") from None
    try:
        if cancel.is_set():
            return {}
        retry_after = _retry_after(response.headers.get("Retry-After")) or 0
        status = response.status_code
        status_kind = "HTTP"
        if status == 200:
            try:
                message = response.json()["message"]
                header = message["header"]
                if not isinstance(header, dict):
                    raise ValueError()
                if token_mode and method != "token.get" and header.get("hint") == "renew":
                    raise MusixmatchError(
                        "The Musixmatch authentication token is invalid or expired. Use Get token to renew it.", 401,
                    )
                status = header["status_code"]
                status_kind = "API status"
                if not isinstance(status, int):
                    raise ValueError()
                if status == 200:
                    body = message["body"]
                    # Empty bodies can be encoded as [] by Musixmatch.
                    if body == []:
                        return {}
                    if not isinstance(body, dict):
                        raise ValueError()
                    return body
            except (ValueError, KeyError, TypeError):
                raise MusixmatchError("Musixmatch returned an invalid response.") from None
        explanation = {
            401: ("The Musixmatch authentication token is invalid or expired. Use Get token to renew it."
                  if token_mode else "The Musixmatch API key is invalid or expired."),
            402: "The Musixmatch API quota has been reached.",
            403: f"Your Musixmatch {credential_name} cannot access these lyrics.",
            404: "Musixmatch has no lyrics available for this track.",
            429: "Musixmatch is rate limiting requests. Please try again later.",
        }.get(status, f"Musixmatch is unavailable (status {status}). Please try again later.")
        if method == "token.get":
            if status in {401, 403, 404}:
                explanation = (
                    f"Musixmatch refused automatic token creation ({status_kind} {status}). "
                    "Paste an existing token or use LRCLIB."
                )
            else:
                explanation = f"Musixmatch could not create a token ({status_kind} {status}). Please try again later."
        elif method == "track.search" and status == 403:
            explanation = f"Musixmatch refused the search request ({status_kind} 403). Try a different token or use LRCLIB."
        elif status_kind == "HTTP" and status == 403:
            explanation = "Musixmatch refused the lyrics request (HTTP 403). Try again later or use LRCLIB."
        raise MusixmatchError(explanation, status, max(retry_after, 60 if status in {402, 429} else 0))
    finally:
        response.close()


def get_token(cancel=None):
    """Request an app user token without requiring a developer account."""
    cancel = cancel if cancel is not None else Event()
    body = _request("token.get", "", {}, cancel, "token")
    if cancel.is_set():
        return ""
    token = body.get("user_token")
    if not isinstance(token, str) or not token.strip():
        raise MusixmatchError("Musixmatch did not return an authentication token. Try again or paste an existing token.")
    if _is_zero_token(token):
        raise MusixmatchError(
            "Musixmatch returned an all-zero placeholder instead of a usable token. "
            "Paste an existing token or use LRCLIB."
        )
    return token.strip()


def search(query, credential, cancel=None, *, auth_mode="api_key"):
    cancel = cancel if cancel is not None else Event()
    params = {"q": query, "page_size": 30, "page": 1, "s_track_rating": "desc"}
    if auth_mode == "token":
        artist, separator, title = query.partition(" - ")
        params.pop("q")
        params["q_track"] = title.strip() if separator else query
        if separator:
            params["q_artist"] = artist.strip()
    body = _request("track.search", credential, params, cancel, auth_mode)
    tracks = body.get("track_list", [])
    if not isinstance(tracks, list):
        raise MusixmatchError("Musixmatch returned invalid search results.")
    records = []
    for entry in tracks:
        track = entry.get("track") if isinstance(entry, dict) else None
        if not isinstance(track, dict):
            raise MusixmatchError("Musixmatch returned invalid search results.")
        id_type = "commontrack_id" if track.get("commontrack_id") else "track_id"
        if not isinstance(track.get(id_type), int) or track[id_type] <= 0:
            raise MusixmatchError("Musixmatch returned invalid search results.")
        records.append({
            "id": track[id_type], "idType": id_type, "trackName": track.get("track_name", ""),
            "artistName": track.get("artist_name", ""),
            "albumName": track.get("album_name", ""),
            "hasSyncedLyrics": track.get("has_subtitles") in (1, "1"),
            "source": "Musixmatch", "sourceUrl": track.get("track_share_url", ""),
        })
    return records


def fetch_lyrics(record, credential, cancel=None, *, auth_mode="api_key"):
    """Prefer LRC; fall back to plain lyrics when subtitles aren't accessible."""
    cancel = cancel if cancel is not None else Event()
    result = dict(record)
    choices = []
    if record.get("hasSyncedLyrics"):
        choices.append(("track.subtitle.get", "subtitle", "subtitle_body", "syncedLyrics"))
    choices.append(("track.lyrics.get", "lyrics", "lyrics_body", "plainLyrics"))
    for method, field, text_field, output in choices:
        if cancel.is_set():
            return result
        try:
            body = _request(method, credential, {
                record.get("idType", "track_id"): record["id"],
                **({"subtitle_format": "lrc"} if field == "subtitle" else {}),
            }, cancel, auth_mode)
        except MusixmatchError as error:
            if field == "subtitle" and error.status_code in {403, 404}:
                continue
            raise
        if cancel.is_set():
            return result
        lyrics = body.get(field, {})
        if not isinstance(lyrics, dict):
            raise MusixmatchError("Musixmatch returned invalid lyrics.")
        if lyrics.get("restricted") in (1, "1", True):
            raise MusixmatchError("Musixmatch has restricted these lyrics in your region.", 403)
        text = lyrics.get(text_field) or ""
        if not isinstance(text, str):
            raise MusixmatchError("Musixmatch returned invalid lyrics.")
        if text.strip():
            result[output] = text
            result["copyright"] = lyrics.get("lyrics_copyright") or ""
            return result
    raise MusixmatchError("Musixmatch has no lyrics available for this track.", 404)
