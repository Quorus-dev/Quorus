# YC S26 Application — Quorus (v8)

> paste-ready answers. branch `feat/may4-sprint`. HEAD `0dc9ce7`. last refreshed 2026-05-03.
> founder voice: arav + aarya. no marketing words. real numbers.

---

## company

**Company name:** Quorus

**URL:** https://quorus.dev

**Demo URL:** https://quorus.dev/demo (recorded asciinema cast of cross-vendor reflexd pipeline) and live cli demo: `pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git" && quorus init && quorus`

**Repo:** https://github.com/Quorus-dev/Quorus (apache-2.0 spec, MIT relay)

---

## describe what your company does in 50 characters or less

```
the agent-native operating system. apache-2.0.
```

(46 chars)

---

## describe what your company does

what unix did for processes — identity, memory, coordination, scheduling — we're doing for AI agents.

today every coding agent works in a silo. claude code on your laptop doesn't know what codex did 5 minutes ago. cursor can't ask gemini for a second opinion mid-refactor. every agent framework reinvents the same primitives badly. nothing speaks to anything else.

quorus gives agents 8 primitives, one wire format, apache-2.0:

1. coordination (cross-vendor rooms, QSP wire format) — LIVE
2. safety (durable, reversible, verifiable, replayable) — LIVE
3. memory (persistent KV + vector, capability-gated) — 30 days
4. discovery (capability advertisement + search) — 30 days
5. tool catalog (room-scoped MCP servers + legacy-wraps) — 30 days
6. identity (cryptographic agent-DID, portable cross-tenant) — 90 days
7. reputation (audit-derived, signed, verifiable) — 90 days
8. wallet (programmatic budgets, stripe + x402) — 120 days

we shipped primitives 1 and 2. we have 6 vendor harnesses verified live (claude code, codex cli, gemini cli, cursor, opencode, cline) — each replies as itself in a shared room using its own login, no new api keys. 1801+ tests passing. apache-2.0 spec at github.com/Quorus-dev/Quorus.

---

## describe in more detail what your company does

a quorus room is a chat room that ai agents can join. you @-mention an agent by name. on that agent's host machine, our daemon (reflexd) wakes the right vendor cli — `claude --print`, `codex exec --json`, `gemini --prompt`, `cursor-agent -p`, `opencode run`, `cline` — and the agent replies into the room as itself. all 6 of those harnesses are verified end-to-end today.

the wire format underneath is the quorus social protocol (QSP v1, apache-2.0). small set of social verbs — `claim`, `release`, `disagree`, `defer`, `vote`, `interrupt`, `say`. richer state machines compose from these. published spec: github.com/Quorus-dev/Quorus/blob/main/docs/QSP_V1.md.

once you have a real shared room with real shared identities, the rest of the OS becomes possible. memory the agents share. discovery so an agent can find another with the right capability. tool catalogs scoped per room (with legacy-wraps that turn any rest endpoint into an mcp tool). cryptographic identity that's portable across tenants. reputation derived from the audit ledger. wallets with programmatic budgets and stripe + x402 top-up. that's the 8-primitive roadmap above.

today we have 1801+ tests passing on the production branch, a hosted relay at `quorus-relay.fly.dev` with sub-250ms p95, cold-install CI across {macos, linux, windows} × {python 3.10-3.13}, and a working website at quorus.dev. about 10 people use it daily right now (us + early collaborators). that's not traction, that's a working substrate.

---

## where do you live now, and where would the company be based after YC?

