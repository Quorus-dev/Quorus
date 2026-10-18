import { readFileSync } from "node:fs";
import { defineConfig } from "vite";

// The footer names the version this site was built with, from the repo's
// pyproject.toml. Build-time, not a live PyPI call: the CSP allows no
// third-party requests.
const version = /^version = "([^"]+)"/m.exec(readFileSync("../pyproject.toml", "utf8"))?.[1];
if (!version) throw new Error("no version in ../pyproject.toml");

export default defineConfig({
  plugins: [
    {
      name: "quorus-version",
      transformIndexHtml: (html) => html.replaceAll("%QUORUS_VERSION%", version),
    },
  ],
  build: {
    outDir: "dist",
    // Don't ship sourcemaps publicly.
    sourcemap: false,
  },
  server: {
    port: 3000,
  },
});
