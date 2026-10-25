// The "needs you" board: one row per agent, sorted by what needs a human (2026-10-17).
// This module renders; the row logic is rows.js (pure, node-tested). Preact + htm are
// vendored ESM served same-origin, so the relay CSP stays script-src 'self' with no build
// step and no eval (docs/plans/board-inbox.md, slice 2). Data only ever becomes text:
// htm interpolations are text nodes, and nothing here writes markup from data.
import { h, render as mount } from "/vendor/preact.module.js";
import htm from "/vendor/htm.module.js";
import { AGENT, B, ago, briefTick, clock, enc, me, ownKey, rowState, token, trackOwner }
  from "/rows.js";
import { HISTORY_LIMIT, historyWindowFull, inboxItems } from "/inbox.js";

const html = htm.bind(h);
const $ = (id) => document.getElementById(id);
// The inbox view (slice 4): what needs the viewer, one row each, driven by the keyboard.
B.mode = "inbox"; B.items = []; B.rows = []; B.sel = 0; B.openRef = null; B.help = false;
B.note = ""; B.full = false; B.quorum = null;
B.diffs = {};  // sha -> { lines } | { missing: true } | { pending: true } (slice 5)

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
  if (btn) btn.disabled = true;
  api("/v1/approvals/" + enc(id) + "/decision", { method: "POST",
    // No caller-supplied decider: the relay takes it from the viewer's own JWT
    // (Codex, 2026-10-17). Buttons only exist when ownKey() is true.
    body: JSON.stringify({ approve, reason: approve ? "" : "denied from the board" }) })
    .then(refresh)
    .catch((e) => {
      if (btn) { btn.disabled = false; btn.textContent = "failed (" + e.message + ")"; }
      B.note = "failed (" + e.message + ")"; renderInbox();
    });
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
      <input placeholder=${"Ask or assign @" + r.name + "…"} aria-label=${"Ask or assign @" + r.name}
             autocomplete="off" />
    </form>
  </article>`;
}

// ── the inbox ─────────────────────────────────────────────────────────────────
// Status is never colour alone: a glyph and the kind's word on every row.
const GLYPH = { approval: "!", block: "✗", review: "⟳", hold: "⏸", question: "?" };
const KEYS = [["j / k", "next / previous"], ["enter", "open or close the item"],
  ["a", "approve (an approval, or vote approve on a review or block)"],
  ["d", "deny an approval, or /disagree advisory on a review"],
  ["r", "reply: the composer, addressed to them"], ["l", "the agents list"],
  ["esc", "close"], ["?", "these keys"]];

// A social verb from the viewer, bound to their own JWT by the relay (routes/social.py).
function verb(name, ref, payload) {
  return api("/v1/social/" + name, { method: "POST",
    // room_id, not room: routes/social.py:233 setdefault()s over the body's explicit null.
    body: JSON.stringify({ actor: me(), room_id: B.room, ref_message_id: ref, payload }) })
    .then(refresh)
    .catch((e) => { B.note = "failed (" + e.message + ")"; renderInbox(); });
}

function reply(who) {
  if ($("composer").hidden) show(false);  // a phone: back to the chat
  const input = $("msgInput");
  input.value = "@" + who + " ";
  input.focus();
}

// `what` is one of the item's own actions (inbox.js): approve | deny | vote | disagree |
// reply. A prose block is reply-only, so `a` and `d` do nothing on it (Codex on bb78c474).
function act(it, what) {
  const can = (it.actions || []).includes(what);
  if (what === "reply") return can ? reply(it.who) : undefined;
  if (!can || !ownKey()) return undefined;  // the shared secret decides nothing
  if (what === "approve" || what === "deny") return decide(it.ref, what === "approve", null);
  if (what === "vote") {
    // An explicit poll: an unrelated active block must never capture this vote
    // (social_svc.py:389-404). A block item votes in the block's own poll.
    const ref = it.kind === "block" ? it.poll.slice("vote_".length) : it.ref;
    return ref ? verb("vote", ref, { option: "approve", poll_id: "vote_" + ref }) : undefined;
  }
  if (what === "disagree") {
    const reason = window.prompt("Why? Posted to the room as /disagree advisory.");
    if (reason) {
      return verb("disagree", it.ref, { ref_message_id: it.ref, reason, mode: "advisory" });
    }
  }
  return undefined;
}
// The keys: `a` is the item's approve or vote, `d` its deny or disagree, whichever it has.
const KEY_ACTION = { a: ["approve", "vote"], d: ["deny", "disagree"], r: ["reply"] };
function keyAction(it, key) {
  return (KEY_ACTION[key] || []).find((w) => (it.actions || []).includes(w));
}

// The commit beside the vote (slice 5): GET /v1/rooms/{room}/commits/{sha}/diff, the
// viewer's own JWT, text only. Each line becomes one text node in a <span>; nothing is
// ever parsed as HTML. A 404 (no repo bound here, or not a commit) shows the command.
function loadDiff(sha) {
  if (!sha || !B.room || B.diffs[sha]) return;
  B.diffs[sha] = { pending: true };
  fetch("/v1/rooms/" + enc(B.room) + "/commits/" + enc(sha) + "/diff",
        { headers: { Authorization: "Bearer " + token() } })
    .then((r) => (r.ok ? r.text() : Promise.reject(new Error("HTTP " + r.status))))
    .then((text) => { B.diffs[sha] = { lines: text.split("\n") }; renderInbox(); })
    .catch(() => { B.diffs[sha] = { missing: true }; renderInbox(); });
}
const lineClass = (l) => (l.startsWith("+++") || l.startsWith("---")) ? "file"
  : l.startsWith("+") ? "add" : l.startsWith("-") ? "del" : l.startsWith("@@") ? "hunk" : "";
function Diff({ sha }) {
  const d = B.diffs[sha] || {};
  if (d.pending) return html`<p class="hint">loading the diff…</p>`;
  if (d.missing || !d.lines) {
    return html`<p class="hint">diff not available here · <code>${"git show " + sha}</code></p>`;
  }
  return html`<pre class="ibdiff">${d.lines.map((l) => html`<span class=${lineClass(l)}>${l}
</span>`)}</pre>`;
}

function Card({ it }) {
  // Each button is a literal with its registry id (quorus/actions/registry.py
  // BOARD_CONTROLS), so the action-registry test can read label and binding from the file.
  const on = (what) => (e) => { e.stopPropagation(); act(it, what); };
  // One button per action the item carries (inbox.js decides; a prose block is reply-only).
  const BUTTON = {
    approve: html`<button type="button" class="ok" data-action="quorus.approval.decide"
      onClick=${on("approve")}>Approve</button>`,
    deny: html`<button type="button" data-action="quorus.approval.decide"
      onClick=${on("deny")}>Deny</button>`,
    vote: html`<button type="button" class="ok" data-action="quorus.social.vote"
      onClick=${on("vote")}>Vote approve</button>`,
    disagree: html`<button type="button" data-action="quorus.social.disagree"
      onClick=${on("disagree")}>Disagree</button>`,
    reply: html`<button type="button" data-action="quorus.message.say"
      onClick=${on("reply")}>Reply</button>`,
  };
  const buttons = (it.actions || []).map((what) => BUTTON[what]).filter(Boolean);
  const cli = it.kind === "approval" ? "quorus approve " + it.ref : "quorus web";
  const withDiff = (it.kind === "review" || it.kind === "block") && it.sha && ownKey();
  if (withDiff) loadDiff(it.sha);
  return html`<div class=${"ibcard" + (withDiff ? " withdiff" : "")}>
    ${withDiff && html`<${Diff} sha=${it.sha} />`}
    <div class="ibverdict">
      <span class="ibtext">${it.what}</span>
      ${ownKey() ? buttons : html`<code>${cli}</code>`}
      ${B.note && html`<span class="bad">${B.note}</span>`}
    </div>
  </div>`;
}

function Inbox({ items, rows, full }) {
  if (!items.length) {
    const working = rows.filter((r) => r.rank === 4).length;
    return html`<p class="hint ibempty">Nothing needs you · ${working + " agent"
      + (working === 1 ? "" : "s") + " working"} · <kbd>l</kbd> agents · <kbd>?</kbd> keys</p>`;
  }
  const sel = Math.min(B.sel, items.length - 1);
  return html`<ol class="inbox" id="inboxList" role="listbox" aria-label="Needs you" tabindex="0"
      aria-activedescendant=${"ib-" + sel} onKeyDown=${inboxKeys}>
    ${items.map((it, i) => html`<li id=${"ib-" + i} role="option" aria-selected=${i === sel}
        key=${it.kind + ":" + it.ref} class=${"ibrow " + it.kind + (i === sel ? " sel" : "")}
        onClick=${() => {
          B.sel = i; B.openRef = B.openRef === it.ref ? null : it.ref; renderInbox();
        }}>
      <span class=${"ibkind " + it.kind}>${GLYPH[it.kind] + " " + it.kind}</span>
      <b class="ibwho">${"@" + it.who}</b>
      <span class="ibwhat">${it.what}</span>
      ${it.sha && html`<code class="ibsha">${it.sha}</code>`}
      <time class="ibage">${ago(it.age)}</time>
      ${B.openRef === it.ref && html`<${Card} it=${it} />`}
    </li>`)}
    ${full && html`<li class="hint" role="presentation">${"older items not shown (the last "
      + HISTORY_LIMIT + " messages)"}</li>`}
  </ol>`;
}

function inboxKeys(e) {
  if (e.target.tagName === "INPUT" || e.target.tagName === "BUTTON") return;
  const items = B.items, it = items[Math.min(B.sel, items.length - 1)];
  const k = e.key;
  if (k === "j" || k === "ArrowDown") B.sel = Math.min(B.sel + 1, items.length - 1);
  else if (k === "k" || k === "ArrowUp") B.sel = Math.max(B.sel - 1, 0);
  else if (k === "Enter") B.openRef = it && B.openRef !== it.ref ? it.ref : null;
  else if (k === "Escape") { B.openRef = null; B.help = false; }
  else if ((k === "a" || k === "d" || k === "r") && it) {
    const what = keyAction(it, k);
    if (!what) return;  // not one of this item's actions: the key does nothing here
    act(it, what);
  }
  else if (k === "?") B.help = !B.help;
  else if (k === "l") { mode("list"); return; }
  else return;
  e.preventDefault();
  renderInbox();
}

function renderInbox() {
  const counts = {};
  B.items.forEach((it) => { counts[it.kind] = (counts[it.kind] || 0) + 1; });
  mount(Object.keys(GLYPH).filter((k) => counts[k]).map((k) =>
    html`<span class=${"chip" + (k === "approval" || k === "block" ? " hot" : "")}>
      ${counts[k] + " " + k + (counts[k] === 1 ? "" : "s")}</span>`), $("boardSummary"));
  mount(html`${B.help && html`<dl class="ibhelp">${KEYS.map(([key, what]) =>
      html`<dt><kbd>${key}</kbd></dt><dd>${what}</dd>`)}</dl>`}
    <${Inbox} items=${B.items} rows=${B.rows} full=${B.full} />`, $("inbox"));
}

// ── the agents list ───────────────────────────────────────────────────────────
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
    api("/rooms/" + enc(B.room) + "/history?limit=" + HISTORY_LIMIT).catch(() => []),
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
    B.rows = rows;
    if (B.mode === "inbox") {
      B.note = "";
      const keep = B.items[B.sel] && B.items[B.sel].ref;
      B.items = inboxItems(history || [], approvals, me(), now, B.members.length || names.size);
      B.full = historyWindowFull(history || []);
      const i = B.items.findIndex((it) => it.ref === keep);
      B.sel = i >= 0 ? i : Math.min(B.sel, Math.max(0, B.items.length - 1));
      renderInbox();
    } else {
      render(rows);
    }
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
    mount(html`<p class="hint">Loading the team…</p>`, $("inbox"));
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

function mode(m) {  // "inbox" (default), "list", "quorum" or "brief": looks over the same data
  B.mode = m;
  $("inbox").hidden = m !== "inbox";
  $("boardRows").hidden = m !== "list";
  $("quorum").hidden = m !== "quorum";
  $("quorumCard").hidden = true;
  [["modeInbox", "inbox"], ["modeList", "list"], ["modeQuorum", "quorum"], ["modeBrief", "brief"]]
    .forEach(([id, name]) => $(id).classList.toggle("on", m === name));
  $("brief").hidden = m !== "brief";
  const menu = $("boardModes").querySelector(".viewmenu");
  if (menu) menu.open = false;
  if (m !== "quorum") document.dispatchEvent(new CustomEvent("quorus:quorum-off"));
  // The council table (quorum.js and three.js) is opt-in: its code loads on the first
  // request, never on page load (board-inbox.md decision 5).
  if (m === "quorum" && !B.quorum) B.quorum = import("/quorum.js").catch(() => null);
  (m === "quorum" && B.quorum ? B.quorum : Promise.resolve()).then(refresh);
}

function onRoom(room) {
  B.room = room; B.membersAt = 0; B.cardAt = 0; B.trackAt = 0; B.track = {};
  B.brief.gen++;  // whatever the old room's request returns, it is dropped
  $("brief").textContent = "";
  // `quorus web` lands here: #view=board or inbox (the default), list, or quorum.
  const view = sessionStorage.getItem("qv");
  if (["board", "inbox", "list", "quorum"].includes(view) && !B.on) {
    sessionStorage.removeItem("qv");
    show(true);
    if (view === "quorum") mode("quorum");
    else if (view === "list") mode("list");
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
$("boardModes").insertBefore(briefBtn, $("boardModes").querySelector(".viewmenu"));
const briefBox = el("pre", "brief");
briefBox.id = "brief";
briefBox.hidden = true;
$("board").appendChild(briefBox);
briefBtn.addEventListener("click", () => mode("brief"));
$("viewToggle").addEventListener("click", () => show(!B.on));
$("modeInbox").addEventListener("click", () => mode("inbox"));
$("modeList").addEventListener("click", () => mode("list"));
$("modeQuorum").addEventListener("click", () => mode("quorum"));
window.QuorusBoard = { rowEl };  // the Quorum view's seat card is the board's row
document.addEventListener("visibilitychange", () => { if (!document.hidden) refresh(); });
// web.js may have opened the room before this module ran (module scripts are deferred).
if (document.documentElement.dataset.room) onRoom(document.documentElement.dataset.room);
