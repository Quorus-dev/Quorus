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
const PUBLISHED = /\(quorus\) published (\d+) commit/;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;

let THREE = null;       // loaded lazily
const Q = { scene: null, cam: null, renderer: null, host: null, seats: new Map(),
            core: null, pillar: [], fx: [], seen: new Set(), on: false, raf: 0,
            last: 0, rows: [], room: null, me: "", lastEvent: 0, ray: null };

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// A text card drawn on a canvas, used as a camera-facing sprite. textContent
// rules don't apply to canvas, but nothing here is parsed as markup either.
function label(text, color, bg, size) {
  const c = document.createElement("canvas");
  const x = c.getContext("2d");
  const font = `600 ${size || 28}px -apple-system, Segoe UI, Roboto, sans-serif`;
  x.font = font;
  const w = Math.ceil(x.measureText(text).width) + 36, h = (size || 28) + 22;
  c.width = w; c.height = h;
  x.font = font;
  x.fillStyle = bg || "rgba(16,20,18,0.86)";
  const r = h / 2;
  x.beginPath(); x.moveTo(r, 0); x.arcTo(w, 0, w, h, r); x.arcTo(w, h, 0, h, r);
  x.arcTo(0, h, 0, 0, r); x.arcTo(0, 0, w, 0, r); x.fill();
  x.fillStyle = color || "#fff"; x.textBaseline = "middle";
  x.fillText(text, 18, h / 2 + 1);
  const tex = new THREE.CanvasTexture(c);
  tex.colorSpace = THREE.SRGBColorSpace;
  const s = new THREE.Sprite(new THREE.SpriteMaterial({ map: tex, depthTest: false }));
  s.scale.set(w / 160, h / 160, 1);
  s.renderOrder = 10;
  return s;
}

function mat(color, emissive) {
  return new THREE.MeshStandardMaterial({ color, flatShading: true, roughness: 0.7,
    emissive: emissive || "#000000" });
}

function build(host) {
  Q.host = host;
  Q.scene = new THREE.Scene();
  Q.scene.background = new THREE.Color(cssVar("--bg", "#0f1412"));
  Q.cam = new THREE.PerspectiveCamera(38, 1, 0.1, 100);
  Q.cam.position.set(0, 8.2, 10.2);
  Q.cam.lookAt(0, 0.6, 0);
  Q.renderer = new THREE.WebGLRenderer({ antialias: true });
  Q.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  host.append(Q.renderer.domElement);
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
  new ResizeObserver(resize).observe(host);
  resize();
}

function resize() {
  const w = Q.host.clientWidth || 600, h = Q.host.clientHeight || 420;
  Q.renderer.setSize(w, h);
  Q.cam.aspect = w / h;
  Q.cam.updateProjectionMatrix();
  draw();
}

// One member's seat: chair, figure (agents have a visor, people don't), name
// plaque, a status bubble, and a floor ring that fills to "% of work kept".
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
  const plaque = label("@" + name, "#fff", vendor ? col : "#59645f", 24);
  plaque.position.y = 2.05;
  g.add(chair, back, fig, ringBg, ring, halo, plaque);
  g.userData = { name, vendor, fig, body, head, arm, ring, halo, plaque, bubble: null,
                 bubbleText: "", angle: 0, state: "" };
  g.traverse((o) => { o.userData.seat = name; });
  Q.scene.add(g);
  return g;
}

function layout(names) {
  [...Q.seats.keys()].forEach((n) => {
    if (!names.includes(n)) { Q.scene.remove(Q.seats.get(n)); Q.seats.delete(n); }
  });
  names.forEach((n, i) => {
    const vendor = (n.match(/-(claude|codex|gemini|opencode|cursor|cline)$/) || [])[1] || null;
    const s = Q.seats.get(n) || seat(n, vendor);
    Q.seats.set(n, s);
    const a = (i / names.length) * Math.PI * 2 + Math.PI / 2;
    s.position.set(Math.cos(a) * (R + 0.75), 0, Math.sin(a) * (R + 0.75));
    s.lookAt(0, 0, 0);
    s.rotateY(Math.PI);  // chair backs face outwards, figures face the core
    s.userData.angle = a;
  });
}

function bubble(s, text, color) {
  const u = s.userData;
  if (u.bubbleText === text) return;
  if (u.bubble) { s.remove(u.bubble); u.bubble.material.map.dispose(); u.bubble = null; }
  u.bubbleText = text;
  if (!text) return;
  u.bubble = label(text.length > 34 ? text.slice(0, 33) + "…" : text, color || "#fff",
    "rgba(16,20,18,0.92)", 22);
  u.bubble.position.y = 2.5;
  s.add(u.bubble);
}

