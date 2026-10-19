// Renders public/og.png (1200x630) from tools/og.html with the built site's own
// stylesheet, so the card is the hero's real finished run next to the headline.
// Run after `npm run build`: `npm run og`. Uses the repo's Chrome probe
// (scripts/site_evals/probe.mjs); nothing third-party.
import { spawn } from "node:child_process";
import { readFileSync, writeFileSync, copyFileSync, existsSync } from "node:fs";
import { createServer } from "node:net";
import { resolve } from "node:path";

const here = resolve(new URL(".", import.meta.url).pathname);
const site = resolve(here, "..");
const dist = resolve(site, "dist");
const probe = resolve(site, "..", "scripts", "site_evals", "probe.mjs");
if (!existsSync(resolve(dist, "index.html"))) { console.error("make-og: run `npm run build` first"); process.exit(2); }

const css = /href="(\/assets\/[^"]+\.css)"/.exec(readFileSync(resolve(dist, "index.html"), "utf8"))?.[1];
if (!css) { console.error("make-og: no stylesheet in dist/index.html"); process.exit(2); }
writeFileSync(resolve(dist, "og.html"), readFileSync(resolve(here, "og.html"), "utf8").replaceAll("%CSS%", css));

const port = await new Promise((r) => { const s = createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => r(p)); }); });
const server = spawn("python3", ["-m", "http.server", String(port), "-d", dist, "--bind", "127.0.0.1"], { stdio: "ignore" });
await new Promise((r) => setTimeout(r, 800));
const out = resolve(site, "public", "og.png");
const shot = spawn("node", [probe, "shot", `http://127.0.0.1:${port}/og.html`, out, "1200", "630", "1", "--settle", "1500"], { stdio: "inherit" });
const code = await new Promise((r) => shot.on("exit", r));
server.kill();
if (code === 0) { copyFileSync(out, resolve(dist, "og.png")); console.log("og.png written:", out); }
process.exit(code ?? 1);
