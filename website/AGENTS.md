# quorus.dev

One static page (`index.html`, `src/site.css`, `src/main.ts`), built with Vite.
No framework and no third-party scripts, fonts or trackers; the CSP in
`vercel.json` allows only same-origin resources.

Content rules: every claim must be true of the package on PyPI, as the README
states it. No logos, badges, counters, testimonials or "trusted by" lines.
Cursor, Windsurf and Cline connect over MCP only; never call them supported.

`/llms.txt` and `/agents.md` are copied from the repo root's `llms.txt` at build
time (`npm run build`), so there is one copy.

Visuals: only the shipped product appears. The hero is the demo recording
(`public/media/demo.{webm,mp4}` with its poster), a real run of the released
package. The feature cards show crops of that same run (`rooms`, `mention`,
`handoff`, `review`), `quorus scorecard` run on this repository, and
`quorus locks` on a test relay; each caption says where it came from. Captures
of the Quorum view and the board rows come back with the release that ships
them (0.6.0). Every image is WebP (no AVIF encoder is installed here). The
one web font is Plus Jakarta Sans (OFL, `public/fonts/`), preloaded and self-hosted.
The footer's version is read from `../pyproject.toml` at build time. Motion is
transform/opacity only and stops under `prefers-reduced-motion`.
