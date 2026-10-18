// The Quorum view: a Quorus room as a round council table, drawn with three.js.
// Every seat is a member; every pose is real state from board.js's rows; every
// click opens the same card (Approve / Deny / ask). No data path of its own.
// Light by design: three.js loads only when this view first opens, it renders
// only while visible, and it idles at a low frame rate. (2026-10-17)

const VENDOR = {
  claude: ["#d97757", "Claude Code"], codex: ["#10a37f", "Codex"],
  gemini: ["#4a8cf7", "Gemini CLI"], opencode: ["#9aa4ad", "opencode"],
  cursor: ["#8b5cf6", "Cursor"], cline: ["#e0a030", "Cline"],
};
const HUMAN = "#e8e4da";
const R = 3.0;          // table radius
const MAX_SEATS = 14;   // past this the figures blur together: the rest go to "+N more"
const PUBLISHED = /\(quorus\) published (\d+) commit/;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
// Phones get the same table drawn flat in HTML: no WebGL, no 650 KB download.
const phone = matchMedia("(max-width: 640px)");

let THREE = null;       // loaded lazily
const Q = { scene: null, cam: null, renderer: null, host: null, seats: new Map(),
            core: null, pillar: [], fx: [], seen: new Set(), on: false, raf: 0,
            last: 0, rows: [], room: null, me: "", lastEvent: 0, ray: null,
            tags: new Map(), layer: null, flat: false, names: [], extra: 0 };

function vendorOf(name) {
  return (name.match(/-(claude|codex|gemini|opencode|cursor|cline)$/) || [])[1] || null;
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// Seat tags are HTML, not canvas sprites: textContent only (never markup from
// room data), focusable buttons for keyboard users, and laid out in screen
// space so a front seat's name can't sit on top of its own or a neighbour's
// status (the overlap in Arav's screenshot).
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
  const v = vendorOf(name);
  t.style.setProperty("--seat", v ? VENDOR[v][0] : HUMAN);
  t.addEventListener("click", () => openCard(name));
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

function mat(color, emissive) {
  return new THREE.MeshStandardMaterial({ color, flatShading: true, roughness: 0.7,
    emissive: emissive || "#000000" });
}

function build(host) {
  Q.scene = new THREE.Scene();
  Q.scene.background = new THREE.Color(cssVar("--bg", "#0f1412"));
  Q.cam = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  Q.cam.position.set(0, 8.2, 10.2);
  Q.cam.lookAt(0, 0.6, 0);
  Q.renderer = new THREE.WebGLRenderer({ antialias: true });
  Q.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  host.prepend(Q.renderer.domElement);  // tags stay above the canvas
  Q.scene.add(new THREE.HemisphereLight("#ffffff", "#30403a", 1.6));
  const sun = new THREE.DirectionalLight("#ffffff", 1.4);
  sun.position.set(4, 10, 6);
  Q.scene.add(sun);
  // The table and the room's core: a ring that pulses when anyone speaks.
  const top = new THREE.Mesh(new THREE.CylinderGeometry(R, R, 0.22, 64), mat("#24302b"));
  top.position.y = 0.9;
  const foot = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.9, 0.9, 24), mat("#1b2420"));
  foot.position.y = 0.45;
  Q.core = new THREE.Mesh(new THREE.TorusGeometry(0.75, 0.07, 12, 64),
    mat(cssVar("--accent", "#3cc7a2"), cssVar("--accent", "#3cc7a2")));
  Q.core.rotation.x = Math.PI / 2;
  Q.core.position.y = 1.05;
  Q.core.material.emissiveIntensity = 0.35;
  Q.scene.add(top, foot, Q.core);
  Q.ray = new THREE.Raycaster();
  Q.renderer.domElement.addEventListener("click", onClick);
  resize();
}

function resize() {
  if (!Q.flat && Q.renderer) {
    const w = Q.host.clientWidth || 600, h = Q.host.clientHeight || 420;
    Q.renderer.setSize(w, h);
    Q.cam.aspect = w / h;
    Q.cam.updateProjectionMatrix();
    draw();
  }
  if (Q.names.length) placeTags();
}

