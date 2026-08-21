# Wake Rebuild — Implementation Spec v1.0

> Date: 2026-08-20. Feeds from `docs/NOTIFICATION_MVP_RESEARCH.md`.
> This is the authoritative spec for the Wake Rebuild. Each stream has scope,
> required changes, tests, and acceptance criteria. Streams F1–F3 (Foundation)
> are parallelizable now; R/D/L follow.

Ground rules (all streams):
- Python 3.10+, async-first, conventional commits (imperative, <50 chars).
- Files under 500 lines; split if larger. New code has unit tests. `ruff check .` passes.
- No secrets in logs. No new module-level mutable state without a backend path.
- Stay within your stream's file list; TODO-comment cross-stream needs.
- MVP harness scope: **Claude Code, Codex CLI, Gemini CLI**. Others keep argv-pinned adapters but are not gated on.

---

## Stream F — Foundation (Phase 0)

### F1. Security & correctness stragglers

Files: `quorus/routes/room_messages.py`, `quorus/services/webhook_svc.py`,
`quorus/auth/routes.py`, `quorus/profiles.py`, dead files listed below.

- **F1.1** `room_messages.py:113` — replace bare `except Exception` on the
  idempotency retry path with `except (HTTPException, BackendError)`; wrap the
  `idempotency.delete(...)` cleanup in its own try/except that logs and swallows
  (mirror the fixed DM path in `routes/messages.py:83-94`).
- **F1.2** `webhook_svc.py` — rename misleading `tenant_id=` log field to
  `target=` at the 4 WARNING sites (`:496-502`, `:518-524`, `:580-587`,
  `:596-602`); SSRF-block logs (`:455`, `:540`) must log host only, never the
  full URL (query strings can carry tokens); the `or job.callback_url`
  fallbacks (`:489`, `:578`) must fall back to `"<unparseable>"`, not the raw URL.
- **F1.3** `auth/routes.py` — gate `register-agent`'s raw `api_key` return
  behind the same `X-Quorus-Setup-Local: 1` header as signup (A4 pattern);
  default response returns `key_prefix`/`key_id` only. Update stale docstrings
  claiming "5 signups per hour" (actual window: 60s).
- **F1.4** `profiles.py` — `_atomic_write_json()` must raise `ValueError` on
  legacy dirs (`~/.murmur`, `~/mcp-tunnel`), same guard as
  `config.py:217-221`; `migrate_legacy_if_needed()` must write the migrated
  profile to the modern dir, never back into legacy.
- **F1.5** Delete dead code: `scripts/autonomous_agent.py` (POSTs to a
  nonexistent `/execute`), `scripts/patch_cli.py`, `scripts/patch_gemini.py`,
  `docs/HACKATHON_REGRESSION 2.md`, `.venv 2/` directory; prune the 5 stale
  `worktree-agent-*` branches (`git worktree prune` + branch delete).

