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
if (frag.get("fps")) sessionStorage.setItem("qfps", "1");  // `quorus web --fps`: Quorum fps readout
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
    b.append(el("span", "", "#" + name), el("small", "", (r.members || []).length
      + ((r.members || []).length === 1 ? " member" : " members")));
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
  const now = Date.now();
  // The relay says when the wake began (epoch seconds from the file backend, ISO
  // text from Redis). A step older than 10 minutes is a stuck daemon, not work:
  // it drops off the line (Arav saw "running tests · 7h 26m", 2026-10-18).
  const began = (p) => { const w = p.last_wake_at;
    const t = typeof w === "number" ? w * 1000 : Date.parse(w || ""); return t > 0 ? t : 0; };
  const live = presence.filter((p) => p.online && p.status === "busy" && p.room === S.room
    && now - (began(p) || S.workSince[p.name] || now) < 600000);
  const busy = live.map((p) => p.name);
  const step = {};
  live.forEach((p) => { step[p.name] = p.activity || "working"; });
  Object.keys(S.workSince).forEach((n) => { if (!busy.includes(n)) delete S.workSince[n]; });
  busy.forEach((n) => { if (!S.workSince[n]) S.workSince[n] = began(live.find((p) => p.name === n)) || now; });
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

// Keep the newest message in view when the box changes size: the board's split
// layout shrinks it after the first render, which left the latest message below
// the fold (review of 608f638b). Reading back up still sticks.
if (typeof ResizeObserver !== "undefined") {
  const box = $("messages");
  let wasAtBottom = true;
  box.addEventListener("scroll", () => {
    wasAtBottom = box.scrollHeight - box.scrollTop - box.clientHeight < 80;
  });
  new ResizeObserver(() => { if (wasAtBottom) box.scrollTop = box.scrollHeight; }).observe(box);
}

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
