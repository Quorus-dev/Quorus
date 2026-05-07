"""Reflexd — Quorus client-side AI-native notification daemon.

Phase 1 (PR-C1, shipped): wakes the right agent on @-mention without
keeping host harnesses open. Subscribes to ``/stream/{participant}``,
runs local triage, bids on ``/v1/bid``, polls ``/v1/claim``, and on win
spawns a headless agent (claude SDK / codex / gemini / cursor-agent)
which posts the reply through ``/rooms/{room}/messages``.

Phase 2 (this module + :mod:`reflexd_triage`): self-assignment. The
daemon now also detects *open work* in chat and bids on it without
being explicitly @-mentioned. Triage produces a ``kind`` of:

  - ``mention``       — literal ``@self_name``                (bid 1.0)
  - ``role_request``  — ``TODO @<role>: ...`` matching caps   (bid 0.7)
  - ``open_todo``     — ``@open ...`` with capability overlap (bid 0.5)
  - ``question``      — ``who can ...?`` / trailing ``?``     (bid 0.3)

Capability sets are derived from the participant-name suffix:

  - ``*-claude*`` → frontend, react, tui, tests, general
  - ``*-codex*``  → backend, relay, audit, policy, general
  - ``*-gemini*`` → docs, research, general
  - ``*-cursor*`` → refactor, general

Bid scoring + capability tables live in :mod:`reflexd_triage` so the
daemon stays under its 1500-LoC cap. All scoring is local — no relay
round-trip — so this scales to 4+ agents without bottleneck.

Lane: client-side ONLY. No relay route changes. Run with
``python scripts/reflexd.py --help``.
"""
from __future__ import annotations

import argparse
import asyncio
import hashlib
import json
import logging
import logging.handlers
import os
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any, Callable

import httpx

# scripts/ is not a package; add the repo root so ``quorus`` imports work when
# this file is run directly via ``python scripts/reflexd.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
for _path in (
    _REPO_ROOT,
    _REPO_ROOT / "packages" / "sdk",
    _REPO_ROOT / "packages" / "cli",
    _REPO_ROOT / "packages" / "mcp",
    _REPO_ROOT / "packages" / "tui",
):
    _path_str = str(_path)
    if _path.exists() and _path_str not in sys.path:
        sys.path.insert(0, _path_str)
del _path, _path_str

# Phase 2 triage v2 lives in a sibling module so reflexd.py stays under its
# 1500-LoC cap. We import via a path-aware loader because ``scripts/`` is
# not a package — same dance the tests use to import reflexd itself.
import importlib.util as _ilu  # noqa: E402

from quorus.config import ConfigManager  # noqa: E402
from quorus.operating_discipline import render_qod_for_agent_loop  # noqa: E402
from quorus.runtime import memory as _mem  # noqa: E402  Stream B
from quorus.runtime.turnguard import busy_path as _tg_busy_path  # noqa: E402
from quorus.runtime.turnguard import is_busy as _tg_is_busy  # noqa: E402

_TRIAGE_PATH = Path(__file__).resolve().parent / "reflexd_triage.py"
_triage_spec = _ilu.spec_from_file_location("reflexd_triage", _TRIAGE_PATH)
assert _triage_spec is not None and _triage_spec.loader is not None
reflexd_triage = _ilu.module_from_spec(_triage_spec)
sys.modules.setdefault("reflexd_triage", reflexd_triage)
_triage_spec.loader.exec_module(reflexd_triage)

# Re-export the public surface so callers (and existing tests that import
# from reflexd directly) can keep using ``reflexd.classify_message`` etc.
classify_message = reflexd_triage.classify_message
TriageResult = reflexd_triage.TriageResult
capabilities_for = reflexd_triage.capabilities_for
matches_capability = reflexd_triage.matches_capability
compute_bid_v2 = reflexd_triage.compute_bid_v2
self_assign_preamble = reflexd_triage.self_assign_preamble
CAPABILITIES_CLAUDE = reflexd_triage.CAPABILITIES_CLAUDE
CAPABILITIES_CODEX = reflexd_triage.CAPABILITIES_CODEX
CAPABILITIES_GEMINI = reflexd_triage.CAPABILITIES_GEMINI
CAPABILITIES_CURSOR = reflexd_triage.CAPABILITIES_CURSOR

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

DEFAULT_RUNTIME_DIR = Path.home() / ".quorus" / "runtime"
DEFAULT_LOG_PATH = Path.home() / ".quorus" / "reflexd.log"
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB rotation per spec
LOG_BACKUP_COUNT = 3
SSE_RECONNECT_S = 2.0
SSE_RECONNECT_MAX_S = 30.0
BID_WINDOW_SECONDS = 2.0
BID_TTL_SECONDS = 5
SUBPROCESS_TIMEOUT_S = 120
HEARTBEAT_HISTORY_LIMIT = 10
MENTION_PREVIEW_CHARS = 80
# Anti-loop guard: if we are about to reply to a message that itself was
# spawned in response to one of OUR prior replies, and the chain is already
# this deep, break it. Three is the soft ceiling because debug-and-fix
# threads commonly run two ping-pongs before a human steps in; four+ is
# almost always a runaway.
MAX_REPLY_DEPTH = 3

logger = logging.getLogger("reflexd")

# ---------------------------------------------------------------------------
# Pure helpers (module-level so tests can call without spinning a daemon)
# ---------------------------------------------------------------------------


def triage_local(
    *,
    content: str,
    sender: str | None,
    self_name: str,
    message_type: str = "chat",
) -> tuple[str, str]:
    """Phase-1-compatible 2-tuple wrapper around :func:`classify_message`.

    Returns ``(action, reason)``. Existing call sites and tests that
    unpack two values keep working unchanged. The new ``kind``/``role``/
    ``description`` fields are reachable via :func:`classify_message`
    directly — see :mod:`reflexd_triage` for the rubric.

    Reason mapping is preserved so log lines stay grep-stable:

    * ``"literal @mention"`` for mentions (Phase 1 wire format)
    * ``"question mark"`` for trailing ``?``
    * other Phase-2 reasons (``"open_todo"``, ``"role_request:<role>"``,
      ``"who_can_question"``) are surfaced verbatim.
    """
    result = classify_message(
        content=content,
        sender=sender,
        self_name=self_name,
        message_type=message_type,
    )
    return result.action, result.reason


def compute_bid(*, is_mention: bool, recency_seconds: float) -> float:
    """Bid in [0,1]. Mention=1.0, question=0.5, minus 0.1 per recency-second."""
    base = 1.0 if is_mention else 0.5
    return max(0.0, min(1.0, base - 0.1 * max(0.0, recency_seconds)))


def envelope_reply_depth(envelope: dict[str, Any]) -> int:
    """Pull the parent-chain reply depth from an envelope, default 0.

    Wire format: an envelope MAY carry ``_reply_depth`` at the top level OR
    ``metadata.reply_depth``. Both are accepted because the relay's
    ``/rooms/{room}/messages`` route currently strips unknown top-level
    keys but preserves ``metadata`` — so over-the-wire propagation has to
    happen via metadata, while in-process propagation (within reflexd's
    own queue) uses the top-level field.

    Treats negative or non-int values as 0 — defensive against
    contributor-chosen string formats from upstream relays.
    """
    raw = envelope.get("_reply_depth")
    if raw is None:
        meta = envelope.get("metadata")
        if isinstance(meta, dict):
            raw = meta.get("reply_depth")
    try:
        depth = int(raw) if raw is not None else 0
    except (TypeError, ValueError):
        return 0
    return max(0, depth)


def should_break_reply_chain(envelope: dict[str, Any], *, max_depth: int = MAX_REPLY_DEPTH) -> bool:
    """Return True iff replying to ``envelope`` would extend a chain past max_depth.

    A reply we would emit lives at depth ``parent + 1``. We refuse when
    that successor depth would equal-or-exceed ``max_depth`` — i.e. we
    allow chains up to depth ``max_depth`` and break the next one.
    Concretely with the default of 3: depths 0, 1, 2, 3 are fine, depth-4
    replies are dropped.
    """
    parent_depth = envelope_reply_depth(envelope)
    return (parent_depth + 1) > max_depth


def busy_path(participant: str, runtime_dir: Path | None = None) -> Path:
    """Return the busy-file path for ``participant``.

    Delegates to :mod:`quorus.runtime.turnguard` so the file format stays in
    one place. The optional ``runtime_dir`` arg keeps the legacy positional
    signature working for in-tree tests that pass a tmp dir directly.
    """
    base = runtime_dir or DEFAULT_RUNTIME_DIR
    return _tg_busy_path(participant, dir_override=base)


def is_busy(participant: str, runtime_dir: Path | None = None) -> bool:
    """Return True iff TurnGuard says the agent is mid-tool-call.

    Same single-source-of-truth wrapper around the helper as ``busy_path``.
    """
    base = runtime_dir or DEFAULT_RUNTIME_DIR
    return _tg_is_busy(participant, dir_override=base)


