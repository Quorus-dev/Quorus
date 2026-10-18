# quorus.dev

One static page (`index.html`, `src/site.css`, `src/main.ts`), built with Vite.
No framework and no third-party scripts, fonts or trackers; the CSP in
`vercel.json` allows only same-origin resources.

Content rules: every claim must be true of the package on PyPI, as the README
states it. No logos, badges, counters, testimonials or "trusted by" lines.
Cursor, Windsurf and Cline connect over MCP only; never call them supported.

`/llms.txt` and `/agents.md` are copied from the repo root's `llms.txt` at build
time (`npm run build`), so there is one copy.

Visuals: only the real product appears. The hero replays the demo run in the
page: the script in `src/run.ts` is the recording's own lines, excerpted and
verbatim, played about 4× faster than it happened (the caption says so); the
static markup in `index.html` is the finished run, which is what no-JS and
reduced-motion visitors see. The second beat is the Quorum view's own stage:
`npm run build` copies `quorus/web/quorum-stage.js`, `quorum-totems.js` and the
vendored three.js into `public/quorum` and `public/vendor` (git-ignored), and
`src/stage-demo.ts` seats the run's three members and plays the same script. It
loads only at ≥960px, after load and idle, when the band is in view; phones and
no-WebGL show `media/quorum-still.webp`, a capture of that same render. Its
caption says it ships in 0.6.0. Below that is the recording itself
(`public/media/demo.{webm,mp4}` with its poster). The feature cards show crops
of that run (`rooms`, `mention`, `handoff`, `review`); the lock and scorecard
cards are HTML tables of real `quorus locks` / `quorus scorecard` output. Every
image is WebP (no AVIF encoder is installed here). The one web font is Plus
Jakarta Sans (OFL, `public/fonts/`), preloaded and self-hosted. The footer's
version is the one on PyPI, read at build time (`QUORUS_SITE_VERSION` offline). Motion is
transform/opacity only (plus SVG stroke drawing), loaded after first paint as
its own chunk, and absent under `prefers-reduced-motion`.