// Rows from board.js → poses. Rank 1 needs you, 2 stuck, 3 waiting, 4 working,
// 5 idle, 6 offline (see board.js rowState).
function apply(rows) {
  const byName = new Map(rows.map((r) => [r.name, r]));
  Q.seats.forEach((s, name) => {
    const u = s.userData, r = byName.get(name);
    u.fig.visible = true;
    u.halo.material.opacity = 0;
    u.state = !r ? "human" : r.rank === 1 ? "needs" : r.rank === 2 ? "stuck"
      : r.rank === 4 ? "busy" : r.rank === 6 ? "offline"
      : /quota/.test(r.line) ? "asleep" : /plan slot/.test(r.line) ? "pacing" : "idle";
    if (u.state === "offline") u.fig.visible = false;
    const text = !r ? "" : u.state === "needs" ? "✋ needs you"
      : u.state === "stuck" ? "! stuck" : u.state === "asleep" ? "z z z  " + r.line.replace("out of quota · ", "")
      : u.state === "pacing" ? "⌛ " + r.line.replace("waiting for its plan slot · ", "")
      : u.state === "busy" ? r.line : "";
    bubble(s, text, u.state === "stuck" ? "#f2776e" : u.state === "needs" ? "#e8a63a" : "#fff");
    const kept = r && r.keptPct !== undefined ? r.keptPct : 0;
    u.ring.geometry.dispose();
    u.ring.geometry = new THREE.RingGeometry(0.5, 0.58, 48, 1, 0, Math.max(0.001, kept * Math.PI * 2));
  });
}

// New room messages since last time → one-shot light effects.
function events(history) {
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
  Q.core.material.emissiveIntensity = 0.35 + 1.2 * (Q.core.userData.pulse || 0);
  Q.seats.forEach((s) => {
    const u = s.userData;
    const sway = reduceMotion ? 0 : Math.sin(t * 2 + u.angle) * 0.02;
    u.fig.rotation.x = u.state === "busy" ? 0.22 : u.state === "pacing" ? -0.12 : 0;
    u.body.position.y = (u.vendor ? 0.95 : 1.02) + (u.state === "busy" && !reduceMotion
      ? Math.abs(Math.sin(t * 9 + u.angle)) * 0.03 : sway);
    u.head.rotation.z = u.state === "asleep" ? 0.5 : 0;
    u.arm.rotation.z = u.state === "needs" ? -2.7 + Math.sin(t * 4) * 0.15 : 0.15;
    u.arm.position.y = u.state === "needs" ? 1.3 : 1.02;
    if (u.state === "needs") u.halo.material.opacity = 0.45 + 0.4 * Math.sin(t * 4);
    else if (u.state === "stuck") { u.halo.material.color.set("#f2776e");
      u.halo.material.opacity = 0.3 + 0.3 * Math.sin(t * 6); }
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
  const card = document.getElementById("quorumCard");
  card.replaceChildren();
  const row = hit && Q.rows.find((x) => x.name === hit.object.userData.seat);
  if (!row || !window.QuorusBoard) { card.hidden = true; return; }
  card.append(window.QuorusBoard.rowEl(row));  // the board's own card, same actions
  card.hidden = false;
}

async function start(host) {
  if (!THREE) THREE = await import("/vendor/three.module.min.js");
  if (!Q.scene) build(host);
  Q.on = true;
  if (!Q.raf) Q.raf = requestAnimationFrame(loop);
}

document.addEventListener("quorus:rows", (e) => {
  const { rows, history, room, members } = e.detail;
  Q.rows = rows;
  const host = document.getElementById("quorum");
  if (!host || host.hidden) return;
  start(host).then(() => {
    if (room !== Q.room) { Q.room = room; Q.seen = new Set(); }
    layout(members);
    apply(rows);
    events(history);
    draw();
  }).catch((err) => {
    host.replaceChildren(Object.assign(document.createElement("p"),
      { className: "hint", textContent: "The Quorum view needs WebGL: " + err.message }));
  });
});
document.addEventListener("quorus:quorum-off", () => { Q.on = false; });
document.addEventListener("keydown", (e) => {  // Esc closes a seat's card
  if (e.key === "Escape") document.getElementById("quorumCard").hidden = true;
});
document.addEventListener("visibilitychange", () => {
  if (!document.hidden && Q.on && !Q.raf) Q.raf = requestAnimationFrame(loop);
});
