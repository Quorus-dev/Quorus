// The "needs you" board: one row per agent, sorted by what needs a human (2026-10-17).
// This module renders; the row logic is rows.js (pure, node-tested). Preact + htm are
// vendored ESM served same-origin, so the relay CSP stays script-src 'self' with no build
// step and no eval (docs/plans/board-inbox.md, slice 2). Data only ever becomes text:
// htm interpolations are text nodes, and nothing here writes markup from data.
import { h, render as mount } from "/vendor/preact.module.js";
import htm from "/vendor/htm.module.js";
import { AGENT, B, briefTick, clock, enc, me, ownKey, rowState, token, trackOwner }
  from "/rows.js";

const html = htm.bind(h);
const $ = (id) => document.getElementById(id);

function api(path, opts) {
  const o = Object.assign({ headers: {} }, opts || {});
  o.headers = Object.assign({ Authorization: "Bearer " + token(),
                              "Content-Type": "application/json" }, o.headers);
  return fetch(path, o).then((r) => {
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}
function el(tag, cls, text) {  // textContent only: no HTML from data, ever
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
}

function decide(id, approve, btn) {
  btn.disabled = true;
  api("/v1/approvals/" + enc(id) + "/decision", { method: "POST",
    // No caller-supplied decider: the relay takes it from the viewer's own JWT
    // (Codex, 2026-10-17). Buttons only exist when ownKey() is true.
    body: JSON.stringify({ approve, reason: approve ? "" : "denied from the board" }) })
    .then(refresh)
    .catch((e) => { btn.disabled = false; btn.textContent = "failed (" + e.message + ")"; });
}

function ask(name, input) {
  const text = input.value.trim();
  if (!text || !B.room) return;
  input.disabled = true;
  api("/rooms/" + enc(B.room) + "/messages", { method: "POST",
    body: JSON.stringify({ from_name: me(), content: "@" + name + " " + text }) })
    .then(() => { input.value = ""; input.disabled = false; refresh(); })
    .catch(() => { input.disabled = false; });
}

function Approval({ a }) {
  const want = "wants to run " + (a.tool_name || "a tool") + ": "
    + String(a.input_preview || "").slice(0, 120);
  if (!ownKey()) {
    return html`<div class="bapproval"><span>${want}</span>
      <code>${"quorus approve " + a.id}</code></div>`;
  }
  // Action registry ids (quorus/actions/registry.py): the hub's /approve and /deny bind to
  // the same record, with the same credential (the viewer's own). No caller-supplied
  // decider: the relay takes it from the viewer's own JWT (Codex, 2026-10-17).
  return html`<div class="bapproval"><span>${want}</span>
    <button type="button" class="ok" data-action="quorus.approval.decide"
            onClick=${(e) => decide(a.id, true, e.currentTarget)}>Approve</button>
    <button type="button" class="no" data-action="quorus.approval.decide"
            onClick=${(e) => decide(a.id, false, e.currentTarget)}>Deny</button></div>`;
}

function Row({ r }) {
  const history = B.track[r.name] || [];
  const last = r.trail.length - 1;
  return html`<article class=${"brow r" + r.rank}>
    <div class="bhead"><b>${"@" + r.name}</b><span class="tag">${r.model || r.vendor}</span>
      <span class="bline">${r.line}</span></div>
    ${r.detail && html`<div class="bdetail">${r.detail}</div>`}
    ${r.trail.length > 1 && html`<ol class="btrail">${r.trail.map((t, i) =>
      html`<li class=${i === last ? "now" : ""}><time>${clock(t.t)}</time>
        <span>${String(t.step || "")}</span></li>`)}</ol>`}
    ${r.asks.map((a) => html`<div class="bask">${"asked you: " + a}</div>`)}
    ${r.approvals.map((a) => html`<${Approval} a=${a} />`)}
    ${history.length > 0 && html`
      <div class="bdetail">observed in this repo, not a ranking · 30 days</div>
      <table class="btrack"><tr>${["kind", "model", "harness version", "task history"]
        .map((v) => html`<th>${v}</th>`)}</tr>
        ${history.map((c) => html`<tr>${[c.kind, c.model, c.version, c.summary]
          .map((v) => html`<td>${String(v)}</td>`)}</tr>`)}</table>`}
    ${r.card && html`<div class="bship">${"track record: " + r.card}</div>`}
    ${r.tuning && html`<div class="btune">${r.tuning.text + " · undo: "}
      <code>${r.tuning.undo}</code></div>`}
    ${r.shipped && html`<div class="bship">${r.shipped}${r.review
      && html`<span class=${r.reviewBad ? "bad" : "good"}>${" · " + r.review}</span>`}</div>`}
    <form class="bassign" data-action="quorus.message.say"
          onSubmit=${(e) => {
            e.preventDefault(); ask(r.name, e.currentTarget.querySelector("input"));
          }}>
      <input placeholder=${"Ask or assign @" + r.name + "…"} autocomplete="off" />
    </form>
  </article>`;
}

function render(rows) {
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
  rows.forEach((r) => { if (counts[r.rank] !== undefined) counts[r.rank] += 1; });
  mount([[1, "need you"], [2, "stuck"], [3, "waiting"], [4, "working"]].map(([k, label]) =>
    html`<span class=${"chip" + (k === 1 && counts[k] ? " hot" : "")}>
      ${counts[k] + " " + label}</span>`),
    $("boardSummary"));
  rows.sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name));
  // Keyed by name: a row's "Ask or assign" input survives the 3 s refresh.
  mount(rows.length ? rows.map((r) => html`<${Row} key=${r.name} r=${r} />`)
    : html`<p class="hint">No agents in this room yet: quorus agent add …</p>`, $("boardRows"));
}

