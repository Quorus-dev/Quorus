"""Reflexd — Quorus client-side AI-native notification daemon (PR-C1).

Wakes the right agent on @-mention without keeping host harnesses open.
Subscribes to /stream/{participant}, runs local triage, bids on /v1/bid,
polls /v1/claim, and on win spawns a headless agent (claude SDK / codex /
gemini / cursor-agent) which posts the reply through /rooms/{room}/messages.

Lane: client-side ONLY. No relay route changes. Run with
``python scripts/reflexd.py --help``.
"""
from __future__ import annotations

import argparse
import asyncio
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
if str(_REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(_REPO_ROOT))

from quorus.config import ConfigManager  # noqa: E402
from quorus.operating_discipline import render_qod_for_agent_loop  # noqa: E402
from quorus.runtime.turnguard import busy_path as _tg_busy_path  # noqa: E402
from quorus.runtime.turnguard import is_busy as _tg_is_busy  # noqa: E402

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
    """Local triage v0: returns ``(action, reason)`` with action in {RESPOND, IGNORE}.

    IGNORE on self-sent or non-conversational; RESPOND on literal ``@self_name``
    token or trailing ``?``; otherwise IGNORE.
    """
    if sender and sender == self_name:
        return "IGNORE", "self message"
    if message_type not in {"chat", "request", "question"}:
        return "IGNORE", f"non-conversational type {message_type!r}"
    text = content or ""
    if re.search(rf"@{re.escape(self_name)}(?![A-Za-z0-9_-])", text):
        return "RESPOND", "literal @mention"
    if text.rstrip().endswith("?"):
        return "RESPOND", "question mark"
    return "IGNORE", "no signal"


def compute_bid(*, is_mention: bool, recency_seconds: float) -> float:
    """Bid in [0,1]. Mention=1.0, question=0.5, minus 0.1 per recency-second."""
    base = 1.0 if is_mention else 0.5
    return max(0.0, min(1.0, base - 0.1 * max(0.0, recency_seconds)))


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


_HARNESS_SUFFIXES = (
    ("-claude", "claude"),
    ("-codex", "codex"),
    ("-gemini", "gemini"),
    ("-cursor", "cursor"),
)

# ---------------------------------------------------------------------------
# Argv-shape pinning — codify the EXACT command shape per harness
# ---------------------------------------------------------------------------
# These constants and helpers exist so contract tests can pin the wire-format
# we send to vendor CLIs. If a vendor renames a flag, ONE of these helpers
# breaks and the corresponding contract test fails — instead of the daemon
# silently invoking a non-existent flag at runtime.
#
# claude does NOT appear here: the claude_agent_sdk is a Python import, not a
# subprocess — its "argv" is ``query(prompt=..., options=ClaudeAgentOptions(...))``
# and is exercised by the dedicated claude contract test below.

CODEX_BIN = "codex"
GEMINI_BIN = "gemini"
CURSOR_BIN = "cursor-agent"


def build_codex_argv(context: str) -> list[str]:
    """Pinned argv shape for codex. Contract: ``codex exec --json --prompt <ctx>``."""
    return [CODEX_BIN, "exec", "--json", "--prompt", context]


def build_gemini_argv(context: str) -> list[str]:
    """Pinned argv shape for gemini. Contract: ``gemini --prompt <ctx>``."""
    return [GEMINI_BIN, "--prompt", context]


def build_cursor_argv(context: str) -> list[str]:
    """Pinned argv shape for cursor-agent. Contract: ``cursor-agent --headless --prompt <ctx>``."""
    return [CURSOR_BIN, "--headless", "--prompt", context]


# Version-probe argv per binary (NOT prompt-bearing — safe to run on startup).
_VERSION_PROBE_ARGV: dict[str, list[str]] = {
    "codex": [CODEX_BIN, "--version"],
    "gemini": [GEMINI_BIN, "--version"],
    "cursor": [CURSOR_BIN, "--version"],
}

# Pinned-known-good version ranges. Entries here say "we have observed this
# CLI working with these versions". Outside the range we still try, just with
# a logged WARNING. Keep the format substring-match to avoid SemVer parsing
# brittleness — vendors emit version strings in different shapes.
KNOWN_GOOD_VERSIONS: dict[str, tuple[str, ...]] = {
    "claude": ("0.0.", "0.1.", "0.2.", "0.3.", "0.4.", "1.", "2."),
    "codex": ("0.", "1.", "2.", "3."),
    "gemini": ("0.", "1.", "2.", "3."),
    "cursor": ("0.", "1.", "2.", "3."),
}


def _claude_sdk_version() -> str | None:
    """Best-effort version pull for the claude_agent_sdk Python module."""
    try:
        import claude_agent_sdk  # type: ignore

        ver = getattr(claude_agent_sdk, "__version__", None)
        if isinstance(ver, str) and ver:
            return ver
    except Exception:
        return None
    # Fall back to importlib.metadata for installed dist version.
    try:
        from importlib.metadata import PackageNotFoundError, version

        return version("claude-agent-sdk")
    except PackageNotFoundError:
        return None
    except Exception:
        return None