def safe_message_preview(content: str) -> str:
    """First N chars, single-line — deliberately excludes full body for PII."""
    if not content:
        return ""
    flat = content.replace("\n", " ").replace("\r", " ")
    if len(flat) <= MENTION_PREVIEW_CHARS:
        return flat
    return flat[: MENTION_PREVIEW_CHARS - 1] + "…"


# Regex set for redacting common PII when --debug surfaces preview text.
# Conservative: emails, north-american-style phone numbers, and @-mentions
# (which can identify a participant or a private agent name).
_PII_PATTERNS: tuple[tuple[re.Pattern[str], str], ...] = (
    (re.compile(r"[\w.+-]+@[\w-]+\.[\w.-]+"), "<redacted-email>"),
    (re.compile(r"\b\+?\d[\d\s().-]{7,}\d\b"), "<redacted-phone>"),
    (re.compile(r"@[A-Za-z0-9._-]{1,80}"), "<redacted-mention>"),
)


def _redact_pii(s: str) -> str:
    out = s
    for pat, repl in _PII_PATTERNS:
        out = pat.sub(repl, out)
    return out


def safe_log_summary(content: str, *, debug: bool = False) -> str:
    """Return a log-safe summary of *content* — never the raw body.

    Default: ``"len=N hash=XXXXXXXX"`` so an operator can correlate two
    log lines that mention the same chat body without the body itself
    landing in ``~/.quorus/reflexd.log`` (or its rotated 30MB tail).

    When *debug* is True, append a redacted preview (emails / phone /
    @-mentions stripped). Only call with debug=True from --debug paths.

    PII guard: this is the canonical helper for any per-message log
    line touching chat content. The function is pure and total — the
    fingerprint stays stable across re-invocations so two related
    events emit the same hash.
    """
    if not content:
        return "len=0 hash=00000000"
    encoded = content.encode("utf-8", errors="replace")
    digest = hashlib.sha256(encoded).hexdigest()[:8]
    base = f"len={len(encoded)} hash={digest}"
    if debug:
        preview = safe_message_preview(_redact_pii(content))
        return f"{base} preview={preview!r}"
    return base


# Stream B helpers (memory + defer + thread + DM loop) live in a sibling
# module to keep this file under its LoC cap. Same path-aware loader
# pattern as reflexd_triage above.
_STREAMB_PATH = Path(__file__).resolve().parent / "reflexd_streamb.py"
_streamb_spec = _ilu.spec_from_file_location("reflexd_streamb", _STREAMB_PATH)
assert _streamb_spec is not None and _streamb_spec.loader is not None
reflexd_streamb = _ilu.module_from_spec(_streamb_spec)
sys.modules.setdefault("reflexd_streamb", reflexd_streamb)
_streamb_spec.loader.exec_module(reflexd_streamb)
MEMORY_CONTEXT_LIMIT = reflexd_streamb.MEMORY_CONTEXT_LIMIT
render_memory_context = reflexd_streamb.render_memory_context
summarise_reply_for_memory = reflexd_streamb.summarise_reply_for_memory
envelope_thread_root = reflexd_streamb.envelope_thread_root
envelope_canonical_id = reflexd_streamb.envelope_canonical_id


_HARNESS_SUFFIXES = (
    ("-claude", "claude"),
    ("-codex", "codex"),
    ("-gemini", "gemini"),
    ("-cursor", "cursor"),
    # Wave-7: Opencode + Cline have real headless CLIs (verified vendor docs
    # 2026-05). Adding suffixes here lets reflexd wake them on @-mention so
    # they reach proactive parity with the original four. Windsurf is NOT
    # in this list — it has no canonical headless CLI; see
    # docs/HARNESS_TIERS.md for the disposition memo.
    ("-opencode", "opencode"),
    ("-cline", "cline"),
)

# ---------------------------------------------------------------------------
# Argv-shape pinning — codify the EXACT command shape per harness
# ---------------------------------------------------------------------------
# These constants and helpers exist so contract tests can pin the wire-format
# we send to vendor CLIs. If a vendor renames a flag, ONE of these helpers
# breaks and the corresponding contract test fails — instead of the daemon
# silently invoking a non-existent flag at runtime.
#
# All four adapters now shell out to a user-installed CLI binary. Reflexd
# never needs ANTHROPIC_API_KEY for claude — Claude Code's own OAuth /
# keychain handles auth, exactly the way ``codex``/``gemini``/``cursor-agent``
# rely on their vendor login state. The MVP runs end-to-end with zero new
# API keys for the user.

CLAUDE_BIN = "claude"
CODEX_BIN = "codex"
GEMINI_BIN = "gemini"
CURSOR_BIN = "cursor-agent"
OPENCODE_BIN = "opencode"
CLINE_BIN = "cline"


def build_claude_argv(context: str) -> list[str]:
    """Pinned argv shape for Claude Code CLI.

    Contract: ``claude --print -- <ctx>``.

    ``--print`` (alias ``-p``) is the headless / non-interactive mode in the
    Claude Code CLI (verified on v2.1.126). It reads the prompt from argv,
    streams the response, then exits. Auth is whatever ``claude /login`` set
    up — we never read ``ANTHROPIC_API_KEY``.

    Argv-injection guard: ``--`` separates options from positional args so
    a chat body that starts with a dash (e.g. ``"-config /etc/passwd"``) is
    parsed as the prompt, not as a CLI flag. Verified via ``claude --print
    --help`` (parses help) vs ``claude --print -- --help`` (treated as prompt).
    """
    return [CLAUDE_BIN, "--print", "--", context]


def build_codex_argv(context: str) -> list[str]:
    """Pinned argv shape for codex CLI.

    Contract: ``codex exec --json -- <ctx>``.

    ``codex exec`` takes the prompt as a positional [PROMPT] arg (verified on
    OpenAI Codex v0.128.0). The ``--prompt`` flag does NOT exist; the legacy
    shape ``codex exec --json --prompt <ctx>`` was broken in production
    because codex rejects ``--prompt`` as ``unexpected argument``.

    Argv-injection guard: ``--`` separates options from the positional
    prompt so a leading-dash chat body cannot be re-interpreted as a flag.
    """
    return [CODEX_BIN, "exec", "--json", "--", context]


def build_gemini_argv(context: str) -> list[str]:
    """Pinned argv shape for gemini CLI.

    Contract: ``gemini --prompt=<ctx>``.

    Argv-injection guard: gemini parses ``--prompt --version`` as the
    ``--version`` flag (verified). It also rejects ``--prompt -- --version``
    with "Not enough arguments following: prompt". The safe form is the
    ``--key=value`` shape, which keeps the value attached to the flag and
    out of the option-parser's consideration.
    """
    return [GEMINI_BIN, f"--prompt={context}"]


def build_cursor_argv(context: str) -> list[str]:
    """Pinned argv shape for cursor-agent.

    Contract: ``cursor-agent -p -- <ctx>``.

    Per the official Cursor docs (https://cursor.com/docs/cli/headless), the
    headless flag is ``-p`` / ``--print`` — same pattern as the Claude Code
    CLI. The legacy reflexd shape used ``--headless --prompt=`` which was
    never canonical (it predates Cursor publishing official docs); we keep
    a single argv shape and pin it.

    Argv-injection guard: ``--`` separates options from the positional
    prompt so a leading-dash payload cannot be re-parsed as a flag. Auth
    is whatever the user set via ``cursor-agent login`` (``CURSOR_API_KEY``
    is read by the binary, not by reflexd).
    """
    return [CURSOR_BIN, "-p", "--", context]


def build_opencode_argv(context: str) -> list[str]:
    """Pinned argv shape for opencode (`opencode.ai/docs/cli/`).

    Contract: ``opencode run -- <ctx>``.

    ``opencode run`` is the documented one-shot / non-interactive entry
    point. The prompt is positional, so ``--`` separates it from any
    options the user might leak into the body. Auth is whatever
    ``opencode auth login`` set up — reflexd never reads provider keys.
    """
    return [OPENCODE_BIN, "run", "--", context]


def build_cline_argv(context: str) -> list[str]:
    """Pinned argv shape for cline (`docs.cline.bot`).

    Contract: ``cline -- <ctx>``.

    The Cline standalone CLI takes the task as a positional argument
    (``cline "Your task here"``). It's in preview as of 2026-05 and only
    available on macOS/Linux. Auth is OAuth via ``cline auth`` — reflexd
    never sees provider keys.

    Argv-injection guard: same ``--`` pattern as the others. If a leading-
    dash payload arrives in the chat body, it stays positional.
    """
    return [CLINE_BIN, "--", context]


