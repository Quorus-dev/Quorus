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
import random
import re
import shutil
import signal
import subprocess
import sys
import time
from dataclasses import dataclass, field
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

import httpx

# scripts/ is not a package; add the repo root so ``quorus`` imports work when
# this file is run directly via ``python scripts/reflexd.py``.
_REPO_ROOT = Path(__file__).resolve().parent.parent
# Only a source checkout needs these sys.path entries. Inside an installed
# wheel (quorus/_reflexd/) the parent is site-packages/quorus, and putting
# THAT on sys.path would let quorus's submodules shadow top-level imports.
_IN_CHECKOUT = (_REPO_ROOT / "pyproject.toml").exists()
if _IN_CHECKOUT:
    for _path in (
        _REPO_ROOT,
        _REPO_ROOT / "packages" / "sdk",
        _REPO_ROOT / "packages" / "cli",
        _REPO_ROOT / "packages" / "mcp",
        _REPO_ROOT / "packages" / "tui",
    ):
        if _path.exists() and str(_path) not in sys.path:
            sys.path.insert(0, str(_path))

# Phase 2 triage v2 lives in a sibling module so reflexd.py stays under its
# 1500-LoC cap. We import via a path-aware loader because ``scripts/`` is
# not a package — same dance the tests use to import reflexd itself.
import importlib.util as _ilu  # noqa: E402

from quorus.config import ConfigManager  # noqa: E402
from quorus.notifications import agent_events as _agent_events  # noqa: E402
from quorus.notifications import notify as _notify  # noqa: E402
from quorus.observability.redaction import env_secret_values, redact_secrets  # noqa: E402
from quorus.operating_discipline import render_qod_for_agent_loop  # noqa: E402
from quorus.private_files import write_private_json, write_private_text  # noqa: E402
from quorus.roles import WRITER_ROLES  # noqa: E402
from quorus.runtime import commit_evidence as _commit_evidence  # noqa: E402  audit F2
from quorus.runtime import failover as _failover  # noqa: E402
from quorus.runtime import memory as _mem  # noqa: E402  Stream B
from quorus.runtime import owner_pin as _owner_pin  # noqa: E402  invitee agents
from quorus.runtime import review_share as _review_share  # noqa: E402
from quorus.runtime import track_record as _track  # noqa: E402  L2 bids
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
# D1 (WAKE_REBUILD_SPEC): room → workspace bindings. Host-local by design —
# a filesystem path only means something on THIS machine, so bindings are
# daemon-local, not shared room state. Written by `quorus room bind`.
ROOM_BINDINGS_PATH = Path.home() / ".quorus" / "room-bindings.json"


def workspace_for(room: str, *, bindings_path: Path | None = None) -> Path | None:
    """Resolve the bound workspace directory for *room*, or None.

    Accepts bindings keyed by room name or room id. Returns the path only
    when it exists and is a directory — a stale binding must degrade to
    the unbound behaviour, never crash a wake.
    """
    path = bindings_path or ROOM_BINDINGS_PATH
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    raw = data.get(room) if isinstance(data, dict) else None
    if isinstance(raw, dict):  # {"path": ..., "mode": ...} (room bind --mode)
        raw = raw.get("path")
    if not raw or not isinstance(raw, str):
        return None
    ws = Path(raw).expanduser()
    if not ws.is_dir():
        logger.warning("room binding for %r points at missing dir %s", room, ws)
        return None
    return ws