Tests: extend `tests/test_webhook_logging.py` with a real monkeypatched-logger
delivery test (the spec'd version was grep-only); add
`tests/test_register_agent_key_gate.py`; add legacy-write regression to
`tests/test_config_resolution.py` driving the ProfileManager path.

Acceptance: `pytest` green on touched files; `grep -n "tenant_id=" quorus/services/webhook_svc.py`
returns nothing; no response field matches a raw key without the setup header;
`ls scripts/autonomous_agent.py` fails.

### F2. Test-order pollution

Files: `tests/`, `conftest.py`, plus minimal reset hooks in source modules that
hold process-global state.

- Full suite currently: 1974 passed / 16 failed / 9 errors; all failing files
  (`test_usage.py`, `test_triage.py`, `test_work_queue.py`) pass in isolation.
- Diagnose the shared state (rate-limiter counters, triage module globals
  `_bid_windows`/`_fairness_credit`, service singletons). Add autouse fixtures
  (or explicit reset helpers exported by the owning modules) that reset
  cross-test state. Prefer reset helpers in the owning module over tests
  reaching into privates.
- Also fix the 12 ruff nits (all in tests) via `ruff check --fix`.
- Docker cold-install test must `skip` (not fail) when Docker is unavailable.

Acceptance: **two consecutive full-suite runs green** (`pytest -q` × 2, no
`-p no:randomly` tricks); `ruff check .` fully clean.

### F3. MCP server file split

Files: `packages/mcp/quorus_mcp/` only.

- `server.py` is 728 lines (cap 500). Extract cohesive modules (e.g.
  `runtime.py` for config/session/breaker state helpers, keeping `tools.py`,
  `sse.py`, `phase1_tools.py` as-is). Public import surface
  (`quorus_mcp.server.mcp`, shim `quorus.mcp_server`) unchanged.
- Remove the dead `poll_mode` config key end-to-end: writes in
  `packages/cli/quorus_cli/cli.py` (4 sites), `packages/tui/quorus_tui/hub.py:197`,
  validation in `quorus/config.py:280-287`, and the unread
  `enable_background_polling` at `server.py:113`. Legacy configs containing the
  key must load without error (ignore + single deprecation note).
- `_reset_runtime_state()` documented as test-only; add a lock-held async variant.

Acceptance: every file in `packages/mcp/quorus_mcp/` ≤ 500 lines;
`grep -rn "poll_mode" quorus packages` returns nothing; `pytest tests/test_mcp_*` green.

---

## Stream R — Relay: durable delivery + presence

Files: `quorus/backends/`, `quorus/routes/`, `quorus/services/`, `quorus/relay.py`, migrations.

- **R1. Durable wake inbox — REUSE, don't build.** (Design refinement
  2026-08-20 after code study.) `MessageBackend` already provides a durable
  per-recipient inbox with `fetch`/`ack`/`ack_ids`/`requeue`/`pending_count`
  (`backends/protocol.py:18-135`), and the relay already enqueues room
  messages per member. The actual gap is consumption: reflexd is SSE-only and
  never drains the inbox, so anything arriving while the daemon is down or
  the laptop asleep is silently missed. Change: on startup AND on every SSE
  reconnect, reflexd calls `fetch` → runs its existing local triage over each
  missed message → handles (wake or ignore) → `ack_ids`. `wake_intent` SSE
  events stay transient (they are recomputable from the fetched messages).
  Relay-side addition limited to: ensure fetch/ack endpoints accept the
  agent's JWT (they exist for the MCP poll path — verify + test).
- **R2. Presence — WIRE, mostly exists.** `PresenceBackend.heartbeat/list_all`
  + `/presence` route already exist (`presence_svc.py`, `routes/presence.py`);
  nothing sends heartbeats. Change: reflexd heartbeats every ≤30s (status:
  active|working, current room). `GET /v1/rooms/{id}/members` gains
  `presence: active|away` (from PresenceBackend, timeout 90s) and
  `queued: <n>` (from `MessageBackend.pending_count`). TUI renders "● active"
  / "○ away — N queued".
- **R3. Distributed triage auction.** Move `_bid_windows` + `_fairness_credit`
  (`routes/triage.py:113-114`) behind a backend (Redis when configured,
  in-memory fallback) so multi-worker/multi-replica deploys elect exactly one
  winner. Reuse the `work_queue_svc` Redis pattern.
- **R4. Persist Phase 1 primitives.** `capability_svc`, `tool_catalog_svc`,
  `persistent_memory_svc` gain the same optional Redis persistence as
  `work_queue_svc` (keys namespaced per tenant). "Persistent memory" must
  survive relay restart.

Tests: inbox replay-after-reconnect; ack idempotency; presence TTL expiry;
two-worker auction single-winner (spawn two app instances against fakeredis);
primitives survive backend restart.

Acceptance: kill/restart relay mid-conversation → no lost mentions; two
uvicorn workers → exactly one claim winner per mention.

---

## Stream D — Daemon (reflexd v2)

Files: `scripts/reflexd.py`, `scripts/reflexd_triage.py`, `packages/cli/quorus_cli/cli.py`
(reflexd/room subcommands), `quorus/routes/room_state.py` (workspace field).

