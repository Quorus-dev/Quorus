# Quorus — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-05-11 (Phase 1 OS primitives now MCP-callable across all 6 harnesses)

---

## Current State

Quorus (package: quorus) is the coordination layer for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `feat/may4-sprint` (1421+ tests passing — Reflex AI-native chat + identity disambiguation + production-deploy hardening)

**Package:** `pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"`

**Entry point:** `quorus` command opens the TUI hub by default (like `claude` or `gemini`).

**Repo layout (monorepo):**

```
quorus/                    # core: relay server + shim re-exports
packages/
  sdk/  → quorus_sdk       # client library (Room, QuorusClient) — httpx only
  cli/  → quorus_cli       # CLI commands (quorus ...) + ui.py theme module
  mcp/  → quorus_mcp       # MCP server (FastMCP tools) — httpx + mcp SDK
  tui/  → quorus_tui       # `quorus` / `quorus begin` Rich terminal hub
```

`quorus/sdk.py`, `quorus/cli.py`, `quorus/mcp_server.py`, `quorus/tui_hub.py`,
`quorus/integrations/http_agent.py`, `quorus/decorators.py`, `quorus/watcher.py`
are re-export shims so all historical `from quorus.X import Y` imports work.

**Setup (3 commands):**

```bash
pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"
quorus init <your-name> --relay-url <url> --secret <secret>
# restart claude code — done
quorus           # opens the hub
```

**Config:** `~/.quorus/config.json` (pointer) + `~/.quorus/profiles/<name>.json` (per-profile data). Legacy `~/mcp-tunnel/` + `~/.murmur/` still read.

**Profile split for cross-identity coordination:**

The TUI uses ONE profile at a time; switch via `quorus -w <name>` or by editing `~/.quorus/config.json`'s `current` field. Conventional split:

- `default` profile — `instance_name=arav-codex`, used by the Codex CLI / MCP servers / agents (so they continue to post under their agent identity).
- `human` profile — `instance_name=arav`, used by the human owner when typing in the TUI (so messages render as `@arav` not `@arav-codex`).

Why this matters: the relay enforces `JWT.sub == from_name` (anti-impersonation; returns 403 `Cannot send as another user`). For the human and the agent to appear as distinct senders, each must have its own participant + api_key in the same tenant. `quorus whoami` confirms the active identity.

**What's built:**

| Module                      | What                                                                                           |
| --------------------------- | ---------------------------------------------------------------------------------------------- |
| quorus/relay.py             | FastAPI relay: rooms, SSE fan-out, history, presence, rate limiting, health, admin             |
| quorus_mcp/server.py        | MCP server: 11 tools incl. claim_task, release_task, get_room_state, send/check, rooms, search |
| quorus_cli/cli.py           | 30+ CLI commands (quorus ...) — `quorus` with no args opens TUI                                |
| quorus_cli/ui.py            | Shared theme, banner, spinner, error/success/info primitives                                   |
| quorus_tui/hub.py           | Full-screen TUI hub: rooms panel, agent list, live chat, first-run wizard                      |
| quorus/routes/room_state.py | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)                |
| quorus/watcher.py           | Primitive C: SSE-driven daemon, writes .quorus/context.md for IDE indexing                     |
| quorus/dashboard.py         | Web dashboard: live messages + swarm activity panel + usage bar                                |
| quorus/backends/            | In-memory + Redis + Postgres + SQLite backends (incl. RoomStateBackend)                        |
| tests/                      | 905 tests passing                                                                              |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**Key features:**

- Rooms with fan-out messaging (send once, all members receive)
- **SSE-only push delivery** — zero polling, instant delivery
- **Shared state matrix** — goal, locked files, claimed tasks, decisions, active agents
- **Distributed mutex locking** — claim files with TTL, SSE broadcast LOCK_ACQUIRED/LOCK_RELEASED
- **Watcher daemon** — writes .quorus/context.md for IDE indexing, event-driven via SSE
- **MCP tools** (11): send/receive, rooms, locks, state, metrics, search
- **JWT auth + API keys** (`quorus_sk_*` prefix, legacy `murm_sk_*` still accepted)
- **Portable join tokens** (`quorus_join_*` prefix, legacy `murm_join_*` still accepted)
- **Rate limiting** on all write + history endpoints
- **Transactional outbox** — atomic Postgres writes + background worker fan-out
- **Audit ledger** — message lifecycle events (MESSAGE*CREATED → FANOUT*\* → DELIVERED)
- **Account-based identity** — participant_id in JWT claims + FK columns
- Docker + Railway/Render/Fly.io deploy configs

