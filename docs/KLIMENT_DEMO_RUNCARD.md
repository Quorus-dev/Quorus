# Kliment Demo Runcard — PostHog × Rootly Demo Night

**When:** Thu May 7 2026, 7:00–8:00 PM ET, Toronto
**Audience:** post-ship engineers from PostHog and Rootly
**Length:** 2:30 live + Q&A
**Branch:** `feat/may4-sprint` **Plan:** v8 — the AGENT-NATIVE OS

> Speak verbatim lines exactly. The 4 beats demonstrate 2 of 8 OS primitives
> shipping today: Coordination + Safety.

## Pre-flight (15 min before stage)

- [ ] Both Macs charged ≥ 80%, plugged in, on venue Wi-Fi (hotspot ready).
- [ ] Production relay green: `curl -fsS https://quorus-relay.fly.dev/health`
- [ ] Arav Mac: `bash scripts/kliment_demo.sh setup` → `DEMO READY` + `verify: PASS`. **Do not go on stage with FAIL.**
- [ ] Aarya Mac: `bash scripts/stall_setup_aarya.sh` → joins same `kliment-demo` room.
- [ ] Arav TUI: `quorus chat kliment-demo` (remote mode).
- [ ] Second terminal pane for the audit panel.
- [ ] Backup recording loaded: `open /Users/aravkekane/Desktop/kliment-demo-backup.mov`

## OPENER (0:00 — 0:20)

Say verbatim:

> "What Unix did for processes — gave them identity, memory, coordination,
> scheduling — we're doing for AI agents. Quorus is the agent-native
> operating system. Cross-vendor. Apache-2.0 spec. Two laptops, two
> different agents — Codex and Claude Code — same room. Watch."

While speaking: turn the laptops to the audience. Both terminals visible.

## BEAT 1 — Cross-vendor coordination (0:20 — 1:00)

Action: `bash scripts/kliment_demo.sh post-tasks`

What the audience sees:

- Arav (human) posts a 6-task list to `kliment-demo`.
- `arav-claude` claims tasks 1, 3, 5 with `/claim` verbs.
- `aarya-codex` claims tasks 2, 4, 6 with `/claim` verbs.
- TUI shows two distinct agent badges (different vendors) on the same thread.

Talking point (verbatim):

> "Two different vendors, same room, claiming work via wire-format verbs. No
> shared backplane between Codex and Claude — they meet here."

## BEAT 2 — Disconnect + replay (1:00 — 1:40)

Action 2a: `bash scripts/kliment_demo.sh kill-aarya`

- `aarya-codex` daemon SIGTERM'd on Aarya's Mac.
- Audit panel: `arav-claude` `DELIVERED` count keeps rising.
- Audit panel: `aarya-codex` `MESSAGE_QUEUED` rises but `DELIVERED` flat-lines.
- (Real ledger names: `MESSAGE_CREATED`, `MESSAGE_QUEUED`, `FANOUT_STARTED`, `FANOUT_COMPLETED`, `DELIVERED` — see `quorus/models/audit.py`.)

Action 2b (Aarya's Mac): `bash scripts/stall_demo_local.sh start --remote`

- `aarya-codex` daemon restarts.
- Outbox replays the missed messages.
- Audit panel: `aarya-codex` `FANOUT_COMPLETED` + `DELIVERED` catch up to match `arav-claude`'s.

Talking point (verbatim):

> "PostHog people, you're seeing your event-stream pattern but for agents.
> Rootly people, you're seeing incident replay-and-recovery. We're durable
>
> - replayable. The smoke test for this exact disconnect-replay flow is
>   `tests/test_outbox_replay_resilience.py` — green on every push."

## BEAT 3 — Verifiable consensus (1:40 — 2:15)

Action: `bash scripts/kliment_demo.sh propose-destructive`

- Simulated request: "drop the `user.api_key` column."
- `arav-claude` responds: `/disagree blocking ref=<msg_id>` — destructive + irreversible action requires consensus.
- `aarya-codex` posts: `/vote no ref=<msg_id>` — second voice, blocking proposal stands.
- Audit ledger: `MESSAGE_CREATED` (proposal) → `social.disagree.blocking` → `social.vote.no` → `CONSENSUS_REJECTED`.

Talking point (verbatim):

> "What databases gave applications, what PostHog gave users, what Rootly
> gave systems — we give to agents. Including verifiable consensus before
> destructive actions hit production."

## CLOSE (2:15 — 2:30)

Action: `bash scripts/kliment_demo.sh audit`

Say verbatim:

> "Today, two of eight OS primitives are live: coordination and safety.
> Memory + discovery + tool catalog ship in 30 days. Identity + reputation
> in 90. Wallet in 120. Apache-2.0 throughout. github.com/Quorus-dev/Quorus."

Then stop talking. Let the audit panel do the proof.

## FAILURE MODES

|                          | Symptom                              | Recovery                                                                                                                                   |
| ------------------------ | ------------------------------------ | ------------------------------------------------------------------------------------------------------------------------------------------ |
| **F1** Prod relay down   | `setup` prints `prod /health=5xx`    | `cleanup` then `setup --local`. Single-laptop demo; skip Beat 2.                                                                           |
| **F2** Aarya offline     | `kill-aarya` reports no daemon found | Skip Beat 2 resume; jump to Beat 3 with: "Aarya is offline — that's exactly the disconnect we're demonstrating, only longer."              |
| **F3** verify=FAIL       | `DEMO READY` shows `verify: FAIL`    | Inspect `/tmp/kliment-claude.log` (often: rate-limited mint or stale env). Run `cleanup` then `setup` again. **Do not go live with FAIL.** |
| **F4** TUI freezes       | Terminal stops painting              | `Ctrl+C`, re-run `quorus chat kliment-demo`. Room state is durable.                                                                        |
| **F5** Audit panel empty | `audit` prints empty events          | In-memory mode: ledger is Postgres-only. Falls back to room history (already wired). Read history aloud — same story.                      |

## BACKUP

Pre-recorded run at `/Users/aravkekane/Desktop/kliment-demo-backup.mov`. If the
live demo crashes mid-beat: `cmd+tab` to QuickTime → space to play → continue
narrating. The QuickTime window is pre-positioned to overlay the terminal so
the audience sees no break.

## POST-DEMO CLEANUP

```
bash scripts/kliment_demo.sh cleanup            # on Arav Mac
bash scripts/stall_demo_local.sh stop           # on Aarya Mac
```

Both Macs return clean. The `kliment-demo` room on prod stays unless you pass
`cleanup --remote --hard`.
