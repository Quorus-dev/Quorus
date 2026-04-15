# Quorus — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-04-15 (Quorus rebrand, TUI/CLI polish, security hardening)

---

## Current State

Quorus (package: quorus) is the coordination layer for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `main` (905 tests passing — post-rebrand + polish)

**Package:** `pip install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"`

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
pip install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"
quorus init <your-name> --relay-url <url> --secret <secret>
# restart claude code — done
quorus           # opens the hub
```

**Config:** `~/.quorus/config.json` (legacy `~/mcp-tunnel/` + `~/.murmur/` still read)

**What's built:**

| Module                      | What                                                                                     |
| --------------------------- | ---------------------------------------------------------------------------------------- |
| quorus/relay.py             | FastAPI relay: rooms, SSE fan-out, history, presence, rate limiting, health, admin       |
| quorus_mcp/server.py        | MCP server: 12 tools incl. claim_task, release_task, get_room_state, SSE push, heartbeat |
| quorus_cli/cli.py           | 30+ CLI commands (quorus ...) — `quorus` with no args opens TUI                          |
| quorus_cli/ui.py            | Shared theme, banner, spinner, error/success/info primitives                             |
| quorus_tui/hub.py           | Full-screen TUI hub: rooms panel, agent list, live chat, first-run wizard                |
| quorus/routes/room_state.py | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)          |
| quorus/watcher.py           | Primitive C: SSE-driven daemon, writes .quorus/context.md for IDE indexing               |
| quorus/dashboard.py         | Web dashboard: live messages + swarm activity panel + usage bar                          |
| quorus/backends/            | In-memory + Redis + Postgres + SQLite backends (incl. RoomStateBackend)                  |
| tests/                      | 905 tests passing                                                                        |

**Stack:** Python 3.10+, FastAPI, asyncio, httpx, mcp (FastMCP), pytest, ruff, rich, hatchling

**Key features:**

- Rooms with fan-out messaging (send once, all members receive)
- **SSE-only push delivery** — zero polling, instant delivery
- **Shared state matrix** — goal, locked files, claimed tasks, decisions, active agents
- **Distributed mutex locking** — claim files with TTL, SSE broadcast LOCK_ACQUIRED/LOCK_RELEASED
- **Watcher daemon** — writes .quorus/context.md for IDE indexing, event-driven via SSE
- **MCP tools** (12): send/receive, rooms, locks, state, metrics, search
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

| Date       | What                                                                     |
| ---------- | ------------------------------------------------------------------------ |
| 2026-04-15 | feat: rebrand Murmur → Quorus (package, CLI, TUI, entry point, configs)  |
| 2026-04-15 | feat: beautiful CLI — grouped help, teal banner, spinners, styled errors |
| 2026-04-15 | feat: new ui.py module with Theme, banner, spinner, status helpers       |
| 2026-04-15 | fix: 4 critical security issues (osascript, bootstrap, invite, TOCTOU)   |
| 2026-04-15 | fix: deploy configs (Dockerfile, alembic.ini, railway.toml, render.yaml) |
| 2026-04-15 | feat: `quorus` with no args opens TUI (like claude/gemini)               |
| 2026-04-15 | feat: key prefix `quorus_sk_` / token `quorus_join_` (legacy-compat)     |
| 2026-04-14 | feat: website Lighthouse polish — 100% SEO, 93% a11y                     |

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
- **Aarya** (Aarya2004) — co-founder