# Version-probe argv per binary (NOT prompt-bearing — safe to run on startup).
_VERSION_PROBE_ARGV: dict[str, list[str]] = {
    "claude": [CLAUDE_BIN, "--version"],
    "codex": [CODEX_BIN, "--version"],
    "gemini": [GEMINI_BIN, "--version"],
    "cursor": [CURSOR_BIN, "--version"],
    "opencode": [OPENCODE_BIN, "--version"],
    "cline": [CLINE_BIN, "--version"],
}

# Pinned-known-good version ranges. Entries here say "we have observed this
# CLI working with these versions". Outside the range we still try, just with
# a logged WARNING. Keep the format substring-match to avoid SemVer parsing
# brittleness — vendors emit version strings in different shapes.
#
# Claude Code CLI prints e.g. ``2.1.126 (Claude Code)`` — leading "2." matches.
KNOWN_GOOD_VERSIONS: dict[str, tuple[str, ...]] = {
    "claude": ("1.", "2.", "3."),
    "codex": ("0.", "1.", "2.", "3."),
    "gemini": ("0.", "1.", "2.", "3."),
    "cursor": ("0.", "1.", "2.", "3."),
    "opencode": ("0.", "1.", "2.", "3."),
    "cline": ("0.", "1.", "2.", "3."),
}


def probe_harness_version(harness: str, *, timeout_s: float = 3.0) -> str | None:
    """Return a version string for ``harness`` or ``None`` if not detected.

    Synchronous on purpose — runs once on daemon startup, not per wake. Uses
    ``shutil.which`` to short-circuit when the binary is missing so we don't
    leak a FileNotFoundError into startup logs.

    All four harnesses (claude, codex, gemini, cursor) follow the same
    pattern now: a user-installed CLI on PATH that prints its version when
    invoked with ``--version``. Claude Code OAuth handles its own auth; we
    never check ``ANTHROPIC_API_KEY``.
    """
    argv = _VERSION_PROBE_ARGV.get(harness)
    if not argv:
        return None
    if shutil.which(argv[0]) is None:
        return None
    try:
        proc = subprocess.run(
            argv,
            capture_output=True,
            text=True,
            timeout=timeout_s,
            check=False,
        )
    except (subprocess.TimeoutExpired, OSError):
        return None
    out = (proc.stdout or "").strip() or (proc.stderr or "").strip()
    # Vendors print multi-line banners; keep the first non-empty line.
    for line in out.splitlines():
        line = line.strip()
        if line:
            return line[:200]
    return None


# Module-level fallback flag. Set by ``log_claude_adapter_status`` when
# we auto-fallback to stub mode (claude CLI missing + no explicit stub
# flag). The adapter's run() consults this OR the env var so the fallback
# works whether the daemon was bootstrapped through cmd_start (which calls
# the logger) or driven directly from a test (which sets env).
_AUTO_FALLBACK_TO_STUB = False


def reset_auto_fallback() -> None:
    """Test seam: reset the module-level auto-fallback flag.

    Production code never calls this; the daemon process is short-lived so
    in-memory state never crosses runs. Tests need it because the flag is
    set by ``log_claude_adapter_status`` and persists in the imported
    module across tests in the same process.
    """
    global _AUTO_FALLBACK_TO_STUB
    _AUTO_FALLBACK_TO_STUB = False


def is_stub_mode() -> bool:
    """True iff the adapter should reply with the stub instead of the CLI."""
    if os.environ.get("REFLEXD_STUB_REPLY") in ("1", "true", "yes"):
        return True
    return _AUTO_FALLBACK_TO_STUB


def log_claude_adapter_status(
    *, version_probe: Callable[[str], str | None] | None = None,
) -> str:
    """Emit one startup line announcing whether the real-claude path is wired.

    The claude adapter now shells out to ``claude --print`` instead of the
    Python ``claude_agent_sdk``. Auth is handled by the Claude Code CLI's
    own OAuth / keychain — reflexd never reads ``ANTHROPIC_API_KEY``.

    Three states matter (returned as the string for caller introspection):

    * ``"real"`` — ``claude --version`` succeeds; the adapter will drive
      real subprocess calls. Logs an INFO line with the detected version.
    * ``"stub"`` — ``REFLEXD_STUB_REPLY=1`` is set explicitly; we never
      attempt a real call. Logs an INFO line.
    * ``"fallback-stub"`` — claude binary is missing AND the explicit stub
      flag is not set. We auto-fall-back to stub mode so the daemon
      doesn't refuse to start, but we log a WARNING so the operator sees
      how to upgrade. The auto-fallback is implemented via
      :data:`_AUTO_FALLBACK_TO_STUB` — a module-level flag that
      :meth:`HeadlessAdapter.run` consults alongside ``REFLEXD_STUB_REPLY``.
      We deliberately do NOT mutate ``os.environ`` because env mutations
      leak across pytest test files and the adapter contract tests
      assume a clean environment.
    """
    global _AUTO_FALLBACK_TO_STUB
    has_stub = os.environ.get("REFLEXD_STUB_REPLY") in ("1", "true", "yes")
    if has_stub:
        logger.info(
            "reflexd: stub adapter active (REFLEXD_STUB_REPLY=1) — no API spend"
        )
        return "stub"
    probe = version_probe or probe_harness_version
    cli_ver = probe("claude")
    if cli_ver:
        logger.info(
            "reflexd: real-claude adapter active (claude CLI %s, OAuth handles auth)",
            cli_ver,
        )
        return "real"
    # claude binary missing. Auto-fall-back so we don't crash, but make
    # the misconfig loud. This is the friendliest behaviour for hosts
    # without Claude Code installed.
    logger.warning(
        "reflexd: claude CLI not found on PATH and REFLEXD_STUB_REPLY not set. "
        "Reflexd will fall back to stub replies. To enable real Claude "
        "replies: install Claude Code (https://claude.ai/code) and run "
        "`claude /login`."
    )
    _AUTO_FALLBACK_TO_STUB = True
    return "fallback-stub"


# Backwards-compat alias. Older callers + tests reference the old name; keep
# it pointing at the new implementation so they don't have to change in lock
# step. The new name is preferred for new code.
log_anthropic_api_key_status = log_claude_adapter_status


def log_adapter_versions(probe: Callable[[str], str | None] | None = None) -> dict[str, str | None]:
    """Probe each adapter's version on startup and emit a structured log line.

    Returns a ``{harness: version_or_None}`` map for inspection by tests. The
    daemon logs one INFO line per harness, and a WARNING when the detected
    version doesn't satisfy any prefix in :data:`KNOWN_GOOD_VERSIONS`.
    """
    probe = probe or probe_harness_version
    versions: dict[str, str | None] = {}
    for harness in ("claude", "codex", "gemini", "cursor", "opencode", "cline"):
        ver = probe(harness)
        versions[harness] = ver
        if ver is None:
            logger.info("adapter_version harness=%s version=not-detected", harness)
            continue
        good = KNOWN_GOOD_VERSIONS.get(harness, ())
        is_good = any(ver.startswith(prefix) or prefix in ver for prefix in good) if good else True
        level = logging.INFO if is_good else logging.WARNING
        logger.log(
            level,
            "adapter_version harness=%s version=%s known_good=%s",
            harness, ver, "yes" if is_good else "no",
        )
    return versions


def detect_harness(participant: str) -> str:
    """Route by participant-name suffix. Fallback claude (most common host)."""
    name = participant.lower()
    for suffix, harness in _HARNESS_SUFFIXES:
        if name.endswith(suffix) or f"{suffix}-" in name:
            return harness
    for needle, harness in (("claude", "claude"), ("codex", "codex"),
                            ("gemini", "gemini"), ("cursor", "cursor"),
                            ("opencode", "opencode"), ("cline", "cline")):
        if needle in name:
            return harness
    return "claude"


# Suffixes the strict participant-name check accepts. Mirrors
# ``_HARNESS_SUFFIXES`` plus a few well-known per-host claude variants
# (claude-1m, claude-2, claude-3, claude-desktop) so power users running
# multiple claude tabs from one host don't get rejected.
_AGENT_PARTICIPANT_SUFFIXES: tuple[str, ...] = (
    "-claude",
    "-claude-1m",
    "-claude-2",
    "-claude-3",
    "-claude-desktop",
    "-codex",
    "-gemini",
    "-cursor",
    "-opencode",
    "-cline",
)


def is_agent_participant(participant: str) -> bool:
    """True iff ``participant`` ends with a recognised agent-harness suffix.

    Reflexd silently mis-routes when started under a non-agent participant
    name (e.g. the human "arav") because triage IGNOREs ``sender ==
    self_name`` — every message from arav is then dropped before bidding.
    This check fails closed at daemon-construction time so the operator
    sees the misconfig immediately instead of in a chat-message black
    hole.
    """
    if not participant:
        return False
    name = participant.lower()
    return any(name.endswith(suffix) for suffix in _AGENT_PARTICIPANT_SUFFIXES)


# ---------------------------------------------------------------------------
# Headless adapter — routes a context prompt to the right harness
# ---------------------------------------------------------------------------


