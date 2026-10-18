// Chrome probe for the design loop (scripts/site_evals.py). No dependencies: it
// drives Chrome over its DevTools protocol with Node's WebSocket.
//
//   node probe.mjs shot <url> <out.png> <cssW> <cssH> <dsf> [--settle ms] [--lock] [--reduce] [--headed]
//   node probe.mjs fps  <url> <seconds> <cssW> <cssH> <dsf>            (always headed: a real display)
//
// `shot` writes a PNG; `--lock` keeps the Quorum stage at full quality on software GL
// (sessionStorage qq=lock, what the stage's budget guard honours); `--reduce` emulates
// prefers-reduced-motion. `fps` samples the stage's own stats() once a second and prints
// the series. Both print one JSON line last. The URL may carry a fragment with a
// throwaway relay secret; it is never printed.
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync, writeFileSync } from "node:fs";
import { tmpdir } from "node:os";
import { join } from "node:path";

const [mode, url, ...rest] = process.argv.slice(2);
if (!mode || !url) { console.error("usage: probe.mjs shot|fps <url> ..."); process.exit(2); }
const flags = new Set(rest.filter((a) => a.startsWith("--") && !/^--settle$/.test(a)));
const settleIx = rest.indexOf("--settle");
const settle = settleIx >= 0 ? Number(rest[settleIx + 1]) : 6000;
const pos = rest.filter((a, i) => !a.startsWith("--") && rest[i - 1] !== "--settle");
const CHROME = process.env.CHROME || "/Applications/Google Chrome.app/Contents/MacOS/Google Chrome";
const headed = mode === "fps" || flags.has("--headed");
const [w, h, dsf] = mode === "shot" ? [Number(pos[1]), Number(pos[2]), Number(pos[3] || 1)] : [Number(pos[1]), Number(pos[2]), Number(pos[3] || 2)];

const prof = mkdtempSync(join(tmpdir(), "quorus-probe-"));
const port = 9500 + Math.floor(Math.random() * 400);
const gpu = headed ? [] : ["--use-angle=swiftshader", "--enable-unsafe-swiftshader", "--ignore-gpu-blocklist"];
const chrome = spawn(CHROME, [...(headed ? ["--window-position=0,0"] : ["--headless=new"]), "--no-first-run",
  "--no-default-browser-check", `--user-data-dir=${prof}`, ...gpu, "--hide-scrollbars",
  `--remote-debugging-port=${port}`, `--window-size=${Math.max(w, 800)},${Math.max(h, 600)}`, "about:blank"],
  { stdio: "ignore" });
const sleep = (ms) => new Promise((r) => setTimeout(r, ms));
// Close Chrome, wait for it to exit, then drop the profile; a profile still being
// written would make rmSync throw and turn a good run into exit 1.
const done = async (code) => {
  const exited = new Promise((r) => { chrome.once("exit", r); setTimeout(r, 3000); });
  try { ws.send(JSON.stringify({ id: ++id, method: "Browser.close" })); } catch { /* already gone */ }
  await exited;
  try { chrome.kill(); } catch { /* already gone */ }
  try { rmSync(prof, { recursive: true, force: true, maxRetries: 3 }); } catch { /* best effort */ }
  process.exit(code);
};

let targets = [];
for (let i = 0; i < 50 && !targets.length; i++) {
  await sleep(200);
  targets = await fetch(`http://127.0.0.1:${port}/json/list`).then((r) => r.json()).catch(() => []);
}
const page = targets.find((t) => t.type === "page");
if (!page) { console.error("probe: Chrome did not start"); chrome.kill(); process.exit(1); }
const ws = new WebSocket(page.webSocketDebuggerUrl);
await new Promise((r) => ws.addEventListener("open", r));
let id = 0; const pending = new Map();
ws.addEventListener("message", (ev) => {
  const m = JSON.parse(ev.data);
  if (m.id && pending.has(m.id)) { pending.get(m.id)(m); pending.delete(m.id); }
});
const send = (method, params = {}) => new Promise((r) => { pending.set(++id, r); ws.send(JSON.stringify({ id, method, params })); });
const evaluate = async (expression) => (await send("Runtime.evaluate", { returnByValue: true, awaitPromise: true, expression })).result.result.value;

await send("Runtime.enable");
await send("Page.enable");
if (flags.has("--lock")) await send("Page.addScriptToEvaluateOnNewDocument", { source: 'sessionStorage.setItem("qq", "lock");' });
await send("Emulation.setDeviceMetricsOverride", { width: w, height: h, deviceScaleFactor: dsf, mobile: w < 700 });
if (flags.has("--reduce")) await send("Emulation.setEmulatedMedia", { features: [{ name: "prefers-reduced-motion", value: "reduce" }] });
await send("Page.navigate", { url });

if (mode === "shot") {
  await sleep(settle);
  const out = pos[0];
  const shot = await send("Page.captureScreenshot", { format: "png" });
  writeFileSync(out, Buffer.from(shot.result.data, "base64"));
  const probe = await evaluate(`JSON.stringify({ canvas: !!document.querySelector("#quorum canvas"), tags: document.querySelectorAll(".qtag").length,
    title: document.title })`);
  console.log(JSON.stringify({ out, width: w, height: h, dsf, ...JSON.parse(probe) }));
  await done(0);
}

if (mode === "fps") {
  const seconds = Number(pos[0] || 20);
  await sleep(settle);
  const renderer = await evaluate(`(() => { const c = document.createElement("canvas"); const gl = c.getContext("webgl2") || c.getContext("webgl");
    const x = gl && gl.getExtension("WEBGL_debug_renderer_info"); return x ? gl.getParameter(x.UNMASKED_RENDERER_WEBGL) : "n/a"; })()`);
  const series = [];
  for (let s = 0; s < seconds; s++) {
    await sleep(1000);
    const st = await evaluate(`JSON.stringify((document.getElementById("quorum") || {}).quorumStage ? document.getElementById("quorum").quorumStage.stats() : null)`);
    if (st && st !== "null") series.push(JSON.parse(st));
  }
  const fps = series.map((s) => s.fps).sort((a, b) => a - b);
  const last = series.at(-1) || {};
  console.log(JSON.stringify({ renderer, samples: series.map((s) => s.fps), min: fps[0] ?? null,
    median: fps.length ? fps[Math.floor(fps.length / 2)] : null, max: fps.at(-1) ?? null,
    dpr: last.dpr ?? null, quality: last.quality ?? null, width: last.width ?? null, height: last.height ?? null,
    tags: await evaluate(`document.querySelectorAll(".qtag").length`) }));
  await done(0);
}
console.error("probe: unknown mode " + mode);
await done(2);
