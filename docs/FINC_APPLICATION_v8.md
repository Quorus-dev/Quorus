# f.inc Application — Quorus (v8)

> paste-ready. last refreshed 2026-05-03. addressed to furqan + the f.inc team.
> founder voice: arav + aarya. lowercase, real numbers, no marketing words.

---

## company name

Quorus (formerly murmur, formerly the cross-vendor coordination layer we built after losing the april 23 2026 yc hackathon. company history: medbuddy → medport → quorus.)

---

## one-liner

the agent-native operating system. apache-2.0. 6 vendor harnesses verified live.

---

## coolest thing you've built

what unix did for processes — identity, memory, coordination, scheduling — we're doing for AI agents.

specifically: a quorus room is a chat room ai agents from different vendors can join. you @-mention an agent by name. on that agent's host machine, our daemon (reflexd) wakes the right vendor cli — `claude --print`, `codex exec`, `gemini --prompt`, `cursor-agent -p`, `opencode run`, `cline` — and the agent replies into the room as itself, using its own login, its own context window, no new api keys. all 6 of those harnesses are verified end-to-end today, with 1801+ tests passing.

underneath it's the quorus social protocol (QSP v1), apache-2.0, with social verbs (claim, release, disagree, defer, vote, interrupt). the relay is one client of the protocol, not the protocol itself. that's the moat — if other agent frameworks adopt the wire format, rooms become interoperable across the whole agent ecosystem.

8-primitive roadmap: coordination + safety live now. memory + discovery + tool catalog in 30 days. identity + reputation in 90. wallet (stripe + x402) in 120.

---

## other ideas you considered

1. **medport** — patient-owned canadian emr, built 2025, raised pre-seed conversations with healthcare investors, paused full-time work 2026-04-15 to focus on quorus. medport still runs as a real production user of quorus's coordination layer (multi-agent FHIR ingestion swarms).

2. **medbuddy** — earlier consumer iteration of the medport idea. shipped, learned, killed.

3. **a healthcare-vertical multi-agent framework.** considered building only the medical version of quorus. rejected — the substrate is the larger market and the harder technical problem.

4. **a closed-source coordination relay.** considered for ~3 days. rejected — the only way the protocol wins is open. apache-2.0 was a strategic decision, not an aesthetic one.

5. **a vscode extension.** rejected for the same reason — the win is being the wire format that runs across every harness, not being one ide's plugin.

---

## most impressive traction

- **1801+ tests passing** on the production branch (`feat/may4-sprint`). cold-install CI matrix across {macos, ubuntu, windows} × {python 3.10, 3.11, 3.12, 3.13} runs nightly + on every PR. caught the same failure mode that lost us the april 23 hackathon (`pytest` green but `pipx install` produced a binary that wouldn't open).

- **6 vendor harnesses verified end-to-end live** — claude code, codex cli, gemini cli, cursor, opencode, cline. each one reflexd-wake-able with the documented headless flag. wave-7 added opencode + cline (real cli, not a wrapper).

- **production relay live** at `quorus-relay.fly.dev`, sub-250ms p95, slo-tracked, single-machine fly deploy with autoscaling off (capacity is by design).

- **18 days from rebrand to ship.** 2026-04-15 we renamed murmur → quorus, fixed 4 critical security issues, rebuilt the cli, shipped a website (quorus.dev, lighthouse 100% seo / 93% a11y). 18 days later we have the daemon, 6 harnesses, 1801 tests, and an apache-2.0 spec. that velocity is the most impressive number we have.

- **medport runs on top of us in production.** real fhir ingestion swarms hit the relay daily. dogfood is not a slide, it's a workload.

- **0 external capital.** ~$2,400 cumulative cloud + domain spend across 12 months.

---

## demo

- live cli demo: `pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git" && quorus init && quorus`
- recorded asciinema cast: https://quorus.dev/demo
- 60-second screen recording at https://quorus.dev/demo (cross-vendor @-mention pipeline)
- repo: https://github.com/Quorus-dev/Quorus
- spec: https://github.com/Quorus-dev/Quorus/blob/main/docs/QUORUS_OS_SPEC.md

happy to do a live screen-share whenever — pick any 15-min window in the next 5 days, we'll demo on whichever 4-of-6 harnesses you want to see talk to each other. also happy to demo the failure modes (we have a recording of the april 23 hackathon disaster too — useful context for understanding how we built the cold-install CI moat).

---

## what convinced you to apply to f.inc

three things, in order.

1. **furqan's nebula thesis matches the substrate-not-product framing.** the public pieces on nebula keep returning to "infrastructure that other people build on top of, where the network value compounds." quorus is exactly that — a wire format other agent frameworks adopt, not a destination product. f.inc's pattern recognition on infra-with-network-effects is the bet we want made on us, not generic seed capital.

2. **sync labs precedent.** sync was a small team that shipped a real demo, kept the api boring, and let the wedge speak for itself. we read every post-mortem and the takeaway we drew is: f.inc backs teams that ship before they pitch. we shipped first (1801 tests, 6 harnesses) and we're pitching second.

3. **timing.** the agent-coordination problem went from "interesting" to "blocking everyone every day" in the last 6 months. we have an 18-month window before anthropic or openai bolts a vendor-locked version into their own product. the right answer is an apache-2.0 cross-vendor protocol shipped loudly enough that the vendors find it cheaper to adopt than to fight. f.inc is the right cap-table partner for that fight because of how nebula plays the protocol-vs-platform game.

---

## how did you hear about f.inc?

following furqan's writing for ~2 years (the early crypto-infra essays, then the recent agent-infra ones). saw the sync labs deal. quorus is the first thing we've built where we genuinely think the substrate-as-network thesis applies, so we're applying.

---

## what are you raising and how much

raising a $1.5M pre-seed at standard SAFE terms. lead check welcome. we have ~$2,400 in lifetime spend so far so capital efficiency is not a story, it's a fact.

primary use of funds:

- 2 senior eng hires in SF (q3 2026), $360k loaded each → $720k year 1
- $200k 18 months of compute + relay infra at projected scale (10x current)
- $180k legal + soc2 type 2 (required to land first 3 enterprise design partners)
- $400k runway buffer

---

## ask

we'd like furqan's read on whether nebula leads or co-leads the round, and (regardless of check) the 5 most-likely cross-pollination intros from the f.inc portfolio — specifically anyone building agent-runtime infra that should be speaking QSP v1 instead of inventing their own.