function rowEl(r) {  // the Quorum view's seat card is the board's row (quorum.js)
  const host = document.createElement("div");
  mount(html`<${Row} r=${r} />`, host);
  return host.firstElementChild;
}

function refresh() {
  // A phone in a pocket keeps the tab open: don't poll the relay while hidden.
  if (!B.on || !B.room || (typeof document !== "undefined" && document.hidden)) {
    return Promise.resolve();
  }
  const now = Date.now() / 1000;
  briefTick(B, now, api, (text) => { $("brief").textContent = text; });
  const members = now - B.membersAt > 30
    ? api("/rooms/" + enc(B.room)).then((r) => { B.members = r.members || []; B.membersAt = now; })
        .catch(() => {})
    : Promise.resolve();
  if (now - B.trackAt > 60) {
    B.trackAt = now;
    const asked = { room: B.room, tok: token(), name: me() };
    const live = () => asked.room === B.room && asked.tok === token() && asked.name === me();
    const owner = trackOwner(asked.name);
    api("/v1/rooms/" + enc(asked.room) + "/track?owner=" + enc(owner)).then((r) => {
      if (!live()) return;
      B.track = {};
      (r.cells || []).forEach((c) => { (B.track[c.agent] ||= []).push(c); });
      refresh();
    }).catch(() => { if (live()) B.track = {}; });
  }
  if (now - B.cardAt > 60) {
    // In the background: the first scorecard walks the repo's git history and
    // can take many seconds. Waiting for it left the board blank (Arav, live).
    B.cardAt = now;
    api("/v1/rooms/" + enc(B.room) + "/scorecard").then((r) => {
      B.card = {}; (r.agents || []).forEach((a) => { B.card[a.agent] = a; });
      refresh();
    }).catch(() => { B.card = {}; });  // 404: no repo bound here; hide the line
  }
  return members.then(() => Promise.all([
    api("/presence?trail=1").catch(() => []),
    api("/rooms/" + enc(B.room) + "/history?limit=100").catch(() => []),
    api("/v1/approvals?room=" + enc(B.room)).then((r) => r.pending || []).catch(() => []),
  ])).then(([presence, history, approvals]) => {
    const pres = {};
    (presence || []).forEach((p) => { pres[p.name] = p; });
    Object.keys(B.since).forEach((n) => {
      if ((pres[n] || {}).status !== "busy") delete B.since[n];
    });
    Object.values(pres).forEach((p) => {
      if (p.status === "busy" && !B.since[p.name]) B.since[p.name] = now;
    });
    const names = new Set(B.members.filter((n) => AGENT.test(n)));
    (presence || []).forEach((p) => {
      if (p.room === B.room && AGENT.test(p.name)) names.add(p.name);
    });
    const rows = [...names].map((n) => rowState(n, pres, history || [], approvals, now));
    render(rows);
    const members = B.members.length ? B.members : [...names];
    document.dispatchEvent(new CustomEvent("quorus:rows",
      { detail: { rows, history: history || [], room: B.room, members } }));
  });
}

