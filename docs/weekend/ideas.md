# Weekend ideas

Each idea: the pain it fixes, prior art, an honest novelty score (1-10) and a
cost (S = under a day, M = 1-3 days, L = a week or more). Prior art comes from
what the lead knows as of 2026-10; web search was blocked for this pass, so
check the named products before quoting them anywhere public.

## 1. Task failover when an agent's vendor goes down

**Pain.** Seen live on 2026-10-17: `@arav-gemini` hit "account is out of
quota" mid-weekend. Its claimed work either stalls silently or, worse, comes
back thin. Today a human has to notice, release the claim and reassign it.

**Idea.** When the daemon sees a quota, auth or outage failure from a harness
(it already classifies crashes since 33ad026), it marks that agent `degraded`
in presence, releases the agent's claims with a handoff note (task, branch,
last commit, what was tried) and posts a `/defer` naming the best idle agent
of a different vendor. The lead or a human can veto with one reply.

**Prior art.** Request-level model fallback is common: LiteLLM, OpenRouter
and most gateways retry a call on another model. claude-squad and Vibe Kanban
run several agent CLIs side by side, but a human picks which one does a task.
Nobody I know of moves a claimed task, with its branch and context, between
agent harnesses of different vendors.

**Novelty: 6/10.** Fallback is old; task-level, cross-harness handoff with
claims and a branch is the new part.

**Cost: M.** Classification exists; we need a `degraded` presence state, a
claim-release-with-note path and a picker. Risk: a flapping vendor causes
claim ping-pong, so we need a cooldown.

## 2. Read receipts with outcome: seen, working, chose silence, failed

**Pain.** BRIEF priority 2: a user can't tell whether a silent agent is
thinking, chose not to reply, or is dead. `busy` presence covers "working"
(`scripts/reflexd.py:2116`), 33ad026 covers "failed", but NO_REPLY is still
invisible (`scripts/reflexd.py:2263` logs it and posts nothing).

**Idea.** One small receipt event per (message, agent): `seen`, then
`working`, then one of `replied`, `silent`, `failed`. The TUI renders it as
a dim line under the message (`codex ✓ seen · silent`). No chat noise: it
is a receipt, not a message, so it never wakes anyone.

**Prior art.** Read receipts and typing indicators (WhatsApp, Slack, iMessage).
MCP Agent Mail has acknowledgement-required messages. Agent dashboards
(Vibe Kanban, claude-squad) show per-task or per-session status, not
per-message outcomes in a shared chat.

**Novelty: 3/10.** It is a well-known pattern applied to agents. Build it
anyway: it is cheap and fixes the confusion in the brief.

**Cost: S.** A relay event type, three daemon emit points and a TUI render.

## 3. Grounding gate for research and claims

**Pain.** Seen live on 2026-10-17: seven research teardowns (f19164a) landed
with no sources, and some claims don't match the tools they describe. A
fluent, wrong document is worse than none, because the reviewer has to
disprove every line.

**Idea.** Docs under `docs/**/research/` must carry a link per factual
claim. A cheap check (a test, like the README tests) rejects unsourced
claims, and the room routes each research commit to an agent of a different
vendor, which spot-checks three random claims against their links and
posts `/disagree blocking` on any miss.

**Prior art.** Perplexity and every deep-research product cite sources.
Cross-model review exists (CodeRabbit, LLM-council style setups,
claude_codex_bridge pairing Claude with Codex). Nobody I know of runs a
commit-time citation gate with cross-vendor spot checks inside an agent team.

**Novelty: 5/10.** Each part exists; the gate in the commit loop is new.

**Cost: S** for the citation test, **M** for automatic cross-vendor routing.

## Lead's pick

Build #2 first: it is the brief's own UX example and is about a day of work.
Then #1: it carries the most novelty and fixes a failure we hit on day one.
#3's citation test is a cheap add-on to whoever redoes the teardowns.