def probe_harness_version(harness: str, *, timeout_s: float = 3.0) -> str | None:
    """Return a version string for ``harness`` or ``None`` if not detected.

    Synchronous on purpose — runs once on daemon startup, not per wake. Uses
    ``shutil.which`` to short-circuit when the binary is missing so we don't
    leak a FileNotFoundError into startup logs.
    """
    if harness == "claude":
        return _claude_sdk_version()
    argv = _VERSION_PROBE_ARGV.get(harness)
    if not argv:
        return None
    if shutil.which(argv[0]) is None:
        return None
    try:
        proc = subprocess.run(  # noqa: S603 — argv is hardcoded above
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


def log_adapter_versions(probe: Callable[[str], str | None] | None = None) -> dict[str, str | None]:
    """Probe each adapter's version on startup and emit a structured log line.

    Returns a ``{harness: version_or_None}`` map for inspection by tests. The
    daemon logs one INFO line per harness, and a WARNING when the detected
    version doesn't satisfy any prefix in :data:`KNOWN_GOOD_VERSIONS`.
    """
    probe = probe or probe_harness_version
    versions: dict[str, str | None] = {}
    for harness in ("claude", "codex", "gemini", "cursor"):
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
                            ("gemini", "gemini"), ("cursor", "cursor")):
        if needle in name:
            return harness
    return "claude"


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
        # the regular path, only triggered by an explicit env var.
        if os.environ.get("REFLEXD_STUB_REPLY") in ("1", "true", "yes"):
            return self._stub_reply(context)
        if self.dry_run:
            return self._record_dry_run(harness, context)
        if harness == "claude":
            return await self._run_claude(context)
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
        raise ValueError(f"unknown harness {harness!r}")

    def _record_dry_run(self, harness: str, context: str) -> str:
        """Record what argv WOULD have been called and return a sentinel string.

        This is the lever ``--dry-run`` uses: the full pipeline runs, but the
        adapter never touches a real binary. Tests and CI smoke can assert on
        ``adapter.last_dry_run`` to confirm the wire-format we picked.
        """
        if harness == "claude":
            argv: Any = {
                "kind": "sdk-call",
                "module": "claude_agent_sdk",
                "callable": "query",
                "kwargs": {
                    "prompt": context,
                    "options": {"permission_mode": "bypassPermissions", "max_turns": 1},
                },
            }
        elif harness == "codex":
            argv = build_codex_argv(context)
        elif harness == "gemini":
            argv = build_gemini_argv(context)
        elif harness == "cursor":
            argv = build_cursor_argv(context)
        else:
            raise ValueError(f"unknown harness {harness!r}")
        self.last_dry_run = {"harness": harness, "argv": argv}
        logger.info(
            "dry_run harness=%s argv=%s",
            harness,
            argv if isinstance(argv, list) else argv.get("callable", "<sdk-call>"),
        )
        return f"[reflexd:dry-run] would invoke {harness} adapter (see last_dry_run)"

    async def _run_claude(self, context: str) -> str:
        try:
            from claude_agent_sdk import ClaudeAgentOptions, query
        except ImportError:
            logger.warning("claude_agent_sdk not installed; skipping claude wake")
            return "[reflexd] claude_agent_sdk not installed on this host"

        options = ClaudeAgentOptions(permission_mode="bypassPermissions", max_turns=1)
        chunks: list[str] = []
        try:
            async for message in query(prompt=context, options=options):
                content = getattr(message, "content", None)
                if isinstance(content, str):
                    chunks.append(content)
                elif isinstance(content, list):
                    for block in content:
                        text = getattr(block, "text", None) or (
                            block.get("text") if isinstance(block, dict) else None
                        )
                        if text:
                            chunks.append(text)
        except Exception as exc:  # pragma: no cover — SDK runtime errors
            logger.warning("claude_agent_sdk.query failed: %s", exc)
            return "[reflexd] claude harness errored"
        return "".join(chunks).strip() or "[reflexd] (no reply)"

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
        except asyncio.TimeoutError:
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

    async def _headers(self) -> dict[str, str]:
        # Cache JWT for 4 min (relay token lifetime is 5min).
        # In legacy-bearer mode (e.g. local demo against RELAY_SECRET-only
        # relay) we skip the /v1/auth/token exchange because that endpoint
        # requires a Postgres-backed account/key.
        if self.legacy_bearer:
            return {"Authorization": f"Bearer {self.api_key}"}
        now = time.time()
        if self._bearer is None or now >= self._bearer_expires_at:
            resp = await self.client.post(
                "/v1/auth/token", json={"api_key": self.api_key}
            )
            resp.raise_for_status()
            self._bearer = resp.json()["token"]
            self._bearer_expires_at = now + 4 * 60
        return {"Authorization": f"Bearer {self._bearer}"}

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
    ) -> dict[str, Any]:
        headers = await self._headers()
        resp = await self.client.post(
            f"/rooms/{room}/messages", headers=headers,
            json={
                "from_name": from_name, "content": content,
                "message_type": message_type,
            },
        )
        resp.raise_for_status()
        return resp.json()


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

    @classmethod
    def from_env(cls) -> "ReflexdConfig":
        data = ConfigManager().load() or {}
        relay_url = (
            os.environ.get("RELAY_URL") or data.get("relay_url")
            or "http://localhost:8080"
        )
        api_key = os.environ.get("API_KEY") or data.get("api_key") or ""
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
                "reflexd: no API key. Set API_KEY or run `quorus init`."
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

        Returns True iff we POSTed a bid (regardless of claim outcome);
        False on IGNORE / busy-queued.
        """
        sender = envelope.get("from_name") or ""
        content = envelope.get("content") or ""
        room = envelope.get("room") or ""
        message_id = envelope.get("id") or ""
        message_type = envelope.get("message_type") or "chat"

        action, reason = triage_local(
            content=content, sender=sender,
            self_name=self.config.participant_name, message_type=message_type,
        )
        logger.debug(
            "triage room=%s sender=%s id=%s preview=%r → %s (%s)",
            room, sender, message_id, safe_message_preview(content), action, reason,
        )
        if action != "RESPOND":
            return False

        if is_busy(self.config.participant_name, self.config.runtime_dir):
            logger.info("busy-file present, queueing wake room=%s id=%s", room, message_id)
            self._queue.append(envelope)
            if len(self._queue) > 32:
                self._queue.pop(0)
            return False

        is_mention = reason == "literal @mention"
        recency = max(0.0, time.time() - self._last_wake_at) if self._last_wake_at else 0.0
        bid = compute_bid(is_mention=is_mention, recency_seconds=min(recency, 5.0))

        try:
            await relay.submit_bid(
                room_id=room, message_id=message_id,
                participant=self.config.participant_name,
                bid=bid, reason=reason,
            )
        except httpx.HTTPError as exc:
            logger.warning("bid POST failed: %s", exc)
            return False

        # Poll claim until BID_WINDOW_SECONDS elapses or we see a winner.
        deadline = time.time() + BID_WINDOW_SECONDS
        winner: str | None = None
        while time.time() < deadline:
            try:
                claim = await relay.claim(room_id=room, message_id=message_id)
            except httpx.HTTPError as exc:
                logger.debug("claim retry: %s", exc)
                await asyncio.sleep(0.25)
                continue
            if claim.get("claimed"):
                winner = claim.get("winner")
                break
            await asyncio.sleep(0.25)

        if winner != self.config.participant_name:
            logger.info("lost or no claim room=%s id=%s winner=%s", room, message_id, winner)
            return True

        await self._wake_and_reply(relay, envelope, reason=reason)
        self._last_wake_at = time.time()
        return True

    async def _wake_and_reply(
        self, relay: RelayClient, envelope: dict[str, Any], *, reason: str,
    ) -> None:
        room = envelope.get("room") or ""
        if not self.config.spawn_enabled:
            logger.info("spawn disabled (test mode); skipping harness call")
            return

        history = await relay.fetch_recent(room=room, limit=HEARTBEAT_HISTORY_LIMIT)
        prompt = self._build_prompt(envelope, history)
        harness = detect_harness(self.config.participant_name)
        logger.info("waking harness=%s room=%s reason=%s", harness, room, reason)
        try:
            reply = await self.adapter.run(harness, context=prompt)
        except Exception as exc:
            logger.warning("harness %s raised: %s", harness, exc)
            return

        reply = (reply or "").strip()
        if not reply:
            logger.info("harness produced empty reply, skipping post")
            return
        try:
            await relay.post_reply(
                room=room, from_name=self.config.participant_name, content=reply,
            )
            logger.info("posted reply room=%s len=%d", room, len(reply))
        except httpx.HTTPError as exc:
            logger.warning("reply POST failed: %s", exc)

    def _build_prompt(
        self, envelope: dict[str, Any], history: list[dict[str, Any]],
    ) -> str:
        """Compose: QOD constitution + last N messages + WakeIntent block."""
        recent = history[-HEARTBEAT_HISTORY_LIMIT:] if history else []
        lines = [
            f"@{m.get('from_name') or m.get('sender') or '?'}: {m.get('content') or ''}"
            for m in recent
        ]
        history_block = "\n".join(lines) if lines else "(no prior messages)"
        wake_sender = envelope.get("from_name") or "?"
        wake_content = envelope.get("content") or ""
        wake_room = envelope.get("room") or "?"
        return (
            f"{render_qod_for_agent_loop()}\n\n"
            f"# Wake Intent\n"
            f"You are `{self.config.participant_name}`. You were @-mentioned in "
            f"room `{wake_room}` by `{wake_sender}`. Reply concisely (1-3 lines) "
            f"and do not run any tools that require user approval.\n\n"
            f"# Recent room transcript (most recent last)\n{history_block}\n\n"
            f"# Triggering message\n@{wake_sender}: {wake_content}\n"
        )

    # ── main loop ─────────────────────────────────────────────────────────

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

    async def _sleep_or_stop(self, seconds: float) -> None:
        try:
            await asyncio.wait_for(self._stop.wait(), timeout=seconds)
        except asyncio.TimeoutError:
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
    prompt = daemon._build_prompt(envelope, history=[])
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
