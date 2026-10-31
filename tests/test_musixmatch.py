from threading import Event
from types import SimpleNamespace

import pytest
import requests

from core import musixmatch


def install_replies(monkeypatch, *replies):
    calls, closed = [], []
    responses = iter(replies)

    def get(url, **kwargs):
        calls.append((url, kwargs))
        status, body = next(responses)
        return SimpleNamespace(
            status_code=200, headers={}, close=lambda: closed.append(True),
            json=lambda: {"message": {"header": {"status_code": status}, "body": body}},
        )

    monkeypatch.setattr(musixmatch.requests, "get", get)
    return calls, closed


def test_search_normalizes_metadata_without_fetching_every_lyric(monkeypatch):
    calls, closed = install_replies(monkeypatch, (200, {"track_list": [{"track": {
        "track_id": 42, "track_name": "Song", "artist_name": "Artist",
        "album_name": "Album", "has_subtitles": 1,
    }}]}))
    records = musixmatch.search("Artist - Song", " secret ")
    assert records[0]["id"] == 42
    assert records[0]["trackName"] == "Song"
    assert records[0]["hasSyncedLyrics"] is True
    assert len(calls) == len(closed) == 1
    assert calls[0][1]["params"]["apikey"] == "secret"
    assert calls[0][1]["params"]["q"] == "Artist - Song"
    assert calls[0][0].startswith("https://api.musixmatch.com/")


def test_fetch_prefers_lrc_and_preserves_copyright(monkeypatch):
    calls, closed = install_replies(monkeypatch, (200, {"subtitle": {
        "subtitle_body": "[00:01.00]Hello", "lyrics_copyright": "Copyright owner",
    }}))
    result = musixmatch.fetch_lyrics({"id": 42, "hasSyncedLyrics": True}, "key")
    assert result["syncedLyrics"] == "[00:01.00]Hello"
    assert result["copyright"] == "Copyright owner"
    assert calls[0][1]["params"]["subtitle_format"] == "lrc"
    assert len(closed) == 1


@pytest.mark.parametrize("subtitle_reply", [(403, {}), (404, {}), (200, {})])
def test_falls_back_to_plain_when_subtitles_unavailable(monkeypatch, subtitle_reply):
    calls, closed = install_replies(monkeypatch, subtitle_reply, (200, {"lyrics": {
        "lyrics_body": "Hello\n\n******* Preview notice", "lyrics_copyright": "Owner",
    }}))
    result = musixmatch.fetch_lyrics({"id": 42, "hasSyncedLyrics": True}, "key")
    assert result["plainLyrics"] == "Hello\n\n******* Preview notice"
    assert "syncedLyrics" not in result
    assert len(calls) == len(closed) == 2


@pytest.mark.parametrize("status, message", [(401, "invalid or expired"), (402, "quota"), (429, "rate limiting")])
def test_api_errors_inside_http_200(monkeypatch, status, message):
    calls, closed = install_replies(monkeypatch, (status, []))
    with pytest.raises(musixmatch.MusixmatchError, match=message) as error:
        musixmatch.search("Song", "key")
    assert error.value.status_code == status
    assert len(closed) == 1


def test_restricted_lyrics_are_never_returned(monkeypatch):
    calls, closed = install_replies(monkeypatch, (200, {"subtitle": {
        "restricted": 1, "subtitle_body": "[00:01]Restricted",
    }}))
    with pytest.raises(musixmatch.MusixmatchError, match="restricted"):
        musixmatch.fetch_lyrics({"id": 42, "hasSyncedLyrics": True}, "key")
    assert len(calls) == len(closed) == 1


def test_network_errors_do_not_expose_key(monkeypatch):
    def fail(*args, **kwargs):
        raise requests.ConnectionError("https://api.musixmatch.com/?apikey=private-key")
    monkeypatch.setattr(musixmatch.requests, "get", fail)
    with pytest.raises(musixmatch.MusixmatchError) as error:
        musixmatch.search("Song", "private-key")
    assert "private-key" not in str(error.value)


def test_cancelled_request_and_missing_key_do_not_connect(monkeypatch):
    calls, closed = install_replies(monkeypatch)
    cancel = Event()
    cancel.set()
    assert musixmatch.search("Song", "key", cancel) == []
    with pytest.raises(musixmatch.MusixmatchError, match="API key"):
        musixmatch.search("Song", "")
    assert not calls


@pytest.mark.parametrize("body", [{"track_list": {}}, {"track_list": [None]}, {"track_list": [{"track": {}}]}])
def test_malformed_results(monkeypatch, body):
    calls, closed = install_replies(monkeypatch, (200, body))
    with pytest.raises(musixmatch.MusixmatchError, match="invalid search results"):
        musixmatch.search("Song", "key")
    assert len(closed) == 1