def _parse_codex_json(out: str) -> str:
    """Codex --json emits NDJSON; return concatenated assistant deltas."""
    chunks: list[str] = []
    for line in out.splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(ev, dict):
            delta = ev.get("delta") or ev.get("content") or ev.get("text")
            if isinstance(delta, str):
                chunks.append(delta)
    return "".join(chunks).strip()


class HeadlessAdapter:
    """Spawn the right headless harness for a participant; return reply text.

    One wake = one reply for PR-C1; no streaming chunks. Each method returns
    a best-effort string the daemon then posts to the room.
    """

    def __init__(
        self,
        *,
        timeout_s: int = SUBPROCESS_TIMEOUT_S,
        dry_run: bool = False,
    ) -> None:
        self.timeout_s = timeout_s
        self.dry_run = dry_run
        # Records the argv (or sdk-call signature) that WOULD have been spawned
        # in dry-run mode. Kept on the instance for tests + smoke scripts.
        self.last_dry_run: dict[str, Any] | None = None

    @staticmethod
    def _stub_reply(context: str) -> str:
        """Demo / smoke stub — no LLM call, no API spend.

        Activated by ``REFLEXD_STUB_REPLY=1``. Echoes the triggering message
        back so the user can see the full pipeline (SSE → triage → bid →
        claim → spawn → post) without burning tokens. The marker prefix
        ``(reflexd-stub)`` makes it obvious in the room that this isn't a
        real model reply.
        """
        # The triggering line is the last "@sender: <body>" block of the
        # rendered prompt. Pull just the body and trim for readability.
        body = ""
        for line in reversed(context.splitlines()):
            line = line.strip()
            if line.startswith("@") and ":" in line:
                body = line.split(":", 1)[1].strip()
                break
        if not body:
            body = "(no message)"
        if len(body) > 60:
            body = body[:60] + "…"
        return f"(reflexd-stub) on it, working on '{body}'"

    async def run(self, harness: str, *, context: str) -> str:
        # Smoke / demo path: avoid spawning any real harness. ~10 LoC, off
        # the regular path. Triggered by an explicit env var OR by the
        # module-level auto-fallback flag set when the claude binary is
        # missing at startup. ``is_stub_mode`` consults both.
        if is_stub_mode():
            return self._stub_reply(context)
        if self.dry_run:
            return self._record_dry_run(harness, context)
        if harness == "claude":
            return await self._run_subprocess(
                build_claude_argv(context),
                parser=lambda out: out.strip(),
            )
        if harness == "codex":
            return await self._run_subprocess(
                build_codex_argv(context),
                parser=_parse_codex_json,
            )
        if harness == "gemini":
            return await self._run_subprocess(
                build_gemini_argv(context),
                parser=lambda out: out.strip(),
            )
        if harness == "cursor":
            return await self._run_cursor(context)
        if harness == "opencode":
            return await self._run_subprocess(
                build_opencode_argv(context),
                parser=lambda out: out.strip(),
            )
        if harness == "cline":
            return await self._run_subprocess(
                build_cline_argv(context),
                parser=lambda out: out.strip(),
            )
        raise ValueError(f"unknown harness {harness!r}")

    def _record_dry_run(self, harness: str, context: str) -> str:
        """Record what argv WOULD have been called and return a sentinel string.

        This is the lever ``--dry-run`` uses: the full pipeline runs, but the
        adapter never touches a real binary. Tests and CI smoke can assert on
        ``adapter.last_dry_run`` to confirm the wire-format we picked.
        """
        if harness == "claude":
            argv: list[str] = build_claude_argv(context)
        elif harness == "codex":
            argv = build_codex_argv(context)
        elif harness == "gemini":
            argv = build_gemini_argv(context)
        elif harness == "cursor":
            argv = build_cursor_argv(context)
        elif harness == "opencode":
            argv = build_opencode_argv(context)
        elif harness == "cline":
            argv = build_cline_argv(context)
        else:
            raise ValueError(f"unknown harness {harness!r}")
        self.last_dry_run = {"harness": harness, "argv": argv}
        logger.info("dry_run harness=%s argv=%s", harness, argv)
        return f"[reflexd:dry-run] would invoke {harness} adapter (see last_dry_run)"

    async def _run_subprocess(
        self,
        argv: list[str],
        *,
        parser: Callable[[str], str],
    ) -> str:
        # Pre-flight: if the binary truly isn't on PATH, surface the same
        # sentinel string regardless of platform. ``shutil.which`` returning
        # None is a deterministic check; ``FileNotFoundError`` from
        # ``create_subprocess_exec`` is a backstop for racy installs.
        if shutil.which(argv[0]) is None:
            logger.warning("harness binary missing on PATH: %s", argv[0])
            return f"[reflexd] {argv[0]} not installed on this host"
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
            )
        except FileNotFoundError:
            logger.warning("harness binary missing: %s", argv[0])
            return f"[reflexd] {argv[0]} not installed on this host"
        try:
            stdout, stderr = await asyncio.wait_for(
                proc.communicate(), timeout=self.timeout_s
            )
        except TimeoutError:
            # L4: Python 3.11+ aliases ``asyncio.TimeoutError`` to the
            # builtin ``TimeoutError``; the asyncio prefix is deprecated.
            proc.kill()
            await proc.wait()
            logger.warning("harness %s timed out", argv[0])
            return "[reflexd] harness timed out"
        if proc.returncode != 0:
            err = (stderr or b"").decode("utf-8", errors="replace")[:200]
            logger.warning("harness %s exited %s: %s", argv[0], proc.returncode, err)
            return "[reflexd] harness errored"
        return parser((stdout or b"").decode("utf-8", errors="replace")) or "[reflexd] (no reply)"

    async def _run_cursor(self, context: str) -> str:
        # Try the binary first; fall back to the file-inbox protocol if it's
        # missing. Cursor's stop-hook picks the inbox file up on next session.
        if shutil.which(CURSOR_BIN) is not None:
            try:
                out = await self._run_subprocess(
                    build_cursor_argv(context),
                    parser=lambda o: o.strip(),
                )
                if not out.startswith("[reflexd] cursor-agent not installed"):
                    return out
            except Exception as exc:  # pragma: no cover
                logger.info("cursor-agent failed, falling back to inbox: %s", exc)
        return self._write_cursor_inbox(context)

    @staticmethod
    def _write_cursor_inbox(context: str, *, participant: str | None = None) -> str:
        """File-inbox fallback: ``~/.cursor/inbox/<participant>/wake_<ts>.md``.

        The cursor stop-hook reads this directory at session start. The
        participant subdirectory keeps multi-tenant hosts from colliding.
        """
        who = participant or os.environ.get("REFLEXD_PARTICIPANT") or "default"
        # Defensive sanitisation — participant comes from config, but
        # belt-and-braces against a path-traversal value.
        who = re.sub(r"[^A-Za-z0-9._-]", "_", who)[:64] or "default"
        inbox = Path.home() / ".cursor" / "inbox" / who
        inbox.mkdir(parents=True, exist_ok=True)
        fname = inbox / f"wake_{int(time.time() * 1000)}.md"
        fname.write_text(context, encoding="utf-8")
        logger.info("cursor file-inbox wrote %s", fname)
        return f"[reflexd] wrote prompt to {fname}"


# ---------------------------------------------------------------------------
# Relay client — minimal HTTP surface for what the daemon needs
# ---------------------------------------------------------------------------


