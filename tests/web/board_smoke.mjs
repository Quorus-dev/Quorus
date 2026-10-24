// Browser smoke for the board (docs/plans/board-inbox.md, slice 2): a reviewer runs it,
// pytest does not. It starts its own file-mode relay on a free port in a scratch HOME,
// seeds a room with two agents, a question and a pending approval, opens the page the
// way `quorus web` does (token in the fragment, shared secret, so no Approve buttons) and
// checks that the board renders through the vendored Preact + htm under the relay's real
// CSP: no CSP violation, no page error, the chips and rows, the approval's CLI hint, the
// typed text in a row's input surviving a poll (keyed rows), and `QuorusBoard.rowEl`
// (the Quorum view's seat card). Needs Playwright with Chromium: the jury skill's
// install at ~/.cache/jury is used unless PLAYWRIGHT_DIR points elsewhere.
//
//   node tests/web/board_smoke.mjs            (from the repo root; ~15 s)
//   QUORUS_PYTHON=.venv/bin/python node tests/web/board_smoke.mjs
import { createRequire } from "node:module";
import { spawn } from "node:child_process";
import { mkdtempSync, rmSync } from "node:fs";
import { createServer } from "node:net";
import { tmpdir } from "node:os";
import { join, resolve } from "node:path";

const root = resolve(new URL("../..", import.meta.url).pathname);
const pwDir = process.env.PLAYWRIGHT_DIR || join(process.env.HOME, ".cache/jury/node_modules");
const { chromium } = createRequire(pwDir + "/")("playwright");
const SECRET = "smoke-secret-0123456789";
const EXPECTED_404 = [/\/scorecard$/, /\/track\?owner=/];  // no repo bound here: by design

const port = await new Promise((ok) => {
  const s = createServer(); s.listen(0, "127.0.0.1", () => { const p = s.address().port; s.close(() => ok(p)); });
});
const home = mkdtempSync(join(tmpdir(), "quorus-board-smoke-"));
const pythonPath = ["", "packages/cli", "packages/mcp", "packages/sdk", "packages/tui"]
  .map((p) => join(root, p)).join(":");
const relay = spawn(process.env.QUORUS_PYTHON || "python3", ["-m", "quorus.relay_cli"], {
  cwd: root, stdio: ["ignore", "pipe", "pipe"],
  env: { PATH: process.env.PATH, HOME: home, QUORUS_CONFIG_DIR: join(home, ".quorus"),
         RELAY_SECRET: SECRET, ALLOW_LEGACY_AUTH: "1", JWT_SECRET: "smoke-jwt-0123456789abcdef",
         MESSAGES_FILE: join(home, "messages.jsonl"), HOST: "127.0.0.1", PORT: String(port),
         LOG_LEVEL: "WARNING", PYTHONPATH: pythonPath },
});
let relayLog = "";
relay.stdout.on("data", (d) => { relayLog += d; });
relay.stderr.on("data", (d) => { relayLog += d; });
const base = `http://127.0.0.1:${port}`;
const stop = () => { try { relay.kill("SIGTERM"); } catch { /* already gone */ } rmSync(home, { recursive: true, force: true }); };
process.on("exit", stop);

const until = Date.now() + 20000;
while (Date.now() < until) {
  try { if ((await fetch(base + "/health")).ok) break; } catch { /* not up yet */ }
  await new Promise((r) => setTimeout(r, 250));
}
const api = async (path, body) => {
  const r = await fetch(base + path, { method: "POST", body: JSON.stringify(body),
    headers: { Authorization: "Bearer " + SECRET, "Content-Type": "application/json" } });
  if (!r.ok) throw new Error(`${path}: HTTP ${r.status} ${await r.text()}`);
  return r.json();
};
await api("/rooms", { name: "demo", created_by: "sam" });
await api("/rooms/demo/join", { participant: "sam-codex" });
await api("/rooms/demo/join", { participant: "sam-claude" });
await api("/rooms/demo/messages", { from_name: "sam-codex", content: "@sam should I take the legal memo next?" });
const approval = await api("/v1/approvals", { room_id: "demo", agent: "sam-codex", tool_name: "Bash",
                                              tool_input: "git push origin main" });

