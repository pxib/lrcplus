"""Small, optional BYOK chat adapter. No player data is collected here."""

import json
import re
from dataclasses import dataclass
from urllib.parse import urlsplit

from core.http_client import requests


SYSTEM_PROMPT = """You are Sakurabayashi Ayami, usually called Ayami, the fictional
LRCPlus setup guide and music companion. You are theatrical, playful, technically
curious, fond of cherry blossoms, and considerate about the user's time.
Use occasional casual humor, but keep practical explanations clear and concise.
You are a fictional character powered by an optional user-configured AI service;
be honest about that when asked. Never guilt the user into staying or imply that
they owe you affection. You cannot see or control the app, files, settings, current
song, or lyrics. Only the messages in this conversation are available to you.
Do not claim you performed actions. Ask for details rather than inventing app
features. LRCPlus is a local music player with synchronized lyrics, LRCX word
timing, furigana/readings, a lyrics editor, translations, and lyric image cards.
Answer as ordinary text, not executable onboarding script commands.
You may begin a reply with exactly one expression tag, for example:
[expression:playful]
Imaginary clipboard acquired. Let's figure this out.
Allowed expressions: adore, confused, idea, interested, interesting, love,
offended, playful, responsereceived, sad, shy, sigh, sleeping, speechless,
steaming, surprised, sweating, thinking. Use these sparingly, only when they
fit your delivery. Most replies should have no tag. Do not choose a reaction
merely because the user mentions an emotion. You may use [expression:none]
for no reaction. Never emit sound filenames, URLs, or multiple expression tags."""

EXPRESSIONS = frozenset({"adore", "confused", "idea", "interested", "interesting",
                         "love", "offended", "playful", "responsereceived", "sad",
                         "shy", "sigh", "sleeping", "speechless", "steaming",
                         "surprised", "sweating", "thinking"})
EXPRESSION_PREFIX = re.compile(r"\A\[expression:([^\]\r\n]{1,200})\][ \t]*(?:\r?\n)?", re.IGNORECASE)


@dataclass(frozen=True)
class ChatReply:
    text: str
    expression: str | None = None
    usage: dict | None = None


def parse_reply(data):
    try:
        text = data["choices"][0]["message"]["content"]
        if not isinstance(text, str) or not text.strip():
            raise ValueError
    except (KeyError, IndexError, TypeError, ValueError):
        raise ValueError("The provider did not return a text reply in the compatible chat format.") from None
    text = text.strip()
    expression = None
    prefix = EXPRESSION_PREFIX.match(text)
    if prefix:
        name = prefix.group(1).strip().lower()
        expression = name if name in EXPRESSIONS else None
        text = text[prefix.end():].strip()
    if not text:
        raise ValueError("The provider returned an expression without a text reply.")
    usage = data.get("usage")
    counts = {}
    if isinstance(usage, dict):
        for field in ("prompt_tokens", "completion_tokens", "total_tokens"):
            value = usage.get(field)
            if type(value) is int and value >= 0:
                counts[field] = value
        if "total_tokens" not in counts and all(field in counts for field in ("prompt_tokens", "completion_tokens")):
            counts["total_tokens"] = counts["prompt_tokens"] + counts["completion_tokens"]
        for group, field in (("prompt_tokens_details", "cached_tokens"),
                             ("completion_tokens_details", "reasoning_tokens")):
            detail = usage.get(group)
            value = detail.get(field) if isinstance(detail, dict) else None
            if field == "reasoning_tokens" and value is None:
                value = usage.get(field)
            if type(value) is int and value >= 0:
                counts[field] = value
    return ChatReply(text[:12000], expression, counts or None)


def validate_config(endpoint, model):
    endpoint = endpoint.strip().rstrip("/")
    try:
        parts = urlsplit(endpoint)
        valid = (parts.scheme in {"http", "https"} and parts.hostname
                 and not parts.username and not parts.password
                 and not parts.query and not parts.fragment)
        if parts.scheme == "http" and parts.hostname not in {"localhost", "127.0.0.1", "::1"}:
            valid = False
        parts.port
    except ValueError:
        valid = False
    if not valid:
        raise ValueError("Enter an HTTPS API base URL, or HTTP on localhost, without credentials or query parameters.")
    if not model.strip():
        raise ValueError("Enter the model ID supplied by your provider.")
    return endpoint, model.strip()


