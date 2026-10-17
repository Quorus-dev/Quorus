# What Arav's reels point at (overseer, Sat 2026-10-17)

Arav sent 13 Instagram reels as inspiration for the UI overhaul, which also double as a
competitor map. Most of them gate the full list behind "comment X and I'll DM you", so this
is what is visible plus the public sources behind each one. Woken agents keep all of Arav's
293 Claude skills (735a7868), so the installed ones below are usable right now.

## Design and motion
| Reference | What it is | On this machine | Use it for |
|---|---|---|---|
| "Impeccable: design fluency for AI harnesses" | [pbakaus/impeccable](https://github.com/pbakaus/impeccable), ~10k stars. 23 commands, references for typography, colour (OKLCH), spacing, motion, interaction, responsive, UX writing; 59 detector rules for AI-looking UIs | **installed** (`impeccable`) | Run `/impeccable init` in `website/` and the new app; critique every screen with it |
| "UI/UX pro max is best used with other tools" | design-system skill | **installed** (`ui-ux-pro-max`) | palette, type scale, component rules |
| — | `frontend-design`, `web-design-guidelines`, `frontend-patterns` | **installed** | layout, a11y, React patterns |
| "GSAP skills made Claude stop writing ugly animations" | [greensock/gsap-skills](https://docsearch.algolia.com/mcp/docs/repo/greensock/gsap-skills): 8 modules (core, timeline, ScrollTrigger, plugins, utils, React, performance, frameworks) | not installed | Only if we pick GSAP over framer-motion (`website/` already uses framer-motion). The rules apply either way: transforms not layout props, timelines, cleanup |
| "Steal these colours for your UI" | proven palettes over ad-hoc greys | — | one deliberate palette, accessible in dark and light |
| "3 frontend libraries for premium websites" / "websites like this in 5 minutes" | cinematic 3D product renders, big type pairings, dark high-contrast with one neon accent | — | the marketing site's hero; the Quorum view's lighting and stage |

## Technical
| Reference | What it is | On this machine | Use it for |
|---|---|---|---|
| "imagine using this in 2026" (luciomakes) | a grid of many agent terminals running at once: "you run one agent at a time, then you wait" | — | the app's centre: mission-control panels streaming each agent's live steps, chat beside it |
| "Turn Claude into a 64-agent dev team" (ECC) | everything-claude-code: one vendor, one session | **installed** (plugin) | competitor row; Quorus = many vendors, people and machines in one room |
| "Top 10 Claude Code / Codex skills by stars" (Graphify #1, 123k) | public lists: anthropics/skills, Superpowers, ECC, claude-mem ([toolsforhumans](https://www.toolsforhumans.ai/skills/coding), [heyuan110](https://www.heyuan110.com/posts/ai/2026-01-20-claude-code-skills-top20/)) | graphify, Superpowers-style workflow skills **installed** | graphify = the shared codebase map in the team-memory design; claude-mem = prior art for room memory (study, don't copy) |
| "5 plugins to code like a senior dev" | plugin list (blurred in the reel) | — | pick the strongest installed skill per task |
| "5 ways your vibecoded login endpoint isn't secure" | auth hardening checklist | — | Codex: check claim-name, token exchange, invites, phone/web links |
| "Don't publish your website until…" / "20 things to tell Claude before launching" | SEO/meta, SSR, OG images, sitemap, performance | — | `website/` and the app shell before launch (LAUNCH.md) |

## How we compare (the UI must make this obvious)
Claude is adding the table to the design brief: ECC, mission-control terminal grids,
Pixel Agents / Claw3D-style offices, Paperclip vs Quorus on vendors, people, machines,
measured outcomes and live control.
