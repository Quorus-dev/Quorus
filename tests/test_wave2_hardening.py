"""Wave-2 regression tests — HIGH security/correctness fixes.

This file collects tests that don't fit cleanly inside an existing route or
service test module (queue caps, CORS startup guard, _bid_windows TTL,
reflexd 401 handling). One file per fix group keeps the spec-to-test
mapping legible.

Coverage map:
* H3 — :func:`SocialSvc._apply_queue` caps at 1000 entries
* H4 — :func:`triage._bid_windows` evicts after TTL + LRU cap
* H6 (security) — relay refuses to start with ``CORS_ORIGINS=*``
* H11 — reflexd claim-polling breaks on 401/403/422
"""
from __future__ import annotations

import importlib
import importlib.util
import sys
from datetime import timedelta
from pathlib import Path
from unittest.mock import AsyncMock, MagicMock

import httpx
import pytest

from quorus.protocol.social_verbs import SocialVerb
from quorus.routes import triage as triage_mod
from quorus.services.social_svc import (
    _MAX_QUEUE_PER_ROOM,
    SocialSvc,
)

# ---------------------------------------------------------------------------
# H3 — queue cap
# ---------------------------------------------------------------------------


async def test_social_svc_queue_caps_at_1000():
    """H3 — per-room queue caps at _MAX_QUEUE_PER_ROOM, oldest evicted."""
    svc = SocialSvc()
    # Add 1100 queue entries — only the most-recent _MAX_QUEUE_PER_ROOM
    # should remain.
    for i in range(_MAX_QUEUE_PER_ROOM + 100):
        sv = SocialVerb.model_validate({
            "kind": "social", "verb": "queue", "actor": "alice",
            "room_id": "r1",
            "payload": {
                "after": f"prev-{i}",
                "task_summary": f"task {i}",
                "eta_seconds": 60,
            },
        })
        await svc.apply("tid", "rid", sv)
    state = await svc.get_state("tid", "rid")
    queue = state["queue"]
    assert len(queue) == _MAX_QUEUE_PER_ROOM
    # The first 100 should have been evicted; the most recent entries
    # remain. Verify the oldest retained is at offset 100.
    assert queue[0]["after"] == "prev-100"
    assert queue[-1]["after"] == f"prev-{_MAX_QUEUE_PER_ROOM + 99}"


# ---------------------------------------------------------------------------
# H4 — _bid_windows TTL eviction
# ---------------------------------------------------------------------------


def test_bid_windows_evicted_after_ttl(monkeypatch):
    """H4 — windows older than ``_BID_WINDOW_TTL_S`` get evicted by
    ``_cleanup_expired`` regardless of claim status.
    """
    triage_mod.reset_triage_state()

    # Insert a window with a stale ``inserted_at``.
    key = ("t1", "rid", "msg-1")
    window = triage_mod._BidWindow(
        expires_at=triage_mod._now() + timedelta(seconds=300),
    )
    # Force inserted_at into the past beyond the TTL floor.
    window.inserted_at = triage_mod._now() - timedelta(
        seconds=triage_mod._BID_WINDOW_TTL_S + 1,
    )
    triage_mod._bid_windows[key] = window

    triage_mod._cleanup_expired()
    assert key not in triage_mod._bid_windows


def test_bid_windows_lru_caps_at_max(monkeypatch):
    """H4 — once over ``_BID_WINDOW_MAX`` entries, the oldest are evicted."""
    triage_mod.reset_triage_state()
    monkeypatch.setattr(triage_mod, "_BID_WINDOW_MAX", 5)

    for i in range(7):
        key = ("t1", "rid", f"msg-{i}")
        triage_mod._record_window(
            key,
            triage_mod._BidWindow(
                expires_at=triage_mod._now() + timedelta(seconds=60),
            ),
        )

    assert len(triage_mod._bid_windows) == 5
    # Oldest two evicted.
    assert ("t1", "rid", "msg-0") not in triage_mod._bid_windows
    assert ("t1", "rid", "msg-1") not in triage_mod._bid_windows
    # Most recent five retained.
    for i in range(2, 7):
        assert ("t1", "rid", f"msg-{i}") in triage_mod._bid_windows


# ---------------------------------------------------------------------------
# H6 (security) — CORS wildcard refusal
# ---------------------------------------------------------------------------


