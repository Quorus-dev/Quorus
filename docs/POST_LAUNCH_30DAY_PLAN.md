# Quorus — Post-launch 30-day plan

> Month-1 priorities, ranked by leverage. Each item has an owner, a doneness criterion, and a kill-switch (when to drop it if it stalls).
> The prevailing tempo is speed + novelty + revenue. Anything that doesn't move one of those forward goes to the bottom.

---

## Priority order (top = ship first)

| #   | Item                                           | Owner | Doneness                                                                       | Drop-dead |
| --- | ---------------------------------------------- | ----- | ------------------------------------------------------------------------------ | --------- |
| 1   | YC S26 application drop-dead                   | Arav  | Application submitted. Demo cast attached.                                     | T+7d      |
| 2   | Real-harness CI for Cursor / Opencode / Cline  | Arav  | `tests/test_reflexd_realharness.py` runs all 6 in CI nightly                   | T+10d     |
| 3   | Sentry rule tuning + alert hookup              | Arav  | `/health` failure -> Slack within 60s; spam-error rules muted                  | T+5d      |
| 4   | Upstash plan upgrade trigger                   | Arav  | Auto-alert when 80% of free-tier connections sustained for 1h                  | T+12d     |
| 5   | 7th harness research                           | Arav  | Disposition memo committed under `docs/HARNESS_TIERS.md`                       | T+20d     |
| 6   | Bandit-learned speaker selection               | Arav  | `/v1/bid` switches from heuristic to UCB1; A/B harness on internal rooms       | T+25d     |
| 7   | QSP v1 outreach: cold-DM framework maintainers | Arav  | 8 DMs sent (CrewAI, AutoGen, OpenAgents, TAP, LangGraph, Letta, Smol, Phidata) | T+18d     |

---

## 1 - YC S26 application drop-dead `T+7d`

**Why first:** The application window doesn't move. Everything else can slip a week; this can't.

**Plan:**

- Use the launch demo cast (`website/public/casts/demo_reflex.cast`) as the 60-second video.
- One-line: "Cross-vendor coordination layer for AI coding agents. 6 of 7 harnesses fully proactive. Apache-2.0 spec."
- Founders: Arav, Saad, Aarya. Ownership / equity TBD before submit.
- Traction: GitHub stars + install count from the launch tweet. Pull from `quorus.dev/usage` if instrumented; else from GitHub Insights.
- Risk to call out honestly: "we are pre-revenue; the route to revenue is enterprise tenancy + usage-based pricing on the relay; spec stays free."

**Kill-switch:** none — must submit.

---

## 2 - Real-harness CI for Cursor / Opencode / Cline `T+10d`

**Why:** Today these three pass argv-shape tests but no CI exercises live OAuth. The launch tweet implies tier-A across all six; we need to keep that claim true under real load.

**Plan:**

1. Add `tests/test_reflexd_realharness.py` with one parametrize per harness:
   - Setup: `pipx install <harness>` in the runner.
   - Auth: a CI-only test account with refresh token stored in GitHub Secrets.
   - Action: spawn reflexd, post `@<role>-<vendor> ping`, assert reply within 30s.
