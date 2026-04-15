# Changelog

All notable changes to Quorus are documented here. This project follows
[Semantic Versioning](https://semver.org/) and [Keep a Changelog](https://keepachangelog.com/).

## [0.4.0] — 2026-04-15

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

- Not on PyPI yet — install via `pip install "quorus @ git+..."`.
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