def _cors_guard(origins: str, allow_wildcard: str | None) -> None:
    """Re-implement the relay's CORS startup guard so the regression test
    doesn't have to reload the whole ``quorus.relay`` module (which would
    rebuild the singleton ``app`` and break subsequent test fixtures).

    The logic mirrors the inline guard at the top of ``quorus/relay.py``;
    we keep them in lockstep via this dedicated regression.
    """
    cors_list = [o.strip() for o in origins.split(",") if o.strip()]
    if "*" in cors_list and (
        not allow_wildcard
        or allow_wildcard.lower() not in ("1", "true", "yes")
    ):
        raise RuntimeError(
            "CORS_ORIGINS must not contain '*' in production. "
            "Set explicit https origins, or export "
            "QUORUS_ALLOW_WILDCARD_CORS=1 for local dev."
        )


def test_cors_wildcard_origins_refused():
    """H6 (security) — ``CORS_ORIGINS=*`` without opt-in must raise."""
    with pytest.raises(RuntimeError, match="CORS_ORIGINS must not"):
        _cors_guard("*", None)
    with pytest.raises(RuntimeError, match="CORS_ORIGINS must not"):
        _cors_guard("https://app.example.com,*", None)


def test_cors_wildcard_allowed_when_opted_in():
    """H6 (security) — operator can opt-in via QUORUS_ALLOW_WILDCARD_CORS."""
    # Must not raise.
    _cors_guard("*", "1")
    _cors_guard("*", "true")
    _cors_guard("https://app.example.com", None)


def test_cors_relay_module_inline_guard_string_present():
    """H6 (security) — sanity check the relay still ships the guard.

    A pure regex check — confirms the production string lives in
    ``quorus/relay.py`` so a refactor that silently drops the guard fails
    here. Avoids the test-pollution risk of reimporting the whole module.
    """
    relay_src = (Path(__file__).resolve().parent.parent
                 / "quorus" / "relay.py").read_text()
    assert "CORS_ORIGINS must not contain '*'" in relay_src
    assert "QUORUS_ALLOW_WILDCARD_CORS" in relay_src


# ---------------------------------------------------------------------------
# H11 — reflexd claim polling breaks on 401/403/422
# ---------------------------------------------------------------------------


# scripts/ is not a package — load reflexd by path so pytest can import it.
_REPO_ROOT = Path(__file__).resolve().parent.parent
_REFLEXD_PATH = _REPO_ROOT / "scripts" / "reflexd.py"
_spec = importlib.util.spec_from_file_location("reflexd", _REFLEXD_PATH)
assert _spec is not None and _spec.loader is not None
reflexd = importlib.util.module_from_spec(_spec)
sys.modules.setdefault("reflexd", reflexd)
_spec.loader.exec_module(reflexd)


async def test_reflexd_claim_breaks_on_401():
    """H11 — relay returning 401 inside the bid window must break the
    polling loop, not be retried until deadline. We assert ``relay.claim``
    raises ``HTTPStatusError`` so the caller sees the unrecoverable code.
    """
    # Stub relay client whose POST returns 401.
    fake_response = MagicMock()
    fake_response.status_code = 401
    fake_response.text = "API key has been revoked"

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)

    # Make raise_for_status raise an HTTPStatusError carrying the response.
    fake_response.raise_for_status = MagicMock(
        side_effect=httpx.HTTPStatusError(
            "401 Unauthorized",
            request=MagicMock(),
            response=fake_response,
        )
    )
    fake_response.json = MagicMock(return_value={})

    relay = reflexd.RelayClient(
        relay_url="http://test",
        api_key="key",
        legacy_bearer=True,  # skip JWT exchange
    )
    relay._client = fake_client

    with pytest.raises(httpx.HTTPStatusError) as exc_info:
        await relay.claim(room_id="r1", message_id="m1")
    assert exc_info.value.response.status_code == 401


async def test_reflexd_claim_404_returns_unclaimed():
    """H11 sanity — 404 from /v1/claim is the "no bids yet" path and
    must continue to return ``{"claimed": False}`` so the daemon keeps
    polling within the bid window. Don't regress the recoverable case.
    """
    fake_response = MagicMock()
    fake_response.status_code = 404

    fake_client = MagicMock()
    fake_client.post = AsyncMock(return_value=fake_response)

    relay = reflexd.RelayClient(
        relay_url="http://test",
        api_key="key",
        legacy_bearer=True,
    )
    relay._client = fake_client
    out = await relay.claim(room_id="r1", message_id="m1")
    assert out == {"claimed": False}
