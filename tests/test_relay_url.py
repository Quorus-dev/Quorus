"""Unit tests for quorus.relay_url — the choke point that keeps a
client-controlled forwarded header out of anything executed or copy-pasted.

Regression context: the join installer interpolates the relay URL into a
``curl | sh`` command line, and signup interpolates it into a copy-pasteable
``quorus init ... --relay-url ...`` line. A hostile ``X-Forwarded-Host`` such
as ``x$(touch pwned)`` must never survive validation.
"""

from __future__ import annotations

import pytest
from fastapi import HTTPException
from starlette.requests import Request

from quorus.relay_url import public_relay_url, validate_relay_url


def _request(headers: dict[str, str] | None = None) -> Request:
    raw = [
        (k.lower().encode("latin-1"), v.encode("latin-1"))
        for k, v in (headers or {}).items()
    ]
    scope = {
        "type": "http",
        "method": "GET",
        "path": "/",
        "query_string": b"",
        "headers": raw,
        "scheme": "http",
        "server": ("test", 80),
    }
    return Request(scope)


@pytest.mark.parametrize(
    "value",
    [
        "http://relay.example.com",
        "https://relay.example.com:8080",
        "http://test",
        "https://a-b.c.example.com:443",
    ],
)
def test_validate_accepts_http_hosts(value: str):
    assert validate_relay_url(value) == value


@pytest.mark.parametrize(
    "value",
    [
        "http://x$(touch pwned)",
        "http://evil.com;rm -rf /",
        "javascript://evil.com",
        "http://evil.com/path",
        'http://evil.com"',
        "http://evil.com y",
        "relay.example.com",  # no scheme
        "ftp://relay.example.com",  # scheme not allowed
        "http://",  # empty host
    ],
)
def test_validate_rejects_injections_and_bad_shapes(value: str):
    with pytest.raises(HTTPException) as excinfo:
        validate_relay_url(value)
    assert excinfo.value.status_code == 400


def test_public_relay_url_defaults_to_request_host():
    assert public_relay_url(_request()) == "http://test"


def test_public_relay_url_uses_forwarded_host():
    req = _request({"x-forwarded-host": "relay.example.com", "x-forwarded-proto": "https"})
    assert public_relay_url(req) == "https://relay.example.com"


def test_public_relay_url_takes_leftmost_of_forwarded_list():
    req = _request({"x-forwarded-host": "relay.example.com, internal:8080"})
    assert public_relay_url(req) == "http://relay.example.com"


def test_public_relay_url_rejects_command_substitution_host():
    req = _request({"x-forwarded-host": "x$(touch pwned)"})
    with pytest.raises(HTTPException) as excinfo:
        public_relay_url(req)
    assert excinfo.value.status_code == 400


def test_public_relay_url_rejects_non_http_scheme():
    req = _request({"x-forwarded-proto": "javascript", "x-forwarded-host": "relay.example.com"})
    with pytest.raises(HTTPException):
        public_relay_url(req)


def test_public_relay_url_env_override_wins_over_headers(monkeypatch):
    monkeypatch.setenv("QUORUS_PUBLIC_RELAY", "https://public.example.com/")
    req = _request({"x-forwarded-host": "attacker.invalid"})
    assert public_relay_url(req) == "https://public.example.com"
