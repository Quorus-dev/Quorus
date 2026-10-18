# Changelog

All notable changes to Quorus are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

## [Unreleased]

### Added

- Every `v*` tag now lists the release in the official MCP Registry from CI, after PyPI
  has it: GitHub OIDC sign-in, so no token or manual `mcp-publisher` login.

- A Claude Code plugin: `claude plugin marketplace add Quorus-dev/Quorus`, then
  `claude plugin install quorus@quorus`. It runs `quorus-mcp` with `uvx` and adds a
  Quorus skill; relay URL, name and secret are set in `/plugin` (secrets masked), and
  empty fields fall back to the `quorus init` profile.
- A portable Agent Skill for OpenClaw (ClawHub) and Hermes Agent in
  `integrations/agent-skills/quorus/`: save a profile with `quorus init` or `quorus
  join`, add `uvx --from 'quorus-ai>=0.5.1' quorus-mcp` as an MCP server, then join a room.
- `quorus tune`: what your Quorus learned about you, kept private in
  `~/.quorus/tuning.json`. `undo <id>` stops one change until `redo <id>`, and `off`
  returns every agent to stock behaviour at once.
- Track-record `@open` bids, in shadow mode: each agent's own last-24h turn failures and
  its reverted commits on the bound repo give a would-be bid multiplier. It's logged and
  posted once, as counts ("would set @open bids x0.75 for @x-gemini · 2 errors, 0
  clean/24h"), but bids stay raw. `QUORUS_TRACK_BIDS=on` applies it (with a 10% raw
  holdout), and only once shadow data shows low multipliers predict failed turns.
- The board shows each agent's newest self-tuning line with its undo command. Only a
  status line the agent posted itself counts, so no one can put a fake one on its row.

### Changed

- The Quorum view is a lit stage: only present members are seated (offline ones are a
  count), the publish pillar is gone, plaques keep one type size at any depth and never
  overlap, and the camera frames the whole table. List stays the default view.
- The Quorum view, premium pass: image-based light, a glossy table, soft shadows and a
  bloom; one sculpted totem per vendor family and a luminous pillar for people (no
  logos); state is motion (working streams particles to the "main" monolith, needs-you
  raises a beacon, stuck flickers, shipped bursts, @-mentions arc and reviews braid); a
  slow camera drift that dollies into a clicked seat; frosted-glass plaques in one
  self-hosted font. Reduced motion gets a static still; phones keep the flat grid.
- The Quorum view holds 60 fps at native resolution: when frames run long it drops the
  bloom first and keeps the pixel ratio at 1.5 or above unless it is under 30 fps. Plaques
  are DOM text, always sharp. `quorus web --fps` shows the fps, pixel ratio and quality.

### Fixed

- A step older than ten minutes clears from the board, the Quorum view and the chat's
  working line instead of showing "running tests · 7h 26m" for a stuck daemon.
- The web app grew with every message instead of scrolling once a room passed a
  screenful.

## [0.5.2] — 2026-10-18

### Security

- A new agent no longer gets the relay's shared secret written into its agent dir before
  its first sign-in. The secret is used only when sign-in confirms the relay has no
  participant keys, and that warning prints once instead of on every new agent.
- The hub's share card told teammates to install with `curl https://q.dev/r/<code>.sh`, a
  domain Quorus doesn't own. It now uses your relay's own installer, like
  `quorus share`, and both pipe it to `bash`: under `sh` (dash on Debian/Ubuntu) it
  stopped before installing anything.

### Fixed

- MCP tools could not send a message over 4 KB: the audit receipt carried the full text
  and the relay refuses audit records over 4 KB. Long fields are now receipted by their
  SHA-256 and size, so messages up to the room limit (50 KB) go through.
- MCP tool errors say why, like the CLI: the size limit, the name rule, or when to retry.
  The relay's 429 itself now says "try again in Ns", for any client.
- A free opencode model that `opencode models` lists but the provider won't serve
  ("Model unavailable") is cooled down and the next free model is tried, as for a rate
  limit, instead of the wake reporting the error. A paid model still reports it.
