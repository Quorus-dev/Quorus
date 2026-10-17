"""Quorus web: your rooms on a phone or laptop browser, served by the relay at ``/``.

Rewritten 2026-10-17. The old page never ran: the relay's own CSP
(``script-src 'self'``) blocked its inline script and every inline ``onclick``,
and it had latent XSS (``esc()`` kept quotes; ``location.hash`` went into fetch
paths). The rewrite keeps the CSP tight instead of loosening it:

* no inline script or handler anywhere: the page loads ``/web.js`` (same
  origin) and wires events with ``addEventListener`` and delegation;
* message text is only ever set with ``textContent`` (never ``innerHTML``);
* the token comes from the URL fragment (``#token=``), never the query
  string, and is stripped from history straight away;
* it's mobile first, with the "working…" line, quiet status lines and a send box.
"""

from fastapi import APIRouter
from fastapi.responses import HTMLResponse, Response

router = APIRouter()

DASHBOARD_HTML = """\
<!DOCTYPE html>
<html lang="en">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1, viewport-fit=cover">
<!-- Admin surface (2026-05-16 audit B6): never index. -->
<meta name="robots" content="noindex, nofollow, noarchive">
<meta name="googlebot" content="noindex, nofollow, noarchive">
<meta name="referrer" content="no-referrer">
<title>Quorus</title>
<style>
:root{--bg:#f5f6f4;--panel:#fff;--ink:#17201c;--muted:#5f6b65;--rule:#dfe4e1;
--accent:#0f7a63;--me:#e3f1ec;--warn:#9a5b00;--bad:#b3261e;--chip:#eef2f0;
--font:-apple-system,BlinkMacSystemFont,"Segoe UI",Roboto,sans-serif;
--mono:ui-monospace,SFMono-Regular,Menlo,monospace;color-scheme:light}
@media (prefers-color-scheme:dark){:root{--bg:#0f1412;--panel:#161d1a;--ink:#e5ebe8;
--muted:#93a29b;--rule:#27322e;--accent:#3cc7a2;--me:#1d2e28;--warn:#e8a63a;--bad:#f2776e;
--chip:#1e2925;color-scheme:dark}}
*{box-sizing:border-box}html,body{height:100%}[hidden]{display:none!important}
body{margin:0;background:var(--bg);color:var(--ink);font:15px/1.45 var(--font)}
#app{display:grid;grid-template-columns:260px 1fr;grid-template-rows:minmax(0,1fr);height:100vh;height:100dvh;overflow:hidden}
#rooms{border-right:1px solid var(--rule);background:var(--panel);overflow-y:auto;
padding:calc(env(safe-area-inset-top,0px) + 12px) 12px 12px}
#rooms h1{font-size:17px;margin:4px 6px 12px;display:flex;align-items:center;gap:8px}
#connDot{width:8px;height:8px;border-radius:50%;background:var(--muted)}
#connDot.conn-ok{background:var(--accent)}#connDot.conn-err{background:var(--bad)}
.room{display:block;width:100%;text-align:left;background:none;border:0;color:var(--ink);
padding:10px;border-radius:8px;font:inherit;cursor:pointer}
.room:hover,.room.on{background:var(--chip)}.room small{color:var(--muted);display:block}
#newRoom{margin:10px 6px;font:inherit;color:var(--accent);background:none;border:0;cursor:pointer}
#chat{display:flex;flex-direction:column;min-width:0;min-height:0;height:100%}
#bar{display:flex;align-items:center;gap:10px;padding:calc(env(safe-area-inset-top,0px) + 10px) 16px 10px;
border-bottom:1px solid var(--rule);background:var(--panel)}
#bar h2{font-size:16px;margin:0;flex:1;min-width:0;overflow:hidden;text-overflow:ellipsis}
#back{display:none;background:none;border:0;color:var(--accent);font:inherit;cursor:pointer}
#who{font:12px var(--mono);color:var(--muted)}
#messages{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:6px}
.msg{max-width:min(680px,88%);background:var(--panel);border:1px solid var(--rule);
border-radius:12px;padding:8px 12px;align-self:flex-start;white-space:pre-wrap;overflow-wrap:anywhere}
.msg.me{align-self:flex-end;background:var(--me)}
.msg b{display:block;font-size:12px;color:var(--accent);margin-bottom:2px}
.msg time{font:11px var(--mono);color:var(--muted);margin-left:6px}
.status{align-self:center;color:var(--muted);font-size:12px;font-style:italic;text-align:center}
#working{min-height:20px;padding:0 16px;color:var(--muted);font-size:13px;font-style:italic}
#composer{display:flex;gap:8px;padding:10px 12px calc(env(safe-area-inset-bottom,0px) + 10px);
border-top:1px solid var(--rule);background:var(--panel)}
#msgInput{flex:1;min-width:0;font:inherit;padding:10px 12px;border-radius:10px;
border:1px solid var(--rule);background:var(--bg);color:var(--ink)}
#send{font:inherit;padding:10px 16px;border-radius:10px;border:0;background:var(--accent);color:#fff}
#signin{max-width:360px;margin:15vh auto;padding:0 16px;display:grid;gap:10px}
#signin input{font:inherit;padding:10px;border-radius:8px;border:1px solid var(--rule);
background:var(--panel);color:var(--ink)}
#signin button{font:inherit;padding:10px;border-radius:8px;border:0;background:var(--accent);color:#fff}
.hint{color:var(--muted);font-size:13px}#err{color:var(--bad);font-size:13px}
#viewToggle{font:inherit;font-size:13px;padding:6px 12px;border-radius:999px;border:1px solid var(--rule);
background:var(--chip);color:var(--ink);cursor:pointer}
#board{flex:1;overflow-y:auto;padding:12px 16px;display:flex;flex-direction:column;gap:10px}
#boardTop{display:flex;flex-wrap:wrap;justify-content:space-between;gap:8px;align-items:center}
#boardSummary{display:flex;flex-wrap:wrap;gap:6px}
#boardModes{display:flex;gap:4px}#boardModes button{font:inherit;font-size:12px;padding:4px 12px;
border-radius:999px;border:1px solid var(--rule);background:none;color:var(--muted);cursor:pointer}
#boardModes button.on{background:var(--chip);color:var(--ink)}
#quorum{position:relative;flex:1;min-height:420px;border-radius:12px;overflow:hidden;
border:1px solid var(--rule)}#quorum canvas{display:block;width:100%;height:100%}
#board{position:relative}
#quorumCard{position:absolute;right:28px;top:110px;width:min(440px,calc(100% - 56px));z-index:2;
box-shadow:0 12px 40px rgba(0,0,0,.35);border-radius:12px}
.chip{font-size:12px;padding:4px 10px;border-radius:999px;background:var(--chip);color:var(--muted)}
.chip.hot{background:var(--warn);color:#fff}
.brow{background:var(--panel);border:1px solid var(--rule);border-left:4px solid var(--rule);
border-radius:10px;padding:10px 12px;display:grid;gap:6px}
.brow.r1{border-left-color:var(--warn)}.brow.r2{border-left-color:var(--bad)}
.brow.r4{border-left-color:var(--accent)}.brow.r6{opacity:.6}
.bhead{display:flex;flex-wrap:wrap;align-items:baseline;gap:8px}
.bhead .tag{font:11px var(--mono);color:var(--muted)}.bline{color:var(--ink)}
.bdetail,.bship{font-size:13px;color:var(--muted);overflow-wrap:anywhere}
.bask,.bapproval{font-size:13px;background:var(--chip);border-radius:8px;padding:6px 8px;
display:flex;flex-wrap:wrap;gap:8px;align-items:center;overflow-wrap:anywhere}
.bapproval button{font:inherit;font-size:13px;padding:4px 12px;border-radius:8px;border:0;cursor:pointer}
.bapproval .ok{background:var(--accent);color:#fff}.bapproval .no{background:var(--rule);color:var(--ink)}
.good{color:var(--accent)}.bad{color:var(--bad)}
.bassign input{width:100%;font:inherit;font-size:13px;padding:6px 10px;border-radius:8px;
border:1px solid var(--rule);background:var(--bg);color:var(--ink)}
@media (max-width:720px){#app{grid-template-columns:1fr}#app.inroom #rooms{display:none}
#app:not(.inroom) #chat{display:none}#back{display:block}}
</style>
</head>
<body>
<form id="signin" hidden>
  <h1>Quorus</h1>
  <p class="hint">Open the link from <code>quorus phone</code>, or sign in with your relay secret.</p>
  <input id="siName" placeholder="Your name" autocomplete="username" required>
  <input id="siToken" placeholder="Relay secret or API token" type="password"
         autocomplete="current-password" required>
  <button type="submit">Sign in</button><div id="err"></div>
</form>
<div id="app" hidden>
  <nav id="rooms"><h1><span id="connDot" title="connection"></span>Quorus</h1>
    <div id="roomList"></div><button id="newRoom" type="button">+ New room</button></nav>
  <main id="chat">
    <div id="bar"><button id="back" type="button">‹ Rooms</button><h2 id="roomName">Pick a room</h2>
      <button id="viewToggle" type="button">Board</button><span id="who"></span></div>
    <section id="board" hidden aria-live="polite">
      <div id="boardTop"><div id="boardSummary"></div>
        <div id="boardModes"><button id="modeList" class="on" type="button">List</button>
          <button id="modeQuorum" type="button">Quorum</button></div></div>
      <div id="boardRows"></div>
      <div id="quorum" hidden></div><div id="quorumCard" hidden></div></section>
    <div id="messages" aria-live="polite"></div>
    <div id="working"></div>
    <form id="composer"><input id="msgInput" placeholder="Message the room…" autocomplete="off">
      <button id="send" type="submit">Send</button></form>
  </main>
</div>
<script src="/web.js"></script>
<script src="/board.js"></script>
<script type="module" src="/quorum.js"></script>
</body>
</html>
"""

