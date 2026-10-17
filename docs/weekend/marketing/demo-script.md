# 60-second demo script (draft)

> Status: **draft only** — a recording plan, not a recorded asset. Nothing has
> been published.
>
> Built on today's real flow in this room: a human @-mentions an agent, the
> agent shows up as *working*, replies, and Quorus publishes its commit for it.
> Four beats — **mention → presence → reply → publish** — in one minute, no
> editing tricks.

## What actually happened today (the thing we're re-enacting)

In `weekend-quorus` this weekend the loop ran end to end, repeatedly:

- a human posted a task and @-mentioned one agent;
- the agent appeared as busy and then posted its result;
- Quorus published the agent's commit for it — the room printed
  `(quorus) published 1 commit(s) from quorus/<agent> to <branch>` — and a
  second model reviewed it.

That last line is the whole pitch: **nobody copy-pasted, and the commit landed.**

## Setup (before you hit record)

- A clean machine or fresh shell where `quorus` is installed.
- Each vendor CLI installed **and already logged in** (Claude Code, Codex,
  opencode). Pre-warm them once so the first wake isn't a login prompt on camera.
- A tiny throwaway repo with one obvious task left undone — e.g. a service with
  no `/healthz` route, and a test file ready for it. Keep it trivial: the demo
  is about coordination, not the task.
- Terminal at a large font, 16:9, dark theme. Close tabs, hide the dock.
- A stopwatch visible off-camera. If a wake is slow, cut the gap in the edit —
  never fake it, just trim the wait.

## Storyboard

| Time | On screen | Narration |
| --- | --- | --- |
| 0:00–0:06 | Three terminal windows, each mid-session, a diff pasted between two of them. | "Three agents, three terminals. Right now *I'm* the message bus." |
| 0:06–0:14 | One clean terminal: `quorus`. First run picks a name, starts a local relay, opens `#general`. | "So they get one room instead. `quorus` — pick a name, it runs a relay on this machine and drops you in." |
| 0:14–0:24 | `quorus agent add claude --room general --repo ~/code/my-app --mode autonomous --lead` then the same for `codex`. | "Put two agents in the room, pointed at the repo. Claude is the lead; Codex backs it up." |
| 0:24–0:32 | `quorus chat general`. Type and send: `@you-claude add a /healthz route with a test`. | "Then I just… ask." |
| 0:32–0:42 | The chat shows `⋯ @you-claude working 0m 12s`. Nothing else for a beat. | "Watch the own-line: *working*. A busy agent never looks like a dead one — that's the difference between waiting and wondering." |
| 0:42–0:50 | The agent's reply lands in the room; Codex posts a short review note. | "It built it in its own git worktree, then asked Codex to review it. Two models, one task, zero copy-paste from me." |
| 0:50–0:58 | `quorus` hub (or the repo) showing the published commit on the branch. | "And it published the commit for me." |
| 0:58–1:00 | Back to the single terminal. | "Quorus. Your agents have a group chat — you're the boss." |

## Exact beat for beat

```bash
# beat 1 — the room
quorus
# pick a name → local relay → #general opens

# beat 2 — the team
quorus agent add claude --room general --repo ~/code/my-app --mode autonomous --lead
quorus agent add codex  --room general --repo ~/code/my-app --mode autonomous

# beat 3 — the mention
quorus chat general
# in the room, type:
#   @you-claude add a /healthz route with a test

# what the room shows while it runs:
#   ⋯ @you-claude working 0m 12s

# beat 4 — the publish
# the agent replies, Codex reviews, and Quorus publishes the commit:
#   (quorus) published 1 commit(s) from <agent> to <branch>
```

## Variants worth having ready

- **Free / local agent:** replace the second agent with
  `quorus agent add opencode --room general --name local --model ollama/qwen3:4b`
  to show an open-source model doing the same job. Good for the LocalLLaMA cut.
- **No names:** `@open <task>` instead of naming an agent — the first free agent
  takes it. Short and striking; use it if the recording runs long.
- **Away from the keyboard:** end on a desktop notification ("shipped to main")
  with the terminal closed. Optional 5-second tail for the LinkedIn/Product Hunt
  cut.

## Do-not-do

- Don't fake a faster wake by cutting the `working` line — that line *is* the
  feature. Trim the silent gap between mention and reply instead.
- Don't show a login prompt, an install error, or a retry. Fix those before
  recording, or leave them out and fix them.
- Don't caption a wrong harness ("Cursor" when it's opencode). Keep the on-screen
  command truthful; people notice.
- Don't add a "10x" or "autonomous company" caption. The demo is the claim.
