# Quorus — Shared Context

> **This file is the shared memory between all contributors' Claude instances.**
> Read this at session start. Update it after every significant change. Commit it with your work.

Last updated: 2026-10-16 (release audit; see Recent Changes)

---

## Current State

Quorus (package: quorus) is the coordination layer for AI agent swarms. "VS Code Live Share for AI Agents" — any model, any machine, any platform coordinates in real-time.

**Branch:** `feat/wake-rebuild-aug26` (2126 tests passing — Wake Rebuild streams F/R/D/L/T complete + three review passes closed). `feat/may4-sprint` is the pre-rebuild ancestor.

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
| quorus_mcp/                 | MCP server: 26 tools — messaging, rooms, locks, state, social verbs, 10 phase-1 primitives, approve  |
| quorus_cli/cli.py           | 30+ CLI commands (quorus ...) — `quorus` with no args opens TUI                                |
| quorus_cli/ui.py            | Shared theme, banner, spinner, error/success/info primitives                                   |
| quorus_tui/hub.py           | Full-screen TUI hub: rooms panel, agent list, live chat, first-run wizard                      |
| quorus/routes/room_state.py | Primitive A+B: GET state, PATCH goal, POST decisions, POST/DELETE locks (mutex)                |
| quorus/watcher.py           | Primitive C: SSE-driven daemon, writes .quorus/context.md for IDE indexing                     |
| quorus/dashboard.py         | Web dashboard: live messages + swarm activity panel + usage bar                                |
| quorus/backends/            | In-memory + Redis + Postgres + SQLite backends (incl. RoomStateBackend)                        |
| tests/                      | 2126 tests passing (`pytest -q`, ~2 min)                                                       |

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

**Dogfood phase (2026-10-08).** The autonomous loop now works with real
agents on one laptop: human posts `@open <task>` → an idle agent claims it,
posts a plan, does the work in the bound repo, commits, @-mentions a teammate
for review → reviewer wakes, reviews, hands findings back → fixer wakes and
fixes → reviewer approves. Proven live 3× in `~/dev/quorus-playground`
(todo CLI: build → 2 review findings fixed → approve; `clear`; `count`).

Live setup: `scripts/dogfood.sh up <repo> [room]` (launchd relay on
127.0.0.1:8787 + one wake daemon per agent + `dogfood` hub profile);
`scripts/dogfood.sh connect` points the human's interactive Claude Code /
Codex MCP at it as `*-desktop` identities. State in `~/.quorus/dogfood/`.

Live push into an OPEN Claude window works: `scripts/dogfood.sh claude`
(= `claude --dangerously-load-development-channels server:quorus`). Room
messages that @-mention your desktop identity (or DM it) arrive as
`<channel>` events and start a turn with no keystrokes — proven live: mention
→ "PONG — todo.py" posted back in 12s. Codex woken agents run with
`-s workspace-write` (Arav opted in 2026-10-08; proven: codex built
`version`, committed, Claude reviewed + approved).

Next:
1. PyPI publish. Release-ready 2026-10-10 (20/20 scenarios, clean-wheel install on 3.10 and
   3.13, CI green). Blocked on Arav: `quorus` is taken on PyPI by an unrelated quantum
   project, so pick a distribution name (e.g. `quorus-ai`); and a PyPI token.
2. Channels are a research preview: custom servers need the dev flag and a
   one-time confirm per launch; interactive Codex has no push API.
3. Codex's own `quorus say` from inside its sandbox failed to reach the relay
   once (final reply still lands via the daemon) — investigate.
4. reflexd.py is ~2.8k lines (500-line rule) — split into a package.
5. Later: optional fast decision gate before a wake (OpenAI Decisions API on gpt-6-luna, or
   TypeSafe Jev) to skip agent runs that would end in NO_REPLY. Only when a key is set.

## Recent Changes

### Release audit: relay access logs (2026-10-16)

- Uvicorn access records redact stream-token query values before formatting.
  Subprocess regressions exercise the actual AccessFormatter for CLI and ASGI
  entry points without sockets.

### Release audit: daemon identity paths (2026-10-16)

- Validate participant names before constructing daemon state paths or writing
  private profiles; agent suffixes alone did not reject traversal or absolute paths.

### Release audit: private credential files (2026-10-16)

- Config/profile, agent cache/plist, and wake credential writes use exclusive
  random temporary files and atomic replacement. Existing loose permissions
  and planted destination or predictable-temp symlinks cannot leak secrets
  or truncate unrelated files. Ten regression cases failed before the fix.

