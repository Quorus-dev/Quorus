"""S3 wave-8 regression — DM log redaction never leaks cleartext names.

The DM service logs sender/recipient on send + fetch. SOC2 + GDPR prefer
those identifiers not be retained verbatim in long-lived log stores. The
``_redact_participant`` helper folds them into ``<first 3 chars>+sha256[:6]``
which lets ops correlate flows without exposing the full identifier.
"""
from __future__ import annotations

import hashlib

from quorus.services.message_svc import _redact_participant


def test_redact_short_name() -> None:
    out = _redact_participant("alice")
    digest = hashlib.sha256(b"alice").hexdigest()[:6]
    assert out == f"ali+{digest}"
    # Cleartext name is NOT a substring (other than the 3-char prefix).
    assert "alice" not in out


def test_redact_long_name_truncates_prefix() -> None:
    name = "very-long-bot-name-12345"
    out = _redact_participant(name)
    digest = hashlib.sha256(name.encode("utf-8")).hexdigest()[:6]
    assert out == f"ver+{digest}"


def test_redact_handles_empty_and_none() -> None:
    assert _redact_participant("") == "<unknown>"
    assert _redact_participant(None) == "<unknown>"  # type: ignore[arg-type]


def test_redact_is_deterministic() -> None:
    """Same input → same output (so log lines for the same name correlate)."""
    a = _redact_participant("bob")
    b = _redact_participant("bob")
    assert a == b


def test_redact_distinguishes_distinct_names() -> None:
    """Different inputs → different outputs (collision-resistant tail)."""
    assert _redact_participant("bob") != _redact_participant("alice")
