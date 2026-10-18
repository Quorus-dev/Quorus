import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

// The footer shows the package version from the repo's pyproject.toml, read at
// build time so the page never fetches anything from a third party.
const pyproject = readFileSync(new URL("../pyproject.toml", import.meta.url), "utf8");
const version = /^version\s*=\s*"([^"]+)"/m.exec(pyproject)?.[1] ?? "";

export default defineConfig({
  define: { __QUORUS_VERSION__: JSON.stringify(version) },
  build: {
    outDir: "dist",
    // Don't ship sourcemaps publicly.
    sourcemap: false,
  },
  server: {
    port: 3000,
  },
});
