"""The "needs you" board: one row per agent, sorted by what needs a human (2026-10-17).

Arav asked for a live view of the team; the room's agents argued (and won)
that an animated office is decoration others already ship, while nobody
answers "who is blocked on me, who is stuck, what shipped?" in two seconds
with a button next to each answer. Every field comes from relay data the
daemons already send (presence ``activity`` / ``waiting_on``, room history,
pending approvals). Approve/Deny only work with the viewer's own participant
token, never the shared secret.
"""

from __future__ import annotations

from fastapi import APIRouter
from fastapi.responses import Response

router = APIRouter()

BOARD_JS = r"""
"use strict";
(() => {
const $ = (id) => document.getElementById(id);
const enc = encodeURIComponent;  // every path segment from data goes through this
const AGENT = /-(claude|codex|gemini|opencode|cursor|cline)$/;
const B = { room: null, timer: null, members: [], membersAt: 0, since: {}, on: false };
const token = () => sessionStorage.getItem("qt") || "";
const me = () => sessionStorage.getItem("qn") || "";
// A participant JWT has three dot-separated parts; the shared secret doesn't.
// Approvals are never decided with the shared secret (team decision, 2026-10-17).
const ownKey = () => token().split(".").length === 3;

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
// "@name" not followed by more name characters ("@arav" isn't "@arav-codex").
const mention = (name) =>
  new RegExp("(^|[^\\w-])@" + name.replace(/[^\w-]/g, "") + "(?![\\w-])");
function ago(sec) {
  sec = Math.max(0, Math.round(sec));
  if (sec < 60) return sec + "s";
  if (sec < 3600) return Math.floor(sec / 60) + "m";
  return Math.floor(sec / 3600) + "h " + Math.floor((sec % 3600) / 60) + "m";
}
function clock(epoch) {
  return new Date(epoch * 1000).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}
const ts = (m) => Date.parse(m.timestamp || "") / 1000 || 0;
const PUBLISHED = /\(quorus\) published (\d+) commit\(s\) from quorus\/([\w-]+) to ([\w./-]+)/;
const OK = /✅|\bapproved?\b|\bLGTM\b|lifting my block/i;
const BLOCK = /\/disagree|\bblocking\b|\brejected\b|\bnot approved\b/i;

// One agent's row, from presence + history + approvals. Pure: easy to reason about.
function rowState(name, presence, history, approvals, now) {
  const p = presence[name] || { online: false, status: "offline" };
  const mine = history.filter((m) => m.from_name === name);
  const r = { name, vendor: (name.match(AGENT) || [, ""])[1], rank: 5, line: "", detail: "",
              asks: [], approvals: approvals.filter((a) => a.agent === name) };
  const myName = me();
  const ask = mine.slice().reverse().find((m) => myName && m.message_type !== "status"
    && mention(myName).test(m.content || "")
    && /\?|please (approve|confirm|decide)|need (your )?(approval|ok|decision)|i'?m blocked/i
       .test(m.content || ""));
  const answered = ask && history.some((m) => m.from_name === myName && ts(m) > ts(ask));
  if (ask && !answered) r.asks.push((ask.content || "").split("\n")[0].slice(0, 160));
  if (!p.online) { r.rank = 6; r.line = "offline"; }
  else if (p.status === "busy") {
    r.rank = 4;
    const since = B.since[name] || now;
    r.line = (p.activity || "working") + " · " + ago(now - since);
  } else if (p.waiting_on === "stuck") {
    r.rank = 2;
    const last = mine.slice().reverse().find((m) => /^⚠/.test(m.content || ""));
    r.line = "stuck" + (last ? " since " + clock(ts(last)) : "");
    if (last) r.detail = (last.content || "").split("\n")[0].slice(0, 160);
  } else if (p.waiting_on === "quota") {
    r.rank = 3;
    r.line = "out of quota" + (p.waiting_until ? " · back about " + clock(p.waiting_until) : "");
  } else if (p.waiting_on === "pacing") {
    r.rank = 3;
    r.line = "waiting for its plan slot"
      + (p.waiting_until ? " · next turn in " + ago(p.waiting_until - now) : "");
  } else { r.rank = 5; r.line = "idle"; }
  if (r.approvals.length || r.asks.length) r.rank = 1;
  const pub = mine.slice().reverse().find((m) => PUBLISHED.test(m.content || ""));
  if (pub) {
    const [, n, , to] = (pub.content || "").match(PUBLISHED);
    r.shipped = "shipped " + n + " commit(s) to " + to + " · " + ago(now - ts(pub)) + " ago";
    const verdict = history.slice().reverse().find((m) => m.from_name !== name
      && ts(m) > ts(pub) && (m.content || "").includes("@" + name)
      && (OK.test(m.content || "") || BLOCK.test(m.content || "")));
    if (verdict) {
      r.review = (BLOCK.test(verdict.content) ? "blocked by @" : "approved by @")
        + verdict.from_name;
      r.reviewBad = BLOCK.test(verdict.content);
    }
  }
  return r;
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

function render(rows) {
  const counts = { 1: 0, 2: 0, 3: 0, 4: 0 };
  rows.forEach((r) => { if (counts[r.rank] !== undefined) counts[r.rank] += 1; });
  const sum = $("boardSummary");
  sum.replaceChildren();
  [[1, "need you"], [2, "stuck"], [3, "waiting"], [4, "working"]].forEach(([k, label]) => {
    const c = el("span", "chip" + (k === 1 && counts[k] ? " hot" : ""), counts[k] + " " + label);
    sum.append(c);
  });
  const list = $("boardRows");
  list.replaceChildren();
  rows.sort((a, b) => a.rank - b.rank || a.name.localeCompare(b.name)).forEach((r) => {
    const row = el("article", "brow r" + r.rank);
    const head = el("div", "bhead");
    head.append(el("b", "", "@" + r.name), el("span", "tag", r.vendor),
                el("span", "bline", r.line));
    row.append(head);
    if (r.detail) row.append(el("div", "bdetail", r.detail));
    r.asks.forEach((a) => row.append(el("div", "bask", "asked you: " + a)));
    r.approvals.forEach((a) => {
      const box = el("div", "bapproval");
      box.append(el("span", "", "wants to run " + (a.tool_name || "a tool") + ": "
        + String(a.input_preview || "").slice(0, 120)));
      if (ownKey()) {
        const yes = el("button", "ok", "Approve"), no = el("button", "no", "Deny");
        yes.type = no.type = "button";
        yes.addEventListener("click", () => decide(a.id, true, yes));
        no.addEventListener("click", () => decide(a.id, false, no));
        box.append(yes, no);
      } else {
        box.append(el("code", "", "quorus approve " + a.id));
      }
      row.append(box);
    });
    if (r.shipped) {
      const s = el("div", "bship", r.shipped);
      if (r.review) s.append(el("span", r.reviewBad ? "bad" : "good", " · " + r.review));
      row.append(s);
    }
    const form = el("form", "bassign");
    const input = el("input");
    input.placeholder = "Ask or assign @" + r.name + "…";
    input.autocomplete = "off";
    form.append(input);
    form.addEventListener("submit", (e) => { e.preventDefault(); ask(r.name, input); });
    row.append(form);
    list.append(row);
  });
  if (!rows.length) list.append(el("p", "hint", "No agents in this room yet: quorus agent add …"));
}

function refresh() {
  if (!B.on || !B.room) return Promise.resolve();
  const now = Date.now() / 1000;
  const members = now - B.membersAt > 30
    ? api("/rooms/" + enc(B.room)).then((r) => { B.members = r.members || []; B.membersAt = now; })
        .catch(() => {})
    : Promise.resolve();
  return members.then(() => Promise.all([
    api("/presence").catch(() => []),
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
    render([...names].map((n) => rowState(n, pres, history || [], approvals, now)));
  });
}

function show(on) {
  B.on = on;
  $("board").hidden = !on;
  ["messages", "working", "composer"].forEach((id) => { $(id).hidden = on; });
  $("viewToggle").textContent = on ? "Chat" : "Board";
  if (on) { refresh(); if (!B.timer) B.timer = setInterval(refresh, 3000); }
  else if (B.timer) { clearInterval(B.timer); B.timer = null; }
}

if (typeof module !== "undefined") {  // node tests only; browsers have no `module`
  module.exports = { rowState, B };
  return;
}
document.addEventListener("quorus:room", (e) => { B.room = e.detail; B.membersAt = 0; refresh(); });
$("viewToggle").addEventListener("click", () => show(!B.on));
})();
"""


@router.get("/board.js", include_in_schema=False)
async def board_js() -> Response:
    """The board's script, same-origin so the CSP stays ``script-src 'self'``."""
    return Response(BOARD_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})