const browser = await chromium.launch();
const page = await browser.newPage({ viewport: { width: 1280, height: 900 } });
const problems = [], scripts = new Set();
page.on("console", (m) => {
  if (m.type() !== "error") return;
  const url = m.location().url || "";
  if (EXPECTED_404.some((re) => re.test(url))) return;
  problems.push("console: " + m.text() + " " + url);
});
page.on("pageerror", (e) => problems.push("pageerror: " + e.message));
page.on("requestfailed", (r) => problems.push("requestfailed: " + r.url()));
page.on("request", (r) => { const p = new URL(r.url()).pathname; if (p.endsWith(".js")) scripts.add(p); });
await page.addInitScript(() => {
  window.__csp = [];
  document.addEventListener("securitypolicyviolation", (e) => window.__csp.push(e.violatedDirective + " " + e.blockedURI));
});
await page.goto(`${base}/#token=${SECRET}&name=sam&room=demo&view=board`);
await page.waitForSelector("#boardRows article.brow", { timeout: 15000 });
const seen = await page.evaluate((approvalId) => {
  const rows = [...document.querySelectorAll("#boardRows article.brow")];
  const codex = rows.find((r) => r.querySelector("b").textContent === "@sam-codex");
  const card = window.QuorusBoard.rowEl({ name: "x-claude", vendor: "claude", rank: 4, line: "working",
    detail: "", model: "m", asks: [], approvals: [{ id: "ap9", tool_name: "Bash", input_preview: "ls" }],
    trail: [{ t: 1, step: "a" }, { t: 2, step: "b" }] });
  codex.querySelector("form.bassign input").value = "typed";
  return {
    csp: window.__csp,
    chips: [...document.querySelectorAll("#boardSummary .chip")].map((c) => c.textContent),
    codexClass: codex.className,
    approvalHint: codex.querySelector(".bapproval code")?.textContent,
    ask: codex.querySelector(".bask")?.textContent,
    noButtons: codex.querySelectorAll("button").length === 0,  // shared secret: never Approve
    formAction: codex.querySelector("form.bassign").dataset.action,
    rowEl: card.tagName === "ARTICLE" && card.className === "brow r4" && !!card.querySelector("form.bassign")
      && card.querySelectorAll(".btrail li").length === 2 && /quorus approve ap9/.test(card.textContent),
    approvalId,
  };
}, approval.id);
await page.waitForTimeout(3500);  // one more 3 s poll
const typed = await page.evaluate(() =>
  [...document.querySelectorAll("#boardRows article.brow")]
    .find((r) => r.querySelector("b").textContent === "@sam-codex").querySelector("form.bassign input").value);
await browser.close();
stop();

const checks = {
  "no CSP violation": seen.csp.length === 0,
  "no page error": problems.length === 0,
  "chips": seen.chips[0] === "1 need you" && seen.chips.length === 4,
  "needs-you row first": seen.codexClass === "brow r1",
  "approval shows the CLI hint, no buttons": seen.approvalHint === "quorus approve " + seen.approvalId && seen.noButtons,
  "question shows": /legal memo/.test(seen.ask || ""),
  "form bound to its action": seen.formAction === "quorus.message.say",
  "typed text survives a poll": typed === "typed",
  "rowEl builds the seat card": seen.rowEl,
  "vendored ESM loaded, three.js not": scripts.has("/vendor/preact.module.js") && scripts.has("/vendor/htm.module.js")
    && scripts.has("/rows.js") && ![...scripts].some((s) => s.includes("three")),
};
let failed = 0;
for (const [name, ok] of Object.entries(checks)) { console.log((ok ? "ok   " : "FAIL ") + name); if (!ok) failed++; }
if (failed) {
  console.log(JSON.stringify({ seen, typed, problems, scripts: [...scripts] }, null, 1));
  console.log(relayLog.split("\n").slice(-20).join("\n"));
}
process.exit(failed ? 1 : 0);