- **D1. Room → workspace binding.** Room state gains optional
  `workspace: {participant_or_"*": path}` (PATCH via existing room-state route;
  CLI `quorus room bind <room> <path> [--participant X]`). reflexd spawns the
  harness with `cwd=<bound path>` (validated: exists, is dir, not a legacy
  config dir). Unbound room → spawn in `$HOME` and prepend a context note
  ("no workspace bound — code tasks need `quorus room bind`").
- **D2. Room → session continuity.** Daemon-local map
  `~/.quorus/sessions-<participant>.json`: `{room_id: {harness, session_id, cwd, last_used}}`.
  Wake = resume by **explicit ID**: `claude -p --resume <id>`,
  `codex exec resume <id>`, `gemini --resume <uuid>`. First wake in a room
  captures the new session id from JSON output (`session_id` field for Claude;
  rollout id for Codex; uuid for Gemini). Invalid/expired id → fresh spawn with
  a prepended room summary (last 20 messages via history endpoint) — continuity
  degrades gracefully, never crashes.
- **D3. Kill the silent stub.** Stub replies only when `REFLEXD_STUB_REPLY=1`.
  Missing binary → post an honest room message
  (`⚠ arav-claude is configured but the claude binary is not installed on its host`)
  + ERROR log. Never fake work.
- **D4. Lifecycle.** `quorus init` installs launchd (macOS) / systemd-user
  (Linux) units by default (`--no-autostart` opt-out). Daemon detects
  sleep→wake (dead-socket heartbeat + reconnect ≤5s after wake), re-attaches
  SSE, drains unacked inbox, acks as it handles. PID/liveness surfaced in
  `quorus doctor`.
- **D5. Guardrails — mission-aware (refined 2026-08-20 per Arav).** Two wake
  classes with different budgets:
  - **Chat wake** (mention/question, no claimed task): `--max-turns 15`,
    wall-clock kill 15 min. Cheap, bounded.
  - **Mission wake** (agent holds a work-queue claim): NO arbitrary
    wall-clock kill. The agent works until the task is completed/released,
    or a human sends the QSP `interrupt` verb ("stop"). Liveness is
    progress-based: TurnGuard busy-file activity, tool events, or room
    posts within `REFLEXD_MISSION_SILENCE_S` (default 20 min) count as
    alive; silence past that → post "agent quiet 20m on <task>" to the
    room and escalate (never silently kill a working agent).
  Per-agent daily budget stays as the backstop (soft → warn/downgrade,
  hard → stop + notice). Concurrency: one active run per room per agent;
  further mentions queue. Reply-depth-3 chain breaking stays.
- **D5b. Mission lifecycle (the Arav rule).** An agent never "falls asleep"
  mid-mission by design: (1) claim task → mission session opens (D2 map
  binds room→session); (2) host sleep/crash/kill is a PAUSE, not an end —
  the durable inbox (R1) + session resume (D2) mean the next wake continues
  the same session with full memory; (3) mission ends ONLY on task
  complete/release or explicit human `interrupt`; (4) a tag after mission
  end wakes the same session — the agent remembers its past work and
  continues. Acceptance: claim task → kill daemon mid-work → restart →
  agent resumes the same session and finishes without any human prompt.
- **D6. Ack protocol.** Every handled wake event (replied, refused, errored)
  is acked to R1's endpoint; unhandled events survive daemon crash and
  redeliver.
- **D7. Wake-success detection.** (Found in real-adapter proof 2026-08-20.)
  A woken Claude Code session is fully agentic: per QOD it posts its reply
  itself via quorus tools (inheriting RELAY_URL/API_KEY from the spawn env),
  and its stdout never "returns" — so reflexd's stdout-capture path hits the
  120s kill and posts a spurious "[reflexd] harness timed out" AFTER the real
  reply already landed. Before posting a timeout/error, reflexd must check
  room history for a reply from its own participant threaded on the wake
  message; if present, the wake succeeded — kill the process quietly and ack.