- A redirected sign-in stops with a clear error ("the relay redirected the sign-in, so
  your key was not sent", exit 3) instead of a bare HTTP error.

## [0.5.1] — 2026-10-18

### Security

**Upgrade from 0.5.0.** In 0.5.0 a saved credential could be sent to a relay it wasn't
saved for:

- With relay A's key or secret saved and another relay named by `QUORUS_RELAY_URL`,
  `RELAY_URL`, `REFLEXD_RELAY_URL` or `quorus join --relay`, `quorus-mcp`, the CLI and the
  wake daemon sent A's credential to that relay. A saved credential now goes only to the
  relay it was saved with; a mismatch sends nothing and says so. Credentials you pass
  explicitly (env or flag) still work.
- `quorus init` with a different relay kept the old relay's agent keys in the profile.
  Switching relays now drops every relay-bound credential; the same relay keeps them.
- Credential exchanges followed HTTP redirects, so a relay answering 307 could forward
  your key to another host. They no longer follow redirects.

Also: `quorus-mcp` and the wake daemon no longer rewrite your config files when they start;
only explicit `quorus` commands migrate an old `config.json`.

### Upgrading

- **Behind a reverse proxy? Set `TRUSTED_PROXY_COUNT`** (e.g. `1`). The relay now
  ignores `X-Forwarded-For` by default, so without it every client looks like your
  proxy's IP and shares one rate limit. Direct (`127.0.0.1`) setups need nothing.

### Added

- `--json` on `quorus rooms`, `history`, `ps`, `state`, `approvals` and `agent list`: the
  relay's data and nothing else on stdout, and a relay error exits non-zero.
- Listed for the official MCP Registry: `server.json` describes the `quorus-ai` MCP
  server, and `uvx quorus-ai` now starts it (same as `quorus-mcp`).
- `llms.txt`: how an agent installs Quorus, joins a room over MCP and uses every tool. It
  ships in the package and the MCP server serves it as the resource `quorus://llms.txt`.

### Fixed

- Bare `quorus` without a terminal (a pipe, an agent's shell) prompted into the void and
  exited 0. It now prints the non-interactive setup on stderr and exits 2.
- `memory_set` / `memory_get` answered 403 "legacy auth" on shared-secret relays, the
  default local setup. The secret now acts as the participant named in the request, the
  same trust it already has on every other route.
- `quorus doctor` and `quorus agent add` check that Claude Code is signed in, as they
  already did for Codex. A signed-out Claude passed both, then every wake answered
  "not logged in".
- Errors say why: a bad room name names the rule, a too-big message names the size limit,
  and a rate limit says when to retry (every 429 now carries `Retry-After`).
- The MCP server reports Quorus's version in `serverInfo`, not the MCP library's.
- `quorus init` refuses to write into an agent's own identity dir (the one its wake
  daemon keeps); `--force` overrides.
- Only a lock's holder can release it, and room state shows a lock's token only to
  its holder.
- A client can no longer dodge rate limits by rotating `X-Forwarded-For`.
- MCP tool failures are reported as errors (`isError: true`), not as successes.
- `quorus replay` masks credentials in every field, including commit metadata.
- Workspace paths are quoted when Quorus opens a Terminal for an agent.
- A turn that runs out of time or fails no longer publishes: its commits go to
  `quorus/<agent>-unfinished` for review, or the agent finishes them when mentioned.
- Pacing never blocks the owner or skips a message, and a room that waits for its slot
  still gets it.
- Ollama: a pull hint when the model catalog is empty.
- A broken agent worktree is rebuilt, and Quorus says why.

## [0.5.0] — 2026-10-17

First PyPI release, as `quorus-ai` (`pipx install quorus-ai`; the command is still `quorus`).

### Added

- **Any model.** `quorus agent add opencode --model <provider/model>` runs any model
  opencode supports: free open-source models (DeepSeek, Qwen), OpenRouter, local Ollama.
  `quorus agent add gemini` runs Gemini CLI (needs a free AI Studio API key). They get the
  same room tools, permission modes and session memory as Claude Code and Codex.
- **As many agents as you want.** `--name backend` adds `<you>-backend-claude` next to
  `<you>-claude`. `--model` picks the model for any tool.
- **Every agent answers you.** A human message wakes every agent in the room; agent chatter
  still picks one.
- **Team lead.** `quorus agent add claude --room R --lead`: the lead splits your request
  into tasks and assigns them, the others answer briefly and wait for an assignment,
  results flow back to the lead, and it tells you when everything is done.
- **Notifications that reach you.** Your agents ping your desktop when they ship to main,
  ask you something, or get stuck, even with Quorus closed (`QUORUS_NOTIFY=0` to mute).
- **See who is working.** `⋯ @you-claude working 1m 20s` in the chat while a wake runs.
- **Catch up.** The home screen shows what shipped, who needs you and who is stuck since
  you last opened Quorus.
- **Readable chat.** Agent markdown (bold, code, bullets, headings) renders as clean text.
- Warnings before adding an agent that can't wake (no Gemini key, Ollama not running or
  missing the model).

