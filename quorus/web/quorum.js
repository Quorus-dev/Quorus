// The Quorum view: a Quorus room as a council table. This file owns the data and
// the HTML (plaques, the seat card, the flat grid); quorum-stage.js draws the lit
// stage and quorum-cast.js the mini agents (light effects via quorum-totems.js), both loaded
// only when the view first opens. Every pose is real state from board.js's rows;
// every click opens the same card (Approve / Deny / ask). No data path of its own.
// v2 2026-10-18 (after Arav: "this looks horrendous"); v3 the same day: premium pass.

const VENDOR = {
  claude: ["#d97757", "Claude Code"], codex: ["#10a37f", "Codex"],
  gemini: ["#4a8cf7", "Gemini CLI"], opencode: ["#9aa4ad", "opencode"],
  cursor: ["#8b5cf6", "Cursor"], cline: ["#e0a030", "Cline"],
};
const HUMAN = "#e8e4da";
const MAX_SEATS = 14;   // past this the marks blur together: the rest go to "+N more"
const PUBLISHED = /\(quorus\) published (\d+) commit/;
const OK = /✅|\bapproved?\b|\bLGTM\b|lifting my block/i;
const BLOCK = /\/disagree|\bblocking\b|\brejected\b|\bnot approved\b/i;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const darkScheme = matchMedia("(prefers-color-scheme: dark)");
// Phones get the same table drawn flat in HTML: no WebGL, no 650 KB download.
const phone = matchMedia("(max-width: 640px)");
const NARROW = 560;     // a host narrower than this can't frame the table either

const Q = { stage: null, T: null, host: null, seats: new Map(), tags: new Map(), layer: null,
            flat: false, names: [], rows: [], room: null, me: "", seen: new Set(), extra: 0,
            offline: 0, on: false, P: null, seatR: 3.62 };

function vendorOf(name) {
  return (name.match(/-(claude|codex|gemini|opencode|cursor|cline)$/) || [])[1] || null;
}
const colourOf = (name) => { const v = vendorOf(name); return v ? VENDOR[v][0] : HUMAN; };

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// Seat plaques are HTML, not canvas sprites: textContent only (never markup from
// room data), focusable buttons for keyboard users, fixed type size, and laid
// out in screen space so a front seat's name can't sit on top of its own or a
// neighbour's status (the overlap in Arav's screenshot).
function tagFor(name) {
  let t = Q.tags.get(name);
  if (t) return t;
  t = document.createElement("button");
  t.type = "button";
  t.className = "qtag";
  const who = document.createElement("b"), model = document.createElement("span");
  const status = document.createElement("span");
  who.textContent = "@" + name;
  model.className = "qm";
  status.className = "qs";
  t.append(who, model, status);
  t.style.setProperty("--seat", colourOf(name));
  t.addEventListener("click", () => pick(name));
  Q.layer.append(t);
  Q.tags.set(name, t);
  return t;
}

function layer(host) {
  if (Q.layer && Q.layer.parentNode === host) return Q.layer;
  Q.layer = document.createElement("div");
  Q.layer.className = "qtags";
  host.append(Q.layer);
  return Q.layer;
}

// Who gets a seat: present members only. An agent is present when the relay
// sees it (rank 1-5); a person when they've spoken in the loaded history or
// are the viewer. Offline agents and silent members go to the note, not to
// empty chairs (Arav: months-old members as white blobs).
function seated(members, rows, history) {
  const rank = new Map(rows.map((r) => [r.name, r.rank]));
  const spoke = new Set((history || []).map((m) => m.from_name));
  const present = members.filter((n) => rank.has(n) ? rank.get(n) !== 6 : (spoke.has(n) || n === Q.me));
  Q.offline = members.length - present.length;
  const order = present.slice().sort((a, b) => a.localeCompare(b));
  Q.extra = Math.max(0, order.length - MAX_SEATS);
  return order.slice(0, MAX_SEATS);
}

function layout(names) {
  Q.names = names;
  [...Q.tags.keys()].forEach((n) => {
    if (!names.includes(n)) { Q.tags.get(n).remove(); Q.tags.delete(n); }
  });
  names.forEach(tagFor);
  if (Q.flat) return;
  [...Q.seats.keys()].forEach((n) => {
    if (!names.includes(n)) { Q.T.disposeAgent(Q.seats.get(n), Q.stage); Q.seats.delete(n); }
  });
  names.forEach((n, i) => {
    let s = Q.seats.get(n);
    if (!s) { s = Q.T.makeAgent(n, vendorOf(n), colourOf(n), Q.P, cssVar("--accent", "#3cc7a2")); Q.stage.add(s, true); Q.seats.set(n, s); }
    const a = (i / names.length) * Math.PI * 2 + Math.PI / 2;
    s.position.set(Math.cos(a) * Q.seatR, 0, Math.sin(a) * Q.seatR);
    s.userData.angle = a;
  });
}

