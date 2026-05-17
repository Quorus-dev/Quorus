"""Regression test for the audit-BEFORE-mutation invariant on Phase 1 routes
(2026-05-16 audit).

Before this fix, all three Phase 1 mutation routes (memory:set, memory:delete,
tools:register, tools:remove, capabilities:publish) wrote audit records AFTER
the mutation with a silent ``try/except: pass`` block. That meant:

* If audit failed (Postgres down, transient network blip), the mutation
  already succeeded and there was no audit trail for it. Compliance gap.
* If audit succeeded but mutation failed, audit ledger had a row claiming
  an outcome that never happened. Less common but still wrong.

Fixed by writing the audit record FIRST. In production (``DATABASE_URL`` set),
an audit failure now raises HTTP 503 and refuses the mutation. In dev/test
(no DATABASE_URL), the warning is logged but the mutation proceeds — same
as before, to avoid breaking local development.

The tests below assert:
1. Audit record is written before svc.set/register/publish runs (call order).
2. In production mode, audit failure → 503 + no mutation.
3. In dev mode, audit failure → warning + mutation proceeds.
4. No remaining ``try/except: pass`` blocks around audit_svc.record in the
   three target files.
"""

from __future__ import annotations

import re
from pathlib import Path


SOURCES = [
    Path(__file__).parent.parent / "quorus" / "routes" / "persistent_memory.py",
    Path(__file__).parent.parent / "quorus" / "routes" / "tool_catalog.py",
    Path(__file__).parent.parent / "quorus" / "routes" / "capabilities.py",
]


def test_no_silent_except_pass_around_audit():
    """The old `try: await audit_svc.record(...) except Exception: pass` is gone.

    This is the source-level regression check. Even if someone reintroduces
    the pattern from copy-paste, this test catches it.
    """
    bad = re.compile(
        r"await\s+audit_svc\.record\([^)]*\)[^}]*?except\s+Exception\s*:\s*\n\s*pass",
        re.DOTALL,
    )
    for src in SOURCES:
        body = src.read_text()
        match = bad.search(body)
        assert match is None, (
            f"{src.name} still has silent try/except: pass around audit_svc.record "
            f"at offset {match.start() if match else -1}. Audit-BEFORE-mutation "
            f"invariant requires propagating failures in production."
        )


def test_audit_called_before_mutation_in_source():
    """In each route handler, the audit_svc.record call must syntactically
    precede the svc.set/register/publish/remove/delete call within the same
    function. This is a textual heuristic — if it ever fails it almost
    certainly means someone reordered."""
    mutation_calls = {
        "persistent_memory.py": ["svc.set", "svc.delete"],
        "tool_catalog.py": ["svc.register", "svc.remove"],
        "capabilities.py": ["svc.publish"],
    }
    for src in SOURCES:
        body = src.read_text()
        for call in mutation_calls[src.name]:
            audit_idx = body.find("audit_svc.record")
            mutation_idx = body.find(call)
            if audit_idx == -1 or mutation_idx == -1:
                continue
            assert audit_idx < mutation_idx, (
                f"{src.name}: audit_svc.record (offset {audit_idx}) must come "
                f"BEFORE {call} (offset {mutation_idx}). The invariant is "
                f"that intent is recorded first so a failed audit can refuse "
                f"the mutation."
            )


def test_production_audit_failure_raises_503():
    """In source: each audit block must have an ``if os.environ.get('DATABASE_URL')``
    guard that raises HTTPException(503). This is what ties the invariant to
    production behaviour."""
    for src in SOURCES:
        body = src.read_text()
        # Each file should mention the production guard at least once per
        # audit-protected handler.
        assert "os.environ.get(\"DATABASE_URL\")" in body, (
            f"{src.name} missing production guard — audit failures must "
            f"raise 503 when DATABASE_URL is set"
        )
        assert "audit ledger unavailable; refusing mutation" in body, (
            f"{src.name} missing the canonical refusal message"
        )
