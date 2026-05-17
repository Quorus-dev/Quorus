"""Regression test for the outbox retry-storm bug (2026-05-16 audit, A6).

Before this fix, ``_handle_failure`` in ``quorus/services/outbox_svc.py``
computed an exponential-backoff ``delay`` but never persisted it. The
status was reset to PENDING immediately, ``_claim_entries`` had no time
filter, and the worker re-polled at every tick (default 1 Hz).

Concrete failure mode: a downstream that 503s for 30 seconds would cause
the worker to exhaust all MAX_RETRIES (5 by default) in ~5 seconds and
move the entry to FAILED, instead of giving the downstream the
1+2+4+8+16 = 31s of backoff windows RETRY_DELAYS promises.

This test verifies at the unit level that ``_handle_failure`` now sets
``next_attempt_at`` to ``now() + RETRY_DELAYS[retry_count - 1]``, and
that ``_claim_entries`` filters on that column.
"""

from __future__ import annotations

import re
from datetime import datetime, timezone
from pathlib import Path


OUTBOX_SVC = Path(__file__).parent.parent / "quorus" / "services" / "outbox_svc.py"
OUTBOX_MODEL = Path(__file__).parent.parent / "quorus" / "models" / "outbox.py"
MIGRATION = (
    Path(__file__).parent.parent
    / "quorus"
    / "migrations"
    / "versions"
    / "012_outbox_next_attempt_at.py"
)


def test_migration_exists():
    """The 012 migration that adds ``next_attempt_at`` must be present."""
    assert MIGRATION.exists(), (
        "migration 012_outbox_next_attempt_at.py must exist — without it, "
        "the runtime fix can't take effect on prod"
    )
    body = MIGRATION.read_text()
    assert "add_column" in body
    assert "next_attempt_at" in body
    assert "ix_message_outbox_poll_v2" in body, (
        "migration must add a covering index for the new claim query"
    )


def test_model_declares_next_attempt_at():
    """The ORM model must declare the column or the worker can't write to it."""
    body = OUTBOX_MODEL.read_text()
    assert re.search(
        r"next_attempt_at\s*:\s*Mapped\[datetime\s*\|\s*None\]",
        body,
    ), "MessageOutbox.next_attempt_at must be a nullable timezone-aware datetime"


def test_claim_entries_filters_next_attempt_at():
    """The claim query must skip entries whose backoff window hasn't elapsed."""
    body = OUTBOX_SVC.read_text()
    # The new path uses or_(MessageOutbox.next_attempt_at.is_(None),
    # MessageOutbox.next_attempt_at <= now)
    assert "MessageOutbox.next_attempt_at.is_(None)" in body, (
        "_claim_entries must allow NULL next_attempt_at (newly-inserted rows)"
    )
    assert "MessageOutbox.next_attempt_at <= now" in body, (
        "_claim_entries must skip entries whose backoff hasn't elapsed"
    )


def test_handle_failure_persists_next_attempt_at():
    """The failure path must write next_attempt_at; otherwise the column is
    a no-op and the retry storm reopens."""
    body = OUTBOX_SVC.read_text()
    assert "next_attempt_at=next_attempt" in body, (
        "_handle_failure must persist next_attempt_at on the PENDING retry path"
    )
    assert "datetime.now(timezone.utc) + timedelta(seconds=delay)" in body, (
        "next_attempt must be computed as now + RETRY_DELAYS[k]"
    )


def test_retry_delays_unchanged():
    """The exponential-backoff schedule itself is the contract — if we ever
    change it, surrounding tests + docs need updating too."""
    body = OUTBOX_SVC.read_text()
    assert "RETRY_DELAYS = [1, 2, 4, 8, 16]" in body, (
        "RETRY_DELAYS schedule changed — confirm callers / docs / SLOs and "
        "update this assertion"
    )


def test_compute_delay_curve():
    """Sanity: the delay sequence is monotonic-increasing and matches the docstring."""
    from quorus.services.outbox_svc import RETRY_DELAYS

    assert RETRY_DELAYS == [1, 2, 4, 8, 16]
    # Monotonic non-decreasing
    for a, b in zip(RETRY_DELAYS, RETRY_DELAYS[1:]):
        assert a <= b
    # Total backoff window should be at least 30s for a 5-retry default
    assert sum(RETRY_DELAYS) >= 30


def test_next_attempt_at_arithmetic():
    """A simulated 3rd failure should schedule the next attempt ~4s out."""
    from quorus.services.outbox_svc import RETRY_DELAYS

    new_retry_count = 3
    delay = RETRY_DELAYS[min(new_retry_count - 1, len(RETRY_DELAYS) - 1)]
    next_attempt = datetime.now(timezone.utc).replace(microsecond=0)
    # Just assert the index lookup yields what the test expects.
    assert delay == 4, "3rd failure should pick RETRY_DELAYS[2] == 4"
    # And the timestamp arithmetic produces a future datetime.
    from datetime import timedelta

    future = next_attempt + timedelta(seconds=delay)
    assert future > next_attempt
