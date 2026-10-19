import { fileURLToPath } from "node:url";
import { defineConfig } from "vite";

// The footer says "vX on PyPI", so X comes from PyPI itself, at build time (the
// browser makes no third-party request; the CSP forbids it). It used to come from
// ../pyproject.toml, but the dev branch carries the next version before it ships:
// built from weekend, the site claimed 0.5.2 while PyPI had 0.5.1. Offline, set
// QUORUS_SITE_VERSION; with neither, the build fails rather than guess.
async function releasedVersion(): Promise<string> {
  const pinned = process.env.QUORUS_SITE_VERSION?.trim();
  if (pinned) return pinned;
  try {
    const resp = await fetch("https://pypi.org/pypi/quorus-ai/json", {
      signal: AbortSignal.timeout(10_000),
    });
    const version = ((await resp.json()) as { info?: { version?: string } }).info?.version;
    if (resp.ok && version && /^\d+\.\d+\.\d+$/.test(version)) return version;
  } catch {
    // fall through to the error below
  }
  throw new Error("can't read quorus-ai's version from PyPI; set QUORUS_SITE_VERSION");
}

export default defineConfig(async () => {
  const version = await releasedVersion();
  return {
    plugins: [
      {
        name: "quorus-version",
        transformIndexHtml: (html: string) => html.replaceAll("%QUORUS_VERSION%", version),
      },
    ],
    build: {
      outDir: "dist",
      // Every page is real, indexable HTML (cleanUrls serves /install from install.html).
      rollupOptions: {
        input: Object.fromEntries(
          ["index", "install", "security", "deploy"].map((page) => [
            page,
            fileURLToPath(new URL(`./${page}.html`, import.meta.url)),
          ]),
        ),
      },
      // Don't ship sourcemaps publicly.
      sourcemap: false,
    },
    server: {
      port: 3000,
    },
  };
});
