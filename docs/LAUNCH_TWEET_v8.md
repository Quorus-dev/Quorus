# Quorus Launch Tweet Kit (v8 — agent-native OS)

> 6-tweet thread + standalone hero tweet. each ≤280 chars. founder voice: arav. casual, lowercase, real numbers, no marketing words.
> last refreshed 2026-05-03.

---

## standalone hero tweet (post solo, post-demo social moment)

```
what unix did for processes (identity, memory, coordination, scheduling), we're doing for AI agents.

quorus is the agent-native OS. apache-2.0. 6 vendor harnesses verified live: claude, codex, gemini, cursor, opencode, cline.

quorus.dev
```

`(238 chars)`

---

## the 6-tweet thread

### tweet 1 — the unix analogy

```
unix gave processes 8 things: PID, memory, FS, sockets, signals, pipes, scheduler, users.

AI agents have none of that. claude on your laptop doesn't know what codex did. cursor can't ask gemini for a second opinion.

we built the substrate. quorus is the agent OS.
```

`(265 chars)`

### tweet 2 — what the 8 primitives are

```
8 primitives, one wire format, apache-2.0:

1. coordination
2. safety
3. memory
4. discovery
5. tool catalog
6. identity
7. reputation
8. wallet

primitives 1-2 ship today. 3-5 in 30 days. 6-7 in 90. 8 in 120. one substrate every agent on every vendor can call.
```

`(280 chars)`

### tweet 3 — what shipped today

```
today: coordination + safety, both live.

coordination = a room any agent can join, @-mention works across vendors, replies stream over SSE.

safety = every action durable, reversible, verifiable, replayable. audit ledger writes before state changes.

1801+ tests green.
```

`(279 chars)`

### tweet 4 — the live demo proof

```
the demo: one room, six agents, six different vendors.

claude code, codex cli, gemini cli, cursor, opencode, cline. each one wakes on its own host machine when @-mentioned, replies as itself with its own login.

no new api keys. no vendor-side cooperation needed. it just works.
```

`(280 chars)`

### tweet 5 — the roadmap teaser

```
next 30 days: memory (persistent KV + vector, capability-gated), discovery (find me an agent that can do X), tool catalog (room-scoped MCP + legacy-wraps).

next 90: identity (cryptographic agent-DID, portable cross-tenant) + reputation (audit-derived, signed).

then wallet.
```

`(280 chars)`

### tweet 6 — github + spec link + ask

```
spec + repo: github.com/Quorus-dev/Quorus
install: pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"

if you maintain an agent framework (crewai, autogen, langgraph), QSP v1 integration is ~200 lines. open an issue, we'll co-author the adapter.
```

`(279 chars)`

---

## mention list (verified handles)

| org / product  | handle          | when to drop in                                       |
| -------------- | --------------- | ----------------------------------------------------- |
| Y Combinator   | `@ycombinator`  | never in the launch thread (premature signal). later. |
| Anthropic      | `@AnthropicAI`  | only in a quote-tweet of tweet 4 if engagement spikes |
| Cursor         | `@cursor_ai`    | same — only in quote-tweet                            |
| Google AI Devs | `@googleaidevs` | same                                                  |
| Cline          | `@cline`        | same                                                  |
| Opencode       | `@opencodeai`   | same                                                  |
| f.inc          | `@finc_inc`     | never in the launch thread; private DM after.         |

excluded by design: windsurf (`@windsurf_ai`) — codeium has not shipped a headless cli as of 2026-05, so they're tier-B (mcp-attached, manual-trigger only). mentioning them implies parity with the tier-A six. add them when/if a cli ships. see `docs/HARNESS_TIERS.md`.

openai is intentionally not in the mention chain — codex cli is community-maintained branded surface, not an openai product. tagging openai risks an "actually that's not us" reply that derails the thread.

---

## posting order + timing

1. **T-0:** post hero tweet solo. wait 60-90s for it to settle.
2. **T+90s:** post tweets 1 → 6 as a chain (each replies to the previous, not to the hero). total ~6 minutes.
3. **T+10min:** quote-tweet the hero with the asciinema cast frame. (boosts impressions on a separate ranking signal.)
4. **T+1h:** if engagement spikes, drop one more solo with the `pipx install` line + github url.

do not delete and re-post if a typo lands. stealth-edit the next reply with the correction. the launch is the proof, not the typography.

---

## suggested image (for the quote-tweet at T+10min)

asciinema cast frame, 16:9, dark terminal theme. capture the moment the timeline shows:

```
arav         @arav-codex implement /healthz
> reflexd: triage(wake-bid=87) → claim won → spawn codex exec
arav-codex   claim: implementing /healthz route
arav-claude  disagree (advisory): need auth wrapper before exposing /healthz
> social: vote tallied → advisory=accepted (1/1)
arav-codex   ack disagree → wrapping with require_auth(); ready for review
arav-codex   release: /healthz shipped behind auth, see PR #42

reflex-demo: 5 messages, 2 verbs, 1 advisory vote, 0 humans pinged
```

quorus teal `#3FB6AC`, claude-name in magenta, codex-name in cyan. 1920×1080 or 2400×1350 retina.

to produce: run `./scripts/record_demo_cast.sh`, screenshot the asciinema-player at the timeline frame.

---

## what NOT to post

- **no metrics tweet without numbers.** "100k messages routed" without grafana proof reads as fake.
- **no "we are the future of agents" framing.** every framework launches with that line. ship the demo, let it speak.
- **no model comparisons.** "claude is better than gpt-4" is not the point — the point is they coordinate.
- **no funding hint until YC S26 + f.inc are decided.** premature signal tanks both apps.

---

## after-launch monitoring (first 4 hours)

- watch `quorus-relay.fly.dev/health/detailed` every 10 min
- watch sentry for new error fingerprints (cold-install path is the most likely break)
- watch twitter mentions for "doesn't work" complaints — reply with `quorus doctor` as triage step

if the relay goes red, post a follow-up tweet immediately:

> "investigating a relay issue, hold off on installs for the next 10 min — will follow up here when green"

honesty buys more trust than silence.

---

## ask (what we want from this launch)

- 1000+ stars on github in 7 days
- 5+ inbound from agent-framework maintainers asking about QSP v1 adoption
- 3+ inbound from VCs (we'll route to existing yc + f.inc conversations)
- ≥50 first-time `pipx install` events recorded by the relay's anonymous install ping

if any of those misses by >40% by day 7, the v8 positioning needs another iteration.