// One member's seat: chair, figure (agents have a visor, people don't) and a
// floor ring that fills to "% of work kept". Name, model and status are HTML tags.
function seat(name, vendor) {
  const g = new THREE.Group();
  const col = vendor ? VENDOR[vendor][0] : HUMAN;
  const chair = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.12, 0.6), mat("#2c3833"));
  chair.position.y = 0.55;
  const back = new THREE.Mesh(new THREE.BoxGeometry(0.62, 0.7, 0.1), mat("#2c3833"));
  back.position.set(0, 0.95, 0.3);
  const fig = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(vendor ? 0.2 : 0.22, vendor ? 0.36 : 0.5,
    4, 10), mat(col));
  body.position.y = vendor ? 0.95 : 1.02;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.19, 16, 12), mat(vendor ? "#f2efe8" : col));
  head.position.y = vendor ? 1.42 : 1.55;
  fig.add(body, head);
  if (vendor) {  // an agent's visor faces the table
    const visor = new THREE.Mesh(new THREE.BoxGeometry(0.26, 0.08, 0.08), mat("#101614", col));
    visor.material.emissiveIntensity = 0.6;
    visor.position.set(0, 1.44, -0.16);
    fig.add(visor);
  }
  const arm = new THREE.Mesh(new THREE.CapsuleGeometry(0.05, 0.36, 3, 6), mat(col));
  arm.position.set(0.26, 1.02, 0);
  fig.add(arm);
  const ringBg = new THREE.Mesh(new THREE.RingGeometry(0.5, 0.58, 48), mat("#26302c"));
  ringBg.rotation.x = -Math.PI / 2; ringBg.position.y = 0.01;
  const ring = new THREE.Mesh(new THREE.RingGeometry(0.5, 0.58, 48, 1, 0, 0.001), mat(col, col));
  ring.rotation.x = -Math.PI / 2; ring.position.y = 0.02;
  const halo = new THREE.Mesh(new THREE.RingGeometry(0.66, 0.74, 48),
    new THREE.MeshBasicMaterial({ color: "#e8a63a", transparent: true, opacity: 0 }));
  halo.rotation.x = -Math.PI / 2; halo.position.y = 0.03;
  g.add(chair, back, fig, ringBg, ring, halo);
  g.userData = { name, vendor, fig, body, head, arm, ring, halo, angle: 0, state: "" };
  g.traverse((o) => { o.userData.seat = name; });
  Q.scene.add(g);
  return g;
}