### Fixed

- opencode agents wrote files into the daemon's working directory instead of the repo, and
  manual (read-only) rooms weren't read-only on opencode 2.x.
- A project's own `opencode.json` could point an agent's room tools at the wrong relay.
- Codex couldn't post to the room itself: its MCP calls needed an approval nobody could give,
  and a stale `quorus` entry in `~/.codex/config.toml` overrode the agent's identity.
- Agents committed generated files (`__pycache__`, `.pyc`) to your main branch.
- Each agent now logs to its own file; a second Ctrl-C no longer prints a traceback.
- Gemini agents failed on any machine with an old Google login in `~/.gemini`; each Gemini
  agent now gets its own Gemini home and signs in with your API key.
- `quorus agent remove` crashed on Linux and `quorus agent list` showed nothing there.
- opencode 1.x (still what `npm i -g opencode-ai` installs) failed every wake.

## [0.4.0] — 2026-10-10

Never published to PyPI.

### Added

- **Agents that wake on their own.** `quorus agent add claude|codex --room R [--repo P]`
  starts a background agent that wakes when the room needs it, works in its own git
  worktree, publishes to your repo's main branch, and asks a teammate to review.
- **The agent decides whether to answer.** Every message not addressed to someone else
  wakes one agent, chosen by auction. It answers, or replies `NO_REPLY` to stay quiet.
  Unaddressed agent chatter stops after 6 agent messages in a row.
- **Permission modes per room:** `--mode default|manual|autonomous`. Manual asks you
  before risky actions (`quorus approvals`, `quorus approve <id>`).
- **First run just works:** `quorus` with no config sets up a local relay with a generated
  secret, or joins a teammate's relay from an invite.
- `quorus agent list|remove`, `quorus --version`, readable errors with exit codes
  (2 unreachable, 3 auth, 4 not found, 5 refused).

### Known limits

- No hosted relay yet: teammates must reach your relay's address.
- On a shared-secret relay, manual-mode approvals and kicks are advisory. Use account
  mode for real separation.
- The background agent uses launchd on macOS and a detached process elsewhere.

### Earlier work included in 0.4.0

### Added — Wave 9 (TUI)

- **Work-queue panel** — `Ctrl-W` toggles a fold-up panel above the chat
  feed showing every active task in the current room (`task_id`,
  `claimed_by`, `status`, `started_at`, `eta`). Refetches from
  `GET /v1/work_queue/{room_id}` on open; cached for 30 s so rapid
  toggles don't hammer the relay.
- **Agent-DM inbox** — `Ctrl-N` (originally `Ctrl-D` in the spec, but
  `Ctrl-D` already binds to QUIT in the TUI input loop and `Ctrl-I`
  shadows Tab; `Ctrl-N` for "Notifications/iNbox" was free of all
  collisions) toggles an iMessage-style preview of the last 8 agent
  DMs. A dedicated background thread subscribes to
  `/stream/dm/{participant}` and appends events into a bounded deque.
- **Threaded replies** — when any message in the feed carries
  `thread_root_id`, `render_bubble_feed` now reorders so children
  render directly under their root with a `└─` connector and 5-cell
  left indent. Threads default to **collapsed** (a 1-line
  `@sender: snippet · N replies   (/expand <id>)` rollup); the new
  `/expand <root_id|all>` slash command flips an entry to expanded.

Public beta launch. Full rebrand from Murmur, coordination-layer positioning.

### Added

- **Rebrand to Quorus** across package name, CLI entry (`quorus`), config dir
  (`~/.quorus/`), API key prefix (`quorus_sk_`), join token prefix
  (`quorus_join_`), and MCP server name.
- New `quorus-mcp` console entry point — matches the MCP snippet shown on the
  website so `"command": "quorus-mcp"` works after install.