@dataclass
class RelayClient:
    """Minimal HTTP surface: JWT exchange, stream-token mint, bid/claim/post."""

    relay_url: str
    api_key: str
    legacy_bearer: bool = False  # if True, send api_key directly as Bearer (skip /v1/auth/token)
    _bearer: str | None = None
    _bearer_expires_at: float = 0.0
    _client: httpx.AsyncClient | None = None

    async def __aenter__(self) -> "RelayClient":
        self._client = httpx.AsyncClient(
            base_url=self.relay_url,
            timeout=httpx.Timeout(10.0, read=None),  # no read timeout for SSE
            follow_redirects=True,
        )
        return self

    async def __aexit__(self, *exc: Any) -> None:
        if self._client is not None:
            await self._client.aclose()
            self._client = None

    @property
    def client(self) -> httpx.AsyncClient:
        assert self._client is not None, "RelayClient must be used as a context manager"
        return self._client

    async def bearer_jwt(self) -> str:
        """Return the current bearer token (minting/refreshing as needed).

        Public counterpart to ``_headers()``. Callers that only need the
        raw JWT (e.g. for sub-claim inspection) should use this method
        rather than parsing the Authorization header out of ``_headers()``.
        """
        if self.legacy_bearer:
            return self.api_key
        now = time.time()
        if self._bearer is None or now >= self._bearer_expires_at:
            resp = await self.client.post(
                "/v1/auth/token", json={"api_key": self.api_key}
            )
            resp.raise_for_status()
            self._bearer = resp.json()["token"]
            self._bearer_expires_at = now + 4 * 60
        return self._bearer or ""

    async def _headers(self) -> dict[str, str]:
        # Cache JWT for 4 min (relay token lifetime is 5min).
        # In legacy-bearer mode (e.g. local demo against RELAY_SECRET-only
        # relay) we skip the /v1/auth/token exchange because that endpoint
        # requires a Postgres-backed account/key.
        token = await self.bearer_jwt()
        return {"Authorization": f"Bearer {token}"}

    async def mint_stream_token(self, recipient: str) -> str:
        headers = await self._headers()
        resp = await self.client.post(
            "/stream/token", headers=headers, json={"recipient": recipient}
        )
        resp.raise_for_status()
        return resp.json()["token"]

    def stream_url(self, recipient: str, stream_token: str) -> str:
        return f"{self.relay_url}/stream/{recipient}?token={stream_token}"

    async def submit_bid(
        self, *, room_id: str, message_id: str,
        participant: str, bid: float, reason: str,
    ) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self.client.post(
            "/v1/bid",
            headers=headers,
            json={
                "room_id": room_id, "message_id": message_id,
                "participant": participant, "bid": bid,
                "reason": reason, "ttl_seconds": BID_TTL_SECONDS,
            },
        )
        resp.raise_for_status()
        return resp.json()

    async def claim(self, *, room_id: str, message_id: str) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self.client.post(
            "/v1/claim", headers=headers,
            json={"room_id": room_id, "message_id": message_id},
        )
        if resp.status_code == 404:
            return {"claimed": False}
        # H11 fix — distinguish unrecoverable 4xx from retryable 5xx.
        # 401/403/422 are auth/policy/validation errors that won't fix
        # themselves inside the bid window; raising HTTPStatusError lets
        # the caller break the polling loop instead of burning retries.
        resp.raise_for_status()
        return resp.json()

    async def fetch_recent(
        self, *, room: str, limit: int = HEARTBEAT_HISTORY_LIMIT
    ) -> list[dict[str, Any]]:
        headers = await self._headers()
        try:
            resp = await self.client.get(
                f"/rooms/{room}/history", headers=headers, params={"limit": limit}
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("history fetch failed for %s: %s", room, exc)
            return []
        if isinstance(data, list):
            return data
        if isinstance(data, dict):
            for k in ("messages", "history", "items"):
                v = data.get(k)
                if isinstance(v, list):
                    return v
        return []

    async def post_reply(
        self, *, room: str, from_name: str,
        content: str, message_type: str = "chat",
        thread_root_id: str | None = None,
        reply_to: str | None = None,
    ) -> dict[str, Any]:
        headers = await self._headers()
        body: dict[str, Any] = {
            "from_name": from_name,
            "content": content,
            "message_type": message_type,
        }
        # Stream B: when the wake envelope lives in a thread, propagate
        # its root_id on the reply so the TUI groups parent+children.
        # ``reply_to`` is the parent-pointer; ``thread_root_id`` is the
        # conversation anchor — sending both is preferred when known.
        if reply_to:
            body["reply_to"] = reply_to
        if thread_root_id:
            body["thread_root_id"] = thread_root_id
        resp = await self.client.post(
            f"/rooms/{room}/messages", headers=headers, json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def post_social_defer(
        self, *, room_id: str, actor: str, target: str,
        ref_message_id: str | None = None, ttl_seconds: int = 300,
    ) -> dict[str, Any]:
        """Post a Stream-A `defer` social verb. Used by reflexd on bid-loss
        to broadcast "I deferred to @<winner>" through the social
        protocol so the room state-machine reflects the dependency.
        """
        headers = await self._headers()
        payload: dict[str, Any] = {"to": target, "ttl_seconds": ttl_seconds}
        if ref_message_id:
            payload["ref_message_id"] = ref_message_id
        body: dict[str, Any] = {
            "actor": actor,
            "room_id": room_id,
            "payload": payload,
        }
        if ref_message_id:
            body["ref_message_id"] = ref_message_id
        resp = await self.client.post(
            "/v1/social/defer", headers=headers, json=body,
        )
        resp.raise_for_status()
        return resp.json()

    async def post_agent_dm(
        self, *, sender: str, recipient: str, content: str,
        in_reply_to: str | None = None,
    ) -> dict[str, Any]:
        """POST /v1/dm — send an agent-to-agent direct message.

        Distinct from the human DM stream; visible only on the
        recipient's ``/stream/dm/{participant}`` channel.
        """
        headers = await self._headers()
        body: dict[str, Any] = {
            "from": sender,
            "to": recipient,
            "content": content,
        }
        if in_reply_to:
            body["in_reply_to"] = in_reply_to
        resp = await self.client.post("/v1/dm", headers=headers, json=body)
        resp.raise_for_status()
        return resp.json()

    def dm_stream_url(self, recipient: str, stream_token: str) -> str:
        """Endpoint URL for the agent-DM SSE stream (Stream B)."""
        return f"{self.relay_url}/stream/dm/{recipient}?token={stream_token}"


# ---------------------------------------------------------------------------
# SSE event parser — yields fully-buffered events
# ---------------------------------------------------------------------------


async def iter_sse_events(client: httpx.AsyncClient, url: str) -> Any:
    """Yield ``(event_name, data_dict|None)`` tuples. Mirrors the TUI parser, async."""
    async with client.stream("GET", url, timeout=httpx.Timeout(10.0, read=None)) as resp:
        resp.raise_for_status()
        event_name = ""
        data_buf: list[str] = []
        async for line in resp.aiter_lines():
            if not line:
                if event_name and data_buf:
                    try:
                        data = json.loads("\n".join(data_buf))
                    except (json.JSONDecodeError, ValueError):
                        data = None
                    yield event_name, data
                event_name = ""
                data_buf = []
                continue
            if line.startswith(":"):
                continue
            if line.startswith("event:"):
                event_name = line[len("event:"):].strip()
            elif line.startswith("data:"):
                data_buf.append(line[len("data:"):].lstrip())


# ---------------------------------------------------------------------------
# Reflexd — the daemon
# ---------------------------------------------------------------------------


@dataclass
class ReflexdConfig:
    """Runtime config snapshot — ConfigManager + env overrides."""

    relay_url: str
    api_key: str
    participant_name: str
    runtime_dir: Path = field(default_factory=lambda: DEFAULT_RUNTIME_DIR)
    log_path: Path = field(default_factory=lambda: DEFAULT_LOG_PATH)
    spawn_enabled: bool = True
    legacy_bearer: bool = False
    dry_run: bool = False
    # Escape hatch: skip the strict agent-suffix check on participant_name.
    # Power users only — bypassing causes silent mis-routing if the name
    # collides with a real chat sender (see ``is_agent_participant``).
    allow_non_agent: bool = False

    @classmethod
    def from_env(cls) -> "ReflexdConfig":
        data = ConfigManager().load() or {}
        relay_url = (
            os.environ.get("REFLEXD_RELAY_URL")
            or os.environ.get("RELAY_URL")
            or data.get("relay_url")
            or "http://localhost:8080"
        )
        # Env precedence (REFLEXD_API_KEY > API_KEY > active profile) lets the
        # operator point reflexd at a DIFFERENT identity than the active TUI
        # profile. Without it, when the user has switched their TUI to the
        # `human` profile (instance=arav), reflexd silently picks up arav's
        # key and JWT.sub becomes 'arav' — but reflexd's participant_name is
        # 'arav-claude' (the agent it's meant to wake). The /stream/token
        # endpoint enforces recipient == JWT.sub and 403s on the mismatch.
        # Concrete prod incident this prevents: 2026-05-03 00:33-00:34 UTC
        # reflexd loop emitting 403 every retry, never subscribing to SSE.
        api_key = (
            os.environ.get("REFLEXD_API_KEY")
            or os.environ.get("API_KEY")
            or data.get("api_key")
            or ""
        )
        participant = (
            os.environ.get("REFLEXD_PARTICIPANT") or data.get("instance_name") or ""
        )
        legacy_bearer = os.environ.get("REFLEXD_LEGACY_BEARER") in ("1", "true", "yes")
        if not participant:
            raise SystemExit(
                "reflexd: no participant. Set REFLEXD_PARTICIPANT "
                "or run `quorus init`."
            )
        if not api_key:
            raise SystemExit(
                "reflexd: no API key. Set REFLEXD_API_KEY or run `quorus init`."
            )
        return cls(
            relay_url=relay_url.rstrip("/"),
            api_key=api_key,
            participant_name=participant,
            legacy_bearer=legacy_bearer,
        )


class Reflexd:
    """SSE loop → triage → bid → claim → spawn → post."""

    def __init__(
        self, config: ReflexdConfig, *, adapter: HeadlessAdapter | None = None,
    ) -> None:
        # Fail closed on misconfigured participant names. Reflexd's triage
        # treats ``sender == self_name`` as a self-message and IGNOREs it,
        # so a daemon started as the human's name (e.g. "arav") silently
        # drops every chat from that human before bidding. Refuse to start
        # unless the participant ends in a recognised agent-harness suffix
        # — or the operator opted into the escape hatch.
        if not config.allow_non_agent and not is_agent_participant(
            config.participant_name
        ):
            allowed = "{claude|codex|gemini|cursor}"
            sys.stderr.write(
                "reflexd: refusing to start — participant "
                f"'{config.participant_name}' has no agent suffix. "
                f"Set REFLEXD_PARTICIPANT={config.participant_name}-{allowed} "
                "or use a name ending in one of those keywords.\n"
            )
            sys.stderr.flush()
            raise SystemExit(2)
        self.config = config
        self.adapter = adapter or HeadlessAdapter()
        self._stop = asyncio.Event()
        self._last_wake_at: float = 0.0
        # Bounded in-memory queue for messages that arrive while a busy-file
        # is set. Drained on stop, not replayed across restarts (PR-C1 scope).
        self._queue: list[dict[str, Any]] = []

    def stop(self) -> None:
        self._stop.set()

    # ── handlers ──────────────────────────────────────────────────────────

    async def handle_room_message(
        self, relay: RelayClient, envelope: dict[str, Any],
    ) -> bool:
        """Process one chat/question/request envelope.

        Phase-2 flow:
          1. v2 triage → (action, reason, kind, role, description)
          2. score bid via :func:`compute_bid_v2` against this agent's
             capability set; bid 0.0 means "not for me, skip"
          3. submit bid + poll claim
          4. on win, wake the harness with a context that includes the
             self-assignment preamble for ``open_todo`` kinds

        Returns True iff we POSTed a bid (regardless of claim outcome);
        False on IGNORE / busy-queued / capability-mismatch.
        """
        sender = envelope.get("from_name") or ""
        content = envelope.get("content") or ""
        room = envelope.get("room") or ""
        message_id = envelope.get("id") or ""
        message_type = envelope.get("message_type") or "chat"

        triage = classify_message(
            content=content, sender=sender,
            self_name=self.config.participant_name, message_type=message_type,
        )
        # PII guard: never log raw chat content (including the truncated
        # preview) at INFO. ``safe_log_summary`` returns ``len=N hash=...``
        # which is enough to correlate two log lines without persisting
        # the body. Debug mode adds a PII-redacted preview.
        logger.debug(
            "triage room=%s sender=%s id=%s %s → %s kind=%s reason=%s",
            room, sender, message_id,
            safe_log_summary(content, debug=logger.isEnabledFor(logging.DEBUG)),
            triage.action, triage.kind, triage.reason,
        )
        if triage.action != "RESPOND":
            return False

        # Anti-loop guard: if this envelope is already deep in a reply
        # chain we ourselves spawned, refuse to bid. Without this, two
        # agents in a room can ping-pong indefinitely once one of them
        # asks a trailing-? question.
        if should_break_reply_chain(envelope):
            logger.info(
                "reply chain depth >= %d — breaking room=%s id=%s",
                MAX_REPLY_DEPTH, room, message_id,
            )
            return False

        # OS-level notification: fire BEFORE bid/claim so the user gets a
        # real macOS banner (or notify-send / fallback log) even if this
        # daemon loses the auction. Per-(sender, room) rate-limit in the
        # notifications module dedupes when several reflexd instances run
        # on the same host. PII guard: the body we pass IS the chat
        # content but the notifications module never persists it — only
        # sender+room hit disk.
        try:
            from quorus.notifications import notify as _notify
            _notify(
                f"Quorus — {room or 'DM'}",
                content,
                sender=sender,
                room=room,
            )
        except Exception as exc:  # pragma: no cover — notifications are best-effort
            logger.debug("notify dispatch failed: %s", exc)

        if is_busy(self.config.participant_name, self.config.runtime_dir):
            logger.info("busy-file present, queueing wake room=%s id=%s", room, message_id)
            self._queue.append(envelope)
            if len(self._queue) > 32:
                # L29: surface the drop so operators don't lose envelopes
                # silently. Cap is 32 to bound memory while busy; older
                # entries are dropped FIFO.
                dropped = self._queue.pop(0)
                logger.warning(
                    "reflexd queue cap (32) exceeded — dropping oldest "
                    "envelope room=%s id=%s",
                    dropped.get("room") or "",
                    dropped.get("id") or "",
                )
            return False

        # Capability-aware bid scoring. Score 0.0 ⇒ this agent isn't a fit;
        # skip the bid POST entirely so we don't poison the auction with
        # zero-bids that look like real participants.
        caps = capabilities_for(
            self.config.participant_name, harness=detect_harness(self.config.participant_name),
        )
        recency = max(0.0, time.time() - self._last_wake_at) if self._last_wake_at else 0.0
        bid, bid_reason = compute_bid_v2(
            kind=triage.kind,
            role=triage.role,
            description=triage.description,
            capabilities=caps,
            recency_seconds=min(recency, 5.0),
        )
        if bid <= 0.0:
            logger.info(
                "skipping bid (no capability match) room=%s id=%s kind=%s reason=%s",
                room, message_id, triage.kind, bid_reason,
            )
            return False

        try:
            await relay.submit_bid(
                room_id=room, message_id=message_id,
                participant=self.config.participant_name,
                bid=bid, reason=bid_reason,
            )
        except httpx.HTTPError as exc:
            logger.warning("bid POST failed: %s", exc)
            return False

        # Poll claim until BID_WINDOW_SECONDS elapses or we see a winner.
        # H11 fix: 401/403/422 from the relay are unrecoverable inside the
        # bid window — break the loop and surface the error so the daemon
        # doesn't burn 8 retries on a permanent failure.
        deadline = time.time() + BID_WINDOW_SECONDS
        winner: str | None = None
        while time.time() < deadline:
            try:
                claim = await relay.claim(room_id=room, message_id=message_id)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response else 0
                if code in (401, 403, 422):
                    logger.error(
                        "claim aborted (unrecoverable %d): "
                        "room=%s id=%s detail=%s",
                        code, room, message_id, exc.response.text[:200],
                    )
                    return True
                logger.debug("claim retry (status %s): %s", code, exc)
                await asyncio.sleep(0.25)
                continue
            except (httpx.TimeoutException, httpx.ConnectError) as exc:
                logger.debug("claim retry (transport): %s", exc)
                await asyncio.sleep(0.25)
                continue
            except httpx.HTTPError as exc:
                logger.debug("claim retry: %s", exc)
                await asyncio.sleep(0.25)
                continue
            if claim.get("claimed"):
                winner = claim.get("winner")
                break
            await asyncio.sleep(0.25)

        if winner != self.config.participant_name:
            logger.info(
                "lost or no claim room=%s id=%s winner=%s",
                room, message_id, winner,
            )
            # Stream B defer-announce: post `defer(to=<winner>)` so the
            # room state machine records the dependency edge. Best-effort.
            if winner and winner != self.config.participant_name:
                try:
                    await relay.post_social_defer(
                        room_id=room,
                        actor=self.config.participant_name,
                        target=winner,
                        ref_message_id=message_id or None,
                        ttl_seconds=300,
                    )
                    logger.info(
                        "posted defer to=%s ref=%s after bid loss",
                        winner, message_id,
                    )
                except Exception as exc:
                    logger.debug("defer-announce failed (non-fatal): %s", exc)
            return True

        await self._wake_and_reply(relay, envelope, triage=triage)
        self._last_wake_at = time.time()
        return True

    async def _wake_and_reply(
        self, relay: RelayClient, envelope: dict[str, Any], *,
        triage: "TriageResult | None" = None, reason: str | None = None,
    ) -> None:
        """Render wake context, dispatch to the harness, post the reply.

        Stream B additions:
        * loads the per-room memory log into the prompt (RECENT MEMORY block)
        * inherits the wake envelope's thread_root_id on the reply post
        * appends a 1-sentence memory entry after a successful POST
        """
        room = envelope.get("room") or ""
        if not self.config.spawn_enabled:
            logger.info("spawn disabled (test mode); skipping harness call")
            return

        history = await relay.fetch_recent(
            room=room, limit=HEARTBEAT_HISTORY_LIMIT,
        )
        memory_entries: list[dict[str, Any]] = []
        try:
            memory_entries = await _mem.read_recent(
                self.config.participant_name, room, n=MEMORY_CONTEXT_LIMIT,
            )
        except Exception as exc:
            logger.debug("memory read failed: %s", exc)

        prompt = self._build_prompt(
            envelope, history, triage=triage,
            memory_entries=memory_entries,
        )
        harness = detect_harness(self.config.participant_name)
        log_reason = (triage.reason if triage else None) or reason or "?"
        logger.info(
            "waking harness=%s room=%s reason=%s memory_entries=%d",
            harness, room, log_reason, len(memory_entries),
        )
        try:
            reply = await self.adapter.run(harness, context=prompt)
        except Exception as exc:
            logger.warning("harness %s raised: %s", harness, exc)
            return

        reply = (reply or "").strip()
        if not reply:
            logger.info("harness produced empty reply, skipping post")
            return

        thread_root_id = envelope_thread_root(envelope)
        # SSE fan-out gives each recipient a per-envelope id while the
        # canonical room-history id is in `message_id`. The shared helper
        # resolves the right one so reply_to actually resolves on the relay
        # side (otherwise the room_msg_svc returns 422 — the parent isn't
        # found because envelope.id is the recipient-scoped id).
        parent_id = envelope_canonical_id(envelope)
        try:
            posted = await relay.post_reply(
                room=room, from_name=self.config.participant_name,
                content=reply,
                thread_root_id=thread_root_id,
                reply_to=parent_id if parent_id else None,
            )
            posted_id = posted.get("id") if isinstance(posted, dict) else None
            logger.info(
                "posted reply room=%s len=%d thread_root=%s id=%s",
                room, len(reply), thread_root_id, posted_id,
            )
        except httpx.HTTPError as exc:
            logger.warning("reply POST failed: %s", exc)
            return

        try:
            summary = summarise_reply_for_memory(
                envelope=envelope, reply_text=reply, triage=triage,
            )
            await _mem.append(
                self.config.participant_name, room, summary,
                extra={
                    "message_id": (
                        posted_id if isinstance(posted_id, str) else None
                    ),
                    "thread_root_id": thread_root_id,
                },
            )
        except Exception as exc:
            logger.debug("memory append failed (non-fatal): %s", exc)

    def build_prompt(
        self, envelope: dict[str, Any], history: list[dict[str, Any]],
        *, triage: "TriageResult | None" = None,
        memory_entries: list[dict[str, Any]] | None = None,
    ) -> str:
        """Public alias for :meth:`_build_prompt`.

        Smoke-test and external callers should prefer this entry point so
        the implementation can refactor freely without breaking them.
        """
        return self._build_prompt(
            envelope, history, triage=triage, memory_entries=memory_entries,
        )

    def _build_prompt(
        self, envelope: dict[str, Any], history: list[dict[str, Any]],
        *, triage: "TriageResult | None" = None,
        memory_entries: list[dict[str, Any]] | None = None,
    ) -> str:
        """Compose QOD + recent memory + history + WakeIntent.

        For ``open_todo`` kinds we *prepend* a self-assignment preamble
        (QOD rules 1 + 3) so the harness's reply naturally follows the
        plan/ship convention without needing extra prompting.

        Stream B: when *memory_entries* is non-empty we render a
        ``# RECENT MEMORY`` block immediately above the room transcript
        so the harness sees its own prior actions in the room.
        """
        recent = history[-HEARTBEAT_HISTORY_LIMIT:] if history else []
        lines = [
            f"@{m.get('from_name') or m.get('sender') or '?'}: {m.get('content') or ''}"
            for m in recent
        ]
        history_block = "\n".join(lines) if lines else "(no prior messages)"
        wake_sender = envelope.get("from_name") or "?"
        wake_content = envelope.get("content") or ""
        wake_room = envelope.get("room") or "?"
        wake_depth = envelope_reply_depth(envelope)

        # Phase-2: open_todo kinds get a self-assignment preamble that
        # tells the harness it just *picked up* this work.
        preamble = ""
        intent_blurb = (
            f"You are `{self.config.participant_name}`. You were @-mentioned in "
            f"room `{wake_room}` by `{wake_sender}`. Reply concisely (1-3 lines) "
            f"and do not run any tools that require user approval."
        )
        if triage is not None and triage.kind == "open_todo":
            preamble = self_assign_preamble(description=triage.description) + "\n"
            intent_blurb = (
                f"You are `{self.config.participant_name}`. You picked up an open "
                f"task in room `{wake_room}` posted by `{wake_sender}`. Follow the "
                f"self-assign preamble above and start with a 1-line plan."
            )
        elif triage is not None and triage.kind == "role_request":
            intent_blurb = (
                f"You are `{self.config.participant_name}`. You picked up a "
                f"role-tagged task (role={triage.role!r}) in room `{wake_room}` "
                f"posted by `{wake_sender}`. Reply concisely with a 1-line plan."
            )

        memory_block = render_memory_context(memory_entries or [])

        return (
            f"{preamble}"
            f"{render_qod_for_agent_loop()}\n\n"
            f"{memory_block}"
            f"# Wake Intent\n"
            f"{intent_blurb}\n\n"
            f"# Reply chain depth: {wake_depth} (max {MAX_REPLY_DEPTH})\n\n"
            f"# Recent room transcript (most recent last)\n{history_block}\n\n"
            f"# Triggering message\n@{wake_sender}: {wake_content}\n"
        )

    # ── main loop ─────────────────────────────────────────────────────────

    async def handle_agent_dm(
        self, _relay: RelayClient, envelope: dict[str, Any],
    ) -> None:
        """Handle a Stream B agent-DM envelope.

        Logged at INFO so the operator can see DM traffic in the daemon
        log. The default behaviour does NOT auto-reply to agent DMs —
        that's left to higher-level orchestrations. Future hooks can
        subclass and override this method to react.
        """
        sender = envelope.get("from") or envelope.get("from_name") or "?"
        recipient = envelope.get("to") or "?"
        # PII guard: don't persist DM body content. See safe_log_summary
        # docstring for the trade-off; debug-mode adds a redacted preview.
        summary = safe_log_summary(
            envelope.get("content") or "",
            debug=logger.isEnabledFor(logging.DEBUG),
        )
        logger.info(
            "agent_dm received from=%s to=%s %s",
            sender, recipient, summary,
        )

    async def _dm_loop(self, relay: RelayClient) -> None:
        """Subscribe to ``/stream/dm/{participant}``. Body lives in
        :mod:`reflexd_streamb` so this file stays under its LoC cap.
        """
        await reflexd_streamb.run_dm_loop(
            relay=relay,
            participant=self.config.participant_name,
            on_event=self.handle_agent_dm,
            stop_event=self._stop,
            iter_sse_events=iter_sse_events,
            sleep_or_stop=self._sleep_or_stop,
            sse_reconnect_s=SSE_RECONNECT_S,
            sse_reconnect_max_s=SSE_RECONNECT_MAX_S,
            log=logger,
        )

    async def run(self) -> None:
        """Connect, subscribe, and dispatch until ``stop()`` is called."""
        backoff = SSE_RECONNECT_S
        async with RelayClient(
            relay_url=self.config.relay_url,
            api_key=self.config.api_key,
            legacy_bearer=self.config.legacy_bearer,
        ) as relay:
            logger.info(
                "reflexd starting participant=%s relay=%s",
                self.config.participant_name, self.config.relay_url,
            )
            # Fail loud when the configured api_key resolves to a JWT sub
            # that doesn't match the configured participant. /stream/token
            # would 403 in an infinite retry loop with no actionable hint.
            # See ReflexdConfig.from_env() for the env precedence that
            # caused the original incident (2026-05-03 00:33 UTC).
            if not self.config.legacy_bearer:
                try:
                    bearer = (await relay.bearer_jwt()).strip()
                    if bearer:
                        import base64 as _b64
                        import json as _json
                        parts = bearer.split(".")
                        if len(parts) == 3:
                            payload_raw = parts[1] + "=" * (-len(parts[1]) % 4)
                            sub = _json.loads(
                                _b64.urlsafe_b64decode(payload_raw)
                            ).get("sub", "")
                            if sub and sub != self.config.participant_name:
                                logger.error(
                                    "reflexd: JWT sub=%r != participant=%r — "
                                    "the api_key you provided belongs to a "
                                    "DIFFERENT identity than the one this "
                                    "daemon is supposed to wake. /stream/token "
                                    "will 403 forever. Set REFLEXD_API_KEY to "
                                    "the api_key of participant %r, or change "
                                    "REFLEXD_PARTICIPANT to %r.",
                                    sub, self.config.participant_name,
                                    self.config.participant_name, sub,
                                )
                                raise SystemExit(3)
                except SystemExit:
                    raise
                except Exception as exc:
                    logger.warning(
                        "preflight JWT-sub check skipped: %s", exc,
                    )

            # Stream B: subscribe to the agent-DM stream in parallel with
            # the room SSE stream. Failures in the DM loop never bring
            # down the main loop — both reconnect independently.
            dm_task = asyncio.create_task(self._dm_loop(relay))

            try:
                while not self._stop.is_set():
                    try:
                        stream_token = await relay.mint_stream_token(self.config.participant_name)
                    except httpx.HTTPError as exc:
                        logger.warning("stream token mint failed: %s", exc)
                        await self._sleep_or_stop(backoff)
                        backoff = min(backoff * 2, SSE_RECONNECT_MAX_S)
                        continue

                    url = relay.stream_url(self.config.participant_name, stream_token)
                    try:
                        async for event_name, data in iter_sse_events(relay.client, url):
                            if self._stop.is_set():
                                return
                            await self._dispatch_event(relay, event_name, data)
                        backoff = SSE_RECONNECT_S
                    except httpx.HTTPError as exc:
                        logger.warning("sse stream dropped: %s", exc)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # pragma: no cover
                        logger.exception("sse loop unexpected error: %s", exc)

                    await self._sleep_or_stop(backoff)
                    backoff = min(backoff * 2, SSE_RECONNECT_MAX_S)
            finally:
                dm_task.cancel()
                try:
                    await dm_task
                except (asyncio.CancelledError, Exception):
                    pass

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except TimeoutError:
            # L4: 3.11+ uses the builtin TimeoutError; asyncio.TimeoutError
            # is a deprecated alias.
            return

    async def _dispatch_event(
        self, relay: RelayClient, event_name: str, data: dict[str, Any] | None,
    ) -> None:
        if not isinstance(data, dict):
            return
        if event_name == "connected":
            logger.info("sse connected")
            return
        if event_name != "message":
            return
        message_type = data.get("message_type") or "chat"
        if message_type == "wake_intent":
            # Server triage already broadcast; we still drive our own bid/claim.
            logger.debug("saw wake_intent room=%s from=%s",
                         data.get("room"), data.get("from_name"))
            return
        if message_type in {"chat", "request", "question"}:
            try:
                await self.handle_room_message(relay, data)
            except Exception as exc:  # pragma: no cover
                logger.exception("handler failed: %s", exc)


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def configure_logging(log_path: Path, *, debug: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s: %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S%z",
    ))
    root = logging.getLogger()
    root.setLevel(logging.DEBUG if debug else logging.INFO)
    if not any(isinstance(h, logging.handlers.RotatingFileHandler) for h in root.handlers):
        root.addHandler(handler)
    if debug:
        stream = logging.StreamHandler(sys.stderr)
        stream.setLevel(logging.DEBUG)
        stream.setFormatter(handler.formatter)
        root.addHandler(stream)


# ---------------------------------------------------------------------------
# CLI
# ---------------------------------------------------------------------------


def build_argparser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="reflexd",
        description=(
            "Quorus client-side AI-native notification daemon. Wakes the right "
            "agent on @-mention without keeping host harnesses open."
        ),
    )
    parser.add_argument("command", nargs="?", default="start",
                        choices=["start", "stop", "status"],
                        help="lifecycle command (default: start)")
    parser.add_argument("--participant",
                        help="override REFLEXD_PARTICIPANT / config instance_name")
    parser.add_argument("--relay-url", help="override RELAY_URL / config relay_url")
    parser.add_argument("--debug", action="store_true",
                        help="verbose stderr logging plus ~/.quorus/reflexd.log")
    parser.add_argument("--no-spawn", action="store_true",
                        help="disable harness spawn (claim-only smoke mode)")
    parser.add_argument("--dry-run", action="store_true",
                        help=("run the full pipeline but never spawn a real harness; "
                              "log what argv WOULD have been called"))
    parser.add_argument("--once", metavar="ROOM",
                        help=("trigger a synthetic wake_intent on ROOM, run one "
                              "adapter pass (honoring --dry-run), then exit. "
                              "Used by scripts/reflexd_smoke.sh in CI."))
    parser.add_argument("--allow-non-agent-participant",
                        dest="allow_non_agent_participant",
                        action="store_true",
                        help=("ESCAPE HATCH: skip the strict agent-suffix check "
                              "on participant name. Causes silent mis-routing "
                              "if the name collides with a real chat sender — "
                              "use only for testing."))
    return parser