arav: kingston, ontario (queen's university, eecs senior). will be in SF for batch + permanently after.

aarya: kingston, ontario. will be in SF for batch + permanently after.

post-batch base: SF. we're already targeting o-1 visa filing in q3 2026 with quorus traction as the basis.

---

## how long have the founders known each other and how did you meet?

3+ years. we met at queen's university in eecs. lived in the same residence in first year, paired up on engineering projects starting then, cofounded an earlier healthcare project (medport) together in 2025, and have been building together full-time since. we've shipped probably 30+ projects side by side at this point — hackathons, school projects, the medport canadian-emr work, and now quorus.

we've already gone through the cofounder dating, the cofounder fighting, the cofounder making-up. we know each other's failure modes. arav drives architecture + product, aarya drives velocity + adapters + frontend polish. we don't trip over each other.

---

## have any of the founders worked together before?

yes — 3+ years across hackathons, course projects, and the medport healthcare startup we cofounded in 2025. quorus is our 4th joint shipping project.

---

## have any of you been programming since high school?

both yes. arav since 13 (started with python + arduino, built robotics projects through highschool). aarya since 14 (web dev + competitive programming).

---

## tell us in one or two sentences something about each founder that shows the kind of person they are.

**arav.** lost the april 23 2026 yc hackathon because his app wouldn't open at demo time, then built a cold-install CI matrix the next week (macos+linux+windows × python 3.10-3.13) so it could never happen again. shipped quorus from rebrand to 1801+ green tests in 18 days.

**aarya.** the reason 6 vendor harnesses work, not 1. wrote argv builders for claude / codex / gemini / cursor / opencode / cline, ran each one against its real cli to catch the flag-name drift no doc page mentions, and turned `tier-A: 4 harnesses` into `tier-A: 6 harnesses` in one wave.

---

## why did you pick this idea to work on? do you have domain expertise in this area? how do you know people need what you're making?

we hit the problem ourselves. building medport, we had claude code on one laptop, codex in a terminal, gemini in another, and we were copy-pasting context between four windows manually. it cost us a yc hackathon — april 23 2026, our app wouldn't open at demo time because the agents weren't actually coordinating, they were each doing their own version.

we spent the next 18 days building the layer we wished existed, then realized everyone else has the same problem and the existing answers (crewai, autogen, langgraph) all force you to use their agent runtime — none of them work across vendors. that's the wedge.

domain: arav has 4 years of full-stack + distributed systems (built medport's fhir + emr stack), aarya has deep frontend + cli adapter experience. we both ship daily across 4+ different agent harnesses for our own work, so we hit every cross-vendor gap personally.

how we know people need it: every developer using more than one ai coding agent has this exact problem. cursor + claude code is now table stakes. add codex or gemini and you need a coordination layer. we're talking to ~25 dev-tool maintainers (crewai, openagents, autogen, smolagents) about adopting QSP v1 as their wire format — early conversations are positive because their users keep asking for cross-vendor support and they don't want to build it themselves.

---

## why now? what has changed recently to make this possible / necessary?

three things converged in the last 6 months:

1. **every major coding agent shipped a real headless cli.** claude `--print` (anthropic), `codex exec` (openai community fork), `gemini --prompt` (google), `cursor-agent -p` (cursor wave 7, april 2026), `opencode run` (opencode), `cline` (cline preview). 12 months ago only 2 of those existed. now 6 do, which means cross-vendor wake-up is mechanically possible — without relying on each vendor to ship their own protocol.

2. **MCP became a standard.** anthropic published it, openai adopted it, every major harness now speaks it. that gives us a stable per-agent boundary — quorus sits between mcp and the relay, not as a competitor to mcp.

3. **multi-agent is the assumed default.** in 2024 it was a research curiosity. by may 2026 every serious dev workflow uses 2-4 agents simultaneously. the coordination problem went from "interesting" to "blocking everyone every day."

we have an 18-month window before either anthropic or openai builds vendor-locked coordination into their own product. the right answer is an apache-2.0 cross-vendor protocol, written by an outside party, before either of them ships their version.

---

## why YC?

three reasons.

1. **YC has shipped the playbook for protocol-first companies.** stripe (api as product), supabase (open-core), retool (developer-first), cursor (yc s22, dev tools) — we've studied each. we want pg / garry / brad's pattern recognition on the version of this we're not seeing.

2. **distribution.** we're not building a closed product, we're building a wire format. crewai / autogen / openagents / langgraph adopting QSP v1 is ~200 lines of code each but ~10 emails of trust. YC alumni network turns those 10 emails into 1 intro per company.

3. **we're racing larger orgs.** anthropic and openai will eventually ship vendor-locked versions of this. the only way the cross-vendor open spec wins is to ship it loudly enough that the vendors find it cheaper to adopt than to fight. YC is the loudest stage on earth for that.

---

## what's your unfair advantage?

four parts.

1. **we already shipped it.** 6 vendor harnesses verified live, 1801+ tests, prod relay running. most teams pitching agent coordination have a roadmap. we have a binary you can `pipx install` right now.

2. **we are users of every harness.** arav has paid logins to claude / codex / gemini / cursor / opencode / cline and uses them daily. when cursor's `-p` flag changed in wave 7 we caught it the same week. you can't fake that — you have to live in 4 terminals at once.

3. **we burned the lesson the hard way.** april 23 2026 yc hackathon — lost because our app wouldn't open. next week we built cold-install CI that catches that exact failure mode across {3 OSes × 4 python versions}. that's the kind of scar tissue that makes the 19th hire-able person not us.

4. **the protocol is the moat, not the product.** apache-2.0 wire format means the more it's adopted, the harder it is to dislodge. once 5+ agent frameworks speak QSP v1, switching costs are network-wide. the relay is just one client.

---

## have you raised any money?

no. zero outside capital. self-funded by arav (savings + scholarship) and aarya. ~$2,400 in cumulative cloud + domain + ci spend across 12 months. asking YC for the standard $500k.

---

## anything else we should know?

we cofounded medport (canadian patient-owned health records) in 2025 and ran it through pre-seed conversations with healthcare investors. medport is alive but on background — the wedge into "coordination layer for AI agents" turned out to be the larger market and the technically harder substrate, so we pivoted full attention to quorus on 2026-04-15. medport runs as a cofounder-side-project that keeps testing quorus in a real production context (multi-agent FHIR ingestion swarms hit our relay daily).

we've open-sourced QSP v1 already (apache-2.0). if you want to reach maintainers we're talking to (crewai, autogen, smolagents, openagents) we'd love warm intros — that's the highest-leverage thing YC could give us between now and demo day.

---

## how did you hear about Y Combinator?

arav read paul graham essays starting at 16. has applied once before (medport, w26 batch, rejected after interview). the april 23 2026 yc hackathon is where quorus's first stress test failed (and where we built the cold-install CI in response). we've been on this track for 4+ years.

---

## founder video (1 minute) — script outline

> direct to camera, no script read-aloud, casual.

**[0:00-0:08] both on camera, no studio lighting, just laptop webcam.**
arav: "we're arav and aarya. we cofounded quorus."

**[0:08-0:25] arav talking, terminal visible behind him.**
arav: "every coding agent today works alone. you've got claude code on one screen, codex on another, cursor on the third. they don't talk to each other. you end up being the coordination layer yourself."

**[0:25-0:40] cut to terminal. live demo of `quorus` opening, 6 agents joining a room, a `@-mention` going across vendors, a reply streaming in.**
aarya voiceover: "this is one room. six agents. each one's a different vendor — claude, codex, gemini, cursor, opencode, cline. when you @-mention one, our daemon wakes that vendor's cli on the host machine, it replies as itself."

**[0:40-0:55] back to both on camera.**
arav: "we shipped 2 of 8 primitives. coordination and safety, both live, 1801 tests green. memory, discovery, tool catalog in 30 days. identity and reputation in 90. wallet in 120. apache-2.0 spec, 6 harnesses verified end-to-end."

**[0:55-1:00] arav.**
arav: "we want YC's help getting other agent frameworks to adopt the protocol. that's the win."

---

## ask

we'd like the standard $500k YC investment, batch s26, and warm intros to the 5 agent-framework maintainers most likely to adopt QSP v1 in q3 2026 (crewai, autogen, smolagents, openagents, langgraph).
