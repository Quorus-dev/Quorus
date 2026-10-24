Vendored browser libraries, served same-origin by quorus/quorum_view.py so the relay
CSP stays script-src 'self'. Each file is byte-pinned by sha256 in the tests.

three.js 0.180.0 (MIT), from the npm registry (npm pack three@0.180.0):
  three.module.min.js, three.core.min.js  — the Quorum view only (loaded on demand)

preact 11.0.2 (MIT), npm pack preact@11.0.2, dist/preact.mjs:
  preact.module.js  sha256 4d2617f3f082550223d37f781114b5f723362fc4a337459715a7083664dc10c1

htm 3.1.1 (Apache-2.0), npm pack htm@3.1.1, dist/htm.module.js:
  htm.module.js     sha256 ab33dd3f38059b9be4d5f5350128eefb2356639c4e0bbe9d9e8b3ba75847e9e4

Neither preact nor htm uses eval or new Function; htm parses template literals in JS,
so no 'unsafe-eval' is needed (docs/plans/board-inbox.md, slice 2).