def _pidfile() -> Path:
    return DEFAULT_RUNTIME_DIR / "reflexd.pid"


def _read_pid() -> int | None:
    p = _pidfile()
    if not p.exists():
        return None
    try:
        return int(p.read_text().strip() or "0") or None
    except (OSError, ValueError):
        return None


def cmd_status() -> int:
    pid = _read_pid()
    if pid is None:
        print("reflexd: not running")
        return 1
    try:
        os.kill(pid, 0)
    except OSError:
        print(f"reflexd: stale pidfile (pid {pid} dead) — removing")
        try:
            _pidfile().unlink()
        except OSError:
            pass
        return 1
    print(f"reflexd: running (pid {pid})")
    return 0


def cmd_stop() -> int:
    pid = _read_pid()
    if pid is None:
        print("reflexd: not running")
        return 1
    try:
        os.kill(pid, signal.SIGTERM)
    except OSError as exc:
        print(f"reflexd: failed to signal pid {pid}: {exc}")
        return 1
    print(f"reflexd: SIGTERM sent to pid {pid}")
    return 0


async def cmd_start(args: argparse.Namespace) -> int:
    cfg = _config_from_args(args)

    cfg.runtime_dir.mkdir(parents=True, exist_ok=True)
    configure_logging(cfg.log_path, debug=args.debug)

    # Probe each adapter's version once at startup so support has a paper
    # trail when a vendor flag changes. Logged to ~/.quorus/reflexd.log.
    log_adapter_versions()

    # Announce real-vs-stub mode so the operator knows immediately whether
    # the claude CLI is reachable. Auto-falls-back to stub mode when the
    # binary is missing AND REFLEXD_STUB_REPLY is not set — the daemon
    # never refuses to start. Claude Code's own OAuth handles auth; we
    # never check ANTHROPIC_API_KEY here.
    log_claude_adapter_status()

    if args.once:
        return await _run_once(cfg, args.once)

    pidfile = _pidfile()
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    daemon = Reflexd(cfg, adapter=HeadlessAdapter(dry_run=cfg.dry_run))

    loop = asyncio.get_running_loop()

    def _stop(*_a: Any) -> None:
        logger.info("signal received, stopping")
        daemon.stop()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    try:
        await daemon.run()
    finally:
        try:
            pidfile.unlink()
        except OSError:
            pass
    return 0


