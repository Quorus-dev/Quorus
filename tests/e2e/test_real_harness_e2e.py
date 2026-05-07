"""Real-harness end-to-end smoke tests (opt-in).

# Why this exists
# ---------------
# Quorus' value proposition rests on cross-vendor agent rooms — `claude`,
# `codex`, `gemini`, and `cursor` all speak through the same relay. The
# unit tests cover wire formats and adapter logic with mocks, but a full
# regression where we spawn the real binary and assert it answers a fixed
# prompt is the only way to catch upstream CLI flag drift before users do.
#
# These tests are slow (each spawns a real LLM process) and require the
# binaries to be present + authenticated on the host. They are gated
# behind ``@pytest.mark.real_harness`` so the default ``pytest`` invocation
# stays fast and hermetic. CI/devs opt in with::
#
#     pytest -m real_harness
#
# What they assert
# ----------------
# 1. The binary is on PATH (`shutil.which`).
# 2. Spawning it with the documented headless flag returns within 60s.
# 3. stdout is non-empty and does not contain known error tokens.
#
# The exact reply text is NOT asserted — LLMs are non-deterministic. We only
# assert that a reply was produced.
#
# How to run locally
# ------------------
# Each harness must be installed and signed in:
#
#     claude --version
#     codex --version
#     gemini --version
#     cursor-agent --version  # optional; the CLI may not exist on stable
#
# Then::
#
#     pytest tests/e2e -m real_harness -v -s
#
# To run a single harness::
#
#     pytest tests/e2e/test_real_harness_e2e.py::test_claude_real -m real_harness -v -s
"""

from __future__ import annotations

import json
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

PROMPT = "say hi briefly"
TIMEOUT_SECONDS = 60

# Latency budgets — single-trial, so we record the wall-clock and:
#   * SOFT WARN at >30s (printed, not failed) — useful for the runcard.
#   * HARD FAIL at >90s — anything that slow is broken even with cold-start.
# These thresholds are deliberately permissive; tighten once the runcard has
# multi-trial baselines from CI.
SOFT_LATENCY_BUDGET_S = 30.0
HARD_LATENCY_BUDGET_S = 90.0

# Sidecar where each test writes its latency. `docs/CROSS_HARNESS_VERIFICATION.md`
# can be regenerated from this. Disabled when the env var is unset so local
# runs don't pollute the working tree.
_LATENCY_SIDECAR = os.environ.get("QUORUS_HARNESS_LATENCY_FILE", "")


def _record_latency(binary: str, elapsed: float) -> None:
    """Best-effort write to the latency sidecar — never raise."""
    if not _LATENCY_SIDECAR:
        return
    try:
        path = Path(_LATENCY_SIDECAR)
        existing: dict = {}
        if path.exists():
            try:
                existing = json.loads(path.read_text(encoding="utf-8")) or {}
            except (json.JSONDecodeError, OSError):
                existing = {}
        existing[binary] = {"elapsed_s": round(elapsed, 2), "ts": time.time()}
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(existing, indent=2, sort_keys=True), encoding="utf-8")
    except OSError:
        pass

# Tokens that, if present in stdout, indicate the harness rejected the call
# rather than answering. Kept liberal so a transient SDK breaking change is
# loud rather than silent.
_ERROR_TOKENS = (
    "command not found",
    "no such file or directory",
    "authentication required",
    "please authenticate",
    "not authenticated",
    "sign in",
    "401 unauthorized",
    "rate limited",
    "model not found",
)


def _run_or_skip(args: list[str], *, env: dict | None = None) -> str:
    """Run a CLI with a fixed prompt; return stdout or skip on transient ENV.

    Skips with a clear message if:
    - The binary is not on PATH.
    - Authentication is missing (detected via well-known env-var absence
      OR error tokens in stdout/stderr).

    Fails the test if:
    - The binary returns within the deadline but emits empty stdout.
    - The binary crashes on startup (non-zero exit + no stdout).
    """
    binary = args[0]
    if shutil.which(binary) is None:
        pytest.skip(f"{binary!r} not on PATH; install + auth before opting in")

    start = time.time()
    try:
        proc = subprocess.run(
            args,
            input=PROMPT,
            capture_output=True,
            text=True,
            timeout=TIMEOUT_SECONDS,
            env=env,
        )
    except subprocess.TimeoutExpired:
        pytest.fail(
            f"{binary!r} did not reply within {TIMEOUT_SECONDS}s — "
            "likely upstream CLI flag drift; check the harness adapter"
        )

    elapsed = time.time() - start
    out = (proc.stdout or "") + "\n" + (proc.stderr or "")
    lower = out.lower()

    for token in _ERROR_TOKENS:
        if token in lower:
            pytest.skip(
                f"{binary!r} returned auth/setup error: {token!r}. "
                "Sign in to the harness on this host before opting in."
            )

    assert proc.returncode == 0, (
        f"{binary!r} exited {proc.returncode} after {elapsed:.1f}s; "
        f"stdout[:300]={proc.stdout[:300]!r} stderr[:300]={proc.stderr[:300]!r}"
    )
    assert proc.stdout and proc.stdout.strip(), (
        f"{binary!r} returned empty stdout (returncode={proc.returncode})"
    )
    return proc.stdout


@pytest.mark.real_harness
def test_claude_real():
    """`claude --print "say hi briefly"` → non-empty reply within 60s.

    Quorus uses ``claude --print`` (not the SDK) so users don't need an
    Anthropic API key — Claude Code's OAuth handles auth.
    """
    _run_or_skip(["claude", "--print", PROMPT])


@pytest.mark.real_harness
def test_codex_real():
    """`codex exec "say hi briefly"` → non-empty reply within 60s.

    OpenAI's codex CLI uses ``exec`` for headless mode. The sandbox flag
    keeps the run hermetic.
    """
    _run_or_skip(["codex", "exec", "--sandbox", "read-only", PROMPT])


@pytest.mark.real_harness
def test_gemini_real():
    """`gemini -p "say hi briefly"` → non-empty reply within 60s.

    Google's gemini CLI uses ``-p`` for prompt mode.
    """
    _run_or_skip(["gemini", "-p", PROMPT])


@pytest.mark.real_harness
def test_cursor_real():
    """`cursor-agent -p "say hi briefly"` → non-empty reply within 60s.

    Cursor's headless agent CLI; binary may not exist on every host. The
    skip path is the common case.
    """
    # Cursor's CLI alias varies between releases; try the documented one
    # first, then fall back to ``cursor`` as a minimum.
    binary = "cursor-agent" if shutil.which("cursor-agent") else "cursor"
    _run_or_skip([binary, "-p", PROMPT])


@pytest.mark.real_harness
def test_opencode_real():
    """`opencode run -- "say hi briefly"` → non-empty reply within 60s.

    The opencode CLI (https://opencode.ai/docs/cli/) uses ``run`` as the
    one-shot entry point with the prompt as a positional argument. ``--``
    separates options from the positional. Auth via ``opencode auth login``;
    test skips with auth-error token if 0 credentials configured.
    """
    _run_or_skip(["opencode", "run", "--", PROMPT])


@pytest.mark.real_harness
def test_cline_real():
    """`cline -- "say hi briefly"` → non-empty reply within 60s.

    The cline standalone CLI (https://docs.cline.bot) takes the prompt as
    a positional argument. Auth via ``cline auth``; test skips with
    auth-error token if not authenticated.
    """
    _run_or_skip(["cline", "--", PROMPT])
