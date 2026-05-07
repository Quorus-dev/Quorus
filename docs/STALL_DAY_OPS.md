# Stall Day Ops — Hour-by-Hour Playbook

> Read this on the train to the venue. Don't read it AT the booth.
> Cross-reference: `docs/STALL_TALK_TRACK.md` (what to say), this doc (what to do).

---

## T-30 min — Setup at the venue

Open these terminals before any visitor arrives. Each gets its own iTerm2 window so you can switch with Cmd+Shift+arrow without losing position.

1. **Terminal A — TUI (the visible one).** `quorus chat stall-may7`. This is the window visitors will type into. Keep it full-screen, font size 18+ so people three feet back can read.
2. **Terminal B — agents.** Run `bash scripts/stall_demo_local.sh start --remote` (multi-laptop) or `--local` (solo). Verify all four daemons attach: claude, codex, gemini, opencode.
3. **Terminal C — relay tail.** `flyctl logs -a quorus-relay`. Side window, half-screen. Watch for 5xx, OOMs, restart loops.
4. **Terminal D — replay buffer.** `bash scripts/replay_kliment_cast.sh` paused at the start, ready to play if a queue forms and you need to entertain a waiting visitor.
5. **Browser tab 1 — website hero.** `https://quorus.dev` (or staging). Verify the cast plays in the hero. If it doesn't, ship a fix or take the cast off the page.
6. **Browser tab 2 — Sentry dashboard.** `https://sentry.io/organizations/<org>/issues/?project=quorus-relay`. New event = pause the demo, glance, decide.
7. **Browser tab 3 — GitHub repo.** Stars counter visible. Anyone who walks up after starring is a warm lead, not cold.
8. **Phone hotspot ON, password handy.** Conference wifi will fail. When it does, switch within 30 seconds.

Rehearse the 4-beat Kliment demo three times back-to-back. If beat 3 (resume-aarya) takes >15 seconds the second time, fall back to local mode early — don't fight it during a visitor's first impression.

QR code on the table: waitlist signup. Print 2 backups in case the first gets soaked / lost.

---

## T+0 to T+60 (7:00–8:00 PM) — Live stall

Default cadence: visitor walks up → you stop typing → 60-second pitch (`STALL_TALK_TRACK.md` Beats 1-5) → hand them the keyboard → they type one @-mention → you collect email → next visitor.

Between visitors, do this in order, every cycle:

- Glance at Terminal C (relay logs). Anything red? Investigate.
- Glance at Sentry tab. New issue? Click, read the title, decide if it's demo-blocking.
- Glance at the website tab. Cast still playing? Hero animation still OK?
- If a queue is forming, hit `bash scripts/replay_kliment_cast.sh` on a side laptop and let waiting visitors watch.

Hard rules during this hour:

- **Never debug at the booth in front of a visitor.** If something breaks, smile, say "we're not released yet — drop your email and I'll send when it's stable", hand them the QR. Fix between visitors.
- **Never type a destructive command into the live TUI.** Don't `git push --force`, don't drop tables, don't restart the relay. Anything that touches prod state waits until after the stall.
- **Capture every email manually if QR fails.** Pen and notepad backup.

---

## T+60 to T+24 hr — Post-stall

Within 2 hours of teardown:

- Drain the QR-collected emails into the waitlist DB. Tag with `source=stall-may7-<night>` for cohort analysis later.
- Post the launch tweet thread (`docs/LAUNCH_TWEET_v8.md`). Pin it.
- Push any cast you re-recorded at the venue (`git push` triggers Vercel rebuild).

Within 24 hours of stall end:

- Send the waitlist follow-up email to every signup. Subject: "Quorus early access — saw you at <venue>". One-line CTA: "reply with the agent you'd plug into a room first."
- Snapshot GitHub stars + repo visitors. Compare to pre-stall baseline.
- Snapshot Sentry new-issue count + Fly request count for the stall window. File any P1+ in the bug tracker.

---

## Failure modes

When something breaks at the booth, these are the only four playbooks you run. Memorize the trigger and the action.

### 1. Production relay 5xx-degrades

**Trigger:** Terminal C shows a wave of 5xx, or Beat 3 of a demo hangs >20s.
**Action:** Stop talking through the live demo. Say "we're going local — same code, just routing to my laptop instead of the cloud." Run `bash scripts/stall_demo_local.sh start --local`. Continue the pitch. Mention the failover honestly — agent-coordination veterans understand. Don't oversell.

### 2. Demo crashes mid-beat (TUI freezes, daemon stack-traces)

**Trigger:** keypresses don't echo in the TUI, or you see Python tracebacks in Terminal B.
**Action:** Cmd-Tab to Terminal D, hit space to play the recorded cast. Visitor sees the same flow they would have seen live. Quietly restart the failed daemon in Terminal B. Mention "this is why we're not GA yet."

### 3. Aarya's laptop disconnects from conference wifi (multi-laptop demos)

**Trigger:** Aarya's daemons stop appearing in `quorus rooms members stall-may7`, or her TUI shows "sse disconnected".
**Action:** Aarya switches her laptop to her phone hotspot (have the password pre-shared). Reconnects in ~20s. Meanwhile keep Beat 1 going on Arav's laptop only — visitor may not notice the second machine is offline.

### 4. Visitor's laptop fails the cold-install demo

**Trigger:** they want to install Quorus live ("can I `pipx install` it right now?") and it fails — wrong Python, missing deps, behind a corporate proxy.
**Action:** Hand them the printed waitlist QR + a one-card cheat sheet. Promise a follow-up DM with a tested install. Do NOT debug their laptop at the booth — burns 20 minutes of stall time and other visitors leave.

### Rootly / PostHog / "real eng" walks up

**Trigger:** they introduce themselves with a company name and say "I lead $TEAM at $COMPANY".
**Action:** Run the full 4-beat Kliment sequence (not the 60-second pitch). Offer the spec walkthrough — `docs/QUORUS_OS_SPEC.md` open on Terminal A or printed. Capture their direct email, not the QR. Ask one specific question: "what would you want from a coordination layer that you don't get from your current stack?" Their answer is worth more than 50 waitlist signups.
