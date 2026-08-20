# Quorus Rebuild Research — Notifications, Competition, MVP

> Research date: 2026-08-20. Authors: Claude (deep research + repo audit) for Arav + Aarya.
> Status: RESEARCH — feeds the planning phase, then product spec, then rebuild.

---

## 1. TL;DR

- The "agents respond instantly when tagged" problem is **solvable now** — and mostly wasn't in May. Claude Code shipped cross-session messaging (inbox socket, v2.1.224, Aug 2026) and Channels; Codex shipped an app-server; Gemini shipped hooks v1 + resume. The primitives Quorus needed now exist.
- Why it felt manual for us: **live sessions only saw messages when the human typed** (UserPromptSubmit hook), and reflexd's headless wakes were **amnesiac one-shots in the wrong directory** with a silent stub fallback. All four are fixable.
- The competitive field is crowded (cc-connect 15.1k stars, Cumora, agentchattr, Claude Agent Teams, Slack Code launched 2026-08-20). The **concept** "cross-harness agent chat with wake" scores 4/10 novelty. The **composite** that remains unoccupied: neutral OSS relay + cross-machine + reliable cold-start wake + per-room session memory + coordination semantics (claims/locks/audit). That's the wedge.
- MVP scope: **Claude Code + Codex + Gemini**, one magical loop: mention → agent wakes in the right repo with the room's memory → works → permission prompts relay into the room → replies with result. Nobody has that cross-vendor.

---

## 2. Why notifications felt manual — proven root causes

Audited the actual code paths (`docs/CROSS_HARNESS_NOTIFICATIONS.md`, `scripts/reflexd.py`, `packages/cli/quorus_cli/cli.py`):

| # | Root cause | Evidence |
|---|---|---|
| 1 | **Live-session delivery requires a human keystroke.** Claude Code integration = `UserPromptSubmit` hook (`quorus hook enable`) — fires only when the human submits a prompt. Messages sit in the inbox until you type. Gemini's `BeforeAgent` and Cursor's `stop` hook have the same turn-boundary constraint. | CROSS_HARNESS_NOTIFICATIONS.md §Claude Code |
| 2 | **Headless wakes are amnesiac.** `build_claude_argv` = `claude --print -- <ctx>` — no `--resume`, no session mapping. Every wake starts from zero. | reflexd.py `build_claude_argv` |
| 3 | **No working-directory targeting.** Zero `cwd` handling in reflexd — woken agents run wherever the daemon started, so they can't do real work in your project repo. | grep cwd reflexd.py → nothing |
| 4 | **Silent stub fallback.** If the vendor binary is missing, reflexd posts "(reflexd-stub) on it…" fake replies — looks like the product is broken. | reflexd.py `_stub_reply` auto-engage |
| 5 | **Daemon lifecycle fragility.** reflexd-manager starts on `quorus init` but doesn't survive reboot unless `install-launchd` was run; auth misconfig (legacy `relay_secret` precedence) caused silent 401s in May. On Arav's machine, iCloud eviction of 42k repo files was also hanging everything. | cli.py `_init_autostart_supervisor`; CONTEXT.md production-bug log |

Bottom line: the SSE → triage → bid → claim → spawn pipeline itself works (stub demo e2e: 340ms, verified 2026-08-20). What failed was the last mile: delivery into live sessions, and quality-of-wake for headless ones.

---

## 3. The 2026 platform unlock — per-harness capability matrix

| Harness | Spawn headless | Inject into LIVE session | Event hooks | Headless resume |
|---|---|---|---|---|
| **Claude Code** | `claude -p "…" --output-format stream-json` (+ Agent SDK) | ✅ **Inbox socket** (v2.1.224+, GA): every session binds a Unix socket (`CLAUDE_CODE_MESSAGING_SOCKET` + token); external process can post; delivery lands between tool calls mid-turn. ✅ **Channels** (preview): MCP server pushes events into a running session, with permission relay. | Full (Stop, Notification, SessionStart, Pre/PostToolUse) | `claude -p --resume <session-id>` (use explicit ID, never `--continue` in automation) |
| **Codex CLI** | `codex exec "…" --json --output-last-message f` | ⚠️ Via `codex app-server` (JSON-RPC 2.0, WebSocket/Unix socket, thread resume/fork) — controls app-server-hosted threads, not an independent TUI | `notify=` hook (`agent-turn-complete` only) | `codex exec resume --last` / `resume <id>` (JSONL rollouts in `~/.codex/sessions/`) |
| **Gemini CLI** | `gemini -p "…" --output-format json` (`--yolo`) | ❌ (tmux only) | ✅ Hooks v1, default-on since v0.26 (BeforeAgent/AfterAgent/Notification…) | `gemini --resume <uuid>` |
| Cursor | `cursor-agent -p` | ❌ | ❌ no public hooks | `--resume <chat-id>` |
| opencode | `opencode run`; `opencode serve` HTTP API | ✅ POST into any session via server API | plugin events / SSE | `run -s <session-id>` |
| Cline | `cline -y --json` | ❌ (resume-by-id only) | ❌ (SDK-driven) | `cline --id <task-id>` |

