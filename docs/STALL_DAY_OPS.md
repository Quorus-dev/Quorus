# Stall Day Ops — Hour-by-Hour Playbook

> Read this on the train to the venue. Don't read it AT the booth.
> Companion to `docs/STALL_TALK_TRACK.md` (what to say); this is what to do.

---

## T-30 min — Setup at the venue

Six terminals + three browser tabs, all open before any visitor arrives.

1. **TUI (visible)** — `quorus chat stall-may7`. Full-screen, font 18+ so people three feet back can read.
2. **Agents** — `bash scripts/stall_demo_local.sh start --remote` (multi-laptop) or `--local` (solo). Verify all four daemons attach: claude, codex, gemini, opencode.
3. **Relay tail** — `flyctl logs -a quorus-relay`. Side window. Watch for 5xx, OOMs, restarts.
4. **Replay buffer** — `bash scripts/replay_kliment_cast.sh` paused, ready to entertain a waiting visitor.
5. **Browser tab — website** — verify the cast plays in the hero. If broken, ship a fix or take the cast off the page.
6. **Browser tab — Sentry** — `sentry.io/.../issues/?project=quorus-relay`. New event = pause demo, glance, decide.
7. **Browser tab — GitHub** — repo stars visible. Anyone arriving after starring is warm, not cold.
8. **Phone hotspot ON, password handy.** Conference wifi will fail. Switch within 30 seconds when it does.

Rehearse the 4-beat Kliment demo three times back-to-back. If beat 3 (resume-aarya) takes >15s the second time, fall back to local mode early — don't fight it during a visitor's first impression.

QR code on the table, two printed backups in case the first gets soaked.

---

## T+0 to T+60 (7-8pm) — Live stall

Default cadence: visitor walks up → stop typing → 60s pitch (`STALL_TALK_TRACK.md` Beats 1-5) → hand them keyboard → they type one @-mention → collect email → next visitor.

Between visitors, every cycle, in order: glance at relay logs, glance at Sentry, glance at the website hero, then if a queue is forming start the replay cast on a side laptop for waiting visitors.

Hard rules:

- **Never debug at the booth in front of a visitor.** Smile, say "we're not released yet — drop your email and I'll send when it's stable", hand them the QR. Fix between visitors.
- **Never run a destructive command in the live TUI.** No force-pushes, no table drops, no relay restarts. Wait until after teardown.
- **Pen + notepad backup if the QR fails.** Capture every email manually.

---

## T+60 to T+24h — Post-stall

Within 2 hours of teardown: drain QR-collected emails into the waitlist DB tagged `source=stall-may7-<night>`; post the launch tweet (`docs/LAUNCH_TWEET_v8.md`) and pin it; push any cast you re-recorded at the venue.

Within 24 hours of stall end: send the waitlist follow-up to every signup ("Quorus early access — saw you at <venue>", one-line CTA: "reply with the agent you'd plug into a room first"); snapshot GitHub stars and repo visitors against pre-stall baseline; snapshot Sentry new-issue count and Fly request count for the stall window, file any P1+.

---

## Failure modes

Memorize four triggers and four actions. Run only these playbooks.

### 1. Production relay 5xx-degrades

**Trigger:** wave of 5xx in the relay tail, or Beat 3 hangs >20s.
**Action:** stop the live demo. Say "we're going local — same code, just routing to my laptop instead of the cloud." Run `bash scripts/stall_demo_local.sh start --local`. Mention the failover honestly — agent-coordination veterans understand. Don't oversell.

### 2. Demo crashes mid-beat (TUI freezes, daemon stack-traces)

**Trigger:** keypresses don't echo, or Python tracebacks in the agents terminal.
**Action:** Cmd-Tab to the replay buffer, hit space — visitor sees the same flow. Quietly restart the failed daemon. "This is why we're not GA yet."

### 3. Aarya's laptop drops conference wifi (multi-laptop demos)

**Trigger:** Aarya's daemons stop appearing in `quorus rooms members stall-may7`, or her TUI shows "sse disconnected".
**Action:** Aarya switches to her phone hotspot (password pre-shared). Reconnects in ~20s. Meanwhile keep Beat 1 going on Arav's laptop only — visitor may not notice.

### 4. Visitor's laptop fails the cold-install demo

**Trigger:** they want to `pipx install` Quorus live and it fails — wrong Python, missing deps, corporate proxy.
**Action:** hand them the printed waitlist QR + a one-card cheat sheet. Promise a follow-up DM with a tested install. Do NOT debug their laptop at the booth — burns 20 minutes and other visitors leave.

### Bonus: Rootly / PostHog / "real eng" walks up

**Trigger:** they introduce themselves with a company name and team.
**Action:** run the full 4-beat Kliment sequence (not the 60s pitch). Offer the spec walkthrough — `docs/QUORUS_OS_SPEC.md` open or printed. Capture their direct email, not the QR. Ask: "what would you want from a coordination layer that you don't get from your current stack?" Their answer is worth more than 50 waitlist signups.
