"""Tests for the Sentry observability bootstrap.

These tests must run cleanly even when sentry-sdk is NOT installed in the
local environment — production installs it via the runtime image, but we
don't want to force it into the dev .venv. The init function is designed to
return False (with a warning) when the import fails.
"""

from __future__ import annotations

import importlib
import sys
from unittest.mock import MagicMock

import pytest

from quorus.observability import sentry as sentry_mod
from quorus.observability.sentry import _scrub_event, init_sentry


@pytest.fixture(autouse=True)
def _clear_sentry_env(monkeypatch):
    """Wipe Sentry-related env vars so each test starts from a known state."""
    for var in (
        "SENTRY_DSN",
        "SENTRY_TRACES_SAMPLE_RATE",
        "FLY_RELEASE_VERSION",
        "GIT_SHA",
        "FLY_APP_NAME",
    ):
        monkeypatch.delenv(var, raising=False)


def test_sentry_no_op_without_dsn(monkeypatch):
    """No DSN -> returns False, never tries to import sentry_sdk."""
    # Sabotage the import so we'd notice if the code attempted it.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert init_sentry() is False


def test_sentry_no_op_with_blank_dsn(monkeypatch):
    """Whitespace-only DSN is treated the same as unset."""
    monkeypatch.setenv("SENTRY_DSN", "   ")
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert init_sentry() is False


def test_sentry_initializes_with_dsn(monkeypatch):
    """With a DSN set, init_sentry calls sentry_sdk.init exactly once."""
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/123")
    monkeypatch.setenv("FLY_RELEASE_VERSION", "v42")
    monkeypatch.setenv("FLY_APP_NAME", "quorus-relay")
    monkeypatch.setenv("SENTRY_TRACES_SAMPLE_RATE", "0.10")

    fake_sdk = MagicMock()
    fake_fastapi = MagicMock()
    fake_starlette = MagicMock()
    fake_asyncio = MagicMock()
    fastapi_int_mod = MagicMock(FastApiIntegration=fake_fastapi)
    starlette_int_mod = MagicMock(StarletteIntegration=fake_starlette)
    asyncio_int_mod = MagicMock(AsyncioIntegration=fake_asyncio)

    monkeypatch.setitem(sys.modules, "sentry_sdk", fake_sdk)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations", MagicMock())
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.fastapi", fastapi_int_mod)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.starlette", starlette_int_mod)
    monkeypatch.setitem(sys.modules, "sentry_sdk.integrations.asyncio", asyncio_int_mod)

    # Re-import to pick up the patched modules.
    importlib.reload(sentry_mod)
    try:
        assert sentry_mod.init_sentry() is True
        fake_sdk.init.assert_called_once()
        kwargs = fake_sdk.init.call_args.kwargs
        assert kwargs["dsn"].startswith("https://")
        assert kwargs["release"] == "v42"
        assert kwargs["environment"] == "relay"  # "quorus-" stripped
        assert kwargs["traces_sample_rate"] == pytest.approx(0.10)
        assert kwargs["profiles_sample_rate"] == 0.0
        assert kwargs["send_default_pii"] is False
        assert kwargs["before_send"] is sentry_mod._scrub_event
    finally:
        # Restore the live module so other tests aren't poisoned.
        for mod in (
            "sentry_sdk",
            "sentry_sdk.integrations",
            "sentry_sdk.integrations.fastapi",
            "sentry_sdk.integrations.starlette",
            "sentry_sdk.integrations.asyncio",
        ):
            sys.modules.pop(mod, None)
        importlib.reload(sentry_mod)


def test_sentry_no_op_when_sdk_missing(monkeypatch):
    """If sentry_sdk import fails, init_sentry returns False (doesn't raise)."""
    monkeypatch.setenv("SENTRY_DSN", "https://public@example.ingest.sentry.io/123")
    # Block the import attempt: setting to None makes ``import sentry_sdk`` raise ImportError.
    monkeypatch.setitem(sys.modules, "sentry_sdk", None)
    assert init_sentry() is False


def test_scrub_event_removes_request_data():
    """Request bodies + auth headers + cookies must be stripped."""
    event = {
        "request": {
            "data": {"text": "private patient note"},
            "cookies": "session=abc",
            "headers": {
                "Authorization": "Bearer eyJhbGciOiJIUzI1NiJ9...",
                "authorization": "Bearer dup",
                "Cookie": "session=xyz",
                "cookie": "session=xyz",
                "user-agent": "curl/8.0",
            },
        },
    }
    out = _scrub_event(event, hint={})
    assert out is event  # mutates in place, returns same object
    assert "data" not in out["request"]
    assert "cookies" not in out["request"]
    headers = out["request"]["headers"]
    assert "Authorization" not in headers
    assert "authorization" not in headers
    assert "Cookie" not in headers
    assert "cookie" not in headers
    # Non-sensitive header preserved.
    assert headers["user-agent"] == "curl/8.0"


def test_scrub_event_redacts_participant_breadcrumbs():
    """Breadcrumbs that mention ``from_name`` or ``@user`` get scrubbed."""
    event = {
        "breadcrumbs": {
            "values": [
                {"message": "received from from_name=arav-claude"},
                {"message": "mention of @arav in room"},
                {"message": "non-sensitive: room joined"},
            ],
        },
    }
    out = _scrub_event(event, hint={})
    msgs = [c["message"] for c in out["breadcrumbs"]["values"]]
    assert msgs[0] == "[scrubbed-identity]"
    assert msgs[1] == "[scrubbed-identity]"
    assert msgs[2] == "non-sensitive: room joined"


def test_scrub_event_handles_minimal_payload():
    """Events without request/breadcrumbs sections must still pass through."""
    event = {"message": "boom", "level": "error"}
    out = _scrub_event(event, hint={})
    assert out == {"message": "boom", "level": "error"}
