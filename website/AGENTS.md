# quorus.dev

One static page (`index.html`, `src/site.css`, `src/main.ts`), built with Vite.
No framework and no third-party scripts, fonts or trackers; the CSP in
`vercel.json` allows only same-origin resources.

Content rules: every claim must be true of the package on PyPI, as the README
states it. No logos, badges, counters, testimonials or "trusted by" lines.
Cursor, Windsurf and Cline connect over MCP only; never call them supported.

`/llms.txt` and `/agents.md` are copied from the repo root's `llms.txt` at build
time (`npm run build`), so there is one copy.

Visuals: `public/media/*.webp` are real screenshots of the shipped UI (the Quorum
view and board rows) taken on a seeded test relay at 2x; every caption says so. The
one web font is Plus Jakarta Sans (OFL, `public/fonts/`), preloaded and self-hosted.
The footer's version is read from `../pyproject.toml` at build time. Motion is
transform/opacity only and stops under `prefers-reduced-motion`.