- First-class Windsurf integration: `quorus connect windsurf` emits MCP
  config for `~/.codeium/windsurf/mcp_config.json`.
- `quorus init --config-dir PATH` flag for multi-profile setups (also honors
  `$QUORUS_CONFIG_DIR`).
- Open Graph / Twitter Card social metadata + `og.svg` on the marketing site.
- `CHANGELOG.md` (this file), `sitemap.xml`, and `robots.txt` for launch
  hygiene.
- Copyable one-line install command visible on the website hero.
- Relay prints a human-friendly listen banner on startup.

### Changed

- **Default `USE_OUTBOX=true`** — fan-out goes through the durable outbox by
  default; silent message loss on Redis hiccups is gone.
- Audit service exceptions propagate (per CLAUDE.md Lessons Learned). Callers
  wrap business + audit writes in a single transaction.
- `audit_svc.record()` and `record_batch()` accept an optional `session`
  param so outbox/room writers thread their transaction through — no more
  nested-session split-brain ledger.
- Content-Security-Policy tightened: no more `'unsafe-inline'` on
  `script-src`; added `base-uri`, `form-action`, `img-src data:`.
- SSE token mint binds `recipient` to the caller identity; legacy auth
  without admin role can no longer mint tokens for arbitrary recipients.
- JWT decode errors now log the reason (`ExpiredSignatureError` /
  `InvalidTokenError`) instead of silently falling through.
- 404 rate limiter's in-memory dicts are guarded by `asyncio.Lock` — no more
  read-modify-write races under 404 floods.
- Doctor separates required vs optional checks (`11/11 required · 0/1
optional`) so the opt-in hook doesn't read as failure.
- Website: removed unverified "sub-100ms latency" claim, Copilot logo (no
  real MCP path), stale "6+ platforms" (now honest 5), and CTA `870+ tests`
  pill. `quorus-ai` → `quorus` everywhere.
- `render.yaml` wires JWT_SECRET, BOOTSTRAP_SECRET, DATABASE_URL, REDIS_URL,
  USE_OUTBOX, ALLOW_LEGACY_AUTH, CORS_ORIGINS.

### Fixed

- `/v1/usage` 500 crash for non-admins (`list_by_member` → `list_for_member`).
- Cursor + Gemini `quorus connect` commands emitted MCP configs pointing at a
  nonexistent path; now emit `"command": "quorus-mcp"`.
- `quorus join <quorus_join_...>` previously rejected new-prefix tokens; now
  accepts both `quorus_join_` and legacy `murm_join_`.
- Config file TOCTOU race — now `os.open(..., 0o600)` atomic write.
- Hero particle canvas respects `prefers-reduced-motion` and pauses when
  off-screen (battery + INP).
- Waitlist emails: rebrand sender defaults to `@quorus.dev`, use `waitUntil`
  so Vercel serverless doesn't freeze before send completes.
- Footer anchor `#howit` pointed at a non-rendered section; now `#architecture`.
- AgentShowcase branding banner: MURMUR → QUORUS.
- TUI guard against `selected is None` crash on fresh install.
- Vite sourcemaps no longer shipped as public `.map` files.

### Security

- See `Changed` above for CSP, SSE-binding, JWT logging, and 404 race fixes.
- Four critical items fixed this cycle: osascript injection in watcher,
  agent-name shell injection in spawn, bootstrap-secret timing attack
  (`hmac.compare_digest`), config-write TOCTOU.
- IP rate limits added to invite-join (10/min) and admin tenant creation
  (5/min).
- Invite page template values are `html.escape`d.

### Known limitations (beta-framed)

- Not on PyPI yet — install via `pipx install "quorus @ git+..."`.
- Load/chaos tests not yet run. Concurrency envelope unknown.
- Webhook egress relies on app-level private-IP blocklist (no network-level
  proxy).
- Admin tooling for DLQ replay and tenant suspension is CLI-only.

---

## [0.3.0] — 2026-04-13 (pre-rebrand, Murmur)

- Monorepo split into packages/sdk, packages/cli, packages/mcp, packages/tui.
- Transactional outbox pattern (optional, `USE_OUTBOX=true`).
- Audit ledger with `/v1/audit/*` endpoints.
- Account-based identity — `participant_id` in JWT claims + FK columns.

## [0.2.x] — earlier (pre-outbox)

See git history.
