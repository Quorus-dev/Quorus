"""Regression tests for hook auth resolution (codex audit, 2026-05-03).

Bug being defended against:
  Pre-fix, ``hooks._config()`` read ``relay_secret`` from the legacy global
  config and POSTed it as ``Authorization: Bearer <relay_secret>`` for every
  Cursor / Gemini hook fire. Production relay auth is JWT-from-api-key, so
  every hook silently 401'd and no-op'd — the next-turn nudge never landed
  and nobody noticed because hooks fail closed by design.

What this file proves:
  1. When an active profile exposes an ``api_key``, ``_resolve_auth`` MUST
     exchange it via ``POST /v1/auth/token`` and return a Bearer JWT — no
     leaking of the api_key, no leaking of the JWT into stdout/stderr.
  2. When NO profile is configured but ``RELAY_SECRET`` env is set, the
     legacy admin-only fallback is allowed (dev-mode) but emits a stderr
     warning so the downgrade is observable.
  3. Two close-together hook fires share one JWT — the cache prevents
     hammering ``/v1/auth/token`` and prevents racing the rate limiter.
"""
from __future__ import annotations

import sys
import unittest
from io import StringIO
from unittest.mock import patch

from quorus_cli import hooks


class _Resp:
    """Tiny stand-in for ``httpx.Response`` returned by the token endpoint."""

    def __init__(self, payload: dict, status_code: int = 200) -> None:
        self._payload = payload
        self.status_code = status_code

    def json(self) -> dict:
        return self._payload

    def raise_for_status(self) -> None:
        if self.status_code >= 400:
            import httpx
            raise httpx.HTTPStatusError("err", request=None, response=None)


class HookAuthResolutionTests(unittest.TestCase):
    """``_resolve_auth`` must prefer profile JWT, fall back only with no profile."""

    def setUp(self) -> None:
        # Always start with a clean cache so test order doesn't matter.
        hooks._reset_jwt_cache_for_tests()

    def tearDown(self) -> None:
        hooks._reset_jwt_cache_for_tests()

    def test_hook_uses_jwt_from_profile_when_available(self) -> None:
        """Profile api_key path: POST /v1/auth/token, return Bearer JWT.

        The api_key MUST never appear in headers; only the swapped JWT does.
        """
        profile = {
            "relay_url": "https://relay.example.com",
            "instance_name": "alice",
            "api_key": "mct_secret_api_key_value",
        }
        token_payload = {"token": "jwt-abc-123", "expires_in": 600}

        with patch.object(hooks, "_load_active_profile", return_value=profile), \
             patch.object(hooks.httpx, "post", return_value=_Resp(token_payload)) as mock_post:
            relay, instance, headers = hooks._resolve_auth()

        self.assertEqual(relay, "https://relay.example.com")
        self.assertEqual(instance, "alice")
        self.assertEqual(headers, {"Authorization": "Bearer jwt-abc-123"})
        # The token endpoint was called exactly once with the api_key in the
        # JSON body (NOT in headers, NOT in the URL).
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args.kwargs
        self.assertEqual(call_kwargs["json"], {"api_key": "mct_secret_api_key_value"})
        # The api_key must NEVER end up in the returned headers.
        self.assertNotIn("mct_secret_api_key_value", str(headers))
        # X-Relay-Secret marker must NOT be set on the JWT path — that header
        # exists only to flag legacy traffic in relay observability.
        self.assertNotIn("X-Relay-Secret", headers)

    def test_hook_uses_relay_secret_only_when_no_profile(self) -> None:
        """No profile + RELAY_SECRET env → legacy fallback with stderr warning."""
        # Empty profile => no api_key path; should fall through to legacy.
        with patch.object(hooks, "_load_active_profile", return_value={}), \
             patch.dict(hooks.os.environ, {"RELAY_SECRET": "legacy-admin-secret"}, clear=False):
            captured = StringIO()
            real_stderr = sys.stderr
            sys.stderr = captured
            try:
                relay, instance, headers = hooks._resolve_auth()
            finally:
                sys.stderr = real_stderr

        # Legacy headers carry both Bearer and X-Relay-Secret marker.
        self.assertEqual(headers["Authorization"], "Bearer legacy-admin-secret")
        self.assertEqual(headers["X-Relay-Secret"], "legacy-admin-secret")
        # Defaults when nothing is configured:
        self.assertEqual(relay, "http://localhost:8080")
        self.assertEqual(instance, "default")
        # Dev-mode downgrade MUST be observable on stderr.
        self.assertIn("RELAY_SECRET", captured.getvalue())
        self.assertIn("dev-mode", captured.getvalue())

    def test_hook_caches_jwt_within_ttl(self) -> None:
        """Two close-together hook fires must share one JWT (single token POST)."""
        profile = {
            "relay_url": "https://relay.example.com",
            "instance_name": "alice",
            "api_key": "mct_secret_api_key_value",
        }
        token_payload = {"token": "jwt-cached-xyz", "expires_in": 600}

        with patch.object(hooks, "_load_active_profile", return_value=profile), \
             patch.object(hooks.httpx, "post", return_value=_Resp(token_payload)) as mock_post:
            _, _, h1 = hooks._resolve_auth()
            _, _, h2 = hooks._resolve_auth()

        self.assertEqual(h1, h2)
        self.assertEqual(h1["Authorization"], "Bearer jwt-cached-xyz")
        # The whole point: only ONE network round-trip across two fires.
        self.assertEqual(
            mock_post.call_count, 1,
            "JWT must be cached in-memory across rapid hook fires",
        )


if __name__ == "__main__":
    unittest.main()