DASHBOARD_JS = r"""
"use strict";
(() => {
const $ = (id) => document.getElementById(id);
const S = { token: "", name: "", room: null, seen: new Set(), workSince: {}, timer: null };

// Token from the URL FRAGMENT (#token=…), never the query string: fragments
// never reach the server, so logs, proxies and Referer can't capture it.
const frag = new URLSearchParams(location.hash.slice(1));
if (frag.get("token")) {
  sessionStorage.setItem("qt", frag.get("token"));
  sessionStorage.setItem("qn", frag.get("name") || "web");
}
// `quorus web` can name the room and the view to land on.
if (frag.get("room")) sessionStorage.setItem("qr", frag.get("room"));
if (frag.get("view")) sessionStorage.setItem("qv", frag.get("view"));
if (new URLSearchParams(location.search).has("token")) {
  console.warn("Quorus: ?token= in a URL is deprecated and leaks via server logs. Use #token=.");
}
history.replaceState(null, "", location.pathname);  // strip credentials from history
S.token = sessionStorage.getItem("qt") || "";
S.name = sessionStorage.getItem("qn") || "";

function api(path, opts) {
  const o = Object.assign({ headers: {} }, opts || {});
  o.headers = Object.assign({ Authorization: "Bearer " + S.token,
                              "Content-Type": "application/json" }, o.headers);
  return fetch(path, o).then((r) => {
    if (r.status === 401 || r.status === 403) throw new Error("auth");
    if (!r.ok) throw new Error("HTTP " + r.status);
    return r.json();
  });
}
const enc = encodeURIComponent;  // every path segment from data goes through this

function el(tag, cls, text) {  // textContent only: no HTML from data, ever
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text != null) e.textContent = String(text);
  return e;
}
function plain(md) {  // agent markdown → readable text (rendered as text, never HTML)
  return String(md || "").replace(/\*\*(?=\S)(.+?)(?<=\S)\*\*/g, "$1")
    .replace(/`([^`\n]+)`/g, "$1").replace(/^\s{0,3}#{1,6}\s+/gm, "")
    .replace(/^(\s*)[-*+]\s+/gm, "$1• ");
}
function conn(ok) { $("connDot").className = ok ? "conn-ok" : "conn-err"; }
function hhmm(ts) { const d = new Date(ts); return isNaN(d) ? "" :
  d.toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" }); }

function showSignin(msg) {
  $("app").hidden = true; $("signin").hidden = false; $("err").textContent = msg || "";
}
$("signin").addEventListener("submit", (ev) => {
  ev.preventDefault();
  sessionStorage.setItem("qn", $("siName").value.trim());
  sessionStorage.setItem("qt", $("siToken").value.trim());
  S.name = sessionStorage.getItem("qn"); S.token = sessionStorage.getItem("qt");
  start();
});

function renderRooms(rooms) {
  const list = $("roomList"); list.replaceChildren();
  rooms.forEach((r) => {
    const name = r.name || r.id;
    const b = el("button", "room" + (name === S.room ? " on" : ""));
    b.type = "button"; b.dataset.room = name;
    b.append(el("span", "", "#" + name), el("small", "", (r.members || []).length + " members"));
    list.append(b);
  });
}
$("roomList").addEventListener("click", (ev) => {
  const b = ev.target.closest("button[data-room]");
  if (b) openRoom(b.dataset.room);
});
$("back").addEventListener("click", () => { $("app").classList.remove("inroom"); });
$("newRoom").addEventListener("click", () => {
  const name = (window.prompt ? window.prompt("New room name") : "") || "";
  if (!/^[A-Za-z0-9_-]{1,64}$/.test(name)) return;
  api("/rooms", { method: "POST", body: JSON.stringify({ name, created_by: S.name }) })
    .then(loadRooms).then(() => openRoom(name)).catch(() => {});
});

function openRoom(name) {
  S.room = name; S.seen = new Set(); $("messages").replaceChildren();
  $("roomName").textContent = "#" + name; $("app").classList.add("inroom");
  document.dispatchEvent(new CustomEvent("quorus:room", { detail: name }));  // the board
  loadRooms(); poll();
}

function renderMessage(m) {
  const box = $("messages");
  const atBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  if ((m.message_type || "chat") === "status") {
    box.append(el("div", "status", plain(m.content)));
  } else {
    const d = el("div", "msg" + (m.from_name === S.name ? " me" : ""));
    d.dataset.replyId = m.id || "";
    const who = el("b", "", "@" + (m.from_name || "?"));
    who.append(el("time", "", hhmm(m.timestamp)));
    d.append(who, el("span", "", plain(m.content)));
    box.append(d);
  }
  if (atBottom) box.scrollTop = box.scrollHeight;  // auto-scroll unless reading back
}

function renderWorking(presence) {
  const live = presence.filter((p) => p.online && p.status === "busy" && p.room === S.room);
  const now = Date.now(), busy = live.map((p) => p.name);
  const step = {};
  live.forEach((p) => { step[p.name] = p.activity || "working"; });
  Object.keys(S.workSince).forEach((n) => { if (!busy.includes(n)) delete S.workSince[n]; });
  busy.forEach((n) => { if (!S.workSince[n]) S.workSince[n] = now; });
  $("working").textContent = busy.map((n) => {
    const s = Math.round((now - S.workSince[n]) / 1000);
    return "⋯ @" + n + " " + step[n] + " " + (s < 60 ? s + "s" : Math.floor(s / 60) + "m");
  }).join(" · ");
}

function poll() {
  if (!S.room) return;
  const room = S.room;
  Promise.all([api("/rooms/" + enc(room) + "/history?limit=100"), api("/presence")])
    .then(([msgs, presence]) => {
      if (room !== S.room) return;
      conn(true);
      (msgs || []).forEach((m) => {
        const key = m.id || m.timestamp + m.from_name;
        if (!S.seen.has(key)) { S.seen.add(key); renderMessage(m); }
      });
      renderWorking(presence || []);
    }).catch((e) => { conn(false); if (e.message === "auth") showSignin("Sign-in expired."); });
}

function loadRooms() {
  return api("/rooms").then((rooms) => { conn(true); renderRooms(rooms || []); })
    .catch((e) => { conn(false); if (e.message === "auth") showSignin("That secret didn't work."); });
}

$("composer").addEventListener("submit", (ev) => {
  ev.preventDefault();
  const text = $("msgInput").value.trim();
  if (!text || !S.room) return;
  $("msgInput").value = "";
  api("/rooms/" + enc(S.room) + "/messages", { method: "POST",
    body: JSON.stringify({ from_name: S.name, content: text }) }).then(poll).catch(() => {
      $("msgInput").value = text; conn(false); });
});

function start() {
  if (!S.token) return showSignin();
  $("signin").hidden = true; $("app").hidden = false; $("who").textContent = "@" + S.name;
  const landing = sessionStorage.getItem("qr");  // `quorus web <room>`
  sessionStorage.removeItem("qr");
  loadRooms().then(() => { if (landing) openRoom(landing); });
  clearInterval(S.timer);
  S.timer = setInterval(() => { poll(); }, 3000);
  setInterval(loadRooms, 30000);
}
start();
})();
"""


@router.get("/", response_class=HTMLResponse, include_in_schema=False)
async def dashboard():
    """Quorus web: rooms, live messages, the working line, a send box."""
    return HTMLResponse(DASHBOARD_HTML, headers={"cache-control": "no-cache"})


@router.get("/web.js", include_in_schema=False)
async def dashboard_js() -> Response:
    """The page's only script, same-origin so the CSP stays ``script-src 'self'``."""
    return Response(DASHBOARD_JS, media_type="application/javascript",
                    headers={"cache-control": "no-cache"})
