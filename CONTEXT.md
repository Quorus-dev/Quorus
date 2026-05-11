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

(rebrand complete; launch polish in flight)

---

## Recent Changes

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
