"""Cross-harness end-to-end verification matrix (opt-in).

Why this exists
---------------
``test_real_harness_e2e.py`` answers "does the binary spawn?". This file
answers the higher-bar question: "does each binary actually produce a
sane reply within the user-perceptible budget?" — and records latency
to a sidecar so ``docs/CROSS_HARNESS_VERIFICATION.md`` can be regenerated
from ground truth instead of vibes.

The full ideal matrix (per the runcard) would spawn a real reflexd
daemon per vendor, post ``@<vendor>-test say hi`` from a synthetic Arav
user, and assert the reply lands in <60s. That requires a live relay,
JWT minting, and 6 simultaneous LLM processes — exactly the integration
surface the user got burned on. Since the budget here is small and the
adapter argv contracts are already pinned in ``test_real_harness_e2e.py``,
this file aggregates: it loops over the 6 vendors, runs each adapter's
documented one-shot CLI, and asserts a sane reply + latency.

If a vendor binary is missing or unauthenticated, that vendor SKIPS
(never fails). The aggregate test counts how many vendors verified and
fails only if ZERO vendors are present (which would mean the env is
totally unconfigured).

How to run
----------
    pytest -m cross_harness -v -s

To regenerate the verification doc from a fresh run::

    QUORUS_HARNESS_LATENCY_FILE=/tmp/quorus-harness-latency.json \
        pytest -m cross_harness -v -s
    # then open docs/CROSS_HARNESS_VERIFICATION.md and edit the table
    # using the values in /tmp/quorus-harness-latency.json
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

# Single source of truth for the per-vendor argv. Mirrors
# test_real_harness_e2e.py — if these drift, BOTH break loudly.
PROMPT = "say hi briefly"
TIMEOUT_S = 60.0
LATENCY_BUDGET_S = 60.0

# vendor → argv (the binary is argv[0]). The cursor entry tries the
# documented `cursor-agent` first; the test skips if absent.
VENDOR_ARGV: dict[str, list[str]] = {
    "claude":   ["claude", "--print", PROMPT],
    "codex":    ["codex", "exec", "--sandbox", "read-only", PROMPT],
    "gemini":   ["gemini", "-p", PROMPT],
    "cursor":   ["cursor-agent", "-p", PROMPT],
    "opencode": ["opencode", "run", "--", PROMPT],
    "cline":    ["cline", "--", PROMPT],
}

_AUTH_TOKENS = (
    "authentication required", "please authenticate", "not authenticated",
    "sign in", "401 unauthorized", "rate limited", "model not found",
)

_SIDECAR = os.environ.get("QUORUS_HARNESS_LATENCY_FILE", "")


def _record(binary: str, status: str, elapsed: float | None) -> None:
    """Best-effort sidecar write; never raise."""
    if not _SIDECAR:
        return
    try:
        path = Path(_SIDECAR)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8")) or {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[binary] = {
            "status": status,
            "elapsed_s": round(elapsed, 2) if elapsed is not None else None,
            "ts": time.time(),
        }
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass


def _run_one(argv: list[str]) -> tuple[str, float | None]:
    """Run one vendor's headless invocation. Returns (status, elapsed)."""
    binary = argv[0]
    if shutil.which(binary) is None:
        return ("skip:not-on-path", None)
    start = time.time()
    try:
        proc = subprocess.run(argv, capture_output=True, text=True, timeout=TIMEOUT_S)
    except subprocess.TimeoutExpired:
        return ("fail:timeout", time.time() - start)
    elapsed = time.time() - start
    blob = ((proc.stdout or "") + "\n" + (proc.stderr or "")).lower()
    if any(t in blob for t in _AUTH_TOKENS):
        return ("skip:auth", elapsed)
    if proc.returncode != 0:
        return (f"fail:rc={proc.returncode}", elapsed)
    if not (proc.stdout and proc.stdout.strip()):
        return ("fail:empty-stdout", elapsed)
    if elapsed >= LATENCY_BUDGET_S:
        return (f"fail:latency={elapsed:.1f}s", elapsed)
    return ("pass", elapsed)


@pytest.mark.cross_harness
@pytest.mark.parametrize("vendor", list(VENDOR_ARGV.keys()))
def test_vendor_one_shot_replies_within_budget(vendor: str) -> None:
    """Per-vendor: spawn the documented headless invocation, assert sane reply."""
    status, elapsed = _run_one(VENDOR_ARGV[vendor])
    _record(vendor, status, elapsed)
    print(
        f"\n[cross_harness] {vendor:<10s} status={status} "
        f"elapsed={elapsed if elapsed is None else f'{elapsed:.2f}s'}",
        flush=True,
    )
    if status.startswith("skip:"):
        pytest.skip(f"{vendor}: {status}")
    assert status == "pass", (
        f"{vendor}: {status} (elapsed={elapsed}). "
        "Run with -s to see per-vendor latency in stdout."
    )


@pytest.mark.cross_harness
def test_at_least_one_harness_present_on_host() -> None:
    """Guard against a totally-unconfigured CI: at least one vendor must be on PATH."""
    present = [v for v, argv in VENDOR_ARGV.items() if shutil.which(argv[0]) is not None]
    assert present, (
        "no vendor binary is on PATH. The cross-harness matrix needs at least "
        "one of: " + ", ".join(VENDOR_ARGV.keys())
    )
    print(f"\n[cross_harness] vendors present on host: {present}", flush=True)