2. Wire into `.github/workflows/cold-install.yml` as a separate job (so a vendor outage doesn't fail the install matrix).
3. Run nightly at 09:00 UTC (after cold-install at 08:00 UTC).
4. Alert on 2 consecutive nightly failures (catches token expiry without spamming on transient blips).

**Kill-switch:** if a vendor refuses CI access tokens (likely Cursor + Cline), fall back to a "weekly manual checklist" until they ship official CI affordances. Update `docs/HARNESS_TIERS.md` with the limitation.

---

## 3 - Sentry rule tuning + alert hookup `T+5d`

**Why:** Right now every error reaches Sentry and nothing routes anywhere. First public incident -> we won't know until a user tweets at us.

**Plan:**

- Mute known-noisy fingerprints (httpx `ReadTimeout` on Codex CLI -> downgrade to `info`).
- Add a "first-seen in last 24h" rule -> Slack `#quorus-alerts`.
- Add a "rate > 10/min for 5 min" rule -> SMS to Arav.
- Wire `/health` failure -> Slack + email via Fly alerts (`docs/LAUNCH_READINESS.md` 10.3).
- Document the alert hierarchy in `docs/runbooks/alerts.md`.

**Kill-switch:** if Sentry quota blows up, switch to self-hosted GlitchTip on the same Fly machine for 30 days.

---

## 4 - Upstash plan upgrade trigger `T+12d`

**Why:** Free tier is 10k commands/day + 30 connections. Reflexd holds one connection per agent + rate limiter does ~5 commands per write. At ~50 active agents we'll burn the budget mid-day.

**Plan:**

- Threshold: upgrade to Pay-as-you-go ($0.20 per 100k commands) when sustained command rate exceeds 5k/day for 7 consecutive days.
- Alternative: jump to $10/mo Pro tier for predictable cost (1M commands/mo + 1000 connections).
- Auto-alert: cron job in CI that scrapes Upstash REST API daily, posts to `#quorus-alerts` if threshold crossed.
- Decision rule: if rate is steady -> Pro tier ($10/mo). If spiky -> Pay-as-you-go.

**$X/month threshold:** $25/mo is the level above which we re-evaluate moving to self-hosted Redis on the Fly machine (one redis-stack container -> $0 marginal). Below $25/mo, Upstash latency + ops savings win.

**Kill-switch:** if Upstash itself becomes unreliable, swap to Fly Redis (one terraform-style migration; the rate-limiter abstraction is already pluggable).

---

## 5 - 7th harness research `T+20d`

**Why:** Six tier-A harnesses is a clean number for marketing. Seven would let us claim "every CLI agent on the market." But we picked six because the research showed only six exist as of 2026-05-03.

**Candidates to revisit (in order):**

1. **Windsurf via the Cline-extension model.** The hypothesis: Cline ships as a VS Code extension AND a CLI. Windsurf is "VS Code with Cascade." If Cline-the-extension can run inside Windsurf, then Windsurf becomes tier-A _transitively_ — reflexd wakes Cline, Cline posts as the user, the work happens in Windsurf. This is a 1-day spike: install Cline as a Windsurf extension, see if the inter-process surface is the same. Cited evidence so far: `docs/HARNESS_TIERS.md` says Windsurf is IDE-only. Re-research after T+15d.
2. **Aider.** `aider` ships a `--message` flag that runs one shot non-interactively. Already a "headless mode" of sorts. Research: does it support OAuth, does it work without a TTY, can reflexd argv-pin it.
3. **Continue.dev CLI.** Continue is a VS Code extension but they've hinted at a CLI. Watch their changelog.
4. **Sourcegraph Cody CLI.** Cody has an SDK; a CLI binary may be one community-built `npm install` away.

**Doneness criterion:** Ship a research memo to `docs/HARNESS_TIERS.md` (one section per candidate, with cited URLs and disposition). If any moves to tier-A, add an argv builder + suffix in the same PR.

**Kill-switch:** If after 20 days nothing shippable, accept "six is the floor for now" and shift the time budget to bandit-learning (item 6).

---

## 6 - Bandit-learned speaker selection `T+25d`

**Why:** This is the next 9+/10 novelty lift. Today the speaker (i.e., which agent gets woken on a generic `@open` tag) is decided by a simple heuristic: capability tags + recent activity. A bandit replaces that with adaptive routing — the more often `@open implement /healthz` resolves cleanly when codex picks it up, the higher codex's bid for similar future tasks.

**Reference:** Dai et al., "Bandit-learned speaker selection in multi-agent dialogue," arXiv 2501.01849 (2026). The paper formulates speaker selection as a contextual bandit with a thompson-sampling prior, and shows ~28% latency drop and ~14% quality lift vs. round-robin on a cross-LLM coding benchmark.

**Plan:**

1. Add a `BanditService` in `quorus/services/bandit_svc.py`.
   - State: per-(tenant, capability-tag) UCB1 estimates with exponential decay (half-life 7d).
   - Persist in Postgres (`bandit_state` table, indexed on tenant + tag).
2. Modify `/v1/bid` to query `BanditService` for the marginal score; final bid = base_heuristic _ 0.6 + bandit_score _ 0.4 (60/40 to keep heuristic as a sanity floor).
3. After each task is "released" (verb=release on the same target the agent claimed), call `BanditService.observe(reward)` where reward = 1 if release happened within target SLA, 0 otherwise.
4. Roll out behind a tenant-level feature flag (`enable_bandit_speaker_select`). Internal rooms first; A/B against heuristic for 7 days; promote if quality holds.

**Doneness:** Bandit code merged behind flag; one internal tenant on the experimental path; metrics show no regression on `release_within_sla` rate.

**Kill-switch:** if A/B shows the bandit underperforms heuristic on real data, leave it shipped but disabled by default. Even a negative result is a paper.

---

## 7 - QSP v1 outreach: cold-DM framework maintainers `T+18d`

**Why:** Quorus's moat is the spec, not the relay. If CrewAI / AutoGen / OpenAgents adopt QSP v1, they get cross-framework coordination for free, and we get distribution. The relay can stay free as long as the spec spreads.

**Plan:**

8 cold DMs (one per maintainer). One paragraph each. Same template:

> Hey [name], saw your work on [project]. We just shipped Quorus — Apache-2.0 wire spec for agent rooms (claims, advisories, votes, social verbs). Six tier-A harnesses live. Looking for a framework partner to be the first non-Quorus QSP-v1 adopter. Spec is ~200 lines: github.com/Quorus-dev/Quorus/blob/main/docs/SOCIAL_PROTOCOL_v1.md. If you want to chat, I'll send a 15-min loom. — Arav

Targets:

1. **CrewAI** (joaomdmoura) — they have a coordination story but it's framework-internal.
2. **AutoGen** (microsoft/autogen) — formal coordination is the gap.
3. **OpenAgents** (xlang-ai) — explicit cross-framework focus, natural fit.
4. **TAP / Tetra** (smithery-ai) — tool routing primitives overlap with QSP claims.
5. **LangGraph** (langchain-ai) — they own the coordination DAG; QSP could be a transport.
6. **Letta** (letta-ai) — agent memory + identity, complementary to room state.
7. **Smol Agent** (huggingface) — distribution is huge.
8. **Phidata** (phidata) — multi-agent teams as a product, QSP is the wire.

**Doneness:** 8 DMs sent. 1 reply scheduled. (Reply rate on cold DMs to maintainers is ~25% with a personalized hook; 8 -> ~2 replies is the realistic median.)

**Kill-switch:** If after T+18d zero replies, that's a signal the spec needs sharper positioning before more outreach. Pause and rework `docs/SOCIAL_PROTOCOL_v1.md` intro paragraph instead of sending more DMs.

---

## What's NOT on this plan (and why)

- **Pricing page.** We don't have it because we don't have paid tiers yet. Premature pricing signals lock us into a number we'll regret in 90 days.
- **Self-hosted relay docs.** Real users will install the relay in Fly's free tier or Render before they hit a need to self-host. Write the docs the day someone asks.
- **Mobile / iOS app.** No coding agent ships a real headless mobile mode. This is `T+90d` at the earliest.
- **Web dashboard polish.** Already at `quorus.dev/dashboard`. Acceptable for launch. Re-revisit when paying users complain.
- **Public benchmark.** Tempting but expensive. Bandit-learning (item 6) generates the data for free; benchmark falls out of that.

---

## Weekly checkpoint cadence

Every Sunday 18:00 PT, walk through this doc top-to-bottom. For each item:

- **Status:** on-track / at-risk / done / dropped
- **Blocker:** what's preventing the next 25% of progress
- **Decision:** what to do this week to unblock

Update the table. Commit with `docs: 30-day plan checkpoint w<n>`.

If two consecutive weeks show "at-risk" on the same item without a clear blocker, drop it and reallocate the time. Sunk-cost is the enemy of speed.

---

## Drop-dead summary

| Date  | What must be done                              |
| ----- | ---------------------------------------------- |
| T+5d  | Sentry alerts wired                            |
| T+7d  | YC S26 submitted                               |
| T+10d | Real-harness CI nightly green                  |
| T+12d | Upstash trigger alert in production            |
| T+18d | 8 cold DMs sent                                |
| T+20d | 7th-harness disposition memo committed         |
| T+25d | Bandit speaker-selection behind flag, A/B live |

If T+30d arrives and any line is unticked: the trade-off was Wrong. Re-rank.
