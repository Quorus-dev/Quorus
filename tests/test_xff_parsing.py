"""Regression tests for X-Forwarded-For parsing safety.

Wave-5 Fix 4: leftmost-trust XFF lets any client spoof their IP. We now
walk the *rightmost* ``TRUSTED_PROXY_COUNT`` hops, so the only IP we trust
is the one our outermost proxy actually observed.
"""
from __future__ import annotations

from quorus.config import get_real_ip


class _StubClient:
    def __init__(self, host: str):
        self.host = host


class _StubRequest:
    def __init__(self, *, xff: str | None, client_host: str = "127.0.0.1"):
        self.headers = {"X-Forwarded-For": xff} if xff is not None else {}
        self.client = _StubClient(client_host)


def test_no_xff_falls_back_to_client_host():
    req = _StubRequest(xff=None, client_host="10.0.0.5")
    assert get_real_ip(req) == "10.0.0.5"


def test_single_hop_returns_that_hop():
    """One proxy hop, trusted_n=1 → return the only entry."""
    req = _StubRequest(xff="1.2.3.4", client_host="10.0.0.5")
    assert get_real_ip(req, trusted_n=1) == "1.2.3.4"


def test_multiple_hops_with_spoof_at_head_ignored():
    """Spoofed XFF at the LEFT must not be trusted. With trusted_n=1
    behind one real proxy, the rightmost entry is the only safe pick."""
    # Public client appended "1.1.1.1" hoping to be trusted.
    # Real edge proxy appended "203.0.113.10" (the real client IP it saw).
    req = _StubRequest(
        xff="1.1.1.1, 203.0.113.10",
        client_host="10.0.0.5",
    )
    # trusted_n=1 → take parts[-1] = "203.0.113.10".
    assert get_real_ip(req, trusted_n=1) == "203.0.113.10"


def test_trusted_n_2_walks_two_hops():
    """When operator runs CloudFront → ALB → app, trusted_n=2 should
    skip the inner-most ALB hop and return what CloudFront recorded."""
    req = _StubRequest(
        xff="9.9.9.9, 198.51.100.7, 10.0.0.1",
        client_host="10.0.0.5",
    )
    # parts[-2] = "198.51.100.7" — the IP CloudFront stamped, the real client.
    assert get_real_ip(req, trusted_n=2) == "198.51.100.7"


def test_malformed_xff_falls_back_to_client_host():
    """Empty / whitespace-only XFF must not return empty string."""
    req = _StubRequest(xff="   ", client_host="10.0.0.5")
    assert get_real_ip(req) == "10.0.0.5"


def test_unknown_when_no_client_no_xff():
    req = _StubRequest(xff=None)
    req.client = None  # type: ignore[assignment]
    assert get_real_ip(req) == "unknown"