Tests: contract tests per harness argv incl. resume forms
(`tests/test_reflexd_adapters.py` extension); session-map round-trip +
invalid-id fallback; workspace validation; stub-gating; budget soft/hard;
queue-per-room.

Acceptance: `quorus doctor` shows daemon healthy after reboot simulation;
wake lands in bound cwd with resumed session (assert via stub harness echoing
cwd + session args).

---

## Stream L — Live-session injection + approval relay (Claude first)

Files: `scripts/reflexd.py`, `packages/mcp/quorus_mcp/` (approval tool),
`packages/cli/quorus_cli/` (hook writers), `docs/`.

- **L1. Live-session registry.** Claude Code `SessionStart` hook registers
  `{pid, cwd, socket: $CLAUDE_CODE_MESSAGING_SOCKET, token: $CLAUDE_CODE_MESSAGING_TOKEN}`
  into `~/.quorus/live-sessions.json` (0600); `SessionEnd` removes; stale
  entries pruned by pid-liveness. Installed by `quorus hook enable` (replacing
  the old UserPromptSubmit-only approach; keep `quorus inbox` as a fallback).
- **L2. Wake ladder.** On mention: (1) if a live Claude session exists whose
  `cwd` matches the room workspace → post the message into its inbox socket
  (auth frame `{"type":"auth","token":...}` then message frame); verify
  write success; (2) on any failure → headless resume path (D2). Codex/Gemini:
  headless resume only in MVP (Codex app-server integration is post-MVP).
- **L3. Approval relay.** Headless Claude wakes run with a per-room
  `allowedTools` list (room state key `tool_policy`) and
  `--permission-prompt-tool mcp__quorus__approve`: a new MCP tool `approve` in
  `quorus_mcp` that posts `[approval] <agent> wants <tool> in <room>` to the
  room, then blocks (max 120s) awaiting `quorus approve <req-id>` /
  `quorus deny <req-id>` (CLI + TUI keybinding). Timeout → deny + room notice
  "blocked on approval". Never `--dangerously-skip-permissions`.
- **L4 (post-MVP, flagged).** Claude Channels plugin (research preview) as an
  alternative transport; Codex `app-server` threads under the daemon.

Tests: fake socket server asserting auth+message frames; ladder fallback on
dead socket; approval roundtrip approve/deny/timeout (fake MCP host).

Acceptance: with an interactive Claude session open in the bound repo, a room
mention appears inside that session (no keystroke) within 5s; with no session
open, headless resume handles it; a blocked tool call surfaces in the room and
an approval unblocks it.

---

## Stream T — Demo gate & CI

- `scripts/demo_wake.sh`: local relay + **two** daemon instances (distinct
  participants), stub harness; posts a mention; asserts: ack < 5s, reply
  present, exactly one winner, presence transitions, inbox drained. Runs 5×
  consecutively; any failure fails the script.
- Real-harness variant `--real` (Claude): resumed session id asserted stable
  across two mentions in one room.
- CI: add demo_wake (stub) to the existing cold-install workflow matrix.
- **The human demo gate** (from research doc): mention from TUI → agent wakes
  in the right repo with room memory → permission prompt relayed → approved →
  result posted. Cold, two machines, five consecutive runs, zero manual triggers.

---

## Judge checklist

1. Scope respected per stream (`git diff --name-only`).
2. Full suite green twice consecutively; `ruff check .` clean.
3. Grep-based acceptance assertions above.
4. Diff review: no secret logging, no new unbacked global state, files ≤500 lines.
5. Cross-stream dry-merge before integration.
6. Stub demo (`demo_reflex.sh`) still passes — no regression to the existing pipeline.

## Sequencing

F1 ∥ F2 ∥ F3 (now) → R1+R2 ∥ D1+D2+D3 → D4+D5+D6 ∥ R3+R4 → L1+L2 → L3 → T.
Open-source-first decision (2026-08-20): no monetization gating; hosted-relay
/ audit-trail thesis deferred until post-launch.