function stateOf(r) {
  return !r ? "human" : r.rank === 1 ? "needs" : r.rank === 2 ? "stuck"
    : r.rank === 4 ? "busy" : r.rank === 6 ? "offline"
    : /quota/.test(r.line) ? "asleep" : /plan slot/.test(r.line) ? "pacing" : "idle";
}

function statusText(r, state) {
  return !r ? "" : state === "needs" ? "✋ needs you"
    : state === "stuck" ? "! stuck"
    : state === "asleep" ? "asleep" + (r.line.includes("back about ")
      ? " · back ~" + r.line.split("back about ")[1] : "")
    : state === "pacing" ? "waiting" + (r.line.includes(" · ") ? " · " + r.line.split(" · ")[1] : "")
    : state === "busy" || state === "offline" ? r.line : "idle";
}

// Rows from board.js → poses. Rank 1 needs you, 2 stuck, 3 waiting, 4 working,
// 5 idle, 6 offline (see board.js rowState).
function apply(rows) {
  const byName = new Map(rows.map((r) => [r.name, r]));
  Q.names.forEach((name) => {
    const r = byName.get(name), state = stateOf(r), v = vendorOf(name);
    const t = tagFor(name);
    t.dataset.state = state;
    // The model tells agents apart (Arav: no vendor logos); else the harness name.
    t.children[1].textContent = r ? (r.model || (v ? VENDOR[v][1] : "")) : "person";
    t.children[2].textContent = statusText(r, state);
    t.setAttribute("aria-label", "@" + name + ", " + (t.children[1].textContent || "")
      + ", " + (t.children[2].textContent || state));
  });
  if (Q.flat) return;
  Q.seats.forEach((s, name) => { s.userData.state = stateOf(byName.get(name)); });
}

// Plaques follow their marks in screen space, every frame while the camera drifts.
// Then nudge any plaque that overlaps one placed before it, front seats first
// (they're nearest, so they keep their spot).
function placeTags() {
  if (Q.flat) {  // phones: the seats as a grid of cards, whoever needs you first
    const rank = new Map(Q.rows.map((r) => [r.name, r.rank]));
    Q.names.slice().sort((a, b) => (rank.get(a) || 5) - (rank.get(b) || 5) || a.localeCompare(b))
      .forEach((n) => Q.layer.append(Q.tags.get(n)));
    return;
  }
  const w = Q.host.clientWidth, h = Q.host.clientHeight;
  const spots = Q.names.filter((n) => Q.seats.has(n)).map((name) => {
    const s = Q.seats.get(name), t = Q.tags.get(name);
    const p = Q.stage.project(s.position.clone().setY(2.25));  // clear of the tallest mark
    return { t, x: p.x, y: p.y, tw: t.offsetWidth, th: t.offsetHeight, behind: p.behind };
  }).sort((a, b) => b.y - a.y);
  const placed = [];
  spots.forEach((p) => {
    p.t.hidden = p.behind;
    if (p.behind) return;
    const left = Math.max(4, Math.min(w - p.tw - 4, p.x - p.tw / 2));
    const clash = (top) => placed.find((q) => left < q.left + q.tw + 4
      && left + p.tw + 4 > q.left && top < q.top + q.th + 4 && top + p.th + 4 > q.top);
    let top = Math.max(4, Math.min(h - p.th - 4, p.y - p.th)), up = true;
    for (let tries = 0, hit; tries < 30 && (hit = clash(top)); tries++) {
      // step above the tag it collided with; at the top edge, go below instead
      if (up && hit.top - p.th - 6 >= 4) top = hit.top - p.th - 6;
      else { up = false; top = hit.top + hit.th + 6; }
    }
    top = Math.min(h - p.th - 4, top);
    p.t.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    placed.push({ left, top, tw: p.tw, th: p.th });
  });
}

function notes() {
  let n = Q.host.querySelector(".qnote");
  if (!n) { n = document.createElement("p"); n.className = "qnote hint"; Q.host.append(n); }
  const parts = [];
  if (!Q.names.some(vendorOf)) {
    parts.push("No agents at this table yet. Add one: quorus agent add claude --room "
      + (Q.room || "<room>") + " (or codex, gemini, opencode)");
  }
  if (Q.offline) parts.push(Q.offline + " offline or away: see List");
  if (Q.extra) parts.push("+" + Q.extra + " more past " + MAX_SEATS + " seats: see List");
  if (Q.flat && !phone.matches) {
    parts.push("Compact view: the 3D table needs a wider screen and WebGL"
      + (Q.flatReason ? " (" + Q.flatReason + ")" : "") + ".");
  }
  n.textContent = parts.join(" · ");
  n.hidden = !n.textContent;
}