// Who gets a seat: everyone up to MAX_SEATS, offline members last.
function seated(members, rows) {
  const rank = new Map(rows.map((r) => [r.name, r.rank]));
  const away = (n) => (rank.get(n) === 6 ? 1 : 0);
  const order = members.slice().sort((a, b) => away(a) - away(b) || a.localeCompare(b));
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
    if (!names.includes(n)) { Q.scene.remove(Q.seats.get(n)); Q.seats.delete(n); }
  });
  names.forEach((n, i) => {
    const s = Q.seats.get(n) || seat(n, vendorOf(n));
    Q.seats.set(n, s);
    const a = (i / names.length) * Math.PI * 2 + Math.PI / 2;
    s.position.set(Math.cos(a) * (R + 0.75), 0, Math.sin(a) * (R + 0.75));
    s.lookAt(0, 0, 0);
    s.rotateY(Math.PI);  // chair backs face outwards, figures face the core
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
    : state === "stuck" ? "! stuck" : state === "asleep" ? "z z z " + r.line.replace("out of quota · ", "")
    : state === "pacing" ? "⌛ " + r.line.replace("waiting for its plan slot · ", "")
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
  Q.seats.forEach((s, name) => {
    const u = s.userData, r = byName.get(name);
    u.state = stateOf(r);
    u.fig.visible = u.state !== "offline";
    u.halo.material.opacity = 0;
    // The ring fills to "% of work kept", but only once there's a track record:
    // 3 commits isn't one (review of the Quorum pitch). Hollow until then.
    const kept = r && r.keptPct !== undefined && (r.commits || 0) >= 5 ? r.keptPct : 0;
    u.ring.geometry.dispose();
    u.ring.geometry = new THREE.RingGeometry(0.5, 0.58, 48, 1, 0, Math.max(0.001, kept * Math.PI * 2));
  });
}

// Tags follow their seats in screen space; the camera never moves, so this runs
// on layout and resize only. Then nudge any tag that overlaps one placed before
// it, front seats first (they're nearest, so they keep their spot).
function placeTags() {
  if (Q.flat) {  // phones: the seats as a grid of cards, whoever needs you first
    const rank = new Map(Q.rows.map((r) => [r.name, r.rank]));
    Q.names.slice().sort((a, b) => (rank.get(a) || 5) - (rank.get(b) || 5) || a.localeCompare(b))
      .forEach((n) => Q.layer.append(Q.tags.get(n)));
    return;
  }
  const w = Q.host.clientWidth, h = Q.host.clientHeight;
  const spots = Q.names.map((name) => {
    const v = Q.seats.get(name).position.clone().setY(1.85).project(Q.cam);
    const t = Q.tags.get(name);
    return { t, x: (v.x + 1) / 2 * w, y: (1 - v.y) / 2 * h, tw: t.offsetWidth, th: t.offsetHeight };
  }).sort((a, b) => b.y - a.y);
  const placed = [];
  spots.forEach((p) => {
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
  n.textContent = !Q.names.some(vendorOf)
    ? "No agents at this table yet. Add one: quorus agent add claude --room " + (Q.room || "<room>")
      + " (or codex, gemini, opencode)"
    : Q.extra ? "+" + Q.extra + " more (offline or past " + MAX_SEATS + " seats): see List"
    : Q.flat ? "Compact view: the 3D table needs a wider screen and WebGL." : "";
  n.hidden = !n.textContent;
}

// New room messages since last time → one-shot light effects.
function events(history) {
  if (Q.flat) return;
  const fresh = history.filter((m) => m.id && !Q.seen.has(m.id));
  const first = Q.seen.size === 0;
  history.forEach((m) => m.id && Q.seen.add(m.id));
  if (first) return;  // don't replay the whole room on open
  fresh.forEach((m) => {
    const from = Q.seats.get(m.from_name);
    pulse();
    if (!from) return;
    const body = m.content || "";
    if (m.message_type === "social") {
      try {
        const sv = JSON.parse(body);
        const to = Q.seats.get(((sv.payload || {}).to) || "");
        if (sv.verb === "defer" && to) fly(from, to, "#f0a040", "orb");
      } catch (e) { /* not a verb */ }
      return;
    }
    if (PUBLISHED.test(body)) fly(from, null, VENDOR[from.userData.vendor || "opencode"][0], "cube");
    (body.match(/@[\w-]+/g) || []).forEach((tag) => {
      const to = Q.seats.get(tag.slice(1));
      if (to && to !== from) fly(from, to, "#ffffff", "arc");
    });
  });
}

function pulse() { Q.lastEvent = performance.now(); Q.core.userData.pulse = 1; }

function fly(from, to, color, kind) {
  if (reduceMotion) return;
  const a = from.position.clone().setY(1.5);
  const b = to ? to.position.clone().setY(1.5) : new THREE.Vector3(0, 1.2 + Q.pillar.length * 0.16, 0);
  const geo = kind === "cube" ? new THREE.BoxGeometry(0.16, 0.16, 0.16)
    : new THREE.SphereGeometry(kind === "orb" ? 0.12 : 0.06, 12, 8);
  const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color }));
  m.position.copy(a);
  Q.scene.add(m);
  Q.fx.push({ m, a, b, t0: performance.now(), dur: kind === "arc" ? 900 : 1400, kind });
  Q.lastEvent = performance.now();
}

function step(now) {
  const t = now / 1000;
  Q.core.userData.pulse = Math.max(0, (Q.core.userData.pulse || 0) - 0.03);
  Q.core.material.emissiveIntensity = 0.35 + (reduceMotion ? 0.4 : 1.2) * (Q.core.userData.pulse || 0);
  Q.seats.forEach((s) => {
    const u = s.userData;
    const sway = reduceMotion ? 0 : Math.sin(t * 2 + u.angle) * 0.02;
    u.fig.rotation.x = u.state === "busy" ? 0.22 : u.state === "pacing" ? -0.12 : 0;
    u.body.position.y = (u.vendor ? 0.95 : 1.02) + (u.state === "busy" && !reduceMotion
      ? Math.abs(Math.sin(t * 9 + u.angle)) * 0.03 : sway);
    u.head.rotation.z = u.state === "asleep" ? 0.5 : 0;
    const wave = reduceMotion ? 0 : 1;  // reduced motion: same poses, nothing oscillates
    u.arm.rotation.z = u.state === "needs" ? -2.7 + wave * Math.sin(t * 4) * 0.15 : 0.15;
    u.arm.position.y = u.state === "needs" ? 1.3 : 1.02;
    if (u.state === "needs") u.halo.material.opacity = 0.65 + wave * 0.25 * Math.sin(t * 4);
    else if (u.state === "stuck") { u.halo.material.color.set("#f2776e");
      u.halo.material.opacity = 0.45 + wave * 0.15 * Math.sin(t * 6); }
    else u.halo.material.color.set("#e8a63a");
  });
  Q.fx = Q.fx.filter((f) => {
    const k = Math.min(1, (now - f.t0) / f.dur);
    f.m.position.lerpVectors(f.a, f.b, k);
    f.m.position.y += Math.sin(k * Math.PI) * (f.kind === "arc" ? 1.2 : 0.8);
    if (k < 1) return true;
    if (f.kind === "cube") {  // lands on the centre pillar: the integration branch
      Q.pillar.push(f.m);
      if (Q.pillar.length > 14) Q.scene.remove(Q.pillar.shift());
      Q.pillar.forEach((c, i) => c.position.set(0, 1.2 + i * 0.17, 0));
    } else { Q.scene.remove(f.m); f.m.geometry.dispose(); }
    return false;
  });
}