def _config_from_args(args: argparse.Namespace) -> ReflexdConfig:
    """Build a ReflexdConfig honoring CLI overrides.

    Pulled out of ``cmd_start`` so ``--once`` (CI smoke) and ``--dry-run`` can
    share the same env-merge logic without duplicating the participant/key
    fail-closed behaviour.
    """
    # Dry-run / --once allows zero-secret fixtures so CI can smoke without a
    # real relay. Inject placeholders ONLY when in those modes.
    if (getattr(args, "dry_run", False) or getattr(args, "once", None)):
        os.environ.setdefault("API_KEY", "dryrun-fake-key")
        os.environ.setdefault("REFLEXD_PARTICIPANT", "reflexd-smoke-claude")
        os.environ.setdefault("RELAY_URL", "http://127.0.0.1:0")
    cfg = ReflexdConfig.from_env()
    if args.participant:
        cfg.participant_name = args.participant
    if args.relay_url:
        cfg.relay_url = args.relay_url.rstrip("/")
    if args.no_spawn:
        cfg.spawn_enabled = False
    if getattr(args, "dry_run", False):
        cfg.dry_run = True
    if getattr(args, "allow_non_agent_participant", False):
        cfg.allow_non_agent = True
    return cfg


async def _run_once(cfg: ReflexdConfig, room: str) -> int:
    """Drive a single synthetic wake_intent through the adapter and exit.

    Used by ``scripts/reflexd_smoke.sh`` so CI can prove the adapter wiring
    is sound without a real relay or a real harness binary. We bypass the
    SSE/bid/claim lane intentionally — the contract under test here is
    "given an envelope, the daemon picks the right harness and we have a
    record of the argv that would have been spawned".
    """
    adapter = HeadlessAdapter(dry_run=True)
    harness = detect_harness(cfg.participant_name)
    envelope = {
        "id": "smoke-msg-1",
        "from_name": "smoke-driver",
        "to": cfg.participant_name,
        "room": room,
        "content": f"@{cfg.participant_name} smoke: dry-run wake_intent",
        "message_type": "chat",
    }
    daemon = Reflexd(cfg, adapter=adapter)
    prompt = daemon.build_prompt(envelope, history=[])
    reply = await adapter.run(harness, context=prompt)
    record = adapter.last_dry_run or {}
    print(json.dumps({
        "ok": True,
        "harness": harness,
        "room": room,
        "participant": cfg.participant_name,
        "dry_run": True,
        "argv_recorded": record.get("argv"),
        "reply_marker": reply,
    }, default=str))
    logger.info(
        "once_done harness=%s room=%s argv_recorded=%s",
        harness, room, record.get("argv"),
    )
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = build_argparser()
    args = parser.parse_args(argv)

    if args.command == "status":
        return cmd_status()
    if args.command == "stop":
        return cmd_stop()
    # start (default)
    try:
        return asyncio.run(cmd_start(args))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    raise SystemExit(main())