**Website:** https://www.quorus.dev (Vite + React + Tailwind, Vercel deploy)

---

## Security Posture (post-audit 2026-04-15)

Critical fixes landed this session:

- **AppleScript injection** in watcher osascript → now passes args via argv (not string interpolation)
- **Agent-name injection** in spawn → validated against `[A-Za-z0-9_-]{1,64}` at entry
- **Bootstrap secret comparison** → `hmac.compare_digest` (constant-time)
- **Invite-join DoS** → IP rate limit (10/min)
- **Admin tenant creation DoS** → IP rate limit (5/min)
- **Config TOCTOU race** → atomic `os.open` with 0o600 from creation
- **Invite page XSS** → all template vars `html.escape`d

Remaining medium-severity items tracked in code review output.

---

## In Progress

**Wake Rebuild (2026-08-20):** research phase complete — see `docs/NOTIFICATION_MVP_RESEARCH.md`.
Next: planning phase → product spec → rebuild. MVP scope: Claude Code + Codex + Gemini
instant wake-on-mention with room→session memory, room→workspace binding, live-session
injection (Claude inbox socket, v2.1.224+), approval relay, honest presence. Stub
auto-fallback to be killed. Pre-rebuild Phase 0 fix list in the doc (test-order
pollution, Redis-backed triage auction, iCloud repo relocation, audit stragglers).

---

## Recent Changes

### Public-install verification (2026-08-21)

Ran the path a stranger actually takes — `pipx install "quorus @ git+…"` —
instead of a local checkout. It found two shipping bugs no test could:

- **`quorus-mcp` was broken on install.** The L3 file-size split moved
  `main_cli` out of `quorus_mcp.server` while `pyproject.toml` still pinned
  it there, so the binary died with ImportError at startup while the whole
  suite stayed green (tests import `mcp` directly and never exercise a
  console script). Fixed, plus a regression test that imports every declared
  entry-point target — mutation-verified to fail when `main_cli` moves.
- **`--help` required configuration.** `quorus.relay` enforces its env at
  IMPORT time, so `quorus-relay --help` answered with a config error. A thin
  `quorus.relay_cli` wrapper answers help/version before importing the relay
  (the fail-fast guard is untouched for real starts); `quorus-analytics`
  short-circuits the same way.

All four binaries now verified from a real pipx install: `quorus`,
`quorus-relay`, `quorus-analytics`, `quorus-mcp`.

**LAUNCH BLOCKER — `main` is 199 commits behind.** The README's install
command resolves to the default branch, which still carries the unbounded
`mcp>=1.2.0` that resolves to 2.0.0 and breaks every fresh install. Nothing
from this rebuild is on `main`. Merging `feat/wake-rebuild-aug26` → `main`
is Arav's call and the last step before anyone can be told to install it.



### Wake Rebuild — Streams D, R, L complete (2026-08-20 night → 21 early)

Branch renamed `feat/may4-sprint` → **`feat/wake-rebuild-aug26`**.
Suite: **2069 passing**, ruff clean, stub demo 0.35s e2e.

- **D1 workspace binding** — `quorus room bind <room> <repo>`; wakes run the
  harness inside the bound directory (host-local, stale bindings degrade).
- **D2 session continuity** — claude wakes use `--output-format json`,
  capture `session_id`, persist a room→session map, resume by explicit
  `--resume`. Proven live at the CLI layer (teach 7391 → resume → recall).
- **D3 honest degradation** — stub replies only on explicit
  `REFLEXD_STUB_REPLY=1`; a missing binary posts a real error to the room.
- **D4 lifecycle** — init installs launchd by default; SSE connections that
  lived >60s reconnect at the 2s floor (laptop sleep/wifi change).
- **D5/D5b mission budgets** — chat wakes capped (15 turns); mission wakes
  (agent holds a work-queue claim) get a 1h leash and an actionable
  escalation naming the task instead of a bare sentinel. Never kill a
  working agent.
- **D7 wake-success detection** — suppress timeout/error sentinels when the
  agent already posted its own threaded reply via quorus tools.
- **R1/R2** — durable inbox drain on every (re)connect; presence heartbeats
  + `member_presence` (active/away + queued depth) on `GET /rooms/{id}`.
- **R3** — Redis-shared triage auction: bids in a shared hash, claim via
  `SET NX`, credits applied once. 8-way concurrent race proves ONE winner
  across replicas. In-memory path stays as the fallback.