def mode_for(room: str, *, bindings_path: Path | None = None) -> str:
    """Permission mode the owner chose for *room* (default | manual | autonomous)."""
    try:
        data = json.loads((bindings_path or ROOM_BINDINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "default"
    raw = data.get(room) if isinstance(data, dict) else None
    mode = raw.get("mode") if isinstance(raw, dict) else None
    return mode if mode in reflexd_wake.MODES else "default"


ROOM_LEADS_PATH = Path.home() / ".quorus" / "room-leads.json"
PACING_PATH = Path.home() / ".quorus" / "pacing.json"
PACE_WINDOW_S = 3600.0
MAX_WAKE_ATTEMPTS = 2
GEMINI_TIMEOUT_S = 10 * 60  # its quiet internal retries hung one wake for 55 min


def pace_for(participant: str, *, path: Path | None = None) -> int | None:
    """Turns per hour this agent may take (`quorus agent pace`), or None."""
    try:
        data = json.loads((path or PACING_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    cap = (data.get(participant) or {}).get("per_hour") if isinstance(data, dict) else None
    return int(cap) if isinstance(cap, (int, float)) and cap > 0 else None


def pace_wait_s(starts: list[float], cap: int, now: float) -> float:
    """Seconds until another turn fits under *cap* per rolling hour."""
    recent = sorted(t for t in starts if now - t < PACE_WINDOW_S)
    if len(recent) < cap:
        return 0.0
    return recent[len(recent) - cap] + PACE_WINDOW_S - now
SILENCE_STATUS_EVERY_S = 10 * 60
ANSWERED_MAX = 500
PAUSE_NOTICE = "(quorus) agents paused"
CHATTER_NOTICE = "(quorus) agents stopped answering unaddressed chatter"


def _pause_announced(recent: list[dict[str, Any]], marker: str = PAUSE_NOTICE) -> bool:
    """Has this notice been posted since the last human message?"""
    for m in reversed(recent):
        body = m.get("content") or ""
        # A full-pause notice also covers chatter; any notice by a teammate on
        # another machine counts (three agents posted one each, 2026-10-17).
        if marker in body or (marker == CHATTER_NOTICE and PAUSE_NOTICE in body):
            return True
        if not is_agent_participant(m.get("from_name") or ""):
            return False
    return False


def trust_for(room: str, *, bindings_path: Path | None = None) -> str:
    """Who may trigger write-capable turns in *room*: "owner" (default) or "members"."""
    try:
        data = json.loads((bindings_path or ROOM_BINDINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return "owner"
    raw = data.get(room) if isinstance(data, dict) else None
    trust = raw.get("trust") if isinstance(raw, dict) else None
    return "members" if trust == "members" else "owner"


def share_for(room: str, *, bindings_path: Path | None = None) -> bool:
    """Owner opted *room* into attaching review bundles (``"share": true``)."""
    try:
        data = json.loads((bindings_path or ROOM_BINDINGS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return False
    raw = data.get(room) if isinstance(data, dict) else None
    return isinstance(raw, dict) and raw.get("share") is True


def agent_owner(participant: str) -> str:
    """The human who owns this agent: REFLEXD_OWNER, else the name's first part."""
    return (os.environ.get("REFLEXD_OWNER") or "").strip() or participant.split("-", 1)[0]


def trusted_sender(sender: str, owner: str) -> bool:
    """The owner, or one of the owner's own agents (``<owner>-...``)."""
    return bool(sender) and (sender == owner or sender.startswith(f"{owner}-"))


TRUST_CHAIN_LIMIT = 100  # messages searched for the request an agent's message serves


def trusted_origin(envelope: dict[str, Any], history: list[dict[str, Any]], owner: str) -> bool:
    """Whether this message serves the owner's own request.

    An owner's agent carries the trust of whoever it is working for, not of
    its name: a stranger given a read-only turn could otherwise have that
    agent ask a teammate to do the work and get it done write-capable. Walk
    the reply chain through the owner's agents to the first human; only the
    owner counts. A chain that leaves the history, or never reaches a human
    (a mid-turn message carries no reply_to), is not trusted."""
    sender = envelope.get("from_name") or ""
    if not trusted_sender(sender, owner):
        return False
    if sender == owner:
        return True
    by_id = {m.get("id"): m for m in history if m.get("id")}
    cur, seen = envelope.get("reply_to"), set()
    while cur and cur in by_id and cur not in seen:
        seen.add(cur)
        who = by_id[cur].get("from_name") or ""
        if not trusted_sender(who, owner):
            return False
        if who == owner:
            return True
        cur = by_id[cur].get("reply_to")
    return False


def _answer_only(triage: Any, envelope: dict[str, Any], me: str, lead: str | None) -> bool:
    """A non-lead agent woken by a human's message to the whole room."""
    if not lead or lead == me or triage is None or triage.kind != "question":
        return False
    return not is_agent_participant(envelope.get("from_name") or "")


def lead_for(room: str, *, leads_path: Path | None = None) -> str | None:
    """The agent that leads *room* (`quorus agent add ... --lead`), or None."""
    try:
        data = json.loads((leads_path or ROOM_LEADS_PATH).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    lead = data.get(room) if isinstance(data, dict) else None
    return lead if isinstance(lead, str) and lead else None


def live_session_for_workspace(ws: Path | None) -> dict[str, Any] | None:
    """L1/L2: is a human's interactive Claude session already open on this
    room's workspace? If so its Stop hook delivers the room's messages in
    full context, so we must NOT cold-spawn a competing headless agent
    (that would double-reply). Returns the registry entry or None."""
    if ws is None:
        return None
    try:
        from quorus_cli import live_sessions as _ls

        return _ls.find_for_cwd(str(ws))
    except Exception as exc:
        logger.debug("live-session lookup failed: %s", exc)
        return None


# D2 (WAKE_REBUILD_SPEC): room → harness-session map, so every wake resumes
# the same conversation and the agent keeps its memory across wakes, sleeps,
# and daemon restarts. One file per participant, 0600.

def _sessions_path(participant: str) -> Path:
    return Path.home() / ".quorus" / f"sessions-{participant}.json"


def session_for(participant: str, room: str, harness: str | None = None) -> str | None:
    """Stored session id for this room, or None.

    ``harness`` guards against handing a claude session id to codex (or the
    reverse) after a participant is re-pointed at a different CLI — the ids
    are not interchangeable and a wrong one wastes a whole wake on a resume
    failure.
    """
    try:
        data = json.loads(_sessions_path(participant).read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    entry = data.get(room) if isinstance(data, dict) else None
    if not isinstance(entry, dict) or not isinstance(entry.get("session_id"), str):
        return None
    if harness and entry.get("harness") and entry["harness"] != harness:
        return None
    return entry["session_id"]


def remember_session(
    participant: str, room: str, session_id: str, harness: str = "claude",
) -> None:
    path = _sessions_path(participant)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            data = {}
    except (OSError, json.JSONDecodeError):
        data = {}
    data[room] = {
        "harness": harness,
        "session_id": session_id,
        "last_used": datetime.now(timezone.utc).isoformat(),
    }
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, "w", encoding="utf-8") as f:
            json.dump(data, f, indent=2)
    except OSError as exc:
        logger.warning("could not persist session map: %s", exc)


def forget_session(participant: str, room: str) -> None:
    path = _sessions_path(participant)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return
    if isinstance(data, dict) and room in data:
        del data[room]
        try:
            fd = os.open(path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except OSError:
            pass
LOG_MAX_BYTES = 10 * 1024 * 1024  # 10MB rotation per spec
LOG_BACKUP_COUNT = 3
SSE_RECONNECT_S = 2.0
SSE_RECONNECT_MAX_S = 30.0
# How long the relay keeps bidding open, and how long we wait for the
# award. The relay now holds the claim until the window CLOSES (an auction
# that awards on first-arrival is not an auction), so the TTL must be
# comfortably shorter than the poll deadline — otherwise every claim gets
# 425 until we give up and nobody answers at all.
BID_TTL_SECONDS = 1
BID_WINDOW_SECONDS = max(3.0, BID_TTL_SECONDS + 2.0)
# D5 (WAKE_REBUILD_SPEC): two wake classes with different budgets.
# Chat wakes (mention/question, no claimed task) are bounded tight; mission
# wakes (the agent holds an active work-queue claim in the room) get a long
# leash and an ESCALATION message instead of a bare failure sentinel —
# never silently kill a working agent (the Arav rule, D5b).
# Real coding wakes (read, edit, run tests, commit) took 2-6 min live; 300s
# killed them mid-task. 30 min default, still env-tunable.
SUBPROCESS_TIMEOUT_S = int(os.environ.get("REFLEXD_SUBPROCESS_TIMEOUT_S", "1800"))
# Bounded memory of handled message ids — enough to cover a reconnect
# backlog without growing forever.
_HANDLED_ID_CAP = 2000
CHAT_MAX_TURNS = int(os.environ.get("REFLEXD_CHAT_MAX_TURNS", "60"))
MISSION_TIMEOUT_S = int(os.environ.get("REFLEXD_MISSION_TIMEOUT_S", "3600"))
HEARTBEAT_HISTORY_LIMIT = 10
# At most one "is stuck" desktop banner per agent this often.
STUCK_ALERT_EVERY_S = 3600.0
# After the relay says our name is claimed but this machine has no key for it,
# don't ask again for this long (a key that appears meanwhile is used at once).
# Retrying on every stream reconnect burned the relay's per-IP claim budget and
# locked every agent on the machine out for an hour (release check, 2026-10-17).
CLAIM_BACKOFF_S = 600.0
# A free opencode model that prints nothing for this long is dropped for the
# turn and the next free model takes it (release check: nemotron sat 111-300s
# before its first token while beam answered in 1.2s). Paid and local models
# never get this deadline: a local Ollama model can rightly take minutes.
FREE_FIRST_EVENT_S = 60.0
# All free models slow at once: pause wakes this long, like a rate limit.
SLOW_COOLDOWN_S = 15 * 60.0
# Live steps: at most one presence post this often while a wake runs.
STEP_POST_MIN_S = 3.0
# R2: presence heartbeat cadence. Relay classifies away after ~90s silence,
# so 30s gives three missed beats of slack. Env-tunable for tests.
HEARTBEAT_INTERVAL_S = float(os.environ.get("REFLEXD_HEARTBEAT_S", "30"))
DRAIN_INTERVAL_S = float(os.environ.get("REFLEXD_DRAIN_INTERVAL_S", "120"))
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

# Wake contract (tools for the woken agent, codex parser, prompt) — sibling
# module, same loader pattern. See reflexd_wake.py for the proven gaps.
_WAKE_PATH = Path(__file__).resolve().parent / "reflexd_wake.py"
_wake_spec = _ilu.spec_from_file_location("reflexd_wake", _WAKE_PATH)
assert _wake_spec is not None and _wake_spec.loader is not None
reflexd_wake = _ilu.module_from_spec(_wake_spec)
sys.modules.setdefault("reflexd_wake", reflexd_wake)
_wake_spec.loader.exec_module(reflexd_wake)
_READONLY_PATH = Path(__file__).resolve().parent / "reflexd_readonly.py"
_readonly_spec = _ilu.spec_from_file_location("reflexd_readonly", _READONLY_PATH)
assert _readonly_spec is not None and _readonly_spec.loader is not None
reflexd_readonly = _ilu.module_from_spec(_readonly_spec)
sys.modules.setdefault("reflexd_readonly", reflexd_readonly)
_readonly_spec.loader.exec_module(reflexd_readonly)
_UNFINISHED_PATH = Path(__file__).resolve().parent / "reflexd_unfinished.py"
_unfinished_spec = _ilu.spec_from_file_location("reflexd_unfinished", _UNFINISHED_PATH)
assert _unfinished_spec is not None and _unfinished_spec.loader is not None
reflexd_unfinished = _ilu.module_from_spec(_unfinished_spec)
sys.modules.setdefault("reflexd_unfinished", reflexd_unfinished)
_unfinished_spec.loader.exec_module(reflexd_unfinished)
_HELD_PATH = Path(__file__).resolve().parent / "reflexd_held.py"
_held_spec = _ilu.spec_from_file_location("reflexd_held", _HELD_PATH)
assert _held_spec is not None and _held_spec.loader is not None
reflexd_held = _ilu.module_from_spec(_held_spec)
sys.modules.setdefault("reflexd_held", reflexd_held)
_held_spec.loader.exec_module(reflexd_held)
_MODELS_PATH = Path(__file__).resolve().parent / "reflexd_models.py"
_models_spec = _ilu.spec_from_file_location("reflexd_models", _MODELS_PATH)
assert _models_spec is not None and _models_spec.loader is not None
reflexd_models = _ilu.module_from_spec(_models_spec)
sys.modules.setdefault("reflexd_models", reflexd_models)
_models_spec.loader.exec_module(reflexd_models)
_LIMITS_PATH = Path(__file__).resolve().parent / "reflexd_limits.py"
_limits_spec = _ilu.spec_from_file_location("reflexd_limits", _LIMITS_PATH)
assert _limits_spec is not None and _limits_spec.loader is not None
reflexd_limits = _ilu.module_from_spec(_limits_spec)
sys.modules.setdefault("reflexd_limits", reflexd_limits)
_limits_spec.loader.exec_module(reflexd_limits)
_STEPS_PATH = Path(__file__).resolve().parent / "reflexd_steps.py"
_steps_spec = _ilu.spec_from_file_location("reflexd_steps", _STEPS_PATH)
assert _steps_spec is not None and _steps_spec.loader is not None
reflexd_steps = _ilu.module_from_spec(_steps_spec)
sys.modules.setdefault("reflexd_steps", reflexd_steps)
_steps_spec.loader.exec_module(reflexd_steps)
# A stream line longer than this is a tool result (file contents), not a step.
STEP_LINE_MAX = 256 * 1024


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


def build_claude_argv(
    context: str, *, resume: str | None = None, max_turns: int | None = None,
    extra: list[str] | None = None,
) -> list[str]:
    """Pinned argv shape for Claude Code CLI.

    Contract: ``claude --print --output-format stream-json --verbose
    [--resume <sid>] -- <ctx>``.

    ``--print`` (alias ``-p``) is the headless / non-interactive mode in the
    Claude Code CLI (verified on v2.1.126). Auth is whatever ``claude /login``
    set up — we never read ``ANTHROPIC_API_KEY``.

    D2 (WAKE_REBUILD_SPEC): ``--output-format json`` so we can capture the
    ``session_id`` from the result envelope and persist a room→session map;
    ``--resume <sid>`` (explicit id — never ``--continue`` in automation)
    makes the next wake for the same room CONTINUE the same conversation, so
    the agent remembers its prior work instead of cold-starting amnesiac.

    Argv-injection guard: ``--`` separates options from positional args so
    a chat body that starts with a dash (e.g. ``"-config /etc/passwd"``) is
    parsed as the prompt, not as a CLI flag.
    """
    # stream-json (it requires --verbose with --print) streams each tool call
    # as it happens, for the live "editing relay.py" step; the reply and the
    # session id arrive in the final ``result`` line.
    argv = [CLAUDE_BIN, "--print", "--output-format", "stream-json", "--verbose"]
    if max_turns:
        argv += ["--max-turns", str(max_turns)]
    if resume:
        argv += ["--resume", resume]
    if extra:
        argv += extra
    return argv + ["--", context]


def _parse_claude_json(out: str) -> tuple[str, str | None]:
    """Parse ``claude --print --output-format json`` output.

    Returns ``(reply_text, session_id)``. Degrades to the raw text with no
    session id when the output isn't the expected JSON envelope (older CLI,
    plain-text mode, or a crash message). Accepts the single ``json`` envelope
    or a ``stream-json`` stream, whose last ``result`` line is that envelope.
    """
    try:
        data = json.loads(out)
    except (json.JSONDecodeError, TypeError):
        data = None
        for line in reversed((out or "").splitlines()):
            if line.startswith("{") and '"result"' in line:
                try:
                    ev = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(ev, dict) and ev.get("type") == "result":
                    data = ev
                    break
        if data is None:
            return (out or "").strip(), None
    if isinstance(data, dict):
        text = data.get("result")
        sid = data.get("session_id")
        if data.get("is_error") is True:
            # Claude's result envelope can carry an API error even with
            # exit status 0. Only explicit errors are diagnosed: an ordinary
            # model answer discussing quota must never trigger failover.
            errors = [text] if isinstance(text, str) else []
            if isinstance(data.get("errors"), list):
                errors.extend(e for e in data["errors"] if isinstance(e, str))
            diagnosis = (diagnose_harness_failure("claude", "\n".join(errors))
                         or vendor_error("claude", json.dumps({**data, "type": "result"}), ""))
            return diagnosis or "[reflexd] harness errored", sid if isinstance(sid, str) else None
        if isinstance(text, str):
            return text.strip(), sid if isinstance(sid, str) else None
    return out.strip(), None


def build_codex_argv(
    context: str, *, resume: str | None = None, extra: list[str] | None = None,
) -> list[str]:
    """Pinned argv shape for codex CLI.

    Contract: ``codex exec --json --skip-git-repo-check [resume <id>] -- <ctx>``.

    ``--skip-git-repo-check`` is required: codex refuses to run with
    "Not inside a trusted directory" when woken in a directory it has not
    been trusted for (verified live on v0.132.0, 2026-08-21) — which is
    every fresh workspace binding.

    D2 parity: ``codex exec resume <SESSION_ID> <PROMPT>`` continues a prior
    conversation. The id comes from the ``thread.started`` event's
    ``thread_id`` field in the ``--json`` stream (verified live).

    ``codex exec`` takes the prompt as a positional [PROMPT] arg (verified on
    OpenAI Codex v0.128.0). The ``--prompt`` flag does NOT exist; the legacy
    shape ``codex exec --json --prompt <ctx>`` was broken in production
    because codex rejects ``--prompt`` as ``unexpected argument``.

    Argv-injection guard: ``--`` separates options from the positional
    prompt so a leading-dash chat body cannot be re-interpreted as a flag.
    """
    if resume:
        # Options go AFTER `resume`: they belong to the subcommand. Placed
        # before it, the resumed session kept its stored sandbox/config.
        return [CODEX_BIN, "exec", "resume", "--json", "--skip-git-repo-check",
                *(extra or []), resume, "--", context]
    return [CODEX_BIN, "exec", "--json", "--skip-git-repo-check", *(extra or []),
            "--", context]


def build_gemini_argv(context: str, *, extra: list[str] | None = None) -> list[str]:
    """Pinned argv shape for gemini CLI.

    Contract: ``gemini --prompt=<ctx>``.

    Argv-injection guard: gemini parses ``--prompt --version`` as the
    ``--version`` flag (verified). It also rejects ``--prompt -- --version``
    with "Not enough arguments following: prompt". The safe form is the
    ``--key=value`` shape, which keeps the value attached to the flag and
    out of the option-parser's consideration.
    """
    return [GEMINI_BIN, *(extra or []), f"--prompt={context}"]


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


def build_opencode_argv(context: str, *, extra: list[str] | None = None) -> list[str]:
    """Pinned argv shape for opencode (`opencode.ai/docs/cli/`).

    Contract: ``opencode run -- <ctx>``.

    ``opencode run`` is the documented one-shot / non-interactive entry
    point. The prompt is positional, so ``--`` separates it from any
    options the user might leak into the body. Auth is whatever
    ``opencode auth login`` set up — reflexd never reads provider keys.
    """
    return [OPENCODE_BIN, "run", *(extra or []), "--", context]


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
# D3: the auto-fallback flag is gone (stub is explicit-env only). The
# resetter stays as a no-op so older tests/callers don't break.
_AUTO_FALLBACK_TO_STUB = False


def reset_auto_fallback() -> None:
    """Compat no-op — the stub auto-fallback was removed (spec D3)."""


def is_stub_mode() -> bool:
    """True iff the adapter should reply with the stub instead of the CLI.

    D3 (WAKE_REBUILD_SPEC): stub replies happen ONLY on the explicit env
    flag. A missing harness binary must produce an honest room error via
    the subprocess preflight ("<bin> not installed on this host"), never a
    fake "(reflexd-stub) on it…" acknowledgment that looks like progress.
    """
    return os.environ.get("REFLEXD_STUB_REPLY") in ("1", "true", "yes")


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
    * ``"missing"`` — claude binary is missing AND the explicit stub flag
      is not set. D3: the daemon still starts (other harnesses may be
      fine) but logs at ERROR and every wake posts an honest
      "not installed on this host" message to the room. There is NO
      silent stub fallback any more.
    """
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
    # claude binary missing. D3: no silent stub fallback — wakes will post
    # an honest "claude not installed on this host" error to the room via
    # the subprocess preflight. Fake acknowledgments erode trust faster
    # than visible errors (observed May 2026: stub replies read as the
    # product being broken).
    logger.error(
        "reflexd: claude CLI not found on PATH and REFLEXD_STUB_REPLY not set. "
        "Wakes for this harness will post an honest install error to the "
        "room. To enable real Claude replies: install Claude Code "
        "(https://claude.ai/code) and run `claude /login`; for demos set "
        "REFLEXD_STUB_REPLY=1 explicitly."
    )
    return "missing"


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


# Known vendor-side failures, newest first. A generic "[reflexd] harness
# errored" tells a room nothing; these turn the harness's own stderr into a
# line a human can act on. Patterns are lowercase substrings matched against
# stderr — deliberately narrow so an unknown failure stays generic rather
# than being mislabelled.
# opencode only (Codex: other harnesses must not get opencode advice). 1.x refusing
# the 2.x permission key made every wake "exit 1 with no known cause" (0.6.3 audit,
# BUG 22). Named here instead of quoting stderr, which can echo secrets.
_OPENCODE_DIAGNOSTICS: tuple[tuple[str, str], ...] = (
    (
        "v2 permissions are not supported",
        "this opencode is 1.x and refused Quorus's permission settings. Upgrade to "
        "opencode 2 (`brew install opencode` or `npm i -g @opencode/cli`; npm's "
        "`opencode-ai` package is still 1.x), then mention me again",
    ),
    (
        "configuration is invalid",
        "opencode refused its configuration. Check `opencode --version` (Quorus is "
        "tested on opencode 2) and any opencode.json in this repo, then mention me again",
    ),
)

_HARNESS_DIAGNOSTICS: tuple[tuple[str, str], ...] = (
    (
        "ineligibletiererror",
        "Gemini CLI is no longer supported on this account tier "
        "(Google moved individual Code Assist to Antigravity). This agent "
        "cannot wake until its login is migrated or switched.",
    ),
    (
        "is no longer available to new users",
        "this Gemini CLI asks for a model Google retired. Update it: "
        "`npm i -g @google/gemini-cli@latest` (gemini-cli 0.40 hit this, 0.64 works).",
    ),
    (
        "not inside a trusted directory",
        "codex refused to run: the bound workspace is not a trusted "
        "directory. Reflexd passes --skip-git-repo-check; if you see this, "
        "check the workspace path bound to this room.",
    ),
    ("please run /login", "not logged in — run `claude /login` on this host."),
    ("not logged in", "not logged in — run the harness's login command on this host."),
    ("rate limit", "the vendor rate-limited this account; the wake was dropped."),
    ("usage limit", "the vendor reports the account's usage limit has been reached."),
    (
        "exhausted your daily quota",
        "hit the free tier's daily limit on this model (Gemini's default allows "
        "about 20 requests a day). Switch to a lighter model: `quorus agent "
        "remove <agent>` then add it again with `--model gemini-3.5-flash-lite`.",
    ),
    ("quota", "the vendor reports the account is out of quota."),
)


_LIMIT_RE = re.compile(r"quota|rate.?limit|usage limit|resource_exhausted", re.IGNORECASE)
LIMIT_DEFAULT_S = reflexd_limits.LIMIT_DEFAULT_S
LIMIT_MAX_S = reflexd_limits.LIMIT_MAX_S


def _human_wait(seconds: float) -> str:
    minutes = max(1, round(seconds / 60))
    if minutes < 90:
        return f"{minutes} min"
    hours = minutes / 60
    return f"{hours:.0f} h" if hours >= 10 else f"{hours:.1f} h".replace(".0 h", " h")


def _is_limit(stderr: str) -> bool:
    return bool(_LIMIT_RE.search(stderr or ""))


def _retry_after_s(stderr: str, now: datetime | None = None) -> float:
    """Vendor's "retry in 21h39m46s" or Codex's "try again at 1:46 AM" →
    how long Quorus pauses (capped); default 30 min. The vendor's exact
    reset is the reading `reflexd_limits.read_limit_message` returns with it."""
    return reflexd_limits.read_limit_message(stderr, time.time(), local_now=now)[1]


def diagnose_harness_failure(harness: str, stderr: str) -> str | None:
    """Map a harness's stderr to an actionable room message, or None.

    Returning None means "we do not recognise this" — the caller keeps the
    generic sentinel rather than inventing a cause.
    """
    low = (stderr or "").lower()
    if Path(harness or "").name == "opencode":
        for needle, message in _OPENCODE_DIAGNOSTICS:
            if needle in low:
                return f"⚠ {harness}: {message}"
    for needle, message in _HARNESS_DIAGNOSTICS:
        if needle in low:
            text = f"⚠ {harness}: {message}"
            category = {"quota": "quota", "usage limit": "quota", "rate limit": "rate_limit",
                        "please run /login": "login", "not logged in": "login",
                        "ineligibletiererror": "login"}.get(needle)
            return _failover.VendorFailure(text, category) if category else text
    return None


_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
# A model the vendor can't serve: say which key is missing, not just "exit 1".
_NO_ROUTE_RE = re.compile(r"provider\.no-route|model unavailable|unrecognized_model|"
                          r"issue with the selected model|"
                          r"models?\b.{0,60}not (?:found|supported)", re.IGNORECASE)
_NO_ROUTE_HINT = {
    "opencode": " Check the name with `opencode models`, or set the provider's key: "
                "`opencode auth login`, or XAI_API_KEY / OPENROUTER_API_KEY.",
}


def _json_error_message(value: Any) -> str | None:
    """The innermost ``message`` in a vendor error, which may itself be JSON
    in a string (codex wraps the API's error that way)."""
    for _ in range(5):
        if isinstance(value, str):
            text = value.strip()
            if not text.startswith("{"):
                return text or None
            try:
                value = json.loads(text)
            except json.JSONDecodeError:
                return text
            continue
        if not isinstance(value, dict):
            return None
        if value.get("error") and isinstance(value["error"], (dict, str)):
            value = value["error"]
            continue
        data = value.get("data")
        value = value.get("message") or (data.get("message") if isinstance(data, dict) else None)
        if value is None:
            return None
    return None


def vendor_error(harness: str, stdout: str, stderr: str) -> str | None:
    """The vendor's own words when it can't run the model, as a room line, or None.

    "exited 1 with no known cause" told nobody that Grok needs an xAI key
    (release check, 2026-10-17). Only bad-model / no-route errors are quoted:
    anything else stays generic, because harness output can echo credentials
    or tool inputs and must not reach the room (test_reflexd_harness_failure).
    Error events on stdout (opencode, codex, claude's result envelope) win;
    else the last matching stderr line. Control characters are stripped, the
    line is capped, and the post is secret-redacted downstream like any reply.
    """
    found: str | None = None
    for line in (stdout or "").splitlines():
        line = line.strip()
        if not line.startswith("{"):
            continue
        try:
            ev = json.loads(line)
        except json.JSONDecodeError:
            continue
        if not isinstance(ev, dict):
            continue
        if ev.get("type") in ("error", "turn.failed"):
            found = _json_error_message(ev.get("error") or ev.get("message")) or found
        elif ev.get("type") == "result" and ev.get("is_error") is True:
            found = _json_error_message(ev.get("result")) or found
        elif ev.get("type") == "result" and ev.get("status") == "error":  # gemini stream-json
            said = _json_error_message(ev.get("error"))
            found = reflexd_models.trim_gemini_error(said) if said else found
    if not found:
        lines = [ln.strip() for ln in _ANSI_RE.sub("", stderr or "").splitlines() if ln.strip()]
        found = next((ln for ln in reversed(lines) if _NO_ROUTE_RE.search(ln)), None)
    if not found or not _NO_ROUTE_RE.search(found):
        return None
    text = "".join(ch for ch in _ANSI_RE.sub("", found) if ch.isprintable())[:200].strip()
    hint = _NO_ROUTE_HINT.get(harness, " Check the model name and that this CLI can use it.")
    return f"⚠ {harness}: {text.rstrip('.')}.{hint}"


async def ollama_problem(model: str, *, timeout: float = 3.0,
                         transport: httpx.AsyncBaseTransport | None = None) -> str | None:
    """Why a local Ollama model can't answer right now, or None.

    With Ollama stopped, opencode waited ~3 minutes and the room got "exited 1
    with no known cause" (release check, 2026-10-17). One local GET first.
    """
    host = os.environ.get("OLLAMA_HOST") or "127.0.0.1:11434"
    base = host if host.startswith("http") else f"http://{host}"
    try:
        async with httpx.AsyncClient(timeout=timeout, transport=transport) as client:
            resp = await client.get(f"{base.rstrip('/')}/api/tags")
            resp.raise_for_status()
            names = {m.get("name") for m in resp.json().get("models", [])
                     if isinstance(m, dict)}
    except (httpx.HTTPError, ValueError, AttributeError):
        return ("Ollama isn't running: start it with "
                "`OLLAMA_CONTEXT_LENGTH=32768 ollama serve`.")
    want = model if ":" in model else f"{model}:latest"
    if want not in names and model not in names:  # an empty catalog lacks it too (codex)
        return f"Ollama doesn't have {model}: `ollama pull {model}`."
    return None


def _parse_codex_stream(out: str) -> tuple[str, str | None]:
    """Parse codex ``--json`` NDJSON → ``(reply_text, thread_id)``.

    Delegates to :func:`reflexd_wake.parse_codex_stream`, which reads the
    real ``item.completed``/``agent_message`` shape (the old top-level-key
    parse dropped every codex reply).
    """
    return reflexd_wake.parse_codex_stream(out)


def _parse_codex_json(out: str) -> str:
    """Back-compat wrapper — reply text only."""
    return _parse_codex_stream(out)[0]


def _ts_after(ts: Any, start: datetime) -> bool:
    """True iff ISO timestamp *ts* is at/after *start* (unparseable → False)."""
    if not isinstance(ts, str):
        return False
    try:
        t = datetime.fromisoformat(ts.replace("Z", "+00:00"))
    except ValueError:
        return False
    if t.tzinfo is None:
        t = t.replace(tzinfo=timezone.utc)
    return t >= start


_MENTION_RE = re.compile(r"(?<![\w-])@([A-Za-z][\w-]*)")


def _undelivered_lines(reply: str, posted: list[str]) -> list[str]:
    """Lines of *reply* the room hasn't seen that matter for the loop: ones
    @-mentioning someone the agent's own posts never mentioned, and the
    daemon's ``[quorus]`` status lines."""
    seen = {m for text in posted for m in _MENTION_RE.findall(text)}
    out = []
    for line in reply.splitlines():
        new_mentions = set(_MENTION_RE.findall(line)) - seen - {"open"}
        if line.startswith(reflexd_wake.STATUS_PREFIX) or new_mentions:
            out.append(line.strip())
    return out


def _timed_out_line(last_step: str = "") -> str:
    """What the room sees instead of the raw "[reflexd] harness timed out".
    With the last live step it says where the time went ("while searching the
    code"), so a stuck agent says why instead of just that it stopped."""
    where = f" while {last_step}," if last_step else ""
    return (f"⚠ ran out of time{where} before finishing its reply. "
            "Mention me to continue.")


def _same_result(posted: str, reply: str) -> bool:
    """Did the agent already post *reply* (or its ✅ result) itself?"""
    a = " ".join(posted.split())[:60]
    b = " ".join(reply.split())[:60]
    if a and a == b:
        return True
    return reply.lstrip().startswith("✅") and posted.lstrip().startswith("✅")


class SlowStart(str):
    """The harness printed nothing before its first-output deadline (a free
    model whose provider takes minutes to its first token)."""

    def __new__(cls, harness: str, seconds: float):
        obj = super().__new__(cls, f"⚠ {harness}: the model sent nothing for {seconds:.0f}s "
                                   "(its free provider is slow right now).")
        return obj


class HarnessExitFailure(str):
    """Keep exit metadata while preserving legacy sentinel/retry comparisons."""

    def __new__(cls, exit_code: int):
        obj = super().__new__(cls, "[reflexd] harness errored")
        obj.exit_code = exit_code
        return obj


# After the harness exits, how long whatever it left running may hold our
# pipes before it is killed (a background server it started, a relaunched child).
LINGER_S = 5.0


def _kill_group(proc: Any) -> None:
    """SIGKILL the harness and everything in its process group."""
    pid = getattr(proc, "pid", None)
    if isinstance(pid, int) and pid > 0:
        try:
            os.killpg(pid, signal.SIGKILL)
            return
        except OSError:  # no group (or already gone): kill the process itself
            pass
    try:
        proc.kill()
    except (ProcessLookupError, OSError):
        pass


async def _reap(proc: Any, timeout: float = 10.0) -> None:
    """``proc.wait()`` with a limit: on Python 3.12+ it doesn't return until
    every pipe closes, so one surviving grandchild blocked it forever."""
    try:
        await asyncio.wait_for(proc.wait(), timeout)
    except (TimeoutError, asyncio.TimeoutError):
        logger.warning("harness pid %s didn't finish closing; moving on", proc.pid)


async def _collect(
    proc: Any, on_line: Callable[[bytes], None] | None,
    first_out: asyncio.Event | None = None,
) -> tuple[bytes, bytes]:
    """``proc.communicate()``, but each stdout line also goes to *on_line* as it
    arrives, so the room sees the agent's steps live instead of after the turn.
    *first_out* is set when the first stdout bytes arrive."""
    if (on_line is None and first_out is None) or not isinstance(
            getattr(proc, "stdout", None), asyncio.StreamReader):
        return await proc.communicate()
    out, err = bytearray(), bytearray()

    async def drain_out() -> None:
        pending, skip = b"", False
        while chunk := await proc.stdout.read(65536):
            if first_out is not None:
                first_out.set()
            out.extend(chunk)
            if on_line is None:
                continue
            pending += chunk
            *lines, pending = pending.split(b"\n")
            for raw in lines:
                if skip or len(raw) > STEP_LINE_MAX:  # a tool result, not a step
                    skip = False
                    continue
                try:
                    on_line(raw)
                except Exception as exc:  # a step is cosmetic: never fail a wake
                    logger.debug("step parse failed: %s", exc)
            if len(pending) > STEP_LINE_MAX:
                pending, skip = b"", True

    async def drain_err() -> None:
        while chunk := await proc.stderr.read(65536):
            err.extend(chunk)

    drains = asyncio.gather(drain_out(), drain_err())
    while not drains.done():
        await asyncio.wait({drains}, timeout=1.0)
        if proc.returncode is not None and not drains.done():
            # The harness exited but something it started still holds the
            # pipes: give it a moment, then kill its process group.
            await asyncio.wait({drains}, timeout=LINGER_S)
            if not drains.done():
                logger.warning("harness exited; killing what it left running")
                _kill_group(proc)
                await asyncio.wait({drains}, timeout=LINGER_S)
            break
    if not drains.done():
        drains.cancel()
    await _reap(proc)
    return bytes(out), bytes(err)


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
        # Set by Reflexd: MCP server entry binding the woken agent to this
        # daemon's relay + identity, and where to write claude's config file.
        self.wake_spec: dict[str, Any] | None = None
        self.mcp_config_path: Path | None = None
        self.agent_config_dir: Path | None = None
        # `quorus agent add --model`: which model the harness runs (any
        # provider/model for opencode, e.g. ollama/qwen3:4b). None = the
        # harness's own default.
        self.model: str | None = os.environ.get("REFLEXD_MODEL", "").strip() or None
        # opencode 1.x rejects `run --standalone` (help text, exit 1): every
        # wake failed for users on the `opencode-ai` npm package (still 1.x).
        self.opencode_v1 = False
        # The pause: epoch seconds until which reflexd won't wake the agent,
        # and why ("quota": a vendor limit; "slow_start" / "no_route": a free
        # model we cooled down ourselves, never shown as quota).
        self.limited_until = 0.0
        self.pause_reason = "quota"
        # What the vendor said: its exact reset and source (reflexd_limits).
        # Memory only, so a restart starts with none.
        self.limit: Any = None
        # Usage the vendor's CLI reported, by window ("5h" / "7d"). Claude
        # only: `codex exec --json` doesn't carry it (spike, 2026-10-21).
        self.usage: dict[str, Any] = {}
        # Set by Reflexd: called with "editing relay.py"-style steps parsed
        # from the harness's JSON stream while a wake runs.
        self.on_step: Callable[[str], None] | None = None
        # opencode free-model failover: the agent's own model, and when it
        # moved off it (0 = it hasn't).
        self._home_model: str | None = None
        self._rotated_at = 0.0
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
        # Neutralize trigger tokens before echoing: a quoted "@open …" or
        # "TODO @role: …" in this acknowledgment would be re-triaged as
        # fresh work by every OTHER agent in the room (echo storm, observed
        # live 2026-08-20). A visible space after "@" breaks both trigger
        # regexes while staying human-readable.
        # Neutralize EVERY @token: the gate caught a stub echoing
        # "@arav-claude …" and re-waking that agent (2026-08-21).
        body = re.sub(r"@(?=[A-Za-z])", "@ ", body)
        return f"(reflexd-stub) on it, working on '{body}'"

    async def run(
        self, harness: str, *, context: str, cwd: Path | None = None,
        resume: str | None = None,
        on_session: Callable[[str], None] | None = None,
        timeout_s: int | None = None,
        max_turns: int | None = None,
        writable_roots: list[Path] | None = None,
        mode: str = "default",
        approval_room: str | None = None,
    ) -> str:
        # Smoke / demo path: avoid spawning any real harness. ~10 LoC, off
        # the regular path. Triggered by an explicit env var OR by the
        # module-level auto-fallback flag set when the claude binary is
        # missing at startup. ``is_stub_mode`` consults both.
        if is_stub_mode():
            return self._stub_reply(context)
        if self.dry_run:
            return self._record_dry_run(harness, context)
        if harness == "claude":
            return await self._run_claude(
                context, cwd=cwd,
                resume=resume, on_session=on_session,
                timeout_s=timeout_s, max_turns=max_turns, mode=mode,
                approval_room=approval_room,
            )
        if harness == "codex":
            return await self._run_codex(
                context, cwd=cwd, resume=resume, on_session=on_session,
                timeout_s=timeout_s, writable_roots=writable_roots, mode=mode,
            )
        if harness == "gemini":
            return await self._run_gemini(
                context, cwd=cwd, resume=resume, on_session=on_session,
                timeout_s=timeout_s, mode=mode,
            )
        if harness == "cursor":
            return await self._run_cursor(context)
        if harness == "opencode":
            return await self._run_opencode(
                context, cwd=cwd, resume=resume, on_session=on_session,
                timeout_s=timeout_s, mode=mode,
            )
        if harness == "cline":
            return await self._run_subprocess(
                build_cline_argv(context),
                parser=lambda out: out.strip(),
                cwd=cwd,
                env=reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir),
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

    async def _run_claude(
        self, context: str, *, cwd: Path | None,
        resume: str | None, on_session: Callable[[str], None] | None,
        timeout_s: int | None = None, max_turns: int | None = None,
        mode: str = "default", approval_room: str | None = None,
    ) -> str:
        """Claude wake with session continuity (D2).

        Resumes the room's stored session when given; captures the (new or
        continued) session id from the JSON envelope via ``on_session`` so
        the caller can persist the room→session map. An expired/unknown
        resume id makes the CLI exit non-zero — retry ONCE without resume
        so continuity degrades to a fresh session instead of a dead wake.
        """
        captured: dict[str, str | None] = {"sid": None}

        def parser(out: str) -> str:
            text, sid = _parse_claude_json(out)
            captured["sid"] = sid
            return text

        cfg = None
        if self.wake_spec is not None and self.mcp_config_path is not None:
            spec = self.wake_spec
            if approval_room:
                spec = {**spec, "env": {**spec["env"],
                                        "QUORUS_APPROVAL_ROOM": approval_room}}
            try:
                cfg = reflexd_wake.write_claude_mcp_config(
                    spec, self.mcp_config_path,
                )
            except OSError as exc:
                logger.warning("could not write wake MCP config: %s", exc)
        extra = reflexd_wake.claude_wake_flags(cfg, mode)
        if cfg is not None:  # a daemon wake: skip the owner's heavy user hooks
            try:
                extra += reflexd_wake.claude_isolation_flags(
                    cfg.with_name(cfg.stem + "-settings.json"))
            except OSError as exc:
                logger.warning("could not write agent settings: %s", exc)
        if self.model:
            extra += ["--model", self.model]
        env = reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir)
        reply = await self._run_subprocess(
            build_claude_argv(context, resume=resume, max_turns=max_turns, extra=extra),
            parser=parser, cwd=cwd, timeout_s=timeout_s, env=env,
        )
        if resume and reply == "[reflexd] harness errored":
            logger.warning(
                "claude resume failed for session %s — retrying fresh", resume,
            )
            captured["sid"] = None
            reply = await self._run_subprocess(
                build_claude_argv(context, max_turns=max_turns, extra=extra),
                parser=parser, cwd=cwd, timeout_s=timeout_s, env=env,
            )
        if captured["sid"] and on_session is not None:
            on_session(captured["sid"])
        return reply

    async def _run_codex(
        self, context: str, *, cwd: Path | None,
        resume: str | None, on_session: Callable[[str], None] | None,
        timeout_s: int | None = None,
        writable_roots: list[Path] | None = None,
        mode: str = "default",
    ) -> str:
        """Codex wake with D2 session continuity (thread_id ↔ resume)."""
        captured: dict[str, str | None] = {"tid": None}

        def parser(out: str) -> str:
            text, tid = _parse_codex_stream(out)
            captured["tid"] = tid
            return text

        extra = reflexd_wake.codex_wake_flags(self.wake_spec, writable_roots, mode)
        if self.model:
            extra += ["-c", f"model={json.dumps(self.model)}"]
        env = reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir)
        reply = await self._run_subprocess(
            build_codex_argv(context, resume=resume, extra=extra), parser=parser,
            cwd=cwd, timeout_s=timeout_s, env=env,
        )
        if resume and reply == "[reflexd] harness errored":
            logger.warning(
                "codex resume failed for thread %s — retrying fresh", resume,
            )
            captured["tid"] = None
            reply = await self._run_subprocess(
                build_codex_argv(context, extra=extra), parser=parser,
                cwd=cwd, timeout_s=timeout_s, env=env,
            )
        if captured["tid"] and on_session is not None:
            on_session(captured["tid"])
        return reply

    def _side_file(self, kind: str) -> Path | None:
        """Per-agent config file next to the claude MCP config (0600)."""
        if self.mcp_config_path is None:
            return None
        name = self.mcp_config_path.name.replace("wake-mcp-", f"wake-{kind}-", 1)
        return self.mcp_config_path.with_name(name)

    async def _run_with_resume(
        self, build: Callable[[str | None], list[str]], parse: Callable[[str], tuple],
        *, harness: str, cwd: Path | None, resume: str | None,
        on_session: Callable[[str], None] | None, timeout_s: int | None,
        env: dict[str, str], first_output_s: float | None = None,
    ) -> str:
        """Shared wake for JSON-speaking harnesses: run, read (reply, session,
        error), retry once fresh if a stored session can't be resumed."""
        captured: dict[str, str | None] = {"sid": None, "err": None}

        def parser(out: str) -> str:
            reply, sid, err = parse(out)
            captured["sid"], captured["err"] = sid, err
            return reply

        reply = await self._run_subprocess(
            build(resume), parser=parser, cwd=cwd, timeout_s=timeout_s, env=env,
            first_output_s=first_output_s,
        )
        failed = reply == "[reflexd] harness errored"
        if failed or (resume and captured["err"] and reply == "[reflexd] (no reply)"):
            # A stale session id fails the resume; and two opencode agents
            # woken by the same message on a fresh install race its first-run
            # setup ("Standalone server exited before reporting readiness",
            # reproduced on opencode 2.0.30). One retry, fresh, after a jitter.
            logger.warning("%s wake failed (session %s) — retrying fresh",
                           harness, resume or "new")
            await asyncio.sleep(random.uniform(1.0, 3.0))
            captured["sid"] = captured["err"] = None
            reply = await self._run_subprocess(
                build(None), parser=parser, cwd=cwd, timeout_s=timeout_s, env=env,
                first_output_s=first_output_s,
            )
        if captured["err"] and reply == "[reflexd] (no reply)":
            # The vendor's own words beat a generic sentinel (e.g. "model not
            # found", "OpenCode 1.18.0 or newer is required").
            logger.error("harness %s error: %s", harness, captured["err"])
            reply = (diagnose_harness_failure(harness, captured["err"])
                     or f"⚠ {harness}: {captured['err']}")
        if captured["sid"] and on_session is not None:
            on_session(captured["sid"])
        return reply

    async def _run_opencode(
        self, context: str, *, cwd: Path | None, resume: str | None,
        on_session: Callable[[str], None] | None, timeout_s: int | None = None,
        mode: str = "default",
    ) -> str:
        """opencode wake: Quorus tools, room permissions, any provider/model.

        opencode's free models are rate-limited hour to hour, one at a time
        (night 1: DeepSeek and MiMo limited while LongCat answered; a fresh
        user's very first message hit it, 2026-10-17). On a free model, a limit
        moves the agent to the next free model at once instead of dropping
        the wake. A model the user picked and pays for is never swapped."""
        if self._rotated_at and time.time() - self._rotated_at > reflexd_models.ROTATE_BACK_S:
            self.model, self._rotated_at, self.limit = self._home_model, 0.0, None
        if (self.model or "").startswith("ollama/"):
            problem = await ollama_problem(self.model.split("/", 1)[1])
            if problem:  # say so now, not "exited 1 with no known cause" 3 min later
                return f"⚠ opencode: {problem}"
        first = self.model
        reply = await self._opencode_once(context, cwd=cwd, resume=resume,
                                          on_session=on_session, timeout_s=timeout_s,
                                          mode=mode)
        if not reflexd_models.is_free_opencode(first):
            return reply
        self._note_slow(reply)
        self._note_no_route(reply)
        tried = {first}
        from quorus.runtime.opencode_free import free_models

        # The live catalog, not just our tuple: it churns (release check, 2026-10-17).
        rotation = await asyncio.to_thread(free_models, reflexd_models.OPENCODE_FREE_MODELS)
        for alt in rotation:
            if self.limited_until <= time.time():
                break  # answered (or failed for another reason): done
            if alt in tried:
                continue
            tried.add(alt)
            logger.warning("opencode %s is rate-limited; trying %s", self.model or "default",
                           alt)
            if not self._rotated_at:
                self._home_model, self._rotated_at = first, time.time()
            # Another model: the old one's pause and reading don't apply.
            self.model, self.limited_until, self.pause_reason, self.limit = (
                alt, 0.0, "quota", None)
            reply = await self._opencode_once(context, cwd=cwd, resume=resume,
                                              on_session=on_session, timeout_s=timeout_s,
                                              mode=mode)
            self._note_slow(reply)
            self._note_no_route(reply)
        if self.limited_until > time.time():
            # Every free model is busy: cool down as before, on the original
            # model. The last one's reading was about another model.
            self.model, self.limit = first, None
            if first == self._home_model:
                self._rotated_at = 0.0
        return reply

    def _note_slow(self, reply: str) -> None:
        """A free model that never started answering: send it to the back of
        the rotation and let the loop move on, as it does for a rate limit."""
        if not isinstance(reply, SlowStart):
            return
        from quorus.runtime.opencode_free import mark_slow

        mark_slow(self.model)
        self._cool_down("slow_start")
        if self.on_step is not None:  # one live line, not a room post per model
            self.on_step(f"{(self.model or 'default').split('/')[-1]} is slow to start; "
                         "trying another free model")

    def _note_no_route(self, reply: str) -> None:
        """A free model the catalog lists but the provider won't serve ("Model
        unavailable", provider.no-route; opencode/beam-free, 2026-10-18): cool it
        down and move on, as for a rate limit, instead of reporting it."""
        if not _NO_ROUTE_RE.search(str(reply or "")):
            return
        from quorus.runtime.opencode_free import mark_slow

        mark_slow(self.model)
        self._cool_down("no_route")
        if self.on_step is not None:
            self.on_step(f"{(self.model or 'default').split('/')[-1]} is unavailable; "
                         "trying another free model")

    def _cool_down(self, reason: str) -> None:
        """Our own pause for a free model: not a vendor quota, and no reading."""
        until = time.time() + SLOW_COOLDOWN_S
        if until > self.limited_until:
            self.limited_until, self.pause_reason = until, reason

    def note_limit(self, raw: str) -> None:
        """A vendor limit message: the vendor's reading, and our quota pause."""
        now = time.time()
        reading, retry = reflexd_limits.read_limit_message(raw, now)
        self.limited_until, self.pause_reason = now + retry, "quota"
        self.limit = reflexd_limits.merge(self.limit, reading, now)

    def _note_usage(self, binary: str, stdout: bytes | None) -> None:
        """Claude's latest rate_limit_event replaces our usage snapshot whole
        (Codex on 20cee4b4: merging kept windows the vendor no longer reported).
        A turn with no event leaves it as it was."""
        if Path(binary).name != CLAUDE_BIN or not stdout or b"rate_limit_event" not in stdout:
            return
        usage = reflexd_limits.read_claude_usage(
            stdout.decode("utf-8", errors="replace"), time.time())
        if usage is not None:
            self.usage = usage

    def current_usage(self, now: float | None = None) -> dict[str, Any]:
        """Reported usage still worth showing; older or reset windows dropped."""
        now = time.time() if now is None else now
        self.usage = reflexd_limits.live_usage(self.usage, now)
        return self.usage

    def current_limit(self, now: float | None = None) -> Any:
        """The vendor reading if it still holds; an expired one is dropped."""
        now = time.time() if now is None else now
        self.limit = reflexd_limits.live(self.limit, now, self.limited_until,
                                         self.pause_reason)
        return self.limit

    async def _opencode_once(
        self, context: str, *, cwd: Path | None, resume: str | None,
        on_session: Callable[[str], None] | None, timeout_s: int | None, mode: str,
    ) -> str:
        env = reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir)
        env["OPENCODE_CONFIG_CONTENT"] = json.dumps(reflexd_models.opencode_config(
            self.wake_spec, mode=mode, model=self.model, v1=self.opencode_v1))
        return await self._run_with_resume(
            lambda sid: build_opencode_argv(context, extra=reflexd_models.opencode_flags(
                model=self.model, resume=sid, standalone=not self.opencode_v1)),
            reflexd_models.parse_opencode_stream, harness="opencode", cwd=cwd,
            resume=resume, on_session=on_session, timeout_s=timeout_s, env=env,
            first_output_s=(FREE_FIRST_EVENT_S
                            if reflexd_models.is_free_opencode(self.model) else None),
        )

    async def _run_gemini(
        self, context: str, *, cwd: Path | None, resume: str | None,
        on_session: Callable[[str], None] | None, timeout_s: int | None = None,
        mode: str = "default",
    ) -> str:
        """Gemini CLI wake in the agent's own Gemini home (GEMINI_CLI_HOME).

        The user's ~/.gemini pins whatever they set up by hand: on most
        machines a Google login that Google retired for the CLI
        (IneligibleTierError even with an API key present, verified on
        gemini-cli 0.64.0), plus stale hooks and MCP entries. The agent's
        home holds only its Quorus tools and API-key sign-in.
        """
        env = reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir)
        key = reflexd_models.gemini_api_key(env)
        if key:
            env["GEMINI_API_KEY"] = key  # env, never argv
        # Outside a daemon (e.g. `quorus duel`'s judge) there's no agent dir: still
        # isolate from ~/.gemini, whose retired Google login fails every call.
        from quorus.config import resolve_config_dir
        home = (self.agent_config_dir / "gemini-home" if self.agent_config_dir is not None
                else resolve_config_dir() / "gemini-home")
        try:
            write_private_json(home / ".gemini" / "settings.json",
                               reflexd_models.gemini_settings(
                                   self.wake_spec, api_key=bool(key), mode=mode))
            env["GEMINI_CLI_HOME"] = str(home)
        except OSError as exc:
            logger.warning("could not write gemini settings: %s", exc)
        flags = reflexd_models.gemini_flags(mode=mode, model=self.model)
        timeout_s = min(timeout_s or self.timeout_s, GEMINI_TIMEOUT_S)
        return await self._run_with_resume(
            lambda sid: build_gemini_argv(
                context, extra=flags + (["--resume", sid] if sid else [])),
            reflexd_models.parse_gemini_json, harness="gemini", cwd=cwd,
            resume=resume, on_session=on_session, timeout_s=timeout_s, env=env,
        )

    def _step_handler(self) -> Callable[[bytes], None] | None:
        sink = self.on_step
        if sink is None:
            return None

        def handle(raw: bytes) -> None:
            step = reflexd_steps.step_of(raw.decode("utf-8", errors="replace"))
            if step:
                sink(step)
        return handle

    async def _run_subprocess(
        self,
        argv: list[str],
        *,
        parser: Callable[[str], str],
        cwd: Path | None = None,
        timeout_s: int | None = None,
        env: dict[str, str] | None = None,
        first_output_s: float | None = None,
    ) -> str:
        # Pre-flight: if the binary truly isn't on PATH, surface the same
        # sentinel string regardless of platform. ``shutil.which`` returning
        # None is a deterministic check; ``FileNotFoundError`` from
        # ``create_subprocess_exec`` is a backstop for racy installs.
        if shutil.which(argv[0]) is None:
            logger.warning("harness binary missing on PATH: %s", argv[0])
            return f"[reflexd] {argv[0]} not installed on this host"
        if cwd is not None:
            # Node/Bun CLIs (opencode) resolve the project from $PWD, not the
            # real cwd: an agent woken from launchd wrote files into `/`.
            env = {**(env if env is not None else os.environ), "PWD": str(cwd)}
        try:
            proc = await asyncio.create_subprocess_exec(
                *argv,
                # Codex reads stdin ("Reading additional input from
                # stdin...") and a harness inheriting a live terminal can
                # block forever waiting for EOF. Always hand it /dev/null.
                stdin=asyncio.subprocess.DEVNULL,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                # D1: run in the room's bound workspace so the agent can do
                # real repo work; None = inherit the daemon's cwd (unbound).
                cwd=str(cwd) if cwd else None,
                env=env,
                # Its own process group, so a timeout can kill everything it
                # started: the Gemini CLI relaunches itself as a child node
                # process, and killing only the parent left that child
                # working and the wake hung for hours (2026-10-17).
                start_new_session=True,
            )
        except FileNotFoundError:
            logger.warning("harness binary missing: %s", argv[0])
            return f"[reflexd] {argv[0]} not installed on this host"
        except OSError as exc:  # E2BIG etc.: never drop the wake silently
            logger.warning("harness %s failed to start: %s", argv[0], exc.strerror)
            return f"[reflexd] {argv[0]} could not start ({exc.strerror})"
        budget = timeout_s or self.timeout_s
        first = asyncio.Event() if first_output_s else None
        collecting = asyncio.ensure_future(_collect(proc, self._step_handler(), first))
        try:
            if first is not None:
                # A free model can sit minutes before its first token (nemotron:
                # 111-300s, release check 2026-10-17). Waiting a whole turn on it
                # beats nothing; moving to the next free model beats waiting.
                started = time.monotonic()
                waiter = asyncio.ensure_future(first.wait())
                await asyncio.wait({waiter, collecting}, timeout=first_output_s,
                                   return_when=asyncio.FIRST_COMPLETED)
                waiter.cancel()
                if not first.is_set() and not collecting.done():
                    _kill_group(proc)
                    collecting.cancel()
                    await _reap(proc)
                    logger.warning("harness %s sent nothing in %ss", argv[0], first_output_s)
                    return SlowStart(Path(argv[0]).name, first_output_s)
                budget = max(1, budget - (time.monotonic() - started))
            stdout, stderr = await asyncio.wait_for(collecting, timeout=budget)
        except asyncio.CancelledError:
            # Daemon stopping: never orphan a running agent.
            _kill_group(proc)
            collecting.cancel()
            raise
        except (TimeoutError, asyncio.TimeoutError):  # distinct classes on 3.10
            # L4: Python 3.11+ aliases ``asyncio.TimeoutError`` to the
            # builtin ``TimeoutError``; the asyncio prefix is deprecated.
            _kill_group(proc)
            await _reap(proc)
            logger.warning("harness %s timed out", argv[0])
            return "[reflexd] harness timed out"
        self._note_usage(argv[0], stdout)
        if proc.returncode != 0:
            raw = (stderr or b"").decode("utf-8", errors="replace")
            # opencode reports failures as JSON error events on STDOUT with an
            # empty stderr: its "Rate limit exceeded" reached the room as a bare
            # "harness errored" and never started the quota cooldown (live
            # 2026-10-17). Only error lines, so a reply mentioning "quota" can't
            # trip the limit check.
            out_errors = [ln for ln in (stdout or b"").decode("utf-8", errors="replace")
                          .splitlines() if '"type":"error"' in ln or '"error":' in ln]
            if out_errors:
                raw = raw + "\n" + "\n".join(out_errors[-5:])
            # Harness stderr can echo credentials or tool inputs. Diagnose
            # it locally, but only persist the binary and exit status.
            logger.warning("harness %s exited %s", argv[0], proc.returncode)
            diagnosis = diagnose_harness_failure(argv[0], raw)
            if diagnosis and _is_limit(raw):
                # Out of quota: stop waking this agent until it resets instead
                # of posting the same ⚠ on every message (gemini, 2026-10-17).
                self.note_limit(raw)
            if diagnosis:
                # Actionable > generic: the room learns WHY nobody answered.
                logger.error("harness %s: %s", argv[0], diagnosis)
                return diagnosis
            said = vendor_error(Path(argv[0]).name,
                                (stdout or b"").decode("utf-8", errors="replace"), raw)
            if said:  # the vendor's own words beat "exited 1 with no known cause"
                return said
            return HarnessExitFailure(proc.returncode)
        reply = parser((stdout or b"").decode("utf-8", errors="replace"))
        if reply == "[reflexd] harness errored":
            # Structured error results can accompany process exit zero.
            return HarnessExitFailure(proc.returncode)
        return reply or "[reflexd] (no reply)"

    async def _run_cursor(self, context: str) -> str:
        # Try the binary first; fall back to the file-inbox protocol if it's
        # missing. Cursor's stop-hook picks the inbox file up on next session.
        if shutil.which(CURSOR_BIN) is not None:
            try:
                out = await self._run_subprocess(
                    build_cursor_argv(context),
                    parser=lambda o: o.strip(),
                    env=reflexd_wake.wake_env(self.wake_spec, self.agent_config_dir),
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
        # The prompt holds the room transcript: owner-only, not 0644 (audit).
        write_private_text(fname, context)
        logger.info("cursor file-inbox wrote %s", fname)
        return f"[reflexd] wrote prompt to {fname}"


# ---------------------------------------------------------------------------
# Relay client — minimal HTTP surface for what the daemon needs
# ---------------------------------------------------------------------------


class ParticipantAuthError(httpx.HTTPError):
    """This agent can't sign in as itself; never fall back to the shared secret.

    An ``httpx.HTTPError`` so the daemon's loops treat it like any relay
    failure: log, back off, retry, without crashing and without the secret."""


@dataclass
class RelayClient:
    """Minimal HTTP surface: JWT exchange, stream-token mint, bid/claim/post."""

    relay_url: str
    api_key: str
    legacy_bearer: bool = False  # if True, send api_key directly as Bearer (skip /v1/auth/token)
    # On a shared-secret relay the daemon claims its own name and signs in
    # with a participant JWT, so identity-bound endpoints (social verbs like
    # /v1/social/defer, which refuse legacy auth) work and stay bound to this
    # agent. The key lives in the agent's private config dir, where its MCP
    # server finds the same key instead of claiming again (409).
    participant: str = ""
    key_dir: Path | None = None
    _bearer: str | None = None
    # Heartbeat and the SSE subscriber sign in at once on startup; without
    # this they both claimed, the loser got 409 and cached the shared secret.
    _sign_in: asyncio.Lock = field(default_factory=asyncio.Lock)
    _bearer_expires_at: float = 0.0
    _client: httpx.AsyncClient | None = None
    # Name claimed elsewhere and no key here: until this time, fail without
    # asking the relay again. The callback tells the owner once per episode.
    on_name_taken: Callable[[str], None] | None = None
    _claim_blocked_until: float = 0.0
    _claim_blocked_reason: str = ""
    # Learned at sign-in: True once a participant key worked, False when the
    # relay answered that it has none (404/405). None until then.
    keys_supported: bool | None = None

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
            if not self.participant:
                return self.api_key
            async with self._sign_in:
                now = time.time()
                if self._bearer is None or now >= self._bearer_expires_at:
                    try:
                        token = await self._participant_jwt()
                    except ParticipantAuthError as exc:
                        self._bearer = None  # nothing cached: retry on the next call
                        logger.error("cannot sign in as %s: %s", self.participant, exc)
                        raise
                    self._bearer = token or self.api_key
                    self._bearer_expires_at = now + 4 * 60
                return self._bearer
        now = time.time()
        if self._bearer is None or now >= self._bearer_expires_at:
            resp = await self.client.post(
                "/v1/auth/token", json={"api_key": self.api_key}, follow_redirects=False,
            )
            if resp.is_redirect:  # never resend a credential to wherever it points
                raise httpx.HTTPError(f"/v1/auth/token answered {resp.status_code} (redirect)")
            resp.raise_for_status()
            self._bearer = resp.json()["token"]
            self._bearer_expires_at = now + 4 * 60
        return self._bearer or ""

    async def _participant_jwt(self) -> str | None:
        """Claim (once) and use this agent's participant key.

        Returns None only when the relay doesn't support claims (404/405 on
        claim-name or token: an older build or a relay with API keys), which
        keeps the shared secret as before. Anything else that stops us signing
        in as ourselves (name claimed elsewhere, saved key rejected, network
        error) fails closed with ``ParticipantAuthError``: falling back to the
        shared admin secret would drop identity binding (review of 397dd2db).
        """
        from quorus import bundle_client

        unsupported = (404, 405)
        key = bundle_client.load_participant_key(self.relay_url, self.participant, self.key_dir)
        if not key and time.time() < self._claim_blocked_until:
            raise ParticipantAuthError(self._claim_blocked_reason)
        try:
            if not key:
                resp = await self.client.post(
                    "/v1/auth/claim-name", json={"name": self.participant},
                    headers={"Authorization": f"Bearer {self.api_key}"},
                    follow_redirects=False)
                if resp.status_code in unsupported:
                    logger.info("relay has no participant keys; using the shared secret")
                    self.keys_supported = False
                    return None
                if resp.status_code == 409:
                    # Our own MCP server may have claimed and saved it meanwhile.
                    key = bundle_client.load_participant_key(
                        self.relay_url, self.participant, self.key_dir)
                    if not key:
                        reason = (f"name {self.participant} is claimed on this relay but its "
                                  "key isn't saved here; the relay owner can free it with "
                                  f"`quorus release-name {self.participant}`")
                        if self._claim_blocked_until == 0.0 and self.on_name_taken:
                            self.on_name_taken(reason)  # once, not once per retry
                        self._claim_blocked_until = time.time() + CLAIM_BACKOFF_S
                        self._claim_blocked_reason = reason
                        raise ParticipantAuthError(reason)
                elif resp.status_code != 200:
                    raise ParticipantAuthError(f"claim-name answered {resp.status_code}")
                else:
                    key = resp.json()["participant_key"]
                    bundle_client.save_participant_key(self.relay_url, self.participant, key,
                                                       self.key_dir)
            # Credential exchanges never follow redirects: a 307/308 would resend
            # the key in the body to another host (codex review of dc24ee6c).
            # Any 3xx is "not 200" below and fails closed.
            resp = await self.client.post("/v1/auth/token", json={"api_key": key},
                                          follow_redirects=False)
            if resp.status_code in unsupported:
                logger.info("relay has no participant keys; using the shared secret")
                self.keys_supported = False
                return None
            if resp.status_code != 200:
                raise ParticipantAuthError(
                    f"the relay rejected {self.participant}'s saved key ({resp.status_code}); "
                    "the name may have been released")
            self.keys_supported = True
            return resp.json()["token"]
        except (httpx.HTTPError, ValueError, KeyError, OSError) as exc:
            raise ParticipantAuthError(f"participant sign-in failed: {exc}") from exc

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

    async def fetch_history(self, *, room: str, limit: int) -> list[dict[str, Any]]:
        """Like :meth:`fetch_recent`, but raises instead of returning [] when
        the relay can't answer: the publish guard must tell "no messages" from
        "couldn't read them" (it holds on the second)."""
        resp = await self.client.get(f"/rooms/{room}/history", headers=await self._headers(),
                                     params={"limit": limit})
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            data = next((data[k] for k in ("messages", "history", "items")
                         if isinstance(data.get(k), list)), None)
        if not isinstance(data, list):
            raise ValueError("room history isn't a list")
        return data

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

    async def fetch_inbox(
        self, *, participant: str,
    ) -> tuple[list[dict[str, Any]], str | None]:
        """Drain the durable per-participant inbox (R1).

        Returns ``(messages, ack_token)``. The relay's ``ack=manual`` mode
        gives at-least-once semantics: anything we fail to ack is redelivered
        after the visibility timeout, so a crash mid-drain loses nothing.
        """
        headers = await self._headers()
        resp = await self.client.get(
            f"/messages/{participant}",
            headers=headers,
            params={"wait": 0, "ack": "manual"},
        )
        resp.raise_for_status()
        data = resp.json()
        if isinstance(data, dict):
            msgs = data.get("messages")
            return (msgs if isinstance(msgs, list) else []), data.get("ack_token")
        return [], None

    async def ack_inbox(self, *, participant: str, ack_token: str) -> None:
        headers = await self._headers()
        resp = await self.client.post(
            f"/messages/{participant}/ack",
            headers=headers,
            json={"ack_token": ack_token},
        )
        resp.raise_for_status()

    async def post_heartbeat(
        self, *, participant: str, status: str = "active", room: str = "",
        reason: str = "",
        activity: str = "",
        waiting_on: str = "",
        waiting_until: float = 0.0,
        model: str = "",
        pause: dict[str, Any] | None = None,
        readings: list[dict[str, Any]] | None = None,
    ) -> None:
        """Presence heartbeat (R2) — lets the relay show active/away + queued.
        ``pause`` / ``readings``: limit status; relays before 0.6.6 ignore them."""
        headers = await self._headers()
        body: dict[str, Any] = {"instance_name": participant, "status": status, "room": room}
        if reason:
            body["reason"] = reason
        if activity:
            body["activity"] = activity
        if waiting_on:
            body["waiting_on"], body["waiting_until"] = waiting_on, max(0.0, waiting_until)
        if model:
            body["model"] = model
        if pause:
            body["pause"] = pause
        if readings:
            body["readings"] = readings
        resp = await self.client.post("/heartbeat", headers=headers, json=body)
        resp.raise_for_status()

    async def fetch_member_roles(self, *, room: str) -> dict[str, str] | None:
        """{name: role} for the room right now, or None if the relay didn't say."""
        try:
            resp = await self.client.get(f"/rooms/{room}", headers=await self._headers())
            resp.raise_for_status()
            roles = resp.json().get("member_roles")
        except Exception as exc:
            logger.debug("member role lookup failed for %s: %s", room, exc)
            return None
        return {str(k): str(v) for k, v in roles.items()} if isinstance(roles, dict) else None

    async def fetch_members(self, *, room: str) -> list[str]:
        """Room member names, or [] on any failure (prompt context only)."""
        try:
            resp = await self.client.get(f"/rooms/{room}", headers=await self._headers())
            resp.raise_for_status()
            members = resp.json().get("members")
        except Exception as exc:
            logger.debug("member lookup failed for %s: %s", room, exc)
            return []
        return [m for m in members if isinstance(m, str)] if isinstance(members, list) else []

    async def mission_task_for(
        self, *, room: str, participant: str,
    ) -> dict[str, Any] | None:
        """Return the first active work-queue task claimed by *participant*
        in *room*, or None. Failures degrade to None (chat budgets)."""
        headers = await self._headers()
        try:
            resp = await self.client.get(
                f"/v1/work_queue/{room}",
                headers=headers,
                params={"actor": participant,
                        "status": "pending,in_progress,blocked"},
            )
            resp.raise_for_status()
            data = resp.json()
        except Exception as exc:
            logger.debug("work-queue lookup failed for %s: %s", room, exc)
            return None
        tasks = data.get("tasks") if isinstance(data, dict) else None
        if isinstance(tasks, list) and tasks:
            first = tasks[0]
            return first if isinstance(first, dict) else None
        return None

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
        failover: dict | None = None,
    ) -> dict[str, Any]:
        """Post a Stream-A `defer` social verb. Used by reflexd on bid-loss
        to broadcast "I deferred to @<winner>" through the social
        protocol so the room state-machine reflects the dependency.
        """
        headers = await self._headers()
        payload: dict[str, Any] = {"to": target, "ttl_seconds": ttl_seconds}
        if failover is not None:
            payload["failover"] = failover
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

    async def fetch_presence(self) -> list[dict]:
        resp = await self.client.get("/presence", headers=await self._headers())
        resp.raise_for_status()
        return resp.json()

    async def release_for_failover(self, *, room: str, actor: str, target: str,
                                   task_id: str) -> None:
        resp = await self.client.post(f"/v1/work_queue/{room}", headers=await self._headers(),
                                      json={"op": "release", "actor": actor,
                                            "task_id": task_id, "handoff_to": target})
        resp.raise_for_status()
        resp = await self.client.post("/v1/social/release", headers=await self._headers(),
                                      json={"actor": actor, "room_id": room, "payload": {
                                          "task_id": task_id, "reason": "vendor failure",
                                          "handoff_to": target}})
        resp.raise_for_status()

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
        data = ConfigManager().load(migrate=False) or {}  # read-only at startup
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
        # A saved key only goes to the relay it was saved for: with the relay
        # overridden to another one, only an env key may be used.
        from quorus.config import DEFAULT_RELAY_URL, same_relay, withheld_credential_message
        saved_key = data.get("api_key") or ""
        bound = same_relay(relay_url, data.get("relay_url") or DEFAULT_RELAY_URL)
        api_key = (
            os.environ.get("REFLEXD_API_KEY")
            or os.environ.get("API_KEY")
            or (saved_key if bound else "")
        )
        if not api_key and saved_key and not bound:
            raise SystemExit(f"reflexd: {withheld_credential_message(relay_url.rstrip('/'))}. "
                             "Or set REFLEXD_API_KEY for that relay.")
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
        log_path = os.environ.get("REFLEXD_LOG_PATH")  # per-agent log from `agent add`
        return cls(
            relay_url=relay_url.rstrip("/"),
            api_key=api_key,
            participant_name=participant,
            legacy_bearer=legacy_bearer,
            **({"log_path": Path(log_path)} if log_path else {}),
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
        reflexd_wake.validate_participant(config.participant_name)
        self.config = config
        self.adapter = adapter or HeadlessAdapter()
        if getattr(self.adapter, "wake_spec", None) is None and hasattr(
            self.adapter, "wake_spec",
        ):
            # The identity itself is written by _refresh_identity: right away
            # when the credential is already known, else after sign-in. A new
            # agent on a shared-secret relay has no participant key until the
            # daemon signs in, and writing the secret here meant every new
            # agent's dir held it until its first wake (live check, 0.5.1).
            self.adapter.agent_config_dir = (
                config.runtime_dir / "agents" / config.participant_name)
            self.adapter.mcp_config_path = (
                config.runtime_dir / f"wake-mcp-{config.participant_name}.json"
            )
            self._wants_wake_spec = True
            if self._agent_credential() is not None:
                self._refresh_identity()
        self._stop = asyncio.Event()
        self._degraded_reason = _failover.load_reason(config)
        self._presence: tuple[str, str] = (
            "degraded" if self._degraded_reason else "active", "")
        # The live step of the running wake ("editing relay.py"), sent with
        # the busy presence at most every STEP_POST_MIN_S.
        self._activity = ""
        self._stuck_alerted_at = 0.0
        self._waiting: tuple[str, float] = ("", 0.0)  # (kind, until epoch)
        self._presence_relay: Any = None
        self._step_task: asyncio.Task[None] | None = None
        if hasattr(self.adapter, "on_step"):
            self.adapter.on_step = self._on_step
        self._limit_announced: set[tuple[str, float]] = set()
        # room -> ordered ids the agent saw in a wake it then answered.
        self._answered: dict[str, dict[str, None]] = {}
        self._turns: list[float] = []  # harness run start times, for pacing
        self._pace_announced: dict[str, float] = {}
        # Paced envelopes wait here (newest per room, coalesced) instead of
        # holding the wake lock: a pacing sleep under the lock kept the owner's
        # own @mentions queued for an hour (overseer, fix 16).
        self._pace_parked: dict[str, tuple[dict[str, Any], Any]] = {}
        self._pace_task: asyncio.Task | None = None
        self._readonly_warned: set[str] = set()  # worktrees told "stuck marker"
        # Unfinished-turn holds whose marker couldn't be written: still honoured
        # by this process (the marker is what survives a restart).
        self._unfinished_held: set[str] = set()
        self._last_spoke: dict[str, float] = {}
        self._last_wake_at: float = 0.0
        # @open message id -> the track-record bid snapshot; the turn that wins
        # takes it when it starts, so its outcome joins that exact prediction.
        self._predictions: dict[str, str] = {}
        # Bounded in-memory queue for messages that arrive while a busy-file
        # is set. Drained on stop, not replayed across restarts (PR-C1 scope).
        self._queue: list[dict[str, Any]] = []
        # Ids already handled on EITHER delivery path (SSE or inbox drain).
        # Persisted so a restart doesn't re-answer: SSE-delivered messages
        # stay in the durable inbox, and the reconnect drain used to replay
        # every one of them after each restart (live run 2026-10-08: a
        # launchd restart re-ran an already-claimed build task).
        self._handled_path = config.runtime_dir / f"handled-{config.participant_name}.json"
        self._attempts_path = config.runtime_dir / f"attempts-{config.participant_name}.json"
        self._handled_order: list[str] = self._load_handled()
        self._handled_ids: set[str] = set(self._handled_order)
        self._inflight: set[str] = set()
        # One harness run at a time per agent. Held for the whole wake; new
        # work waits on it FIFO instead of blocking the SSE reader (the old
        # serial loop went deaf for the full length of every wake).
        self._wake_lock = asyncio.Lock()
        self._bg_tasks: set[asyncio.Task[Any]] = set()

    def stop(self) -> None:
        self._stop.set()

    def _load_handled(self) -> list[str]:
        try:
            data = json.loads(self._handled_path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return []
        ids = [i for i in data if isinstance(i, str)] if isinstance(data, list) else []
        return ids[-_HANDLED_ID_CAP:]

    def _save_handled(self) -> None:
        try:
            self._handled_path.parent.mkdir(parents=True, exist_ok=True)
            tmp = self._handled_path.with_suffix(".tmp")
            fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump(self._handled_order, f)
            os.replace(tmp, self._handled_path)
        except OSError as exc:
            logger.debug("could not persist handled ids: %s", exc)

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
        # Bid/claim MUST key on the canonical room-message id. Fan-out gives
        # every recipient its own envelope uuid in ``id``; bidding with that
        # would give each agent a private auction window — every capable
        # agent "wins" an @open broadcast and all of them reply. ``message_id``
        # is the shared canonical id set by room_msg_svc; ``id`` is only the
        # fallback for legacy envelopes that predate the field.
        message_id = envelope.get("message_id") or envelope.get("id") or ""
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
        if self._degraded_reason and triage.kind != "mention":
            return False  # an explicit mention may retry/recover this vendor

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

        # Agent↔agent loop guard. ``_reply_depth`` metadata is never set on the
        # wire, so walk the reply_to chain instead: a review→fix→review loop
        # is one conversation; independent overnight tasks never add up.
        if is_agent_participant(sender):
            try:
                recent = await relay.fetch_recent(room=room, limit=100)
            except Exception:
                recent = []
            depth = reflexd_wake.reply_chain_agent_depth(
                envelope, recent, is_agent_participant,
            )
            run = reflexd_wake.trailing_agent_chain(recent, is_agent_participant)
            unaddressed = triage.reason == "agent to room"
            if (depth >= reflexd_wake.MAX_AGENT_CHAIN
                    or run >= reflexd_wake.MAX_ROOM_AGENT_RUN
                    or (unaddressed and run >= reflexd_wake.MAX_UNADDRESSED_AGENT_RUN)):
                logger.info(
                    "agent loop guard: chain=%d room_run=%d in room=%s — not "
                    "waking until a human speaks (id=%s)", depth, run, room, message_id,
                )
                full = (depth >= reflexd_wake.MAX_AGENT_CHAIN
                        or run >= reflexd_wake.MAX_ROOM_AGENT_RUN)
                # Only the full stop is a pause: past the chatter limit,
                # @-mentions still wake agents, so "paused" overstated it.
                marker, text = ((PAUSE_NOTICE, f"{PAUSE_NOTICE} after {run} agent messages "
                                 "in a row. Say anything to keep them going.") if full else
                                (CHATTER_NOTICE, f"{CHATTER_NOTICE} after {run} in a row; "
                                 "@-mentions still wake them. Say anything to reset."))
                if await self._pause_unannounced(relay, room, recent, marker):
                    await self._post_status(relay, room, envelope, text)
                return False

        # OS-level notification: fire BEFORE bid/claim so the user gets a
        # real macOS banner (or notify-send / fallback log) even if this
        # daemon loses the auction. Per-(sender, room) rate-limit in the
        # notifications module dedupes when several reflexd instances run
        # on the same host. PII guard: the body we pass IS the chat
        # content but the notifications module never persists it — only
        # sender+room hit disk.
        # Every human line now wakes someone, so only direct mentions get a banner.
        if triage.kind == "mention":
            try:
                from quorus.notifications import notify as _notify
                _notify(f"Quorus — {room or 'DM'}", content, sender=sender, room=room)
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

        # A human's message, or a direct mention, wakes every agent it reaches:
        # the owner wants all agents to answer them (2026-10-16), and a mention
        # must never lose an auction. Only open tasks and agent chatter bid.
        if triage.kind == "mention" or (
                triage.kind == "question" and not is_agent_participant(sender)):
            async with self._wake_lock:
                await self._wake_and_reply(relay, envelope, triage=triage)
            return True

        # Capability-aware bid scoring. Score 0.0 ⇒ this agent isn't a fit;
        # skip the bid POST entirely so we don't poison the auction with
        # zero-bids that look like real participants.
        caps = capabilities_for(
            self.config.participant_name, harness=detect_harness(self.config.participant_name),
        )
        # Anti-monopoly penalty must SHRINK as the last win recedes. The old
        # call passed "seconds since last wake" (capped at 5), so any agent
        # idle for 5s+ paid the maximum 0.5 forever and bid 0 on all open
        # work — after its first job no agent ever took an @open again (seen
        # live 2026-10-08). Pass how RECENT the last win is instead.
        since = time.time() - self._last_wake_at if self._last_wake_at else None
        recent = max(0.0, 5.0 - since) if since is not None else 0.0
        bid, bid_reason = compute_bid_v2(
            kind=triage.kind,
            role=triage.role,
            description=triage.description,
            capabilities=caps,
            recency_seconds=recent,
        )
        if bid <= 0.0:
            logger.info(
                "skipping bid (no capability match) room=%s id=%s kind=%s reason=%s",
                room, message_id, triage.kind, bid_reason,
            )
            return False
        if triage.kind == "open_todo":
            # L2: this agent's own recent turns and kept/reverted commits
            # inform the bid (shadow by default: logged, not applied).
            repo = workspace_for(room) if room else None
            try:
                decision = _track.decide_bid(
                    bid, bid_reason, message_id=message_id,
                    runtime_dir=self.config.runtime_dir,
                    participant=self.config.participant_name, repo=repo,
                    scope=self._track_scope(repo),
                    harness=detect_harness(self.config.participant_name) or "",
                    model=str(getattr(self.adapter, "model", None) or ""),
                    text=str(envelope.get("content") or ""))
                bid, bid_reason = decision.bid, decision.reason
                if decision.prediction_id:
                    self._predictions.pop(message_id, None)
                    self._predictions[message_id] = decision.prediction_id
                    while len(self._predictions) > 256:
                        self._predictions.pop(next(iter(self._predictions)))
                if decision.announce:
                    await self._announce_adaptation(relay, room, envelope, decision.announce)
            except Exception as exc:  # a bad track record must never stop a bid
                logger.debug("track-record bid skipped: %s", exc)
            if await self._read_only_for(relay, envelope, room):
                # It would win and then only answer read-only, so the task stalled
                # (audit 0.6.3, bug 20). Bid the floor: an agent that may write for
                # this person wins; if none bids, this one still answers.
                bid, bid_reason = min(bid, 0.05), f"{bid_reason}+read-only"
        if self._wake_lock.locked() and triage.kind != "mention":
            # Busy on another job: still bid (so the work never goes
            # ownerless when everyone is busy) but low, so an idle teammate
            # wins. If we win anyway, the job runs after the current one.
            bid = max(0.05, bid - 0.3)
            bid_reason = f"{bid_reason}+busy"

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
        won: dict[str, Any] = {}
        while time.time() < deadline:
            try:
                claim = await relay.claim(room_id=room, message_id=message_id)
            except httpx.HTTPStatusError as exc:
                code = exc.response.status_code if exc.response else 0
                if code == 425:
                    # Bid window still open — exactly what we want to wait
                    # for, so poll again rather than treating it as an error.
                    await asyncio.sleep(0.1)
                    continue
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
                winner, won = claim.get("winner"), claim
                if triage.kind == "open_todo":
                    _track.record_claim(
                        self.config.runtime_dir, self.config.participant_name,
                        message_id=message_id, claim=claim,
                        scope=self._track_scope(workspace_for(room) if room else None))
                break
            await asyncio.sleep(0.25)

        if winner != self.config.participant_name:
            logger.info(
                "lost or no claim room=%s id=%s winner=%s",
                room, message_id, winner,
            )
            # No `/defer → @winner` post on a lost bid: with four agents in a room every
            # @open produced four such lines (multi-owner audit, Arav), and the winner's
            # claim already tells the room who took the task. Nothing reads these edges
            # for routing; a failover handoff still posts its own defer.
            return True

        if won.get("read_only") and triage.kind == "open_todo":
            # Every bid was read-only for the requester: a turn here could only
            # answer, so the task stalled (audit 0.6.3, bug 20). Say so once, from
            # the one winner, and take no turn.
            requester = envelope.get("from_name") or "the requester"
            try:
                await relay.post_reply(
                    room=room, from_name=self.config.participant_name,
                    message_type="status", reply_to=message_id or None,
                    content=(f"{reflexd_wake.STATUS_PREFIX} no agent here can make changes "
                             f"for @{requester} on this: every agent that bid would only "
                             f"get read-only turns. @{requester}, add one of your own "
                             "agents to this room, or ask its owner to set "
                             "`trust: members`."))
            except httpx.HTTPError as exc:
                logger.warning("read-only @open notice failed: %s", exc)
            return True

        if self._wake_lock.locked():
            logger.info("won room=%s id=%s while busy — queued behind current job",
                        room, message_id)
        async with self._wake_lock:
            await self._wake_and_reply(relay, envelope, triage=triage)
            self._last_wake_at = time.time()
        return True

    async def _wake_and_reply(
        self, relay: RelayClient, envelope: dict[str, Any], *,
        triage: "TriageResult | None" = None, reason: str | None = None,
    ) -> None:
        """One wake, shown to the room as presence ``busy`` while it runs:
        before this, nobody could tell a working agent from a dead one
        until its reply landed (sometimes minutes later)."""
        mid = envelope_canonical_id(envelope) or ""
        if mid and self._attempt(mid) > MAX_WAKE_ATTEMPTS:
            # A wake that never finished (hung harness, crash, restart) was
            # replayed on every daemon restart forever: Gemini re-ran one stuck
            # task 4 times overnight (2026-10-17). Give up after two tries.
            logger.warning("giving up on %s after %d unfinished attempts", mid,
                           MAX_WAKE_ATTEMPTS)
            self._finish_attempt(mid)
            await self._post_status(
                relay, envelope.get("room") or "", envelope,
                f"@{self.config.participant_name} couldn't finish this after "
                f"{MAX_WAKE_ATTEMPTS} tries and dropped it; @-mention it again to retry")
            return
        await self._set_presence(relay, "busy", envelope.get("room") or "")
        try:
            await self._wake_and_reply_inner(
                relay, envelope, triage=triage, reason=reason)
        finally:
            await self._set_presence(
                relay, "degraded" if self._degraded_reason else "active",
                envelope.get("room") or "" if self._degraded_reason else "")
        if mid:
            self._finish_attempt(mid)  # reached only when the wake completed

    def _attempt(self, mid: str) -> int:
        """Count a start for *mid* (persisted, so restarts count); return it."""
        data = self._load_attempts()
        data[mid] = int(data.get(mid, 0)) + 1
        self._save_attempts(data)
        return data[mid]

    def _finish_attempt(self, mid: str) -> None:
        data = self._load_attempts()
        if data.pop(mid, None) is not None:
            self._save_attempts(data)

    def _load_attempts(self) -> dict[str, int]:
        try:
            data = json.loads(self._attempts_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            return {}
        return data if isinstance(data, dict) else {}

    def _save_attempts(self, data: dict[str, int]) -> None:
        try:
            keep = dict(list(data.items())[-200:])
            write_private_json(self._attempts_path, keep)
        except OSError as exc:
            logger.debug("attempts save failed: %s", exc)

    async def _set_presence(self, relay: RelayClient, status: str, room: str) -> None:
        self._presence = (status, room)
        self._presence_relay = relay
        self._activity = ""  # a new wake (or the end of one) has no step yet
        await self._post_presence()

    async def _post_presence(self) -> None:
        post = getattr(self._presence_relay, "post_heartbeat", None)
        if post is None:
            return
        status, room = self._presence
        extra = self._presence_extra(status)
        try:
            if status == "degraded":
                extra["reason"] = self._degraded_reason
            await post(participant=self.config.participant_name, status=status, room=room,
                       **extra)
        except Exception as exc:  # presence is cosmetic: never fail a wake on it
            logger.debug("presence update failed: %s", exc)

    def _presence_extra(self, status: str) -> dict[str, Any]:
        """The fields that go with a presence post: the live step while busy,
        otherwise what the agent is waiting on, for the "needs you" board."""
        if status == "busy":
            return {"activity": self._activity} if self._activity else {}
        kind, until = self._waiting_now()
        extra: dict[str, Any] = {"waiting_on": kind, "waiting_until": until} if kind else {}
        return {**extra, **self._limit_fields()}

    def _limit_fields(self) -> dict[str, Any]:
        """Limit status for presence: our pause and the vendor's live reading."""
        now, out = time.time(), {}
        until = getattr(self.adapter, "limited_until", 0.0) or 0.0
        if until > now:
            out["pause"] = {"reason": getattr(self.adapter, "pause_reason", "quota"),
                            "until": until}
        current = getattr(self.adapter, "current_limit", None)
        reading = current(now) if current else None
        usage = getattr(self.adapter, "current_usage", None)
        readings = ([reading] if reading is not None else []) + (
            list(usage(now).values()) if usage else [])
        if readings:
            out["readings"] = [{"window": r.window, "kind": r.kind, "source": r.source,
                                "used_pct": r.used_pct, "resets_at": r.resets_at,
                                "seen_at": r.seen_at} for r in readings]
        return out

    def _waiting_now(self) -> tuple[str, float]:
        until = getattr(self.adapter, "limited_until", 0.0) or 0.0
        # A free-model cooldown is our pause, not the vendor's quota: old
        # relays show nothing for it rather than "out of quota".
        if until > time.time() and getattr(self.adapter, "pause_reason", "quota") == "quota":
            return "quota", until
        kind, until = self._waiting
        if kind == "pacing" and until and until < time.time():
            return "", 0.0
        return kind, until

    def _note_outcome(self, reply: str) -> None:
        """A failed turn leaves the agent "stuck" until its next good one."""
        failed = (reply or "").lstrip().startswith(("⚠", "[reflexd]"))
        if failed and not _is_limit(reply):
            self._waiting = ("stuck", 0.0)
        elif not failed and self._waiting[0] == "stuck":
            self._waiting = ("", 0.0)
        if not failed and hasattr(self.adapter, "limit"):
            self.adapter.limit = None  # a good turn: whatever wall there was is gone

    def _on_step(self, step: str) -> None:
        """A step from the harness stream: show it on the working line soon."""
        if self._presence[0] != "busy" or step == self._activity:
            return
        self._activity = step
        if self._step_task is None or self._step_task.done():
            self._step_task = asyncio.get_running_loop().create_task(self._flush_steps())

    async def _flush_steps(self) -> None:
        """At most one presence post per STEP_POST_MIN_S, ending on the latest step."""
        sent = None
        while self._presence[0] == "busy" and self._activity and self._activity != sent:
            sent = self._activity
            await self._post_presence()
            await asyncio.sleep(STEP_POST_MIN_S)

    def _mark_answered(self, room: str, ids: set) -> None:
        self._last_spoke[room] = time.time()
        seen = self._answered.setdefault(room, {})
        for i in ids:
            if i:
                seen[i] = None
        while len(seen) > ANSWERED_MAX:
            seen.pop(next(iter(seen)))

    async def _paced(self, relay: RelayClient, envelope: dict[str, Any],
                     triage: Any = None) -> bool:
        """Keep the agent inside its owner's plan: past its turns-per-hour cap,
        park the message for its slot (newest per room; the later wake reads
        the room, so nothing is dropped). A human's message always goes
        straight through. Codex burned a 5-hour plan window in ~24 turns on
        night one (2026-10-17).

        Turns are recorded only when the harness actually runs
        (:meth:`_record_turn`), and the wait never holds the wake lock: skipped
        envelopes burned the window and a sleep under the lock blocked the
        owner's own @mentions for an hour (overseer, fix 16)."""
        cap = pace_for(self.config.participant_name)
        if cap is None or not is_agent_participant(envelope.get("from_name") or ""):
            return True
        now = time.time()
        wait = pace_wait_s(self._turns, cap, now)
        if wait <= 0:
            return True
        room = envelope.get("room") or ""
        if now - self._pace_announced.get(room, 0.0) > PACE_WINDOW_S:
            self._pace_announced[room] = now
            await self._post_status(
                relay, room, envelope,
                f"@{self.config.participant_name} is pacing itself ({cap} turns/hour to "
                f"stay inside its plan); next turn in about {_human_wait(wait)}")
        logger.info("pacing: parked for %.0fs (cap %d/h)", wait, cap)
        self._pace_parked[room] = (envelope, triage)
        # Waiting for a plan slot isn't working: it showed as "⋯ @a-codex
        # working 57m" for most of every hour (2026-10-17).
        self._waiting = ("pacing", now + wait)
        if self._pace_task is None or self._pace_task.done():
            self._pace_task = asyncio.get_running_loop().create_task(
                self._release_paced(relay, wait))
        return False

    def _record_turn(self) -> None:
        """A harness turn is starting: it counts against the pace window."""
        now = time.time()
        self._turns = [t for t in self._turns if now - t < PACE_WINDOW_S] + [now]

    async def _release_paced(self, relay: RelayClient, wait: float) -> None:
        """When the slot opens, wake once per room for the parked messages.
        Keep going while anything is parked: a room whose wake re-parked it
        (the slot went to the room before it) waits for the next slot rather
        than being stranded with no worker left (codex, review of 10d8a8e0)."""
        while True:
            await self._sleep_or_stop(wait)
            self._waiting = ("", 0.0)
            if self._stop.is_set():
                return
            parked, self._pace_parked = self._pace_parked, {}
            for envelope, triage in parked.values():
                if self._stop.is_set():
                    return
                try:
                    async with self._wake_lock:
                        await self._wake_and_reply(relay, envelope, triage=triage)
                except Exception as exc:  # one bad wake mustn't strand the rest
                    logger.exception("paced wake failed: %s", exc)
            if not self._pace_parked:
                return
            cap = pace_for(self.config.participant_name)
            wait = 0.0 if cap is None else max(1.0, pace_wait_s(self._turns, cap, time.time()))

    def _agent_credential(self) -> tuple[str, bool] | None:
        """``(credential, legacy)`` for the woken agent.

        Never the relay's shared secret when the agent has its own key: the
        secret is an admin credential (post as anyone, claim any free name),
        and the agent's text can be steered by anyone in the room. On a
        shared-secret relay the daemon's sign-in saved this agent's
        participant key in its agent dir; the agent signs in with that and
        acts only as itself. A relay too old for participant keys has none,
        and the daemon itself runs on the secret there, so the agent does too.

        ``None`` while that isn't known yet (no saved key and the daemon hasn't
        signed in): the caller writes nothing rather than guess the secret.
        """
        if not self.config.legacy_bearer:
            return self.config.api_key, False
        from quorus import bundle_client

        key = bundle_client.load_participant_key(
            self.config.relay_url, self.config.participant_name,
            self.config.runtime_dir / "agents" / self.config.participant_name)
        if key:
            return key, False
        if getattr(getattr(self, "_relay", None), "keys_supported", None) is not False:
            return None  # sign-in hasn't told us yet, or it saved no key (fail closed)
        if not getattr(self, "_warned_shared_secret", False):
            self._warned_shared_secret = True
            logger.warning("relay has no participant keys: the woken agent gets the shared secret")
        return self.config.api_key, True

    def _refresh_identity(self) -> None:
        """Rewrite the agent's private config dir before every wake. An agent
        testing `quorus init` inside its wake wrote a bogus profile there and
        made it current (its QUORUS_CONFIG_DIR beats HOME), so its room tools
        401'd for the rest of the session (opencode, 2026-10-17)."""
        agent_dir = getattr(self.adapter, "agent_config_dir", None)
        if agent_dir is None:
            return
        known = self._agent_credential()
        if known is None:
            logger.warning("not signed in to the relay yet: this wake runs without room tools")
            return
        credential, legacy = known
        if getattr(self.adapter, "wake_spec", None) is not None \
                or getattr(self, "_wants_wake_spec", False):
            self.adapter.wake_spec = reflexd_wake.quorus_mcp_spec(
                relay_url=self.config.relay_url, api_key=credential,
                participant=self.config.participant_name, legacy=legacy,
                config_dir=agent_dir,
            )
        try:
            reflexd_wake.write_agent_config_dir(
                agent_dir, relay_url=self.config.relay_url, api_key=credential,
                participant=self.config.participant_name, legacy=legacy,
            )
            for stray in (agent_dir / "profiles").glob("*.json"):
                if stray.stem != self.config.participant_name:
                    stray.unlink(missing_ok=True)
        except OSError as exc:
            logger.warning("could not refresh agent identity: %s", exc)

    async def _limited(self, relay: RelayClient, envelope: dict[str, Any]) -> bool:
        """While the vendor's quota is out, don't wake the agent: say so once
        per room, then stay quiet until it resets."""
        until = getattr(self.adapter, "limited_until", 0.0) or 0.0
        if until <= time.time():
            return False
        room = envelope.get("room") or ""
        if (room, until) not in self._limit_announced:
            self._limit_announced.add((room, until))
            # Relative, not a clock time: laptop 2 (UTC) said "until about Sat
            # 04:33" to a room reading it in Toronto (2026-10-17).
            await self._post_status(relay, room, envelope, self._pause_line(until))
        logger.info("skipping wake: harness paused until %s", until)
        return True

    def _pause_line(self, until: float) -> str:
        """The vendor's reset when it gave one; our retry, marked "?", when not;
        and never "out of quota" for a free-model cooldown (limit-status rev 3)."""
        name, now = self.config.participant_name, time.time()
        if getattr(self.adapter, "pause_reason", "quota") != "quota":
            return (f"@{name} is paused by Quorus for about {_human_wait(until - now)} "
                    "(its free models are slow to start or unavailable) and will pick up "
                    "again then")
        current = getattr(self.adapter, "current_limit", None)
        reading = current(now) if current else None
        if reading is not None and reading.resets_at is not None:
            return (f"@{name} is out of quota for about {_human_wait(reading.resets_at - now)} "
                    "(the vendor's reset time) and will pick up again then")
        return (f"@{name} is out of quota for about {_human_wait(until - now)}? (no reset "
                "time in the vendor's message, so that's when it tries again)")

    async def _wake_and_reply_inner(
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
        if await self._limited(relay, envelope):
            return
        wake_id = envelope_canonical_id(envelope)
        if wake_id and wake_id in self._answered.get(room, {}):
            # A lead @-mentioned by every teammate built a queue of mentions,
            # then woke once per item it had already seen in an earlier wake's
            # transcript and answered: ~10 NO_REPLY wakes in a row (live
            # 2026-10-17). Seen in context + posted after = handled. Checked
            # before pacing, so a skipped message never waits for a slot.
            logger.info("skipping wake: %s was already in an answered wake's context", wake_id)
            return
        if not await self._paced(relay, envelope, triage):
            return
        self._refresh_identity()

        history = await relay.fetch_recent(
            room=room, limit=HEARTBEAT_HISTORY_LIMIT,
        )
        context_ids = {m.get("id") for m in history if m.get("id")} | {wake_id}
        memory_entries: list[dict[str, Any]] = []
        try:
            memory_entries = await _mem.read_recent(
                self.config.participant_name, room, n=MEMORY_CONTEXT_LIMIT,
            )
        except Exception as exc:
            logger.debug("memory read failed: %s", exc)

        harness = detect_harness(self.config.participant_name)
        # D1: bound workspace → agent works in the real repo. Unbound →
        # inherit daemon cwd and the wake instructions say so.
        ws = workspace_for(room)
        worktree = None
        if ws is not None and reflexd_wake.WORKTREES_ENABLED:
            try:
                worktree = await asyncio.to_thread(
                    reflexd_wake.agent_worktree, ws, self.config.participant_name,
                )
                if worktree is not None:
                    await asyncio.to_thread(reflexd_wake.sync_worktree, worktree)
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("worktree setup failed, using %s: %s", ws, exc)
            if worktree is not None and worktree.repaired:
                logger.warning("%s", worktree.repaired)
                await self._post_status(relay, room, envelope, f"⚠ {worktree.repaired}")
        # Step 3 of code across machines: remember where the branch started
        # so a review request can carry exactly this wake's commits.
        pre_head = (await asyncio.to_thread(_review_share.head_of, worktree)
                    if worktree is not None and share_for(room) else None)
        shared_note = ""
        if pre_head and _review_share.shared_bundle_ids(envelope.get("content") or ""):
            # Step 4: the review request's commits are in the repo before the
            # agent starts, so it reviews code, not a pasted diff.
            try:
                shared_note = await _review_share.fetch_shared(
                    relay_url=self.config.relay_url, bearer=await relay.bearer_jwt(),
                    room=room, worktree=worktree, message=envelope.get("content") or "")
            except httpx.HTTPError as exc:
                logger.warning("auto-fetch skipped: %s", exc)
        # Audit F2: a commit the message names is rendered here, by the daemon,
        # so even a read-only reviewer (no shell) sees the code, not a summary.
        evidence_repo = worktree.path if worktree is not None else ws
        if evidence_repo is not None:
            shared_note += await asyncio.to_thread(
                _commit_evidence.evidence_block, evidence_repo,
                envelope.get("content") or "", os.urandom(6).hex())
        _members = getattr(relay, "fetch_members", None)
        teammates = await _members(room=room) if _members is not None else []
        trusted = await self._trusted_wake(relay, envelope, history)
        prompt = self._build_prompt(
            envelope, history, triage=triage,
            memory_entries=memory_entries,
            teammates=[t for t in teammates if is_agent_participant(t)],
            has_workspace=ws is not None, worktree=worktree,
            shared_note=shared_note, trusted=trusted, harness=harness,
        )
        log_reason = (triage.reason if triage else None) or reason or "?"
        # D2: resume the room's prior session so the agent keeps its memory.
        prior_session = session_for(self.config.participant_name, room, harness)

        def _persist_session(
            sid: str, _room: str = room, _harness: str = harness,
        ) -> None:
            remember_session(
                self.config.participant_name, _room, sid, _harness,
            )

        # L2: defer to a live interactive session on this workspace — its
        # Stop hook delivers the room's messages with full context. Only
        # when it's actually mid-turn (busy-file present); an idle session
        # may never fire Stop, so those still get a headless wake.
        live = live_session_for_workspace(ws)
        if live is not None and is_busy(self.config.participant_name):
            logger.info(
                "deferring to live session pid=%s cwd=%s (Stop hook delivers)",
                live.get("pid"), live.get("cwd"),
            )
            return

        # D5: mission wakes (agent holds an active work-queue claim in this
        # room) get the long leash and no turn cap; chat wakes stay tight.
        _mission_lookup = getattr(relay, "mission_task_for", None)
        mission = None
        if _mission_lookup is not None:
            mission = await _mission_lookup(
                room=room, participant=self.config.participant_name,
            )
        wake_timeout = MISSION_TIMEOUT_S if mission else None
        wake_max_turns = None if mission else CHAT_MAX_TURNS

        wake_started = datetime.now(timezone.utc)
        prediction_id = self._predictions.pop(
            str(envelope.get("message_id") or envelope.get("id") or ""), "")
        logger.info(
            "waking harness=%s room=%s reason=%s memory_entries=%d "
            "workspace=%s session=%s mode=%s",
            harness, room, log_reason, len(memory_entries), ws or "-",
            (prior_session[:8] + "…") if prior_session else "new",
            "mission" if mission else "chat",
        )
        failure_reason = None
        readonly_snap: dict[str, str] | None = None
        readonly_turn = False
        try:
            # Only pass worktree extras when used: adapters written against
            # the older run() signature keep working.
            extra_kw: dict[str, Any] = (
                {"writable_roots": [ws]} if worktree is not None else {}
            )
            room_mode = mode_for(room)
            if _answer_only(triage, envelope, self.config.participant_name,
                            lead_for(room)):
                # The room has a lead and this is a human message to everyone:
                # answer, don't build. Enforced by the harness, not the
                # prompt: a DeepSeek agent told "wait for the lead" did the
                # whole task anyway, duplicating the lead's work (2026-10-17).
                room_mode = "readonly"
                # Never resume or save this session: opencode keeps a
                # session's permissions, so the lead's assignment that
                # arrived next resumed it read-only and the agent reported
                # "no write tools" (live 2026-10-17).
                prior_session, _persist_session = None, None
            if not trusted:
                # Pre-launch audit: in autonomous mode any room member (or text
                # injected into the room) could make the agent run code. Only
                # the owner, and the owner's agents working on the owner's own
                # requests, get write-capable turns unless the owner set the
                # room to `trust: members`. That holds in every mode, `default`
                # included, where the owner's own CLI settings might allow edits:
                # a missing member can't be told apart from a revoked observer
                # (codex, reviews of f5d69289 and ee159a6d).
                logger.info("read-only turn: %s isn't serving this agent's owner",
                            envelope.get("from_name") or "")
                room_mode = "readonly"
                prior_session, _persist_session = None, None
            if room_mode != "default":
                extra_kw["mode"] = room_mode
            if (worktree is None and ws is not None
                    and reflexd_readonly.is_read_only(harness, room_mode)):
                # With worktrees turned off too: disabling isolation doesn't
                # authorize a read-only turn to write (codex, review of 21be4efe).
                # Worktree setup failed, so the turn would run in the shared
                # checkout where the read-only guard can't see its writes
                # (codex, review of 8805dc00). Don't run it.
                readonly_turn = True
                raise reflexd_readonly.SnapshotError(
                    "has no isolated worktree for a read-only turn (setting it up "
                    "failed), so it didn't run")
            if worktree is not None:
                # Settle anything a crashed read-only turn left first, then
                # snapshot this one if it is read-only (security fix 15).
                await self._settle_readonly(relay, room, worktree, None)
                if reflexd_readonly.is_read_only(harness, room_mode):
                    readonly_turn = True
                    try:
                        readonly_snap = await asyncio.to_thread(
                            reflexd_readonly.begin, self._readonly_dir, worktree.path)
                    except reflexd_readonly.MarkerError as exc:
                        # An unresolved earlier turn: its marker already keeps
                        # this worktree from publishing; never overwrite it.
                        logger.error("read-only turn: %s", exc)
                    except (OSError, subprocess.SubprocessError) as exc:
                        # Without a snapshot its writes can't be told apart:
                        # don't run it, and say why instead of a bare "stuck".
                        raise reflexd_readonly.SnapshotError(
                            "couldn't check its worktree before a read-only turn "
                            "(git failed there), so it didn't run") from exc
            if room_mode == "manual":
                # The approve tool must know where to ask: without this every
                # permission prompt was denied "No Quorus room bound".
                extra_kw["approval_room"] = room
            if worktree is not None and not readonly_turn:
                # Hold the worktree before the harness runs (codex, review of
                # 97d81fb8): a crash or a failed turn then can't leave commits
                # a restarted daemon would publish. Only a clean finish clears
                # it. No hold, no turn.
                try:
                    await asyncio.to_thread(reflexd_unfinished.hold,
                                            self._unfinished_dir, worktree)
                    await asyncio.to_thread(
                        reflexd_held.mark_window, self._held_dir, worktree,
                        reflexd_held.turn_start(history, envelope), context_ids)
                except OSError as exc:
                    raise reflexd_readonly.SnapshotError(
                        "couldn't record the turn before starting it (its hold "
                        "marker failed to write), so it didn't run") from exc
                # Commits held from an earlier turn: this turn must reapply or
                # drop them (reflexd_held).
                held_note = await asyncio.to_thread(reflexd_held.prompt_note,
                                                    self._held_dir, worktree)
                if held_note:
                    prompt = f"{prompt}\n\n{held_note}"
            self._record_turn()  # only a turn that runs counts against the pace
            reply = await self.adapter.run(
                harness, context=prompt,
                cwd=worktree.path if worktree is not None else ws,
                resume=prior_session, on_session=_persist_session,
                timeout_s=wake_timeout, max_turns=wake_max_turns, **extra_kw,
            )
        except Exception as exc:
            logger.warning("harness %s raised: %s", harness, exc)
            # Keep the D7 self-reply check: an adapter can fail after the
            # agent already posted its answer. Never expose raw exception
            # text here, which may contain credentials or prompt contents.
            failure_reason = (
                "timed out" if isinstance(exc, (TimeoutError, asyncio.TimeoutError))
                else str(exc) if isinstance(exc, reflexd_readonly.SnapshotError)
                else f"crashed ({type(exc).__name__})"
            )
            reply = "[reflexd] harness errored"

        vendor_failure = reply if isinstance(reply, _failover.VendorFailure) else None
        exit_code = reply.exit_code if isinstance(reply, HarnessExitFailure) else "unknown"
        # Classify before redaction turns the reply into a plain str.
        turn_outcome = _track.classify_turn(
            reply if isinstance(reply, str) else "", vendor_failure=bool(vendor_failure),
            failure_reason=failure_reason or "", slow_start=isinstance(reply, SlowStart))
        # The reply is auto-posted to the room: mask any credential the agent
        # printed (prompt injection or not) before it is published.
        reply = redact_secrets((reply or "").strip(),
                               [self.config.api_key, *env_secret_values(os.environ)])
        unknown_exit = reply == "[reflexd] harness errored"
        kind = triage.kind if triage is not None else ""
        _track.record_turn(
            self.config.runtime_dir, self.config.participant_name, turn_outcome,
            kind=kind, scope=self._track_scope(workspace_for(room) if room else None),
            model=str(getattr(self.adapter, "model", None) or ""),
            started=wake_started.timestamp(),
            prediction_id=prediction_id if kind == "open_todo" else "",
            message_id=(envelope.get("message_id") or envelope.get("id") or "")
            if kind == "open_todo" else "")
        # Didn't finish cleanly: its commits are held back, not published.
        turn_failed = bool(vendor_failure or failure_reason or exit_code != "unknown"
                           or reply.startswith(("[reflexd]", "⚠")))
        previous_degraded = self._degraded_reason
        if vendor_failure:
            self._degraded_reason = vendor_failure.reason
        elif reply and not failure_reason and not reply.startswith(("[reflexd]", "⚠")):
            self._degraded_reason = ""
        if self._degraded_reason != previous_degraded:
            try:
                await _failover.save_reason(self.config, self._degraded_reason)
            except OSError:
                logger.warning("could not persist degraded vendor state")
        if reflexd_wake.is_no_reply(reply):
            # The agent read the message and chose not to answer.
            logger.info("agent chose NO_REPLY room=%s", room)
            reply = ""
        elif reply:
            # "NO_REPLY? No — …" leaked the protocol token into chat (BUG 10).
            scrubbed = reflexd_wake.scrub_no_reply(reply)
            if scrubbed != reply:
                logger.info("scrubbed a NO_REPLY leak room=%s", room)
            reply = scrubbed
        if worktree is not None and (await self._settle_readonly(
                relay, room, worktree, readonly_snap, envelope) or readonly_turn):
            pass  # a read-only turn never publishes, whatever its harness wrote
        elif worktree is not None and turn_failed:
            # Cut off or failed mid-work: hold first (fail closed), then copy
            # its commits to <branch>-unfinished for review. Never to the
            # room's branch (c36101ec: a half-written CHANGELOG reached main).
            parked = None
            self._unfinished_held.add(str(worktree.path))
            try:
                await asyncio.to_thread(reflexd_unfinished.hold, self._unfinished_dir,
                                        worktree)
                parked = await asyncio.to_thread(
                    reflexd_unfinished.park, self._unfinished_dir, worktree,
                    self.config.participant_name,
                    f"wip: unfinished turn by {self.config.participant_name}")
            except (OSError, subprocess.SubprocessError) as exc:
                logger.exception("couldn't park an unfinished turn: %s", exc)
            else:
                if not parked:  # nothing unpublished: park lifted the hold
                    self._unfinished_held.discard(str(worktree.path))
            if parked and reply == "[reflexd] harness timed out":
                reply = reflexd_unfinished.notice(parked, self._activity)
            elif parked:
                await self._post_status(relay, room, envelope,
                                        reflexd_unfinished.notice(parked))
        elif worktree is not None:
            try:
                # A clean finish lifts an unfinished turn's hold. If that fails,
                # the except below keeps the work on its branch, unpublished.
                await asyncio.to_thread(reflexd_unfinished.clear, self._unfinished_dir,
                                        worktree)
                self._unfinished_held.discard(str(worktree.path))
                for line in await asyncio.to_thread(
                        reflexd_held.resolve, self._held_dir, worktree, reply or "",
                        self.config.participant_name):
                    await self._post_status(relay, room, envelope, line)
                published = await self._guard_publish(relay, room, worktree)
                if published is None:
                    published = await self._publish_with_resolution(
                        worktree, harness=harness, resume=prior_session,
                        on_session=_persist_session, timeout_s=wake_timeout,
                        max_turns=wake_max_turns, writable_root=ws, summary=reply,
                        mode=room_mode,
                        approval_room=room if room_mode == "manual" else None,
                        room=room, relay=relay,
                    )
                    await asyncio.to_thread(reflexd_held.settle_window, self._held_dir,
                                            worktree)
            except asyncio.CancelledError:
                raise
            except Exception as exc:  # never lose the agent's reply over git
                logger.exception("publish step failed: %s", exc)
                published = (f"{reflexd_wake.STATUS_PREFIX} couldn't publish this "
                             f"work automatically ({exc.__class__.__name__}); it is "
                             f"kept on {worktree.branch}")
            if published:
                logger.info("%s", published)
                reply = f"{reply}\n{published}" if reply else published
        if pre_head and reply:
            try:
                bearer = await relay.bearer_jwt()
                line = await _review_share.attach_bundle(
                    relay_url=self.config.relay_url, bearer=bearer, room=room,
                    worktree=worktree, pre_head=pre_head, reply=reply,
                    me=self.config.participant_name)
            except (httpx.HTTPError, AttributeError) as exc:
                logger.warning("review bundle not shared: %s", exc)
                line = None
            if line:
                reply = f"{reply}\n{line}"
        if not reply:
            logger.info("harness produced empty reply, skipping post")
            # Silence looked like a dead agent (Arav, 2026-10-17): say it read
            # the message. `status` messages never wake anyone.
            if time.time() - self._last_spoke.get(room, 0.0) > SILENCE_STATUS_EVERY_S:
                # Once per agent per room per 10 min: a busy agent's trail of
                # "nothing to add" lines was noise, not reassurance.
                self._last_spoke[room] = time.time()
                await self._post_status(relay, room, envelope,
                                        f"@{self.config.participant_name} read it, nothing to add")
            return

        if vendor_failure:
            outcome = await _failover.attempt(
                self, relay, envelope, reason=vendor_failure.reason, mission=mission,
                worktree=worktree, mode=room_mode, detect=detect_harness,
                capabilities=capabilities_for, score=compute_bid_v2,
            )
            if outcome == "sent":
                return
            if outcome:
                reply += " " + outcome

        # D7 (WAKE_REBUILD_SPEC): a woken Claude Code session is fully
        # agentic — per QOD it often posts its reply ITSELF via quorus
        # tools (inheriting RELAY_URL/API_KEY from our env) and its stdout
        # never returns, so the subprocess path reports timeout/error even
        # though the wake SUCCEEDED. Before posting an outcome-unknown
        # failure sentinel, check whether our own reply already landed on
        # this thread; if so, the wake worked — stay silent.
        _UNKNOWN_OUTCOME = ("[reflexd] harness timed out",
                            "[reflexd] harness errored",
                            "[reflexd] (no reply)")
        if reply in _UNKNOWN_OUTCOME or unknown_exit:
            try:
                recent = await relay.fetch_recent(room=room, limit=20)
                wake_id = envelope_canonical_id(envelope)
                for m in recent:
                    if (m.get("from_name") == self.config.participant_name
                            and (m.get("reply_to") == wake_id
                                 or m.get("thread_root_id") == wake_id)):
                        logger.info(
                            "wake succeeded via agent's own post (id=%s); "
                            "suppressing %r", m.get("id"), reply,
                        )
                        self._mark_answered(room, context_ids)
                        return
            except Exception as exc:
                logger.debug("D7 self-reply check failed: %s", exc)

        if reply == "[reflexd] (no reply)":
            # Never post a junk sentinel into a human's room.
            logger.warning("harness %s produced no reply text room=%s", harness, room)
            return

        if failure_reason:
            status = (
                f"⚠ {harness} work session {failure_reason}. "
                "Mention me to retry."
            )
            reply = reply.replace("[reflexd] harness errored", status, 1)
        elif unknown_exit:
            status = (f"⚠ {harness} exited {exit_code} with no known cause; "
                      "mention me to retry")
            reply = reply.replace("[reflexd] harness errored", status, 1)

        self._note_outcome(reply)
        self._alert_owner(room, reply, asked_by=envelope.get("from_name") or None)

        # Don't double-post: if the agent already posted this result itself
        # (QOD tells it to post ✅), the room has it.
        try:
            mine = [
                m for m in await relay.fetch_recent(room=room, limit=10)
                if m.get("from_name") == self.config.participant_name
                and _ts_after(m.get("timestamp"), wake_started)
            ]
        except Exception:
            mine = []
        if any(_same_result(m.get("content") or "", reply) for m in mine):
            # The agent posted its own ✅, but its final reply may carry what
            # that post lacked: the @-mention handing off to a reviewer, and
            # the daemon's publish line. Dropping those stalled the loop
            # (live 2026-10-08: review never requested). Post just those.
            leftover = _undelivered_lines(reply, [m.get("content") or "" for m in mine])
            if not leftover:
                logger.info("agent already posted its result room=%s — not reposting", room)
                self._mark_answered(room, context_ids)
                return
            logger.info("agent posted its result; posting %d undelivered line(s)",
                        len(leftover))
            reply = "\n".join(leftover)

        # D5b: a mission agent that hit its (long) leash gets an escalation
        # humans can act on, never a bare sentinel. This runs AFTER the D7
        # check on purpose: building it earlier meant the escalation never
        # matched _UNKNOWN_OUTCOME, so a mission wake that had already
        # posted its own reply still got a contradictory "it stalled" line.
        if mission and reply == "[reflexd] harness timed out":
            task_title = str(
                mission.get("title") or mission.get("description")
                or mission.get("summary") or mission.get("task_id")
                or "its claimed task"
            )[:80]
            limit_min = max(1, (wake_timeout or SUBPROCESS_TIMEOUT_S) // 60)
            reply = (
                f"⚠ still holding the claim on '{task_title}' but my last "
                f"work session hit the {limit_min}m limit. "
                "Mention me to continue, send /interrupt to stop me, or "
                "release the task."
            )
        elif reply == "[reflexd] harness timed out":
            reply = _timed_out_line(last_step=self._activity)

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
            self._mark_answered(room, context_ids)
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

    async def _pause_unannounced(
        self, relay: RelayClient, room: str, recent: list[dict[str, Any]],
        marker: str = PAUSE_NOTICE,
    ) -> bool:
        """Every daemon in the room hits the guard on the same message: jitter,
        re-read, and announce only if nobody has yet (one notice per pause)."""
        if _pause_announced(recent, marker):
            return False
        await asyncio.sleep(random.uniform(0.0, 6.0))
        try:
            recent = await relay.fetch_recent(room=room, limit=30)
        except Exception:
            return False
        return not _pause_announced(recent, marker)

    def _track_scope(self, repo: Path | None) -> str:
        """relay|owner|repo: L2 evidence learned in one context stays there.
        The relay is scheme + host + base path, so two relays behind one host
        (``/team-a``, ``/team-b``) never share evidence."""
        url = urlparse(self.config.relay_url)
        relay = f"{url.scheme}://{url.netloc}{url.path.rstrip('/')}"
        return "|".join((relay,
                         agent_owner(self.config.participant_name), str(repo or "")))

    async def _announce_adaptation(
        self, relay: RelayClient, room: str, envelope: dict[str, Any], text: str,
    ) -> None:
        """A self-tuning line is its owner's business, so it never goes to a room
        (0.6.3 multi-person audit, BUG 18: three daemons' "(shadow, not applied)"
        notes reached friends; Codex: a member list can't prove a room is private,
        "arav-friend" is a human). It goes to the owner as a DM, which the hub
        shows, and `quorus tune list` always has it. The board's tune row reads
        status lines, so it no longer shows these; `quorus tune` is the affordance."""
        del envelope
        owner = agent_owner(self.config.participant_name)
        try:
            await relay.post_agent_dm(sender=self.config.participant_name, recipient=owner,
                                      content=f"[#{room}] {text}")
        except Exception as exc:  # the line is also in `quorus tune list`
            logger.debug("tuning note to owner failed: %s", exc)

    async def _post_status(
        self, relay: RelayClient, room: str, envelope: dict[str, Any], text: str,
    ) -> None:
        """A quiet ``status`` line in the room (rendered dim, ignored by triage)."""
        try:
            parent = envelope_canonical_id(envelope) if envelope else None
            await relay.post_reply(room=room, from_name=self.config.participant_name,
                                   content=text, message_type="status",
                                   reply_to=parent or None)
        except Exception as exc:  # cosmetic: never fail a wake over it
            logger.debug("status post failed: %s", exc)

    def _name_taken(self, reason: str) -> None:
        """The relay won't let this agent sign in as itself until its owner frees
        the name. It can't say so in the room (that needs a sign-in, and falling
        back to the shared secret is refused), so: one desktop banner and the log."""
        logger.error("cannot sign in: %s", reason)
        if not _agent_events.enabled():
            return
        try:
            asyncio.get_running_loop().run_in_executor(None, lambda: _notify(
                f"{self.config.participant_name} can't sign in", reason,
                sender=self.config.participant_name))
        except RuntimeError:
            pass

    def _alert_owner(self, room: str, reply: str, asked_by: str | None = None) -> None:
        """Desktop banner for the agent's owner when the wake shipped work,
        needs them, or got stuck. Fire-and-forget; never blocks the reply."""
        if not _agent_events.enabled():
            return
        alert = _agent_events.owner_alert(
            agent=self.config.participant_name,
            owner=os.environ.get("REFLEXD_OWNER") or None, reply=reply,
            asked_by=asked_by,
        )
        if alert is None:
            return
        title, body = alert
        if title.endswith(" is stuck"):
            # One banner per agent per hour: rate limits fired one per failed
            # wake, 15 "opencode is stuck" in a day (2026-10-17). The room
            # still shows each one.
            if time.time() - self._stuck_alerted_at < STUCK_ALERT_EVERY_S:
                logger.info("owner alert suppressed room=%s: %s (sent recently)", room, title)
                return
            self._stuck_alerted_at = time.time()
        logger.info("owner alert room=%s: %s", room, title)  # never the body
        try:
            asyncio.get_running_loop().run_in_executor(
                None, lambda: _notify(f"{title} · #{room}", body,
                                      sender=self.config.participant_name, room=room))
        except RuntimeError:  # no loop (sync caller in tests)
            _notify(f"{title} · #{room}", body, sender=self.config.participant_name,
                    room=room)

    def build_prompt(
        self, envelope: dict[str, Any], history: list[dict[str, Any]],
        *, triage: "TriageResult | None" = None,
        memory_entries: list[dict[str, Any]] | None = None,
        **kw: Any,
    ) -> str:
        """Public alias for :meth:`_build_prompt`.

        Smoke-test and external callers should prefer this entry point so
        the implementation can refactor freely without breaking them.
        """
        return self._build_prompt(
            envelope, history, triage=triage, memory_entries=memory_entries,
            **kw,
        )

    async def _read_only_for(self, relay: Any, envelope: dict[str, Any], room: str) -> bool:
        """This request would get a read-only turn here: the same trust check a
        wake makes (every mode, `default` included; `trust: members`, owner pins
        and signatures as in :meth:`_trusted_wake`). Unknown (history unreadable)
        is not read-only: never lower a bid on a guess."""
        if not room:
            return False
        fetch = getattr(relay, "fetch_recent", None)
        if fetch is None:
            return False
        try:
            history = await fetch(room=room, limit=TRUST_CHAIN_LIMIT)
        except Exception:
            return False
        return not await self._trusted_wake(relay, envelope, history)

    async def _trusted_wake(self, relay: Any, envelope: dict[str, Any],
                            history: list[dict[str, Any]]) -> bool:
        """Whether this wake may run write-capable (see :func:`trusted_origin`)."""
        room = envelope.get("room") or ""
        pin = _owner_pin.owner_pin(self.config.participant_name)
        if pin is not None:
            # An invitee's agent on a teammate's relay: the name proves nothing
            # there, only the owner's own key does (quorus/runtime/owner_pin.py).
            return await self._pinned_owner_wake(relay, envelope, history, room, pin)
        handoff = _failover.continuation(envelope)
        if handoff and not envelope.get("reply_to"):
            # A failover handoff carries no reply_to; it serves the request it names,
            # so trust is traced from there (unchanged rules, just the right start).
            envelope = {**envelope, "reply_to": handoff["root"]}
        room = envelope.get("room") or ""
        if trust_for(room) != "owner":
            return await self._member_trusted(relay, envelope, history)
        owner = agent_owner(self.config.participant_name)
        if trusted_origin(envelope, history, owner):
            return True
        sender = envelope.get("from_name") or ""
        if sender == owner or not trusted_sender(sender, owner):
            return False
        try:  # the owner's request may sit further back than the prompt's history
            longer = await relay.fetch_recent(room=room, limit=TRUST_CHAIN_LIMIT)
        except Exception:
            return False
        return trusted_origin(envelope, longer, owner)

    async def _pinned_owner_wake(self, relay: Any, envelope: dict[str, Any],
                                 history: list[dict[str, Any]], room: str,
                                 pin: dict[str, str]) -> bool:
        """Write-capable only for a request the pinned owner sent with their own
        key, traced through the owner's agents; room trust settings don't widen it."""
        owner = agent_owner(self.config.participant_name)
        try:  # stamps live on stored messages: read them from the relay's history
            stored = await relay.fetch_recent(room=room, limit=TRUST_CHAIN_LIMIT)
        except Exception:
            return False
        ok = _owner_pin.pinned_owner_request(envelope, [*stored, *history], owner, pin)
        if not ok:
            logger.info("read-only turn: not the pinned owner's own key")
        return ok

    async def _member_trusted(self, relay: Any, envelope: dict[str, Any],
                              history: list[dict[str, Any]]) -> bool:
        """`trust: members`: a write-capable turn only when the request comes from a
        person who, on the relay right now, may change the room.

        The sender must hold a writer role. A person's own message counts as is. An
        agent's message counts only if its reply chain leads, through writers only,
        to a person with a writer role: an agent handing over work with no traceable
        request (no reply_to, an ancestor missing even after fetching older history,
        or a cycle) gets a read-only turn, so an observer can't get work done by
        asking an agent that then asks this one (codex, review of f5d69289). Unknown
        roles, or a relay that can't say, mean read-only too."""
        room = envelope.get("room") or ""
        lookup = getattr(relay, "fetch_member_roles", None)
        roles = await lookup(room=room) if lookup else None
        if not roles:
            return False

        def writer(name: str) -> bool:
            return roles.get(name) in WRITER_ROLES

        sender = envelope.get("from_name") or ""
        if not writer(sender):
            return False
        if not is_agent_participant(sender):
            return True
        by_id = {m.get("id"): m for m in history if m.get("id")}
        fetched_more = False
        cur, seen = envelope.get("reply_to"), set()
        while True:
            if not cur or cur in seen:
                return False  # no traceable request, or a cycle
            if cur not in by_id and not fetched_more:
                fetched_more = True
                try:
                    longer = await relay.fetch_recent(room=room, limit=TRUST_CHAIN_LIMIT)
                except Exception:
                    return False
                by_id.update({m.get("id"): m for m in longer if m.get("id")})
            if cur not in by_id:
                return False  # the chain leaves what the relay keeps
            seen.add(cur)
            who = by_id[cur].get("from_name") or ""
            if not writer(who):
                return False
            if not is_agent_participant(who):
                return True  # reached the person who asked
            cur = by_id[cur].get("reply_to")

    def _build_prompt(
        self, envelope: dict[str, Any], history: list[dict[str, Any]],
        *, triage: "TriageResult | None" = None,
        memory_entries: list[dict[str, Any]] | None = None,
        teammates: list[str] | None = None,
        has_workspace: bool = True,
        worktree: Any = None,
        shared_note: str = "",
        trusted: bool | None = None,
        harness: str | None = None,
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
            reflexd_wake.quote_room_message(m.get("from_name") or m.get("sender"),
                                            m.get("content"))
            for m in recent
        ]
        history_block = "\n".join(lines) if lines else "(no prior messages)"
        wake_sender = envelope.get("from_name") or "?"
        wake_content = envelope.get("content") or ""
        continuation = _failover.continuation(envelope)
        if continuation:
            wake_content = _failover.handoff_line(self.config.participant_name, continuation)
        wake_quoted = reflexd_wake.quote_room_message(
            wake_sender, wake_content, limit=reflexd_wake.TRIGGER_MESSAGE_CHARS)
        nonce = os.urandom(6).hex()
        wake_room = envelope.get("room") or "?"
        wake_depth = envelope_reply_depth(envelope)

        # The old blurb told a mentioned agent to "reply in 1-3 lines and not
        # run tools" — a mention asking for a fix could never be done (seen
        # live 2026-10-08). The wake instructions let the agent work, name
        # its teammates for handoffs, and keep the final reply short.
        preamble = ""
        kind = triage.kind if triage is not None else "mention"
        if triage is not None and triage.reason == "agent to room":
            kind = "agent_room"  # the agent decides whether to answer (or NO_REPLY)
        elif kind == "question" and not is_agent_participant(wake_sender):
            kind = "room"  # every agent got it and each one answers
        if kind in ("open_todo", "role_request"):
            preamble = self_assign_preamble(description=triage.description) + "\n"
            kind = "open_todo"
        owner = agent_owner(self.config.participant_name)
        room_mode = mode_for(wake_room)
        trusted_now = (trusted if trusted is not None
                       else trusted_origin(envelope, history, owner))
        # The owner asked, and this harness is known to run read-only in this
        # room's mode: tell it once, with the command, rather than let it try,
        # fail and defer in a loop (0.6.0 stranger blocker). Never for an
        # untrusted sender: they get the read-only line and no way to widen it.
        cannot_write = (
            f"#{wake_room} is in `{room_mode}` mode and {harness} runs read-only in "
            f"it; your owner can change that with `quorus agent add {harness} --room "
            f"{wake_room} --mode autonomous`"
            if harness and trusted_now and has_workspace
            and reflexd_wake.cannot_write(harness, room_mode) else None)
        intent_blurb = reflexd_wake.wake_instructions(
            participant=self.config.participant_name, room=wake_room,
            sender=wake_sender, kind=kind, teammates=list(teammates or []),
            has_workspace=has_workspace, worktree=worktree,
            from_agent=is_agent_participant(wake_sender),
            manual=room_mode == "manual",
            lead=lead_for(wake_room),
            owner=os.environ.get("REFLEXD_OWNER") or None,
            untrusted=(owner if room_mode != "default"
                       and trust_for(wake_room) == "owner"
                       and not trusted_now
                       else None),
            cannot_write=cannot_write,
        )

        memory_block = render_memory_context(memory_entries or [])

        return (
            f"{preamble}"
            f"{render_qod_for_agent_loop()}\n\n"
            f"{memory_block}"
            f"# Wake Intent\n"
            f"{intent_blurb}\n\n"
            f"# Reply chain depth: {wake_depth} (max {MAX_REPLY_DEPTH})\n\n"
            f"{shared_note}"
            f"{reflexd_wake.UNTRUSTED_OPEN.format(nonce=nonce)}\n"
            f"# Recent room transcript (most recent last)\n{history_block}\n\n"
            f"# Triggering message\n{wake_quoted}\n"
            f"{reflexd_wake.UNTRUSTED_CLOSE.format(nonce=nonce)}\n"
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
            participant=self.config.participant_name,
            key_dir=self.config.runtime_dir / "agents" / self.config.participant_name,
            on_name_taken=self._name_taken,
        ) as relay:
            self._relay = relay  # sign-in tells _agent_credential what the relay supports
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

            # Publish work stranded by a crash/restart or a sandbox that
            # couldn't rebase: rebase + fast-forward each bound repo's branch.
            await self._publish_sweep(relay)

            # Stream B: subscribe to the agent-DM stream in parallel with
            # the room SSE stream. Failures in the DM loop never bring
            # down the main loop — both reconnect independently.
            dm_task = asyncio.create_task(self._dm_loop(relay))
            # R2: presence heartbeat so the relay can render active/away and
            # queue depth for this agent. Independent of the SSE loop.
            hb_task = asyncio.create_task(self._heartbeat_loop(relay))
            drain_task = asyncio.create_task(self._periodic_drain(relay))

            try:
                while not self._stop.is_set():
                    try:
                        stream_token = await relay.mint_stream_token(self.config.participant_name)
                    except httpx.HTTPError as exc:
                        logger.warning("stream token mint failed: %s", exc)
                        await self._sleep_or_stop(backoff)
                        backoff = min(backoff * 2, SSE_RECONNECT_MAX_S)
                        continue

                    # R1: drain anything that arrived while we were down or
                    # the laptop was asleep. SSE is transient; the durable
                    # inbox is the source of truth for missed mentions. Runs
                    # on every (re)connect, before going live on the stream.
                    await self._drain_inbox(relay)
                    await self._drain_busy_queue(relay)

                    url = relay.stream_url(self.config.participant_name, stream_token)
                    connected_at = time.monotonic()
                    try:
                        async for event_name, data in iter_sse_events(relay.client, url):
                            if self._stop.is_set():
                                return
                            await self._dispatch_event(
                                relay, event_name, data, background=True,
                            )
                        backoff = SSE_RECONNECT_S
                    except httpx.HTTPError as exc:
                        logger.warning("sse stream dropped: %s", exc)
                        # D4: a connection that lived >60s and then died is a
                        # network transition (laptop sleep/wake, wifi change),
                        # not a struggling relay — reconnect fast instead of
                        # continuing the exponential climb.
                        if time.monotonic() - connected_at > 60:
                            backoff = SSE_RECONNECT_S
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # pragma: no cover
                        logger.exception("sse loop unexpected error: %s", exc)

                    await self._sleep_or_stop(backoff)
                    backoff = min(backoff * 2, SSE_RECONNECT_MAX_S)
            finally:
                for task in (dm_task, hb_task, drain_task, *self._bg_tasks):
                    task.cancel()
                    try:
                        await task
                    except (asyncio.CancelledError, Exception):
                        pass

    async def _publish_with_resolution(
        self, wt: Any, *, harness: str, resume: str | None,
        on_session: Callable[[str], None], timeout_s: int | None,
        max_turns: int | None, writable_root: Path | None,
        summary: str | None = None, mode: str = "default",
        approval_room: str | None = None, room: str = "",
        relay: "RelayClient | None" = None,
    ) -> str | None:
        """Publish the agent's branch; on a conflict, wake the SAME agent (same
        session) to resolve the merge left in its worktree, then retry once.
        Overnight, nobody is there to resolve it by hand."""
        async def publish() -> str | None:
            try:
                return await asyncio.to_thread(
                    reflexd_wake.publish_worktree, wt, self.config.participant_name,
                    {"agent": self.config.participant_name, "vendor": harness,
                     "model": getattr(self.adapter, "model", None), "room": room})
            except (OSError, subprocess.SubprocessError) as exc:
                return f"{reflexd_wake.STATUS_PREFIX} publish check failed: {exc}"

        status = await publish()
        if not (status or "").startswith(reflexd_wake.CONFLICT_MARK) and \
                await asyncio.to_thread(reflexd_wake.worktree_dirty, wt):
            # Finished work left uncommitted (Codex's sandbox blocks git, or
            # the agent forgot): the daemon commits it as the agent, then
            # publishes. Cheaper and more reliable than waking it again.
            message = reflexd_wake.commit_message_from(summary or "", self.config.participant_name)
            if await asyncio.to_thread(reflexd_wake.commit_all, wt,
                                       self.config.participant_name, message):
                logger.info("committed %s's uncommitted work: %s",
                            self.config.participant_name, message)
                status = await publish()
        if not (status or "").startswith(reflexd_wake.CONFLICT_MARK):
            return status
        logger.info("%s — waking %s to resolve", status, self.config.participant_name)
        prompt = reflexd_wake.RESOLVE_PROMPT.format(
            main=wt.main, branch=wt.branch, path=wt.path,
        )
        extra_kw: dict[str, Any] = {"writable_roots": [writable_root]} if writable_root else {}
        # The resolution wake must keep the room's permission mode: without it
        # a manual room's agent resolved conflicts with no approvals, and an
        # autonomous Codex ran read-only and could never resolve (review).
        if mode != "default":
            extra_kw["mode"] = mode
        if approval_room:
            extra_kw["approval_room"] = approval_room
        try:
            note = await self.adapter.run(
                harness, context=prompt, cwd=wt.path, resume=resume,
                on_session=on_session, timeout_s=timeout_s, max_turns=max_turns,
                **extra_kw,
            )
        except Exception as exc:  # never lose the branch over a failed wake
            logger.warning("conflict-resolution wake failed: %s", exc)
            note = ""
        await asyncio.to_thread(
            reflexd_wake.finish_merge, wt, self.config.participant_name)
        unresolved = await asyncio.to_thread(reflexd_wake.merge_in_progress, wt)
        if not unresolved and relay is not None:
            # The resolution wake took time: re-check the room before publishing.
            held = await self._guard_publish(relay, room, wt)
            if held is not None:
                return held
        status = None if unresolved else await publish()
        if unresolved or (status or "").startswith(reflexd_wake.CONFLICT_MARK):
            await asyncio.to_thread(reflexd_wake.abort_merge, wt)
            return (f"{reflexd_wake.STATUS_PREFIX} {wt.branch} still conflicts with "
                    f"{wt.main} after one resolution attempt; commits kept on the "
                    "branch for a human")
        resolved = (note or "").strip().splitlines()[:1]
        return "\n".join([*(f"{reflexd_wake.STATUS_PREFIX} resolved: {line}" for line in
                             resolved), status or ""]).strip() or None

    @property
    def _readonly_dir(self) -> Path:
        return self.config.runtime_dir / "readonly-wakes"

    @property
    def _unfinished_dir(self) -> Path:
        return self.config.runtime_dir / "unfinished-wakes"

    @property
    def _held_dir(self) -> Path:
        return self.config.runtime_dir / "held-wakes"

    async def _guard_publish(self, relay: RelayClient, room: str, wt: Any) -> str | None:
        """Before publishing *wt*: if the owner or the room's lead @-mentioned
        this agent since its oldest unpublished turn started (or that history
        can't be read in full), hold the commits on a fresh ref instead and
        return the room status. None means publish as usual (reflexd_held)."""
        try:
            if await asyncio.to_thread(reflexd_held.blocks_publish, self._held_dir, wt):
                return (f"{reflexd_wake.STATUS_PREFIX} held commits are still on "
                        f"{wt.branch} behind a merge in progress; not published")
            ahead = await asyncio.to_thread(reflexd_held.oldest_commit_time, wt)
            if ahead is None and not await asyncio.to_thread(reflexd_wake.worktree_dirty, wt):
                await asyncio.to_thread(reflexd_held.settle_window, self._held_dir, wt)
                return None  # nothing unpublished
            since = await asyncio.to_thread(reflexd_held.window, self._held_dir, wt)
            since = since if since is not None else (ahead or time.time())
        except (OSError, subprocess.SubprocessError) as exc:
            logger.warning("publish guard couldn't inspect %s: %s", wt.path, exc)
            return (f"{reflexd_wake.STATUS_PREFIX} couldn't check {wt.branch} before "
                    "publishing; kept unpublished")
        me = self.config.participant_name
        authorities = {agent_owner(me)}
        lead = lead_for(room)
        if lead and lead != me:
            authorities.add(lead)
        try:
            fetch = getattr(relay, "fetch_history", None) or relay.fetch_recent
            history = await fetch(room=room, limit=reflexd_held.HISTORY_LIMIT)
        except Exception as exc:  # any failure: hold, never publish blind
            logger.warning("publish guard history fetch failed: %s", exc)
            trigger, why = None, "the room's history couldn't be read before publishing"
        else:
            seen = await asyncio.to_thread(reflexd_held.seen_ids, self._held_dir, wt)
            trigger, why = reflexd_held.find_stop(history, me=me, authorities=authorities,
                                                  since=since, seen=seen)
        if trigger is None and not why:
            return None
        try:
            ref = await asyncio.to_thread(reflexd_held.hold, self._held_dir, wt, me,
                                          trigger=trigger, why=why)
            await asyncio.to_thread(reflexd_held.settle_window, self._held_dir, wt)
        except (OSError, subprocess.SubprocessError) as exc:
            logger.error("publish guard couldn't hold %s: %s", wt.path, exc)
            return (f"{reflexd_wake.STATUS_PREFIX} couldn't hold {wt.branch}'s commits "
                    "after a mid-turn message; kept unpublished")
        if ref is None:
            return None
        shas = (await asyncio.to_thread(reflexd_held._entries, self._held_dir, wt))[-1]["shas"]
        return reflexd_held.status(ref, shas, trigger, why)

    async def _settle_readonly(self, relay: RelayClient, room: str, worktree: Any,
                               snap: dict[str, str] | None,
                               envelope: dict[str, Any] | None = None) -> bool:
        """True when *worktree* is under a read-only turn (this one, or one a
        crash left behind) and so must not be published. Its changes are kept
        under refs/quorus/discarded/... and the worktree is put back."""
        try:
            snap = snap or await asyncio.to_thread(
                reflexd_readonly.pending, self._readonly_dir, worktree.path)
            if snap is None:
                return False
            ref = await asyncio.to_thread(
                reflexd_readonly.settle, self._readonly_dir, worktree.path, snap,
                self.config.participant_name)
        except (OSError, subprocess.SubprocessError, reflexd_readonly.MarkerError) as exc:
            # Fail closed: the marker stays, so nothing publishes this worktree
            # until a human resolves it. Say so once, not on every wake.
            logger.error("read-only turn: couldn't settle %s: %s", worktree.path, exc)
            key = str(worktree.path)
            if key not in self._readonly_warned:
                self._readonly_warned.add(key)
                await self._post_status(
                    relay, room, envelope or {},
                    "⚠ a read-only turn's changes couldn't be checked or reset, so "
                    f"nothing from {worktree.branch} will be published until the "
                    f"marker in {self._readonly_dir} is resolved")
            return True
        self._readonly_warned.discard(str(worktree.path))
        if ref:
            logger.warning("read-only turn changed files; discarded to %s", ref)
            await self._post_status(relay, room, envelope or {},
                                    reflexd_readonly.notice(ref))
        return True

    async def _publish_sweep(self, relay: RelayClient) -> None:
        try:
            bindings = json.loads(ROOM_BINDINGS_PATH.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            return
        if not isinstance(bindings, dict) or not reflexd_wake.WORKTREES_ENABLED:
            return
        seen: set[Path] = set()
        for room, raw in bindings.items():
            if isinstance(raw, dict):
                raw = raw.get("path")
            if not isinstance(raw, str) or not Path(raw).expanduser().is_dir():
                continue
            repo = Path(raw).expanduser().resolve()
            if repo in seen:  # several rooms can share one repo: sweep it once
                continue
            seen.add(repo)
            try:
                wt = await asyncio.to_thread(
                    reflexd_wake.existing_worktree, repo, self.config.participant_name,
                )
                if wt is not None and await self._settle_readonly(relay, room, wt, None):
                    continue  # a read-only turn's leftovers are never published
                if wt is not None and (str(wt.path) in self._unfinished_held or
                                       reflexd_unfinished.pending(self._unfinished_dir, wt.path)):
                    continue  # a cut-off turn waits for review or its finishing turn
                status = await self._guard_publish(relay, room, wt) if wt is not None else None
                if status is None and wt is not None:
                    status = await asyncio.to_thread(
                        reflexd_wake.publish_worktree, wt, self.config.participant_name)
                    await asyncio.to_thread(reflexd_held.settle_window, self._held_dir, wt)
                if wt is not None and (status or "").startswith(reflexd_wake.CONFLICT_MARK):
                    # No agent is awake to resolve it here: never leave a
                    # half-done merge behind. The next wake retries properly.
                    await asyncio.to_thread(reflexd_wake.abort_merge, wt)
                    status = (f"{reflexd_wake.STATUS_PREFIX} {wt.branch} conflicts "
                              f"with {wt.main}; will resolve on this agent's next wake")
            except (OSError, subprocess.SubprocessError) as exc:
                logger.warning("publish sweep failed for %s: %s", room, exc)
                continue
            if status:
                logger.info("publish sweep room=%s: %s", room, status)
                try:
                    await relay.post_reply(room=room, from_name=self.config.participant_name,
                                           content=status)
                except httpx.HTTPError as exc:
                    logger.warning("publish sweep post failed: %s", exc)

    async def _drain_busy_queue(self, relay: RelayClient) -> None:
        """Replay wakes deferred while the agent was mid-tool-call.

        The queue was append-only: TurnGuard parked envelopes here and
        NOTHING ever read them back, so a mention arriving during a
        Bash/Edit call was acked to the relay and dropped — the user even
        got a desktop notification for a message that would never be
        answered. Drains FIFO once the busy-file clears.
        """
        if not self._queue or is_busy(
            self.config.participant_name, self.config.runtime_dir,
        ):
            return
        parked, self._queue = self._queue, []
        logger.info("busy cleared — replaying %d deferred wake(s)", len(parked))
        for envelope in parked:
            if self._stop.is_set():
                return
            try:
                await self.handle_room_message(relay, envelope)
            except Exception as exc:  # pragma: no cover
                logger.exception("deferred wake failed: %s", exc)

    async def _drain_inbox(self, relay: RelayClient) -> None:
        """R1: fetch + dispatch + ack every message queued while offline.

        Loops until the inbox is empty so a long sleep (hundreds of queued
        messages) fully drains. Each batch is dispatched through the same
        triage path as live SSE events, then acked — at-least-once: a crash
        mid-batch redelivers after the visibility timeout rather than losing
        the mention. Failures are logged and non-fatal (the SSE loop still
        provides live delivery).
        """
        drained = 0
        while not self._stop.is_set():
            try:
                messages, ack_token = await relay.fetch_inbox(
                    participant=self.config.participant_name
                )
            except Exception as exc:
                logger.warning("inbox drain fetch failed: %s", exc)
                return
            if not messages:
                break
            for msg in messages:
                if self._stop.is_set():
                    return
                try:
                    # background: a drained job used to run inline, keeping the
                    # SSE reader deaf for a whole wake after every reconnect.
                    # In-flight jobs keep the batch unacked (see below).
                    await self._dispatch_event(relay, "message", msg, background=True)
                except Exception as exc:  # pragma: no cover
                    logger.exception("inbox drain handler failed: %s", exc)
            drained += len(messages)
            # Never ack a job that is still queued/running from the live
            # stream: the ack deletes the relay's copy, so a restart then
            # lost it forever (scenario gate S12, 2026-10-09). Leave the
            # batch unacked; it reappears after the visibility window and
            # the handled-id dedupe skips whatever finished meanwhile.
            pending = [
                c for c in ((m.get("message_id") or m.get("id") or "") for m in messages)
                if c and c in self._inflight
            ]
            if pending:
                logger.debug("inbox drain: %d job(s) still in flight — not acking",
                             len(pending))
                break
            if ack_token:
                try:
                    await relay.ack_inbox(
                        participant=self.config.participant_name,
                        ack_token=ack_token,
                    )
                except Exception as exc:
                    logger.warning("inbox drain ack failed: %s", exc)
                    return
            else:  # no token → server acked or nothing to ack; avoid spinning
                break
        if drained:
            logger.info("inbox drain: handled %d queued message(s)", drained)

    async def _periodic_drain(
        self, relay: RelayClient, interval: float = DRAIN_INTERVAL_S,
    ) -> None:
        """Re-drain the durable inbox while idle.

        A drain makes fetched messages invisible until acked; if the daemon
        restarts mid-drain they come back only after the visibility window —
        and the old code drained only on (re)connect, so they were never
        fetched again (two overnight tasks lost, 2026-10-08). Handled ids
        dedupe everything already answered."""
        while not self._stop.is_set():
            await self._sleep_or_stop(interval)
            if self._stop.is_set() or self._wake_lock.locked():
                continue
            try:
                await self._drain_inbox(relay)
            except Exception as exc:  # never kill the daemon over a drain
                logger.debug("periodic drain failed: %s", exc)

    async def _heartbeat_loop(
        self, relay: RelayClient, interval: float = HEARTBEAT_INTERVAL_S,
    ) -> None:
        """R2: periodic presence heartbeat until stopped. Never raises."""
        while not self._stop.is_set():
            try:
                status, room = self._presence
                extra = self._presence_extra(status)
                await relay.post_heartbeat(
                    participant=self.config.participant_name,
                    status=status, room=room, **extra,
                    **({"reason": self._degraded_reason} if status == "degraded" else {}),
                    # Lets people tell agents apart by model, without vendor logos.
                    **({"model": str(model)} if (model := getattr(self.adapter, "model", None))
                       else {}),
                )
            except Exception as exc:
                logger.debug("heartbeat failed: %s", exc)
            await self._sleep_or_stop(interval)

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except (TimeoutError, asyncio.TimeoutError):  # distinct classes on 3.10
            # L4: 3.11+ uses the builtin TimeoutError; asyncio.TimeoutError
            # is a deprecated alias.
            return

    async def _dispatch_event(
        self, relay: RelayClient, event_name: str, data: dict[str, Any] | None,
        *, background: bool = False,
    ) -> None:
        if not isinstance(data, dict):
            return
        # Dedupe across the two delivery paths. The relay dual-writes every
        # room message (durable per-recipient queue + SSE push) and the SSE
        # copy is never acked, so after a reconnect the drain returned
        # everything already answered live and the agent re-replied to
        # hour-old mentions. One id set covers both paths.
        canonical = (
            (data.get("message_id") or data.get("id") or "")
            if event_name == "message" else ""
        )
        if canonical:
            if canonical in self._handled_ids or canonical in self._inflight:
                logger.debug("already handled %s — skipping", canonical)
                return
            # In flight now (dedupes SSE vs inbox drain); persisted as handled
            # only once handling finishes, so a crash or restart mid-queue
            # redelivers the job instead of silently dropping it.
            self._inflight.add(canonical)
        if event_name == "connected":
            logger.info("sse connected")
            return
        if event_name != "message":
            return
        handoff = _failover.continuation(data)
        if data.get("message_type") not in (None, "chat", "request", "question") and not handoff:
            self._mark_handled(canonical)
        message_type = data.get("message_type") or "chat"
        if message_type == "wake_intent":
            # Server triage already broadcast; we still drive our own bid/claim.
            logger.debug("saw wake_intent room=%s from=%s",
                         data.get("room"), data.get("from_name"))
            return
        if message_type in {"chat", "request", "question"} or handoff:
            if background:
                # Live SSE: never block the reader on a bid window or a wake.
                task = asyncio.create_task(self._handle_safely(relay, data))
                self._bg_tasks.add(task)
                task.add_done_callback(self._bg_tasks.discard)
                return
            await self._handle_safely(relay, data)

    async def _handle_safely(self, relay: RelayClient, data: dict[str, Any]) -> None:
        canonical = data.get("message_id") or data.get("id") or ""
        try:
            await self.handle_room_message(relay, data)
        except asyncio.CancelledError:
            # Stopping (SIGTERM / launchd restart) while this job was queued or
            # running: do NOT mark it handled, so the inbox redelivers it. A
            # ``finally`` here persisted every queued job as done on restart
            # (lost a whole overnight queue, 2026-10-08).
            self._inflight.discard(canonical)
            raise
        except Exception as exc:  # pragma: no cover
            logger.exception("handler failed: %s", exc)
        self._mark_handled(canonical)

    def _mark_handled(self, canonical: str) -> None:
        if not canonical:
            return
        self._inflight.discard(canonical)
        if canonical in self._handled_ids:
            return
        self._handled_ids.add(canonical)
        self._handled_order.append(canonical)
        while len(self._handled_order) > _HANDLED_ID_CAP:
            self._handled_ids.discard(self._handled_order.pop(0))
        self._save_handled()


# ---------------------------------------------------------------------------
# Logging setup
# ---------------------------------------------------------------------------


def configure_logging(log_path: Path, *, debug: bool = False) -> None:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = logging.handlers.RotatingFileHandler(
        log_path, maxBytes=LOG_MAX_BYTES,
        backupCount=LOG_BACKUP_COUNT, encoding="utf-8",
    )
    handler.setFormatter(reflexd_streamb.StreamLogFormatter(
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
    versions = log_adapter_versions()

    # Announce real-vs-stub mode so the operator knows immediately whether
    # the claude CLI is reachable. Auto-falls-back to stub mode when the
    # binary is missing AND REFLEXD_STUB_REPLY is not set — the daemon
    # never refuses to start. Claude Code's own OAuth handles auth; we
    # never check ANTHROPIC_API_KEY here. Only for Claude agents: every
    # opencode daemon logged "ERROR claude CLI not found" (Linux run).
    if detect_harness(cfg.participant_name) == "claude":
        log_claude_adapter_status()

    if args.once:
        return await _run_once(cfg, args.once)

    pidfile = _pidfile()
    pidfile.write_text(str(os.getpid()), encoding="utf-8")
    adapter = HeadlessAdapter(dry_run=cfg.dry_run)
    adapter.opencode_v1 = reflexd_models.is_opencode_v1(versions.get("opencode"))
    daemon = Reflexd(cfg, adapter=adapter)

    loop = asyncio.get_running_loop()
    main = asyncio.current_task()

    def _stop(*_a: Any) -> None:
        # Setting the stop event alone never worked: an idle SSE read only
        # yields on real events (keepalive comments are skipped), so SIGTERM
        # was ignored until the next message — and the relay, waiting for
        # this open stream, hung on shutdown too. Cancel the run outright.
        logger.info("signal received, stopping")
        daemon.stop()
        if main is not None:
            main.cancel()

    for sig in (signal.SIGINT, signal.SIGTERM):
        try:
            loop.add_signal_handler(sig, _stop)
        except NotImplementedError:  # pragma: no cover — Windows
            pass

    try:
        await daemon.run()
    except asyncio.CancelledError:
        logger.info("reflexd stopped")
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