Key architecture consensus (validated by Slack Socket Mode + every OSS competitor): **tiny always-on daemon per machine, outbound WebSocket to relay, durable per-agent inbox on the relay, ack fast / work async / reply via API.** Laptop sleep is the #1 real-world killer → relay must persist undelivered mentions and show presence ("@claude asleep — queued").

---

## 4. Competitive landscape (2025–2026)

Five layers:

1. **Direct competitors (rooms/chat for agents):** cc-connect (MIT, **15.1k stars** — daemon spawning 10+ harnesses, bridged to 13 chat platforms; the biggest threat; lacks rooms/state/locks), Cumora (2.8k — distributed team chat + atomic task claims, Claude+Codex only), agentchattr (1.5k — @-mention wake via keystroke injection, localhost-only, brittle), MCP Agent Mail (inbox/threads/file leases, pull-only), cross-agent-teams-mcp, agent-talk, Agent Room.
2. **Platform-native walled versions:** Claude Code **Agent Teams** (shared task list + claiming + SendMessage — Quorus's shape, Claude-only, single-machine), Claude **Channels** (documented gap: **no cold-start wake** — events only reach an already-running session), Codex remote-control + Slack/Linear cloud tasks, Cursor 2.x parallel agents, Google Antigravity. **Slack Code launched 2026-08-20** (Salesforce): tag Claude Code / Devin / Vercel Agent / Copilot into Slack project channels — harness-level cross-vendor, but SaaS, not local-first, not agent↔agent.
3. **Session managers (no comms):** claude-squad 7.9k, Conductor, Sculptor, CCManager. Graveyard warning: Terragon dead Jan 2026; bloop/Vibe Kanban dead Apr 2026 at 26k stars ("couldn't find a business model"); Crystal deprecated.
4. **Remote control / agent inbox:** Omnara (YC S25, 7 harnesses, $9/mo), Happy/Happier (17k stars), VibeTunnel, many single-vendor Slack/Telegram bridges.
5. **Protocols:** A2A v1.0 under Agentic AI Foundation — enterprise cloud-to-cloud, zero coding CLIs speak it natively (Gemini CLI has an experimental server + RFC → cheap win: ship an A2A endpoint on the relay). IBM ACP dead (merged into A2A). MCP 2026-07-28 spec went stateless; "agent communication" is on the roadmap but task-shaped and 12+ months out. Zed's ACP won agent↔editor interop — watch it extending to agent↔agent. AGENTS.md = only true cross-vendor convention, static-only.

## 5. White space — what nobody fills

1. **Reliable cold-start wake, cross-vendor.** Anthropic Channels can't launch a dead session (their own docs). agentchattr injects keystrokes. cc-connect spawns but as a chat bridge without coordination semantics or delivery guarantees. Reflexd's strongest claim — if made bulletproof.
2. **Persistent cross-session shared memory between heterogeneous agents.** Nobody offers durable room state (goals, decisions, claims, session continuity) readable identically by a Claude agent today and a Codex agent tomorrow.
3. **Cross-vendor, cross-machine coordination semantics** — task claiming, TTL file locks, conflict prevention. Only single-vendor or advisory versions exist.
4. **Cross-harness portable skills/instructions at runtime** (QOD is already this; demand unvalidated).
5. **The neutral + distributed combination** — self-hostable relay any harness joins over MCP or plain HTTP. Unclaimed as a category standard; Zed/ACP proved a neutral protocol can win in months when it kills an N×M problem.

## 6. Honest novelty assessment

- **Concept** ("cross-harness wake-on-mention agent chat with shared state"): **4/10.** Each mechanic exists somewhere with real traction. Below our 8/10 idea bar — as an *idea*.
- **Composite wedge** (neutral OSS relay + cross-machine + reliable cold-start wake + per-room session memory + claims/locks/audit + approval relay): **~7/10**, unoccupied but contested from six directions.
- **Read**: this is not an idea-generation decision; Quorus exists. It's an **execution race in a validated, white-hot category**. Build **only** under three conditions:
  1. The wake must be demonstrably more reliable than cc-connect's spawn and agentchattr's injection — cold start, delivery guarantees, TurnGuard, presence honesty. Reliability IS the moat.
  2. Position against the vendors' documented gaps: "Channels needs a running session; Quorus wakes agents that aren't running" + "every vendor's native version is walled; Quorus is the neutral layer."
  3. A revenue thesis beyond stars: hosted relay for cross-machine teams; enterprise audit trail (hash-chained ledger of every agent decision/lock is a genuine compliance story).

## 7. The MVP — "Wake that actually works"

**Scope: Claude Code + Codex + Gemini.** One magical loop, polished.

### The demo (the WOW)

Arav posts from the TUI (or phone): `@arav-claude fix the flaky test in MedPort`.
1. Laptop daemon receives the mention over its outbound socket (<1s).
2. Triage passes; daemon finds the room is bound to `~/Desktop/MedPort` and has a stored Claude session for this room → wakes `claude -p --resume <id>` **in that repo, with the room's memory**. If Arav has a live interactive session open for that workspace, the message is instead **pushed into it via the inbox socket** — it appears mid-session, no keystrokes.
3. Agent hits a permission prompt → the prompt **relays into the room** — Arav taps approve from wherever he is.
4. Agent replies in the room with the diff/PR link. Total: visible acknowledgment <5s, work happens async with progress streamed.

Every step is a differentiator: cold-start + live-inject dual path (nobody), per-room session memory (nobody), room→workspace binding (nobody), cross-vendor approval relay (nobody).

### Architecture changes vs. today

| Area | Today | MVP |
|---|---|---|
| Live-session delivery | UserPromptSubmit hook (waits for human) | **Claude inbox socket push** (+ Channels plugin when GA); Gemini/Codex fall back to headless resume |
| Wake context | `claude --print` fresh, no cwd | **Room → workspace binding** (repo path per room) + **room → session-ID map** per harness; wake = resume in the right repo; invalid ID → fresh spawn with daemon-maintained room summary |
| Delivery guarantees | SSE fire-and-forget | Durable per-agent inbox on relay, sequence numbers + acks, redelivery on reconnect; presence: "asleep — queued" |
| Stub | Auto-engages when binary missing | **Killed** unless explicit `REFLEXD_STUB_REPLY=1`; missing binary = honest room error |
| Daemon lifecycle | Manual `install-launchd` | launchd/systemd installed by default on init; sleep/wake reconnect handling |
| Permission prompts | Hang silently | Allowlisted tools per room; prompt relayed to room with approve/deny; timeout reports "blocked on approval" |
| Cost guardrails | Triage + reply-depth 3 (exists) | + `--max-turns`, wall-clock kill timer, per-agent daily budget (soft → cheaper model, hard → stop), one active run per room |
| Codex path | `codex exec` one-shots | `codex app-server` under the daemon — true thread resume/fork over JSON-RPC |

### Also in MVP (already built, keep)

Rooms/SSE/relay, TurnGuard, triage/bid/claim auction (**must move to Redis** — currently process-local, breaks multi-replica), work queue, QOD skills distribution across harnesses (this IS the "skills that last through CLIs" requirement — already shipped via MCP instructions + skills dir + sysprompt prepend), hash-chained audit ledger, TUI.

### Explicitly NOT MVP

Cursor/opencode/Cline tier-A polish (keep argv-pinned, don't gate launch), Windsurf, reputation/wallet/DID primitives, self-PR/self-review (Phase 3 of autonomy plan), A2A endpoint (cheap, do right after MVP).

## 8. Pre-rebuild fix list (from the 2026-08-20 repo audit)

1. Repo out of iCloud-synced Desktop (eviction hangs everything) — or pin + exclude.
2. Test-order pollution: 16 fails + 9 errors in full suite, all pass in isolation (shared rate-limiter/global state) — must be fixed for trustworthy CI.
3. Spec stragglers: `room_messages.py:113` bare except on idempotency path; webhook `tenant_id` mislabel + full-URL SSRF logging; `register-agent` returns raw key ungated; ProfileManager legacy-dir write bypass; `server.py` 728 lines > 500 cap; 12 ruff nits.
4. Dead code: `scripts/autonomous_agent.py`, `patch_cli.py`, `patch_gemini.py`, `HACKATHON_REGRESSION 2.md`, `.venv 2/`, 5 stale worktree branches.
5. Redis-back the triage auction + Phase 1 primitives (capabilities/tool-catalog/memory are in-memory dicts — "persistent memory" currently isn't).

## 9. Phased plan

- **Phase 0 — Foundation (2–3 days):** fix list above; repo relocation; CI green on full suite.
- **Phase 1 — The Wake Rebuild (1–2 weeks):** daemon rework (durable inbox protocol, presence, sleep/wake), room→workspace + room→session mapping, Claude inbox-socket injection, resume-based wakes for all three harnesses, approval relay, guardrails, kill stub. Demo gate: the WOW loop above, cold, on two machines, 5 consecutive runs, zero manual triggers.
- **Phase 2 — Differentiation (1 week):** Codex app-server integration, Claude Channels plugin (when out of preview allowlist), per-room budget dashboard, A2A endpoint, polish TUI presence/queued states.
- **Phase 3 — Launch:** cold-install CI already exists; launch checklist in `docs/LAUNCH_READINESS.md`; positioning: "Channels can't wake a dead session. Quorus can. Any vendor. Any machine."

## 10. Three ways this fails + watch list

1. **cc-connect adds rooms/claims/locks** — 15.1k-star head start erases the composite. Watch releases; speed matters more than polish.
2. **Vendors close the gap** — Claude Channels gains cold-start wake, or Agent Teams goes cross-vendor; Slack Code adds local execution. Mitigation: neutrality + self-host + audit story they won't build.
3. **Monetization graveyard repeats** (Terragon, bloop, Crystal) — OSS stars ≠ revenue. Decide the hosted-relay/enterprise-audit thesis BEFORE the rebuild, not after.

Also watch: Zed ACP extending to agent↔agent; MCP roadmap "agent communication"; Gemini CLI A2A RFC landing.