def test_get_token_uses_app_endpoint_without_developer_key(monkeypatch):
    calls, closed = install_replies(monkeypatch, (200, {"user_token": " user-token "}))
    assert musixmatch.get_token() == "user-token"
    url, options = calls[0]
    assert url == "https://apic-appmobile.musixmatch.com/ws/1.1/token.get"
    assert options["params"]["app_id"] == "mac-ios-v2.0"
    assert "apikey" not in options["params"]
    assert "usertoken" not in options["params"]
    assert "AWSELB=0" in options["headers"]["Cookie"]
    assert len(closed) == 1


def test_token_search_uses_commontrack_id_for_lookup(monkeypatch):
    calls, closed = install_replies(monkeypatch,
        (200, {"track_list": [{"track": {
            "commontrack_id": 99, "track_id": 42, "track_name": "Song", "has_subtitles": 1,
        }}]}),
        (200, {"subtitle": {"subtitle_body": "[00:01.00]Hello"}}),
    )
    records = musixmatch.search("Artist - Song", "token", auth_mode="token")
    result = musixmatch.fetch_lyrics(records[0], "token", auth_mode="token")
    assert result["syncedLyrics"] == "[00:01.00]Hello"
    search_params, lyric_params = [options["params"] for _, options in calls]
    assert search_params["q_artist"] == "Artist"
    assert search_params["q_track"] == "Song"
    assert lyric_params["commontrack_id"] == 99
    assert "track_id" not in lyric_params
    assert all(options["params"]["usertoken"] == "token" for _, options in calls)
    assert len(closed) == 2


def test_token_renew_hint_does_not_masquerade_as_no_results(monkeypatch):
    monkeypatch.setattr(musixmatch.requests, "get", lambda *args, **kwargs: SimpleNamespace(
        status_code=200, headers={}, close=lambda: None,
        json=lambda: {"message": {"header": {"status_code": 200, "hint": "renew"}}},
    ))
    with pytest.raises(musixmatch.MusixmatchError, match="Get token") as error:
        musixmatch.search("Song", "expired-token", auth_mode="token")
    assert error.value.status_code == 401
    assert "expired-token" not in str(error.value)


@pytest.mark.parametrize("body", [{}, {"user_token": ""}, {"user_token": 123}])
def test_get_token_rejects_missing_or_invalid_token(monkeypatch, body):
    install_replies(monkeypatch, (200, body))
    with pytest.raises(musixmatch.MusixmatchError, match="did not return"):
        musixmatch.get_token()


def test_cancel_during_token_request_does_not_return_credentials(monkeypatch):
    cancel = Event()
    def get(*args, **kwargs):
        cancel.set()
        return SimpleNamespace(status_code=200, headers={}, close=lambda: None)
    monkeypatch.setattr(musixmatch.requests, "get", get)
    assert musixmatch.get_token(cancel) == ""


@pytest.mark.parametrize("token", ["0", "0000000000000", " 00000000000000000000000000000000 "])
def test_all_zero_token_response_is_not_reported_as_success(monkeypatch, token):
    calls, closed = install_replies(monkeypatch, (200, {"user_token": token}))
    with pytest.raises(musixmatch.MusixmatchError, match="all-zero placeholder"):
        musixmatch.get_token()
    assert len(closed) == 1


def test_pasted_zero_token_is_rejected_without_network_request(monkeypatch):
    calls, closed = install_replies(monkeypatch)
    with pytest.raises(musixmatch.MusixmatchError, match="only zeros"):
        musixmatch.search("Song", "0000000000000", auth_mode="token")
    assert not calls


@pytest.mark.parametrize("http_status, api_status, expected", [
    (403, None, "HTTP 403"), (200, 403, "API status 403"),
    (401, None, "HTTP 401"), (200, 401, "API status 401"),
])
def test_token_request_refusal_does_not_blame_lyrics_access(monkeypatch, http_status, api_status, expected):
    closed = []
    def json_body():
        assert http_status == 200  # A blocked HTTP request may return an HTML page.
        return {"message": {"header": {"status_code": api_status}}}
    monkeypatch.setattr(musixmatch.requests, "get", lambda *args, **kwargs: SimpleNamespace(
        status_code=http_status, headers={}, close=lambda: closed.append(True), json=json_body,
    ))
    with pytest.raises(musixmatch.MusixmatchError, match="refused automatic token creation") as error:
        musixmatch.get_token()
    assert expected in str(error.value)
    assert "lyrics" not in str(error.value).casefold()
    assert "invalid or expired" not in str(error.value)
    assert closed == [True]