const SPLIT = "(min-width: 1100px)";
if (typeof matchMedia !== "undefined") {  // re-lay out when the window crosses the line
  matchMedia(SPLIT).addEventListener("change", () => { if (B.on) show(true); });
}

function show(on) {
  B.on = on;
  $("board").hidden = !on;
  if (on && !$("boardRows").children.length) {
    mount(html`<p class="hint">Loading the team…</p>`, $("boardRows"));
  }
  // Mission control: on a wide screen the room's chat stays beside the agent
  // panels, so you watch them work and talk to them at once; a phone toggles.
  const wide = typeof matchMedia !== "undefined" && matchMedia(SPLIT).matches;
  ["messages", "working", "composer"].forEach((id) => { $(id).hidden = on && !wide; });
  $("chat").classList.toggle("split", on && wide);
  $("viewToggle").textContent = on ? "Chat" : "Board";
  if (on) { refresh(); if (!B.timer) B.timer = setInterval(refresh, 3000); }
  else if (B.timer) { clearInterval(B.timer); B.timer = null; }
}

function mode(m) {  // "list" or "quorum": two looks over the same rows
  B.mode = m;
  $("boardRows").hidden = m !== "list";
  $("quorum").hidden = m !== "quorum";
  $("quorumCard").hidden = true;
  $("modeList").classList.toggle("on", m === "list");
  $("modeQuorum").classList.toggle("on", m === "quorum");
  $("brief").hidden = m !== "brief";
  $("modeBrief").classList.toggle("on", m === "brief");
  if (m !== "quorum") document.dispatchEvent(new CustomEvent("quorus:quorum-off"));
  refresh();
}

function onRoom(room) {
  B.room = room; B.membersAt = 0; B.cardAt = 0; B.trackAt = 0; B.track = {};
  B.brief.gen++;  // whatever the old room's request returns, it is dropped
  $("brief").textContent = "";
  const view = sessionStorage.getItem("qv");  // `quorus web` lands here (#view=board|quorum)
  if ((view === "board" || view === "quorum") && !B.on) {
    sessionStorage.removeItem("qv");
    show(true);
    if (view === "quorum") mode("quorum");
  } else {
    refresh();
  }
}
document.addEventListener("quorus:room", (e) => onRoom(e.detail));
// Catch-me-up for people (slice 2): a third look next to List and Quorum.
const briefBtn = el("button", "", "Brief");
briefBtn.type = "button";
briefBtn.id = "modeBrief";
briefBtn.dataset.action = "quorus.room.brief";
$("boardModes").appendChild(briefBtn);
const briefBox = el("pre", "brief");
briefBox.id = "brief";
briefBox.hidden = true;
$("board").appendChild(briefBox);
briefBtn.addEventListener("click", () => mode("brief"));
$("viewToggle").addEventListener("click", () => show(!B.on));
$("modeList").addEventListener("click", () => mode("list"));
$("modeQuorum").addEventListener("click", () => mode("quorum"));
window.QuorusBoard = { rowEl };  // the Quorum view's seat card is the board's row
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
// web.js may have opened the room before this module ran (module scripts are deferred).
if (document.documentElement.dataset.room) onRoom(document.documentElement.dataset.room);