// New room messages since last time → one-shot light effects.
function events(history) {
  if (Q.flat) return;
  const fresh = history.filter((m) => m.id && !Q.seen.has(m.id));
  const first = Q.seen.size === 0;
  history.forEach((m) => m.id && Q.seen.add(m.id));
  if (first) return;  // don't replay the whole room on open
  const now = performance.now();
  fresh.forEach((m) => {
    const from = Q.seats.get(m.from_name);
    Q.stage.pulse(0.6);
    if (!from || reduceMotion) return;
    const body = m.content || "";
    if (m.message_type === "social") {
      try {
        const sv = JSON.parse(body);
        const to = Q.seats.get(((sv.payload || {}).to) || "");
        if (sv.verb === "defer" && to) Q.stage.effect(Q.T.arc(Q.stage, from.position, to.position, [Q.T.AMBER], now));
      } catch (e) { /* not a verb */ }
      return;
    }
    if (PUBLISHED.test(body)) { Q.T.celebrate(from); Q.stage.effect(Q.T.burst(Q.stage, from.position, colourOf(m.from_name), now)); }
    const review = OK.test(body) || BLOCK.test(body);
    (body.match(/@[\w-]+/g) || []).forEach((tag) => {
      const to = Q.seats.get(tag.slice(1));
      if (!to || to === from) return;
      Q.T.faceToward(from, to, 6000);
      // A review braids both members' colours; a plain mention is the sender's.
      const colors = review ? [colourOf(m.from_name), colourOf(tag.slice(1))] : [colourOf(m.from_name)];
      Q.stage.effect(Q.T.arc(Q.stage, from.position, to.position, colors, now));
    });
  });
}

function onFrame(t, dt) {
  const accent = cssVar("--accent", "#3cc7a2");
  Q.seats.forEach((s) => Q.T.updateAgent(s, t, dt, reduceMotion, accent, Q.stage));
  placeTags();
}

// A seat's card is the board's own row, on frosted glass; the camera dollies in.
function pick(name) {
  const card = document.getElementById("quorumCard");
  card.replaceChildren();
  const row = name && Q.rows.find((x) => x.name === name);
  const seat = name && Q.seats.get(name);
  if (!row || !window.QuorusBoard) {
    card.hidden = true;
    if (Q.stage) { Q.stage.focus(null); if (reduceMotion) Q.stage.once(); }
    return;
  }
  card.append(window.QuorusBoard.rowEl(row));  // the board's own card, same actions
  card.hidden = false;
  if (Q.stage && seat) { Q.stage.focus(seat.position); if (reduceMotion) Q.stage.once(); }
}

// Two row events can arrive while the stage is still downloading; both must wait
// for the one build, or the scene is built twice (two canvases, seen in the sandbox).
function start(host) {
  if (!Q.starting) Q.starting = boot(host);
  return Q.starting.then(() => { Q.on = true; });
}

async function boot(host) {
  layer(host);
  if (!Q.host) {
    Q.host = host;
    new ResizeObserver(() => { if (Q.stage) Q.stage.resize(); if (Q.names.length) placeTags(); }).observe(host);
  }
  // A host measured at 0 isn't narrow, it isn't laid out yet: the resize observer sizes it.
  const narrow = host.clientWidth > 0 && host.clientWidth < NARROW;
  if (phone.matches || narrow) {
    Q.flatReason = phone.matches ? "" : "host is " + host.clientWidth + "px wide";
    return goFlat(host);
  }
  try {
    const [S, T] = await Promise.all([import("/quorum-stage.js"), import("/quorum-cast.js")]);
    Q.T = T;
    Q.seatR = S.SEAT_R;
    Q.P = S.palette(darkScheme.matches, cssVar("--accent", "#3cc7a2"), cssVar("--bg", "#0f1412"));
    Q.stage = S.create(host, Q.P, { onFrame, onPick: pick, reduceMotion });
  } catch (err) {  // no WebGL (or the library failed): the flat table still works
    Q.flatReason = String(err && err.message || err);
    console.error("quorum: 3D view unavailable", err);
    return goFlat(host);
  }
}

function goFlat(host) {
  Q.flat = true;
  host.classList.add("flat");
}

document.addEventListener("quorus:rows", (e) => {
  const { rows, history, room, members } = e.detail;
  Q.rows = rows;
  Q.me = sessionStorage.getItem("qn") || "";
  const host = document.getElementById("quorum");
  if (!host || host.hidden) return;
  start(host).then(() => {
    if (room !== Q.room) { Q.room = room; Q.seen = new Set(); }
    layout(seated(members, rows, history));
    apply(rows);
    events(history);
    notes();
    placeTags();
    // Reduced motion: a static premium still, redrawn only when the data changes.
    if (!Q.flat) { if (reduceMotion) Q.stage.once(); else Q.stage.start(); }
  }).catch((err) => {  // never a blank panel: say what failed
    host.replaceChildren(Object.assign(document.createElement("p"),
      { className: "hint", textContent: "The Quorum view failed: " + err.message
        + " " + (err.stack || "").split("\n").slice(0, 3).join(" | ") }));
  });
});
document.addEventListener("quorus:quorum-off", () => { Q.on = false; if (Q.stage) Q.stage.stop(); });
document.addEventListener("keydown", (e) => {  // Esc closes a seat's card
  if (e.key === "Escape") pick("");
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Q.on && Q.stage && !reduceMotion) Q.stage.start();
});