function draw() { if (Q.renderer) Q.renderer.render(Q.scene, Q.cam); }

function loop(now) {
  if (!Q.on || document.hidden) { Q.raf = 0; return; }
  const busy = Q.fx.length || now - Q.lastEvent < 1500;
  const every = reduceMotion ? 1000 : busy ? 33 : 120;  // ~30 fps while moving, ~8 idle
  if (now - Q.last >= every) { Q.last = now; step(now); draw(); }
  Q.raf = requestAnimationFrame(loop);
}

function onClick(ev) {
  const r = Q.renderer.domElement.getBoundingClientRect();
  const p = new THREE.Vector2(((ev.clientX - r.left) / r.width) * 2 - 1,
                              -((ev.clientY - r.top) / r.height) * 2 + 1);
  Q.ray.setFromCamera(p, Q.cam);
  const hit = Q.ray.intersectObjects(Q.scene.children, true).find((h) => h.object.userData.seat);
  openCard(hit ? hit.object.userData.seat : "");
}

function openCard(name) {
  const card = document.getElementById("quorumCard");
  card.replaceChildren();
  const row = name && Q.rows.find((x) => x.name === name);
  if (!row || !window.QuorusBoard) { card.hidden = true; return; }
  card.append(window.QuorusBoard.rowEl(row));  // the board's own card, same actions
  card.hidden = false;
}

// Two row events can arrive while three.js is still downloading; both must wait
// for the one build, or the scene is built twice (two canvases, seen in the sandbox).
function start(host) {
  if (!Q.starting) Q.starting = boot(host);
  return Q.starting.then(() => { Q.on = true; startLoop(); });
}

async function boot(host) {
  layer(host);
  if (!Q.host) { Q.host = host; new ResizeObserver(resize).observe(host); }
  if (phone.matches) return goFlat(host);
  try {
    if (!THREE) THREE = await import("/vendor/three.module.min.js");
    build(host);
  } catch (err) {  // no WebGL (or the library failed): the flat table still works
    return goFlat(host);
  }
  Q.on = true;
  startLoop();
}

function goFlat(host) {
  Q.flat = true;
  Q.on = true;
  host.classList.add("flat");
}

function startLoop() {
  if (!Q.flat && !Q.raf) Q.raf = requestAnimationFrame(loop);
}

document.addEventListener("quorus:rows", (e) => {
  const { rows, history, room, members } = e.detail;
  Q.rows = rows;
  const host = document.getElementById("quorum");
  if (!host || host.hidden) return;
  start(host).then(() => {
    if (room !== Q.room) { Q.room = room; Q.seen = new Set(); }
    layout(seated(members, rows));
    apply(rows);
    events(history);
    notes();
    placeTags();
    if (!Q.flat) draw();
  }).catch((err) => {  // never a blank panel: say what failed
    host.replaceChildren(Object.assign(document.createElement("p"),
      { className: "hint", textContent: "The Quorum view failed: " + err.message
        + " " + (err.stack || "").split("\n").slice(0, 3).join(" | ") }));
  });
});
document.addEventListener("quorus:quorum-off", () => { Q.on = false; });
document.addEventListener("keydown", (e) => {  // Esc closes a seat's card
  if (e.key === "Escape") document.getElementById("quorumCard").hidden = true;
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Q.on && !Q.raf) Q.raf = requestAnimationFrame(loop);
});