### Release audit: wake log secrets (2026-10-16)

- Redact stream-token query parameters at daemon log sinks, including httpx
  URL logs and exception tracebacks; DM subscription logs name only the agent.
- Harness errors log exit status, never raw stderr that can echo credentials.

### Release audit: DM stream auth (2026-10-16)

- DM streams reject the shared-secret fallback when DATABASE_URL is set,
  matching the main SSE route; minted stream tokens remain supported.

### Release audit: supervisor log isolation (2026-10-16)

- Resolve the supervisor log from the active config directory at use time;
  imported CLI code no longer writes to the real home after a config override.
- Regression guard rejects any log open outside the isolated config directory.

### Every human message wakes an agent, which decides (2026-10-10)

- Keyword triage kept missing real asks ("respond to this", "arav-claude what time is it").
- Now any human message not addressed to someone else wakes one agent (auction picks), and the
  agent may answer or reply `NO_REPLY` to stay silent. Desktop banners fire for mentions only.
- Agent messages work the same way: an unaddressed agent message wakes one teammate, told to
  reply only with something concrete. After 6 agent messages in a row with no human, only
  @-mentions wake anyone (`REFLEXD_MAX_UNADDRESSED_AGENT_RUN`). Quoted @-echoes stay inert.

### Full review: code review + consumer CLI sweep (2026-10-09 evening)

Two parallel reviews (code review of today's diff; a new-user sweep of every
command against an isolated relay). Fixed: publish into whichever branch the
human checked out (integration branch now recorded); staged conflict markers
could reach main; resolution wakes ignored room mode; restart mid-merge
stranded branches; git hook timeouts swallowed replies; drain blocked the
reader; MCP `-m` let a repo shadow our packages (now `python -I -m`);
`quorus relay` was broken for every install; `quorus init` auto-started a
manager that duplicated `agent add`; first-run wizard defaulted to a dead
public relay AND saved no secret on the local path (now: Enter = local relay
started for you, restarted on next `quorus` if down); tracebacks → readable
errors (missing room, wrong secret, relay down); chat Ctrl+C/Ctrl+D hang;
join claimed success on failure; hub [j] rejected invite codes; doctor
false failures. Known, not fixed: legacy shared-secret relays can't enforce
owner-only approvals against a malicious local agent (needs account mode);
kick is advisory in legacy mode; several commands still exit 0 on
not-found; DMs to unknown names are accepted; bindings don't follow
`rename`; no public/hosted relay exists.

### Scenario gate + CI green (2026-10-09)

`scripts/scenarios.py`: 20 real-user scenarios against an isolated relay
with REAL claude/codex (setup, greetings/acks/questions/mentions, other-human
silence, @open build+publish+review, parallel same-file tasks, two projects,
manual-mode approve/deny + owner-only, unbound room, daemon down, restart
mid-job, relay restart, second human, ping-pong guard, burst, CLI errors,
agent remove). FINAL: 20/20 in one clean run on dac8433 (5 full runs total).
Later fixes from the gate: drain acked in-flight jobs (lost on restart);
a denied agent kept retrying (now stops after first deny/timeout); manual
approvals wait 120s not 300 (one agent = one job across rooms). Bugs it
found: codex `exec resume` has no `-s` (resumed wakes were read-only) and
Codex's sandbox blocks all .git writes → daemon now commits agents' work;
manual mode lost to owners' Claude "auto" default; humans' messages without
@/?/@open were ignored. CI (3.10–3.13, Postgres, Redis, Windows/mac/linux
cold install) green for the first time: py3.10 TimeoutError/asyncio.timeout
bugs, git identity on runners, unconfigured `import quorus_mcp`, Windows
cp1252 crash, sys.modules pollution in tests.

### `quorus agent add` — real-user onboarding (2026-10-09)

Re-enacting a new user showed only scripts/dogfood.sh could give an agent a
wake daemon; `quorus add-agent` built a legacy polling workspace. Now:
`quorus create <room>` → `quorus agent add claude|codex|gemini --room <room>
[--repo PATH --mode default|manual|autonomous]` → `quorus say <room> ...`.
One launchd daemon per agent (reuses an existing one, incl. dogfood's);
account relays mint + cache a per-agent key; `agent remove` stops it and
leaves its rooms. `quorus` symlinked into ~/.local/bin on Arav's Mac.

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
