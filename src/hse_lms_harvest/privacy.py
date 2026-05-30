from __future__ import annotations

from urllib.parse import parse_qsl, urlencode, urlparse, urlunparse

SENSITIVE_QUERY_KEYS = {
    "access_token",
    "auth",
    "code",
    "key",
    "nonce",
    "password",
    "sesskey",
    "session",
    "sid",
    "signature",
    "state",
    "token",
}


def has_sensitive_query(url: str) -> bool:
    parsed = urlparse(url)
    return any(key.lower() in SENSITIVE_QUERY_KEYS for key, _ in parse_qsl(parsed.query))


def redact_url(url: str) -> str:
    parsed = urlparse(url)
    if not parsed.query:
        return url

    query = [
        (key, "[REDACTED]" if key.lower() in SENSITIVE_QUERY_KEYS else value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
    ]
    return urlunparse(parsed._replace(query=urlencode(query)))


def strip_fragment(url: str) -> str:
    parsed = urlparse(url)
    query = [
        (key, value)
        for key, value in parse_qsl(parsed.query, keep_blank_values=True)
        if key.lower() != "forceview"
    ]
    return urlunparse(parsed._replace(fragment="", query=urlencode(query)))
