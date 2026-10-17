# Quorus app UI overhaul — design brief (rev 2, 2026-10-17)

Mock: `docs/weekend/ui-mock/mission-control.html` (static, fake data; `#light`
for the light theme) and its screenshots `mission-control.png` and
`mission-control-light.png`. No app code until this brief is approved.

## How we compare (what the UI must make obvious at a glance)

As we read them on 2026-10-17. Verify the cells before any of this goes on
website/.

| | Vendors | People | Machines | Measured outcomes | Live control |
|---|---|---|---|---|---|
| ECC (everything-claude-code) | 1 (Claude Code) | 1 | 1 | no | yes, inside one session |
| Mission-control terminal grids | usually 1, a few mix CLIs | 1 | 1 | no | yes, one terminal at a time |
| Pixel Agents / Claw3D offices | mostly 1 | 1 | 1 | no | mostly watch-only |
| Paperclip | several (bring your own) | 1 operator | 1 host | budgets and cost, not code kept | via tickets and approvals |
| **Quorus** | **any (Claude, Codex, Gemini, opencode…)** | **many, invited by role** | **many** | **kept %, flags, survival per agent** | **approve, deny, @-ask, verbs, from any device** |

Each column maps to the screen: a model on every panel (vendors), people in the
rail and chat (people), each panel's machine in its tooltip (machines), a track
record on every panel (measured), and the needs-you rail plus "ask @x"
(control).

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

## Colour: one proven palette, dark and light

Radix Colors v3 (MIT): 12-step scales whose steps have fixed jobs (1-2 app
backgrounds, 3-5 components, 6-8 borders, 9-10 solids, 11-12 text). The values are
copied as CSS variables and need no dependency. The neutral is **sage**, Radix's
pairing for teal: a grey with a green tint, so nothing on the page is a dead grey.
Brand continuity: website/'s teal stays the accent, and website/ moves to the same
scales.

| Role | Dark | Light |
|---|---|---|
| app background / pane | sage-1 #101211 / sage-2 #171918 | sage-1 #fbfdfc / white |
| raised / hover | sage-3 #202221 / sage-4 #272a29 | sage-3 #eef1f0 / sage-4 #e6e9e8 |
| hairline / strong border | sage-4 / sage-6 #373b39 | sage-5 #dfe2e0 / sage-7 #cbcfcd |
| text / secondary | sage-12 #eceeed / sage-11 #adb5b2 | sage-12 #1a211e / sage-11 #5f6563 |
| accent text / solid | teal-11 #0bd8b6 / teal-9 #12a594 | teal-12 #0d3d38 / teal-9 |
| needs you: bg / border / text / solid | amber-3 / amber-7 / amber-11 #ffca16 / amber-9 #ffc53d | amber-3 / amber-7 / amber-12 #4f3422 / amber-9 |
| stuck: text / border | tomato-11 #ff977d / tomato-7 | tomato-11 #d13415 / tomato-7 |

Measured WCAG contrast on the pane: dark text 15.2, secondary 8.4, accent 9.7,
amber 11.5, tomato 8.4. Light text 15.5, secondary 5.6, tomato 4.7, amber-12 on
amber-3 about 10. Two light-mode traps are already avoided: teal-11 text (4.3) and
amber-11 on amber-3 (4.2) both fail, so light mode uses step 12 for small text.
Solids (teal-9, amber-9) take dark text (6.1 and 11.9); white on teal-9 is 3.1
and is banned.

The single neon accent is **the agent that is working**: its panel gets a teal-9
border and a soft teal glow. Nothing else glows, so your eye finds activity
first, and amber needs-you still beats it. Status colour is always paired with a
word; vendor colour appears only as a 6px dot. The theme follows
`prefers-color-scheme`, with a toggle.

## Cinematic, where it belongs

Arav's bar is premium, not a dev tool. Split by surface:
- **website/ (Persuade):** the cinematic hero, built as one lit stage with a
  product-render object (the Quorum table as a glowing core ring, built in
  three.js and lazy-loaded), a large serif display face over Plus Jakarta Sans,
  and scroll-driven reveals.
- **App (Operate):** the same palette and type with calm motion. Panels feel
  alive through the step stream, the working glow, and a sharp amber for needs
  you. No scroll theatre.
- **Quorum view:** the product-render treatment. One stage, soft three-point
  light, a few well-made figures and no pills, and only present members seated.

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

## Before launch (the "20 things before you publish" list, applied)

website/:
- prerender every route to static HTML (it's a Vite SPA, so crawlers and link
  unfurlers see an empty `<div id=root>` today);
- per-route `<title>`, description and canonical;
- OG and Twitter cards with a 1200×630 image per key page;
- re-check `public/sitemap.xml` and `robots.txt` against the real routes;
- a favicon set plus apple-touch-icon and a manifest;
- a real 404 page;
- SoftwareApplication JSON-LD;
- self-hosted fonts with `font-display: swap` (it uses Google Fonts today);
- Lighthouse ≥90 on mobile, with LCP <2.5s and CLS <0.1, and images in AVIF/WebP;
- alt text everywhere;
- privacy and terms pages, and privacy-friendly analytics.

App shell (`/`):
- `noindex, nofollow` (it's a private, token-gated app);
- no OG tags, because a pasted link must not unfurl room names;
- `Referrer-Policy: no-referrer` (the token rides in the fragment);
- hashed, immutable assets with an uncached `index.html`;
- a bundle budget of ≤180 KB gzipped, excluding the lazy three.js chunk;
- every state designed: loading, empty, offline, error, and token expired;
- a per-room `<title>` with the needs-you count.

Security items: @arav-codex.

## Build and security constraints (for @arav-codex's plan)

- React + Vite + framer-motion, built to `quorus/web/app/`, served same-origin at
  `/`. pip and pipx installs stay node-free (the built files ship in the wheel).
- No `dangerouslySetInnerHTML` (a lint rule bans it). Room data is rendered as text
  only. The token stays in the fragment, then sessionStorage, as today.
- The old Python-string page stays until the new app has parity: rooms, chat,
  board actions, approvals, and join.

## Skills used

One skill per job, not one for everything:
- **impeccable:** mode and craft floor (no eyebrows, no thick coloured side
  borders, no emoji icons, mono only for data).
- **ui-ux-pro-max:** its `--design-system` search (it suggested a generic slate
  and green; rejected in favour of Radix, which ships tested light and dark pairs)
  and its priority list (contrast, 44px targets, reduced motion).
- **frontend-patterns:** the React build.
- **web-design-guidelines:** the build review.

These are installed in `~/.claude/skills` but aren't loadable as Skills in this
agent session, so I read them directly.
