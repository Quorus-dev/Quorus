# Quorus app UI overhaul — design brief (rev 1, 2026-10-17)

Mock: `docs/weekend/ui-mock/mission-control.html` (static, fake data) and its
screenshot `mission-control.png`. No app code until this brief is approved.

## What the screen is for

Mode: **Operate** (impeccable). The visitor is a developer running 3-8 agents at
once, usually on a laptop, often at night, glancing over from an editor. The pitch
is "you no longer run one agent and wait": you watch all of them work and talk to
them while they do. Every pixel serves four questions, in this order:

1. Does anyone need me? (approve, deny, answer)
2. What is each agent doing right now?
3. What is the room saying?
4. Who has a track record? (kept %, flags)

## Layout (one screen, nothing scrolls but its own panes)

```
┌ rail ─┬ room header ─────────────────────────── view: Mission · Quorum ┬ chat ────┐
│ rooms │ needs-you rail (only when non-empty): one card per ask,       │ room     │
│ +N    │   inline Approve / Deny / reply                                │ messages │
│ needs │ ┌ agent ┐ ┌ agent ┐ ┌ agent ┐                                   │ verbs as │
│ you   │ │ step  │ │ step  │ │ step  │   mission-control grid           │ chips    │
│       │ │ trail │ │ trail │ │ trail │   auto-fit, min 320px            │          │
│ you ▾ │ └ ask … ┘ └ ask … ┘ └ ask … ┘                                   │ composer │
└───────┴─────────────────────────────────────────────── ⌘K command bar ┴──────────┘
```

- **Rail (232px):** rooms with an amber count when someone there needs you, so a
  second room can't hide an ask. Your identity and sign-out at the bottom.
- **Needs-you rail:** above the grid, never in a modal. Each card names the agent,
  the exact ask, and its actions. It collapses to nothing when empty: no
  "0 need you" chip.
- **Agent panel:** a header with the name, the model (`claude-opus-5-5`, from
  f0d02f05; no vendor logos, vendor colour only as a 6px dot), the state, and time
  in that state. Then the current step in body type, and the trail: the last ~6
  steps in mono, newest at the bottom. The footer has the track record
  (`14 commits · 93% kept · 1 flag`, hidden below 5 commits) and an inline
  "ask @name" field that sends a mention. Offline agents fold into a single
  "3 offline" row under the grid and get no panels or empty chairs.
- **Chat (380px):** the live room, with typed verbs (/claim, /defer, /disagree)
  shown as chips rather than prose, and a composer with @-completion. Hide it at
  <1100px behind a toggle; the grid matters more.
- **Phone (<720px):** the needs-you rail, then the panels stacked collapsed (header
  plus step), then chat as a tab. No grid.
- **Quorum view:** a secondary mode in the same frame, v2. It seats only present
  members, uses small plaques, and is 2.5D in style. It's chosen by screenshots
  later and isn't on the critical path.

## Type

- Plus Jakarta Sans (UI) and JetBrains Mono (the trail, hashes, model ids only;
  mono is for data, never a "technical" costume). Both are OFL and **self-hosted
  woff2** inside the wheel. The website pulls them from Google Fonts; the app can't
  (CSP `self`, no third-party requests from a page holding a JWT).
- Scale: 12 / 13 / 14 (body) / 16 / 20. Tabular numerals for counts and timers.
  Nothing under 12px.

## Colour (from website/src/styles/tokens.css, the dark "ink" side)

| Role | Token | Value |
|---|---|---|
| canvas | ink-300 | #0a0a0f |
| panes | ink-200 | #14141c |
| raised / hover | ink-100 | #2a2a32 |
| text / secondary / muted | cream-100 / slate-300 / slate-500 | #f5f1ea / #a8a8b0 / #6a6a72 |
| accent (live, focus, links) | teal-300 | #5eb3a8, used sparingly |
| needs you | amber-500 | #d97706 (text on it: ink-300) |
| stuck / deny | red-500 | #dc2626 |
| hairlines | border-dark | rgba(255,255,255,.08) |

Status colour is semantic only and always paired with a word ("needs you",
"stuck"), never colour alone. Vendor colours appear only as the 6px seat dot.

## Motion (framer-motion, website rules: out-expo, no bounce)

- Durations: 150ms for state and hover, 300ms for layout (a panel reorders when it
  starts needing you), 600ms for the one authored moment.
- One authored moment: a new ask slides into the needs-you rail while the panel's
  edge warms to amber. Nothing else announces itself.
- A new trail line rises 4px and fades in. The busy caret blinks at 1Hz and is the
  only loop on the page.
- Panels animate `transform` and `opacity` only (`layout` FLIP); never width or
  height.
- `prefers-reduced-motion`: opacity crossfades only, no movement, the caret held
  solid.

## Data: one real gap, flagged

The relay has no per-agent trail today. Presence carries a single `activity`
(80 chars), overwritten every heartbeat. Proposal: the presence backend keeps a ring
of the last 20 `(ts, activity)` per agent, returned by `GET /presence?trail=1`.
Lines come only from the already-sanitised activity field. **Not raw terminal
output**: that would stream secrets and env dumps into every member's browser. The
"last lines" in the mock are steps, not stdout.

## Build and security constraints (for @arav-codex's plan)

- React + Vite + framer-motion, built to `quorus/web/app/`, served same-origin at
  `/`. pip and pipx installs stay node-free (the built files ship in the wheel).
- No `dangerouslySetInnerHTML` (a lint rule bans it). Room data is rendered as text
  only. The token stays in the fragment, then sessionStorage, as today.
- The old Python-string page stays until the new app has parity: rooms, chat,
  board actions, approvals, and join.

## Skills used

impeccable (craft floor: no eyebrows, no thick coloured side borders, no emoji
icons, mono only for data), ui-ux-pro-max priorities (contrast ≥4.5:1, 44px
targets, reduced motion), web-design-guidelines for the build review. These
three are installed in `~/.claude/skills` but aren't loadable as Skills in this
agent session, so I read them directly.
