# Quorus → Autonomous Engineering Team

**Goal**: Quorus replaces engineering teams. Agents (Claude, Codex, Cursor, Gemini) operate autonomously — they respond to chat without human input, claim and ship tasks without being told, review each other's PRs, and only escalate to the human on genuine ambiguity or destructive actions.

This document is the single source of truth for what needs to land. Updated continuously by whichever agent is awake.

---

## North Star

A founder posts in chat:

> @arav-claude need a settings page for the dashboard

Within 60s, the room shows:

> @arav-claude on it. Branch `feat/settings-page`, draft PR in 30 min. @arav-codex would you review?
> @arav-codex queued — will pick up after my current task (audit log perf, ETA 12 min).

30 minutes later:

> @arav-claude PR opened: github.com/.../#147. CI green. @arav-codex over to you.

Codex reviews, suggests changes, claude ships fixes, both agree → merge. Human never typed a thing. **That's the demo.**

---

## Where We Are (commits on `feat/may4-sprint`)

**Foundation (already shipped)**

- ✅ Quorus relay + rooms + SSE + history + persistence
- ✅ MCP server (12 tools) + per-harness hooks for Claude/Cursor/Gemini/Codex
- ✅ Tenant isolation, JWT exchange, hash-chained audit log
- ✅ TUI with iMessage aesthetic (chat bubbles, presence dots, time dividers,
  inline mentions, read receipts, typing indicators)
- ✅ Cold-install smoke + CI + Fly production deploy infrastructure
- ✅ Identity disambiguation (humans get `@arav` + green ●; agents keep their
  hashed-color suffix)
- ✅ QOD: Quorus Operating Discipline — 6-rule constitution distributed via
  MCP `instructions` field + Claude skill module + agent-loop sysprompt
  prepend. (Novelty 8.5/10 — first cross-harness operating constitution.)

**Reflex — the autonomous response system (in progress)**

- ✅ Relay endpoints `/v1/triage` `/v1/bid` `/v1/claim` (codex)
- ✅ Race-test: exactly one /claim wins per @-mention (codex)
- ✅ docs/REFLEXD_EVENTS.md SSE flow doc (codex)
- ✅ register-agent puts new agents in PARENT tenant (codex `b4d4c1d`)
- ✅ `scripts/reflexd.py` — daemon with triage, bidding, headless spawn
- ✅ `quorus turnguard` — busy-file protocol so reflexd doesn't interrupt
  agents mid-tool-call
- ✅ `scripts/demo_reflex.sh` — local end-to-end demo (58ms e2e, no API spend)
- ⏳ **Fly deploy of feat/may4-sprint** — blocking the production e2e demo
- ⏳ **SSE wake_intent push from /v1/triage** — codex documented, may need
  implementation verification
- ⏳ **PR-C3** — cursor adapter hardening + contract tests per harness
- ⏳ **Bandit triage v2** — Dai et al. 2026 contextual bandit for skill
  matching (the actual ML moat)

---

## The Phased Plan (in priority order)

### Phase 1: Reflex Production-Ready (this session)

| Task                                              | Owner                      | Status       |
| ------------------------------------------------- | -------------------------- | ------------ |
| Fly deploy of `feat/may4-sprint`                  | codex (he has prod access) | TODO         |
| Verify `wake_intent` SSE actually broadcasts      | codex                      | TODO         |
| Cursor headless adapter robustness                | claude                     | TODO (PR-C3) |
| Contract tests per harness                        | claude                     | TODO (PR-C3) |
| `quorus reflexd start/stop/status` CLI subcommand | claude                     | TODO         |
| Auto-launch reflexd on `quorus init`              | claude                     | TODO         |

**Demo gate**: `@arav-claude help?` posted in TUI with all agent terminals
closed → reply lands in chat in <30s. Currently demo'd locally in 58ms.
Production blocked on deploy.

### Phase 2: Self-Assignment (next session)

Agents read the room, pick up open work without being told. Mechanism:

- **Tagged TODOs in chat** parse via QOD-mandated format: `TODO @ROLE: description`
- Reflex's triage already handles @-mentions; extend to `@open` (broadcast to all)
- Bandit speaker-selection from PR-R-FUTURE (codex lane)
- `/v1/work_queue` endpoint maintaining a priority list of unclaimed work

| Task                                                                              | Owner  |
| --------------------------------------------------------------------------------- | ------ |
| `/v1/work_queue` endpoint + claim TTL                                             | codex  |
| `quorus claim <id>` / `quorus release <id>` already exist; wire bandit to suggest | codex  |
| Reflex extension: bid on `@open` if skill_match > threshold                       | claude |
| QOD update: rule 7 = "scan work queue at start of every turn, claim if idle"      | claude |

### Phase 3: Self-PR & Self-Review

Agents open and review PRs autonomously. Code-review-elite skill exists; need
to wire it to chat events.

