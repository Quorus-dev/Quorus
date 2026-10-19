# quorus.dev

A static home page (`index.html`, `src/site.css`, `src/main.ts`) plus three doc
pages (`install.html`, `security.html`, `deploy.html`, loading only `src/page.ts`),
each a Vite entry in `vite.config.ts` and listed in `public/sitemap.xml`. No framework and no third-party scripts, fonts or trackers; the CSP in
`vercel.json` allows only same-origin resources.

Content rules: every claim must be true of the package on PyPI, as the README
states it. No logos, badges, counters, testimonials or "trusted by" lines.
Cursor, Windsurf and Cline connect over MCP only; never call them supported.

`/llms.txt` and `/agents.md` are copied from the repo root's `llms.txt` at build
time (`npm run build`), so there is one copy.

v3 (2026-10-19, Arav: "the product is the page"): one column on a 1200px grid,
display 76/40 fluid, body 18/1.6, and exactly these sections: hero (one claim
proven by the real run replaying full width, then the one CTA), TLDR, How it
works (four beats of that run with the real capture of each), Try it (`quorus
demo`, the full recording, the three commands), Security, FAQ (six answers true
for the released version), a closing CTA. No logos, mascots, "trusted by" lines
or scorecard numbers.

Visuals: only the real product appears. The hero replays a real run (Claude
Code and Codex, 0.5.1) in the page: the script in `src/run.ts` is the recording's
own lines, excerpted and verbatim, played about 4× faster than it happened (the
caption says so); the static markup in `index.html` is the finished run, which is
what no-JS and reduced-motion visitors see. Neither recording contains a lock
event, so the lock beat uses the real `quorus locks` table from a test relay,
labelled. The Quorum view is not on the site until the rebuilt view ships. The
recording itself is `public/media/demo.{webm,mp4}` with its poster; the beat
crops are `mention`, `review` and `handoff`. Every image is WebP (no AVIF encoder
is installed here). The one web font is Plus Jakarta Sans (OFL, `public/fonts/`),
preloaded and self-hosted. The footer's version is the one on PyPI, read at
build time (`QUORUS_SITE_VERSION` offline). The OG image is a real frame of the
finished run with the headline, rendered by `npm run og`. Motion is
transform/opacity only (plus SVG stroke drawing), loaded after first paint as
its own chunk, and absent under `prefers-reduced-motion`.