- **R4** — phase-1 primitives (memory, capabilities, tool catalog) get a
  write-through Redis mirror + lazy hydrate, so they survive a relay
  restart; the work queue's dormant `redis_conn` is finally wired.
- **L1/L2** — SessionStart/SessionEnd hooks maintain a live-session
  registry (pid+cwd only, **no messaging token at rest**); a Stop hook
  delivers pending room messages into a running session; reflexd defers to
  a live session that is mid-turn rather than spawning a rival agent.
  Raw inbox-socket injection deliberately NOT used: Anthropic publishes the
  auth frame but not the message-frame schema (probes are dropped silently).
- **L3 approvals** — `/v1/approvals` + MCP `approve` tool (wire as Claude
  Code's `--permission-prompt-tool`) + `quorus approvals|approve|deny`.
  Fails closed on deny/expiry/missing-room/relay-error; agents cannot
  decide their own requests; tool input previewed, never stored verbatim.

**Bugs found and fixed while proving these:** auction keyed on the
per-recipient fan-out id (every agent won `@open`); in-memory `ack` ignored
the service's JSON id-list token so ACKs were silent no-ops and messages
redelivered forever; `@open` echoes in agent replies re-triggered other
agents (echo storm); unbounded `mcp>=1.2.0` resolved to 2.0.0 which removed
`fastmcp`, breaking every fresh install.

**Environment:** working copy is `~/dev/Quorus` (Python 3.13). The Desktop
copy is retired — iCloud/Spotlight kept re-applying UF_HIDDEN to the venv
`.pth` files via the SIP-undeletable `com.apple.provenance` xattr, which
Python 3.14 silently skips. A one-hour watcher on `~/dev` never fired once.

**Still open:** production relay `quorus-relay.fly.dev` is NXDOMAIN (needs
`flyctl auth login` + redeploy); two-human runbook test
(`docs/MULTI_USER_TEST_RUNBOOK.md`); TUI surface for pending approvals.



### Stream D core landed (2026-08-20 night, commits 257ee03 + 2573616 + 8e7118d + pending)

- **D1 workspace binding**: `quorus room bind <room> <path>` (host-local,
  ~/.quorus/room-bindings.json); wakes run the harness inside the bound repo.
- **D2 session continuity**: claude wakes use `--output-format json`, capture
  `session_id`, persist room→session map, resume by explicit `--resume` id.
  PROVEN live against the real CLI: teach "7391" in session 7430aeda…,
  resume same id → agent answers "7391". Expired ids retry fresh once.
- **D3 honest degradation**: stub only on explicit REFLEXD_STUB_REPLY=1;
  missing binary posts "not installed on this host" to the room.
- **D7 wake-success detection**: timeout/errored sentinels suppressed when
  the agent already posted its own threaded reply via quorus tools.
- `SUBPROCESS_TIMEOUT_S` now env-tunable (REFLEXD_SUBPROCESS_TIMEOUT_S),
  default 300s — real agentic wakes routinely take 2-5 min (measured).
- **Test-design lesson**: full-pipeline "codeword" memory tests are
  confounded — a woken Claude Code runs the user's full config and saved
  the codeword into its persistent MEMORY system (and reflexd's room-memory
  block also carries prior context). Prove session-resume at the CLI layer;
  prove pipeline glue with unit tests.
- Remaining: D4 (launchd by default + sleep/wake), D5 mission budgets,
  Stream L (inbox-socket injection + approval relay), R3/R4 redis, Fly
  relay redeploy (NXDOMAIN), CLI help listing gaps (room/reflexd/turnguard).



### QA audit + repo relocation (2026-08-20 late, commits df498ad + 61ff9cf)

- **Working copy is now `~/dev/Quorus`.** Desktop copy retired: iCloud/Spotlight
  re-applies UF_HIDDEN to venv .pth files via the SIP-undeletable
  `com.apple.provenance` xattr; Python 3.14 skips hidden .pth → imports die
  minutes after every fix. `scripts/patch_entry_points.sh` (bakes sys.path
  into console scripts) is the working mitigation — run it after every
  reinstall; `tests/test_entry_point_bootstrap.py` enforces it.
- **Fresh installs were broken**: unbounded `mcp>=1.2.0` resolved to mcp
  2.0.0 (removed `mcp.server.fastmcp`). Capped `<2.0.0` in both pyprojects.
- **Echo storm fixed**: live two-daemon proof showed one `@open` → three
  replies (winner's ack quoted the trigger; the other agent triaged the echo).
  `@open` now anchored to message start; stub de-fangs trigger tokens.
- **Autonomous notification stack proven live, zero manual prompting**:
  stub wake 0.345s; real `claude --print` wake round-trip (agent posted its
  own reply via quorus tools — see spec D7); offline mention drained on
  daemon start; two agents + one `@open` → exactly one winner replies.
- **Production relay `quorus-relay.fly.dev` is NXDOMAIN** — Fly app gone;
  needs redeploy before any external demo. Website www.quorus.dev is up.
- Ghost session warning: an older Claude session was auto-committing
  ("auto: sync") and ran a surprise uv editable install mid-audit.



### Wake Rebuild Stream R slice 1 (2026-08-20, commits 17a4998 + 64c4fc9)

- **R1**: reflexd drains the durable inbox (`GET /messages/{p}?ack=manual` →
  dispatch → ack) on every (re)connect — mentions arriving while the daemon
  was down / laptop asleep are handled, not lost. Loops until empty;
  poison messages still ack (no queue wedge).
- **R2**: reflexd sends 30s presence heartbeats; `GET /rooms/{id}` now
  returns `member_presence` per member: `{presence: active|away, queued: N}`
  (heartbeat timeout 90s; queued = peek + pending).
- **Bug fix (auction)**: reflexd bid/claim now key on the canonical
  `message_id`, not the per-recipient fan-out uuid — previously every
  capable agent had a private auction window, so `@open` broadcasts made
  every agent "win" and reply.
- **Bug fix (ack no-op)**: `InMemoryMessageBackend.ack` only understood its
  internal uuid token while the service issues JSON id-list tokens (the
  Redis contract) — all token ACKs against in-memory relays were silent
  no-ops, redelivering acked messages every visibility window. Fixed +
  regression-tested.
- Remaining Stream R: R3 (Redis-backed triage auction), R4 (persist
  capabilities/tool-catalog/memory primitives). Then Stream D (workspace
  binding, session resume, stub removal, lifecycle, guardrails).

### Wake Rebuild Phase 0 — Foundation complete (2026-08-20)

Spec: `docs/WAKE_REBUILD_SPEC.md`. All Stream F items landed, full suite
green twice consecutively (2016 passed / 0 failed, 72s — was 14min with 25
order-dependent failures). Highlights:

- **F1 security**: room_messages idempotency bare-except narrowed;
  webhook logs `tenant_id`→`target` + host-only SSRF logging (no more
  full-URL token leaks); register-agent raw key gated behind
  `X-Quorus-Setup-Local: 1` (CLI sends it); ProfileManager can no longer
  write to legacy `~/.murmur`/`~/mcp-tunnel`; dead scripts deleted
  (`autonomous_agent.py`, `patch_cli.py`, `patch_gemini.py`).
- **F2 test isolation**: root causes were (a) relay 404-sweeper blocking the
  shared test-client IP after accumulated 404s, (b) triage bid-window/fairness
  module globals, (c) `run_*_agent` mutating `os.environ["QUORUS_CONFIG_DIR"]`
  process-wide. Fixes: `reset_not_found_limiter()` / `reset_triage_state()` +
  autouse conftest guards + os.environ snapshot fixture. Docker cold-install
  variant skips cleanly without Docker.
- **F3 MCP**: server.py 728→497 lines (new `runtime.py`); dead `poll_mode`
  config key removed end-to-end (legacy configs still load).
- **Incident**: a fix agent force-removed stale `worktree-agent-*` trees; one
  contained uncommitted May-2 edits (lost) — its committed tip is preserved
  as tag `rescue/worktree-afd3fedf` (e8f7653). Lesson: never force-remove
  worktrees without checking for dirty state.
- **Environment**: this repo lives in iCloud-synced Desktop; iCloud evicted
  42k files (hanging imports/git for minutes) and created " 2"/" 3" conflict
  copies of venv .pth files. Force-download fixed it this session
  (`brctl download` + parallel reads). **Move the repo out of ~/Desktop.**
- R1/R2 design shrank after code study: durable inbox = existing
  `MessageBackend.fetch/ack` (reflexd must drain on start/reconnect);
  presence = existing `/heartbeat` route + `PresenceBackend` (reflexd must
  send; rooms/members must surface). See spec Stream R.

### Wake Rebuild research (2026-08-20)

Full repo audit + 2 deep-research sweeps. Root causes of "notifications felt manual":
(1) live-session delivery = UserPromptSubmit hook → waits for human keystroke;
(2) reflexd wakes are amnesiac (`claude --print`, no --resume) with no cwd targeting;
(3) silent stub fallback posts fake replies when vendor binary missing;
(4) daemon doesn't survive reboot by default. Pipeline itself verified working
(stub demo 340ms e2e). Full test suite: 1974 passed / 16 failed / 9 errors — all
failures pass in isolation (test-order state pollution). Competitive landscape:
cc-connect 15.1k stars is closest threat; Claude Channels documented gap = no
cold-start wake; Slack Code launched 2026-08-20. Doc: `docs/NOTIFICATION_MVP_RESEARCH.md`.

### Phase 1 OS primitives → MCP tools (2026-05-11)

The `feat(os-primitives): Phase 1` commit (850dbf9) shipped HTTP routes for
capability discovery, tool catalog, and persistent memory — but no MCP
wrappers, so the 6 vendor harnesses (Claude Code, Codex, Cursor, Gemini,
Opencode, Cline) couldn't actually invoke them. Closed the gap: 10 new
MCP tools registered in `packages/mcp/quorus_mcp/server.py`, implementations
in a new `phase1_tools.py` module so server.py stays roughly within the
500-line guideline. All 10 audit before mutating writes via the same
`_audit_tool_call` sidecar pattern as `send_message` / `social_verb`, so
the hash-chained audit ledger keeps complete coverage. +22 tests
(`tests/test_mcp_phase1_tools.py`), 1924 total passing.

The new tool surface:

| MCP tool              | Wraps                                           |
| --------------------- | ----------------------------------------------- |
| `publish_capability`  | `POST /v1/capabilities/{INSTANCE_NAME}`         |
| `lookup_capability`   | `GET  /v1/capabilities/{participant}`           |
| `search_capabilities` | `GET  /v1/capabilities/search?has=...`          |
| `register_tool`       | `POST /v1/rooms/{rid}/tools`                    |
| `list_room_tools`     | `GET  /v1/rooms/{rid}/tools`                    |
| `unregister_tool`     | `DELETE /v1/rooms/{rid}/tools/{name}`           |
| `memory_set`          | `PUT  /v1/memory/{INSTANCE_NAME}/{rid}/{key}`   |
| `memory_get`          | `GET  /v1/memory/{owner}/{rid}/{key}`           |
| `memory_list`         | `GET  /v1/memory/{owner}/{rid}`                 |
| `memory_delete`       | `DELETE /v1/memory/{INSTANCE_NAME}/{rid}/{key}` |

This converts Plan v8 primitives 3-5 from "drafted route" to "agents can
actually use them." Demo gate: an agent in a room can now run
`publish_capability(capabilities=["python", "fastapi"])`, another agent
can `search_capabilities(has="fastapi")`, and shared state survives a
restart via `memory_set` / `memory_get`. No new wire format — same JWT
auth, same audit ledger, same Cedar policy gates as the underlying routes.

### Reflex — AI-native cross-harness chat (2026-05-02 → 2026-05-03)

Ships the autonomous-engineering-team wedge described in `QUORUS_AUTONOMY_PLAN.md`. Distributes a Quorus Operating Discipline (QOD) constitution via three channels (MCP `instructions` field + `~/.claude/skills/` + agent-loop sysprompt prepend), runs a per-host `reflexd` daemon that subscribes to relay SSE, classifies room messages via `/v1/triage`, computes a local bid via `/v1/bid`, claims via `/v1/claim`, and spawns a headless harness session (claude-agent-sdk / `codex exec` / `gemini --prompt` / `cursor-agent --headless`). TurnGuard busy-files prevent waking agents mid-tool-call. Phase 2 self-assignment landed: `@open <work>` and `TODO @<role>: ...` patterns route to capability-matched agents (claude→{tui,react,tests}, codex→{relay,backend,audit}, gemini→{docs,research}, cursor→{refactor}). Local end-to-end demo at `scripts/demo_reflex.sh` runs the full pipeline in ~60ms with a stub adapter (no API spend).

Production-deploy bugs caught + fixed during shipping: register-agent 500 (MultipleResultsFound on duplicate unrevoked keys → bulk revoke + mint single canonical key), tenant peering for child agents (b4d4c1d), cold-install smoke clobbering host `~/.gemini` (HOME isolation + atexit backup), TUI auth precedence preferring legacy `relay_secret` over real `api_key` (silent 401), TUI 2-second screen-wipe + scrollback nuke killing copy/paste, multi-line paste exploding into N separate messages, `_send_message` swallowing 4xx response bodies and surfacing misleading "Couldn't reach the relay" errors, identity disambiguation (humans get `@arav` + green ●; agents keep their hashed-color suffix). 5xx retry with exponential backoff added to `_send_message`. reflexd refuses to start with non-agent participant names (must end in claude/codex/gemini/cursor).

### Cold-install CI (2026-05-01)

Added `.github/workflows/cold-install.yml` — runs on every PR, every push to
main, and nightly at 08:00 UTC. Spins up a fresh runner per cell across a
matrix of `{ubuntu, macos, windows} × {3.10, 3.11, 3.12, 3.13}` (Windows×3.10
excluded for known mcp/cffi grief), `pipx install`s the PR's checkout with
the pip wheel cache disabled, then runs `scripts/cold_install_smoke.sh` to
boot the relay, hit `/health`, run `quorus init`, create a room, send a
message, and confirm round-trip in <30s. Total budget per cell: 60s smoke,
8min job. Mirrored locally by `scripts/cold_install_smoke.sh` (POSIX-bash
3.2 clean) which calls `scripts/cold_install_smoke.py` (the actual driver).
A pytest skeleton at `tests/test_cold_install.py` runs the smoke against
`PATH`-installed binaries by default and adds an opt-in Docker variant
(skipped cleanly when Docker isn't there). This is the gate that locks in
the April 23 2026 hackathon failure mode where `pytest` was green but
`pipx install` produced a binary that wouldn't open.

| Date       | What                                                                           |
| ---------- | ------------------------------------------------------------------------------ |
| 2026-05-03 | feat: reflexd-manager — multi-agent supervisor + launchd plist auto-restart    |
| 2026-05-03 | fix: register-agent 500 + 5xx retry + reflexd participant assert + bulk revoke |
| 2026-05-03 | fix: TUI header + send error surfaces + chat_identity dead-code cleanup        |
| 2026-05-02 | feat: Reflex PR-C1+C2+C3 + Phase 2 self-assignment + iMessage TUI polish       |
| 2026-05-02 | feat: Quorus Operating Discipline (QOD) — cross-harness 6-rule constitution    |
| 2026-05-02 | feat: identity disambiguation (humans @arav green ●; agents hashed-color)      |
| 2026-05-02 | fix: TUI race conditions + 2s screen-wipe + scrollback nuke + paste-split      |
| 2026-05-02 | fix: cold-install host isolation + Gemini settings clobber + tenant peering    |
| 2026-05-01 | feat: cold-install CI — `pipx`-from-checkout smoke + nightly cron              |
| 2026-04-15 | feat: rebrand Murmur → Quorus (package, CLI, TUI, entry point, configs)        |
| 2026-04-15 | feat: beautiful CLI — grouped help, teal banner, spinners, styled errors       |
| 2026-04-15 | feat: new ui.py module with Theme, banner, spinner, status helpers             |
| 2026-04-15 | fix: 4 critical security issues (osascript, bootstrap, invite, TOCTOU)         |
| 2026-04-15 | fix: deploy configs (Dockerfile, alembic.ini, railway.toml, render.yaml)       |
| 2026-04-15 | feat: `quorus` with no args opens TUI (like claude/gemini)                     |
| 2026-04-15 | feat: key prefix `quorus_sk_` / token `quorus_join_` (legacy-compat)           |
| 2026-04-14 | feat: website Lighthouse polish — 100% SEO, 93% a11y                           |

---

## Architecture

```
Any Agent (Claude Code / Codex / Cursor / Gemini / Ollama / browser)
    ↓
HTTP API or MCP tools
    ↓
[Quorus Relay (FastAPI)] ← SSE push / long-poll / webhook
    ↓
Fan-out to room members
    ↓
Each member's inbox → agent reads via check_messages / HTTP GET / SSE stream
```

---

## Key Decisions

- Package name: `quorus` (was `murmur-ai`)
- Entry command: `quorus` (was `murmur`)
- Config dir: `~/.quorus/` (was `~/mcp-tunnel/`, then `~/.murmur/`)
- `quorus` with no args opens TUI (matches claude/gemini convention)
- Legacy prefixes (`murm_sk_`, `murm_join_`) accepted on verify
- Free stack only, MIT licensed
- SSE-only delivery — no polling

---

## Contributors

- **Arav** (aravkek) — co-founder, parent Claude directing agents
- **Saad** — co-founder
- **Aarya** (Aarya2004) — co-founder