| Task                                                                           | Owner  |
| ------------------------------------------------------------------------------ | ------ |
| `quorus open-pr` command from chat: agent runs `gh pr create` after passing CI | claude |
| Auto-trigger code-review-elite subagent on PR-opened webhook event             | claude |
| Reflex extension: subscribe to GitHub PR webhooks → triage → bid → review      | shared |
| Cross-agent rotation: claude reviews codex's PRs and vice versa                | shared |

### Phase 4: Self-Deploy & Self-Monitor

Agents merge to main, watch CI, rollback on failure.

| Task                                                                          | Owner  |
| ----------------------------------------------------------------------------- | ------ |
| `quorus ship` command: merge + deploy + watch                                 | shared |
| Sentry integration: agent gets paged on production error → triages → opens PR | claude |
| GrowthBook feature-flag awareness: agent ramps + monitors metrics             | shared |

### Phase 5: True Autonomy

Agents handle the entire SDLC without human in the loop except for:

- Destructive actions (database drops, force-pushes, prod deploys)
- Strategic / product / pricing decisions
- O-1 visa / YC / fundraising materials

Everything else is automated.

---

## Architecture Summary

```
                               ┌─────────────────────────────────┐
                               │  Quorus Relay (Fly.io)          │
                               │  • SSE /events                  │
                               │  • /v1/triage  /v1/bid  /v1/claim│
                               │  • MCP tool audit hash-chain    │
                               │  • Tenant-isolated rooms        │
                               └────────────┬────────────────────┘
                                            │
        ┌───────────────┬───────────────────┼───────────────────┬───────────────┐
        │               │                   │                   │               │
   ┌────▼────┐     ┌────▼────┐         ┌────▼────┐         ┌────▼────┐    ┌────▼────┐
   │ arav    │     │ arav-   │         │ arav-   │         │ arav-   │    │ arav-   │
   │ (TUI)   │     │ claude  │         │ codex   │         │ gemini  │    │ cursor  │
   │ human   │     │ reflexd │         │ reflexd │         │ reflexd │    │ reflexd │
   └─────────┘     │ + claude│         │ + codex │         │ + gemini│    │ + cursor│
                   │ headless│         │ headless│         │ headless│    │ headless│
                   └─────────┘         └─────────┘         └─────────┘    └─────────┘
                        │                   │                   │              │
                   QOD constitution loaded into every system prompt; busy-file
                   coordinated via TurnGuard; bidding via /v1/bid; reply via MCP
```

## Why this beats prior art (novelty 8.5+)

- **HumanLayer / ccgate / AgentCore**: single-vendor, hook-only, no coordination
- **Devin in Slack**: single-vendor (Anthropic) cloud sandbox, no on-host
  execution, leaks code to vendor cloud
- **AutoGen / CrewAI / LangGraph**: same-process orchestration, can't span
  heterogeneous external CLI harnesses
- **Magentic / AG2 GroupChat**: speaker selection via LLM call (expensive,
  bottleneck) — Reflex uses local-bid auction (deterministic, free)
- **Anthropic Claude Code Sonnet-as-a-team**: built into the IDE, doesn't
  cross vendors, no speaker bidding

**Quorus's wedge**: cross-harness operating constitution + on-host execution

- tenant-isolated swarm + deterministic speaker bidding. Nothing else has all
  four.

## Risks / Three Ways This Fails

1. **Vendor API rate limits**: 4 agents bidding on every chat message can
   blow $X/hour at scale. Triage must drop 80%+ as IGNORE. Per-room-per-day
   budget cap. Backoff in bid function.
2. **Hook-surface drift**: each harness's hook events change independently.
   Maintenance burden = N×vendors×monthly. Mitigation: contract tests per
   adapter pinned to specific harness versions.
3. **"Agents talking to themselves" loops**: anti-self check in triage helps;
   need additional safeguards against agent-to-agent infinite reply chains.
   Mitigation: per-message reply-depth counter capped at 3.

## What "done" looks like

- Reflex deployed, demo'd in <30s without human keystrokes
- Two agents (claude + codex) shipping 5+ commits each per day with no human
  intervention (other than approval gates for destructive actions)
- 50% of YC application written by agents conversing in the room
- Live demo: arav posts a feature request, by the time he comes back from
  coffee the PR is open and reviewed

---

## Status update protocol (for the autonomous loop)

Every 3 minutes, the autonomous-loop poster:

1. Reads `~/.quorus/status/<participant>.json` for the latest progress
2. If state changed since last post, posts to `#quorus-may4-sprint` as the
   participant
3. If no state change, posts a heartbeat: `💓 idle — waiting on <blocker>`
4. Captures errors to `~/.quorus/runtime/poster.log` for crash debugging

Status format (any agent updates this):

```json
{
  "participant": "arav-claude",
  "timestamp": "2026-05-02T22:30:00Z",
  "state": "shipping" | "blocked" | "reviewing" | "idle",
  "current_task": "PR-C3 cursor adapter hardening",
  "blocker": null,
  "next_eta": "20 min"
}
```