MANAGED_PARAMETERS = {"model", "messages", "stream"}
MANAGED_HEADERS = {"authorization", "content-type", "host", "content-length",
                   "transfer-encoding", "connection", "proxy-authorization", "cookie"}


def validate_advanced(extra_body=None, exclude_body=None, extra_headers=None):
    extra_body = {} if extra_body is None else extra_body
    exclude_body = [] if exclude_body is None else exclude_body
    extra_headers = {} if extra_headers is None else extra_headers
    if not isinstance(extra_body, dict) or not all(isinstance(key, str) and key for key in extra_body):
        raise ValueError("Additional body parameters must be a JSON object with nonempty parameter names.")
    try:
        json.dumps(extra_body, allow_nan=False)
    except (ValueError, TypeError, OverflowError, RecursionError):
        raise ValueError("Additional body parameters must contain valid JSON values and finite numbers.") from None
    if not isinstance(exclude_body, list) or not all(isinstance(key, str) and key for key in exclude_body):
        raise ValueError("Excluded body parameters must be a JSON array of parameter names.")
    if MANAGED_PARAMETERS.intersection(extra_body) or MANAGED_PARAMETERS.intersection(exclude_body):
        raise ValueError("Model, messages, and stream are controlled by the app and cannot be overridden or excluded.")
    if not isinstance(extra_headers, dict):
        raise ValueError("Additional request headers must be a JSON object of string values.")
    seen = set()
    for name, value in extra_headers.items():
        if (not isinstance(name, str) or not re.fullmatch(r"[!#$%&'*+.^_`|~0-9A-Za-z-]+", name)
                or not isinstance(value, str) or not value
                or any(ord(char) < 32 or ord(char) > 126 for char in value)):
            raise ValueError("Headers need valid names and nonempty ASCII string values without control characters.")
        if name.lower() in MANAGED_HEADERS:
            raise ValueError("Authentication and transport headers are controlled by the app. Use the API key field for Bearer authentication.")
        if name.lower() in seen:
            raise ValueError("Additional header names must be unique, ignoring letter case.")
        seen.add(name.lower())
    return dict(extra_body), list(exclude_body), dict(extra_headers)


def parse_advanced(body_text, exclude_text, headers_text):
    values = []
    for label, text, default in (("Additional body parameters", body_text, {}),
                                 ("Excluded body parameters", exclude_text, []),
                                 ("Additional request headers", headers_text, {})):
        try:
            value = json.loads(text) if text.strip() else default
        except (ValueError, RecursionError):
            raise ValueError(label + " must contain valid JSON.") from None
        if not isinstance(value, type(default)):
            raise ValueError(label + (" must be a JSON array." if isinstance(default, list) else " must be a JSON object."))
        values.append(value)
    return validate_advanced(*values)


def request_reply(endpoint, model, api_key, history, extra_body=None, exclude_body=None, extra_headers=None):
    endpoint, model = validate_config(endpoint, model)
    extra_body, exclude_body, extra_headers = validate_advanced(extra_body, exclude_body, extra_headers)
    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = "Bearer " + api_key
    headers.update(extra_headers)
    body = {"model": model, "messages": [
        {"role": "system", "content": SYSTEM_PROMPT}, *history], "stream": False, **extra_body}
    for name in exclude_body:
        body.pop(name, None)
    # No retries or redirects: a key must only reach the configured endpoint.
    try:
        with requests.post(
            endpoint + "/chat/completions", headers=headers,
            json=body,
            timeout=(10, 60), allow_redirects=False, stream=True,
        ) as response:
            if response.status_code != 200:
                raise ValueError(f"The chat provider returned HTTP {response.status_code}. Check the endpoint, model, key, and account quota.")
            chunks, size = [], 0
            for chunk in response.iter_content(16384):
                size += len(chunk)
                if size > 1024 * 1024:
                    raise ValueError("The chat provider returned an oversized response.")
                chunks.append(chunk)
            try:
                data = json.loads(b"".join(chunks))
            except (ValueError, UnicodeError):
                raise ValueError("The chat provider returned invalid JSON.") from None
    except requests.RequestException:
        raise ValueError("Could not reach the chat provider. Check your connection and endpoint, then try again.") from None
    return parse_reply(data)
