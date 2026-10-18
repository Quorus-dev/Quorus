// The Quorum view: a Quorus room as a council table, drawn with three.js.
// v2 (2026-10-18, after Arav: "this looks horrendous"): one lit stage, only
// present members seated, no humanoid toys and no publish pillar. Every seat
// is a member; every pose is real state from board.js's rows; every click
// opens the same card (Approve / Deny / ask). No data path of its own.
// Light by design: three.js loads only when this view first opens, it renders
// only while visible, and it idles at a low frame rate.

const VENDOR = {
  claude: ["#d97757", "Claude Code"], codex: ["#10a37f", "Codex"],
  gemini: ["#4a8cf7", "Gemini CLI"], opencode: ["#9aa4ad", "opencode"],
  cursor: ["#8b5cf6", "Cursor"], cline: ["#e0a030", "Cline"],
};
const HUMAN = "#e8e4da";
const AMBER = "#e8a63a", TOMATO = "#f2776e";
const R = 3.0;          // table radius
const SEAT_R = R + 0.62;  // seats sit just outside the rim
const MAX_SEATS = 14;   // past this the marks blur together: the rest go to "+N more"
const PUBLISHED = /\(quorus\) published (\d+) commit/;
const reduceMotion = matchMedia("(prefers-reduced-motion: reduce)").matches;
const darkScheme = matchMedia("(prefers-color-scheme: dark)");
// Phones get the same table drawn flat in HTML: no WebGL, no 650 KB download.
const phone = matchMedia("(max-width: 640px)");
const NARROW = 560;     // a host narrower than this can't frame the table either

let THREE = null;       // loaded lazily
const Q = { scene: null, cam: null, renderer: null, host: null, seats: new Map(),
            core: null, glow: null, fx: [], seen: new Set(), on: false, raf: 0,
            last: 0, rows: [], room: null, me: "", lastEvent: 0, ray: null,
            tags: new Map(), layer: null, flat: false, names: [], extra: 0, offline: 0,
            pulse: 0 };

function vendorOf(name) {
  return (name.match(/-(claude|codex|gemini|opencode|cursor|cline)$/) || [])[1] || null;
}

function cssVar(name, fallback) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

// The stage palette follows the page theme (Radix sage, see the UI brief).
function palette() {
  const dark = darkScheme.matches;
  return dark
    ? { bg: cssVar("--bg", "#0f1412"), floor: "#161c19", table: "#242e29", tableEdge: "#2e3934",
        rim: "#5a6862", seat: "#2a3430", key: "#fff1e0", fill: "#9fb8ff", rimLight: "#bfffe9",
        hemi: ["#a7bab2", "#0b100e"], exposure: 1.15 }
    : { bg: cssVar("--bg", "#f5f6f4"), floor: "#e9ece9", table: "#d9dfdb", tableEdge: "#c7cfca",
        rim: "#9aa6a0", seat: "#cfd6d2", key: "#ffffff", fill: "#dfe8ff", rimLight: "#ffffff",
        hemi: ["#ffffff", "#aeb8b2"], exposure: 1.0 };
}

// Seat tags are HTML, not canvas sprites: textContent only (never markup from
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

function mat(color, opts) {
  return new THREE.MeshStandardMaterial(Object.assign({ color, roughness: 0.6, metalness: 0.05 },
    opts || {}));
}

function build(host) {
  const P = palette();
  Q.scene = new THREE.Scene();
  Q.scene.background = new THREE.Color(P.bg);
  Q.scene.fog = new THREE.Fog(P.bg, 20, 30);  // the floor fades into the page; frame() sets the range
  Q.cam = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  Q.renderer = new THREE.WebGLRenderer({ antialias: true });
  Q.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  Q.renderer.outputColorSpace = THREE.SRGBColorSpace;
  Q.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  Q.renderer.toneMappingExposure = P.exposure;
  Q.renderer.shadowMap.enabled = true;
  Q.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  host.prepend(Q.renderer.domElement);  // tags stay above the canvas

  // Three-point light: a warm key with soft shadows, a cool fill, a rim from behind.
  Q.scene.add(new THREE.HemisphereLight(P.hemi[0], P.hemi[1], 0.9));
  const key = new THREE.DirectionalLight(P.key, 3.0);
  key.position.set(-5, 9, 6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.radius = 6;
  key.shadow.bias = -0.0005;
  Object.assign(key.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 30 });
  const fill = new THREE.DirectionalLight(P.fill, 0.5);
  fill.position.set(6, 4, -3);
  const rim = new THREE.DirectionalLight(P.rimLight, 0.9);
  rim.position.set(0, 5, -9);
  Q.scene.add(key, fill, rim);
  const accent = cssVar("--accent", "#3cc7a2");
  Q.glow = new THREE.PointLight(accent, 5, 6, 2);
  Q.glow.position.set(0, 1.6, 0);
  Q.scene.add(Q.glow);

  // The floor, the table and the room's core: a ring that pulses when anyone speaks.
  const floor = new THREE.Mesh(new THREE.CircleGeometry(12, 96), mat(P.floor, { roughness: 0.95 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  const top = new THREE.Mesh(new THREE.CylinderGeometry(R, R * 0.97, 0.14, 128),
    mat(P.table, { roughness: 0.5, metalness: 0.08 }));
  top.position.y = 0.86;
  top.castShadow = top.receiveShadow = true;
  const edge = new THREE.Mesh(new THREE.TorusGeometry(R, 0.028, 12, 160),
    mat(P.rim, { roughness: 0.3, metalness: 0.85 }));
  edge.rotation.x = Math.PI / 2;
  edge.position.y = 0.93;
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.5, 0.8, 48), mat(P.tableEdge));
  stem.position.y = 0.4;
  const base = new THREE.Mesh(new THREE.CylinderGeometry(1.3, 1.4, 0.06, 64), mat(P.tableEdge));
  base.position.y = 0.03;
  base.receiveShadow = true;
  Q.core = new THREE.Mesh(new THREE.TorusGeometry(0.7, 0.04, 16, 120),
    mat(accent, { emissive: accent, emissiveIntensity: 1.1, roughness: 0.4 }));
  Q.core.rotation.x = Math.PI / 2;
  Q.core.position.y = 0.98;
  const pool = new THREE.Mesh(new THREE.CircleGeometry(0.66, 64),
    new THREE.MeshBasicMaterial({ color: accent, transparent: true, opacity: 0.08 }));
  pool.rotation.x = -Math.PI / 2;
  pool.position.y = 0.935;
  Q.scene.add(floor, top, edge, stem, base, Q.core, pool);
  Q.ray = new THREE.Raycaster();
  Q.renderer.domElement.addEventListener("click", onClick);
  resize();
}

// Frame the whole ring of seats (plus their marks) inside the host, whatever
// its aspect: a fixed camera cropped the front seats and shrank the back ones.
function frame() {
  const w = Q.host.clientWidth || 600, h = Q.host.clientHeight || 420;
  Q.cam.aspect = w / h;
  const elev = 0.82;  // radians above the table: steep enough that seats never hide each other
  const target = new THREE.Vector3(0, 0.55, 0);
  let d = 12;
  const pts = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * (SEAT_R + 0.45), 0, Math.sin(a) * (SEAT_R + 0.45)));
    pts.push(new THREE.Vector3(Math.cos(a) * (SEAT_R + 0.2), 1.75, Math.sin(a) * (SEAT_R + 0.2)));
  }
  for (let pass = 0; pass < 4; pass++) {
    Q.cam.position.set(0, target.y + Math.sin(elev) * d, Math.cos(elev) * d);
    Q.cam.lookAt(target);
    Q.cam.updateProjectionMatrix();
    Q.cam.updateMatrixWorld();
    let m = 0;
    pts.forEach((p) => {
      const v = p.clone().project(Q.cam);
      m = Math.max(m, Math.abs(v.x), Math.abs(v.y) * 1.08);  // a little headroom for the tags
    });
    d *= m / 0.92;
  }
  // Fog relative to where the camera ended up: a fixed range swallowed the back seats.
  Q.scene.fog.near = d + 3.5;
  Q.scene.fog.far = d + 11;
}

function resize() {
  if (!Q.flat && Q.renderer) {
    const w = Q.host.clientWidth || 600, h = Q.host.clientHeight || 420;
    Q.renderer.setSize(w, h);
    frame();
    draw();
  }
  if (Q.names.length) placeTags();
}

// One member's seat: a low pedestal at the rim and the member's mark above it,
// a smooth sphere in the vendor colour for an agent, a faceted stone for a
// person. A thin ring under the mark carries the status colour. Name, model
// and status are HTML tags.
function seat(name, vendor) {
  const P = palette();
  const g = new THREE.Group();
  const col = vendor ? VENDOR[vendor][0] : HUMAN;
  const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.38, 0.12, 48),
    mat(P.seat, { roughness: 0.7 }));
  pedestal.position.y = 0.06;
  pedestal.castShadow = pedestal.receiveShadow = true;
  const mark = vendor
    ? new THREE.Mesh(new THREE.SphereGeometry(0.26, 48, 32), new THREE.MeshPhysicalMaterial({
        color: col, roughness: 0.32, metalness: 0.05, clearcoat: 0.7, clearcoatRoughness: 0.25,
        emissive: col, emissiveIntensity: 0 }))
    : new THREE.Mesh(new THREE.IcosahedronGeometry(0.27, 0), new THREE.MeshPhysicalMaterial({
        color: col, roughness: 0.28, metalness: 0.2, flatShading: true,
        emissive: col, emissiveIntensity: 0 }));
  mark.position.y = 0.62;
  mark.castShadow = true;
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.44, 0.02, 10, 72),
    new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0 }));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.135;
  g.add(pedestal, mark, ring);
  g.userData = { name, vendor, mark, ring, angle: 0, state: "" };
  g.traverse((o) => { o.userData.seat = name; });
  Q.scene.add(g);
  return g;
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
    if (!names.includes(n)) { Q.scene.remove(Q.seats.get(n)); Q.seats.delete(n); }
  });
  names.forEach((n, i) => {
    const s = Q.seats.get(n) || seat(n, vendorOf(n));
    Q.seats.set(n, s);
    const a = (i / names.length) * Math.PI * 2 + Math.PI / 2;
    s.position.set(Math.cos(a) * SEAT_R, 0, Math.sin(a) * SEAT_R);
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
    const u = s.userData;
    u.state = stateOf(byName.get(name));
    u.ring.material.color.set(u.state === "stuck" ? TOMATO : u.state === "busy"
      ? cssVar("--accent", "#3cc7a2") : AMBER);
  });
}

// Tags follow their seats in screen space; the camera only moves on resize, so
// this runs on layout and resize only. Then nudge any tag that overlaps one
// placed before it, front seats first (they're nearest, so they keep their spot).
function placeTags() {
  if (Q.flat) {  // phones: the seats as a grid of cards, whoever needs you first
    const rank = new Map(Q.rows.map((r) => [r.name, r.rank]));
    Q.names.slice().sort((a, b) => (rank.get(a) || 5) - (rank.get(b) || 5) || a.localeCompare(b))
      .forEach((n) => Q.layer.append(Q.tags.get(n)));
    return;
  }
  const w = Q.host.clientWidth, h = Q.host.clientHeight;
  const spots = Q.names.map((name) => {
    const v = Q.seats.get(name).position.clone().setY(1.05).project(Q.cam);
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
  fresh.forEach((m) => {
    const from = Q.seats.get(m.from_name);
    pulse();
    if (!from) return;
    const body = m.content || "";
    if (m.message_type === "social") {
      try {
        const sv = JSON.parse(body);
        const to = Q.seats.get(((sv.payload || {}).to) || "");
        if (sv.verb === "defer" && to) fly(from, to, AMBER, "orb");
      } catch (e) { /* not a verb */ }
      return;
    }
    // A publish lands in the room's core: a ripple across the table, not a tower.
    if (PUBLISHED.test(body)) ripple(VENDOR[from.userData.vendor || "opencode"][0]);
    (body.match(/@[\w-]+/g) || []).forEach((tag) => {
      const to = Q.seats.get(tag.slice(1));
      if (to && to !== from) fly(from, to, cssVar("--accent", "#3cc7a2"), "arc");
    });
  });
}

function pulse() { Q.lastEvent = performance.now(); Q.pulse = 1; }

function fly(from, to, color, kind) {
  if (reduceMotion) return;
  const a = from.position.clone().setY(0.75);
  const b = to.position.clone().setY(0.75);
  const m = new THREE.Mesh(new THREE.SphereGeometry(kind === "orb" ? 0.1 : 0.05, 16, 12),
    new THREE.MeshBasicMaterial({ color }));
  m.position.copy(a);
  Q.scene.add(m);
  Q.fx.push({ m, a, b, t0: performance.now(), dur: kind === "arc" ? 900 : 1400, kind });
  Q.lastEvent = performance.now();
}

function ripple(color) {
  pulse();
  if (reduceMotion) return;
  const m = new THREE.Mesh(new THREE.TorusGeometry(0.72, 0.015, 8, 120),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.7 }));
  m.rotation.x = Math.PI / 2;
  m.position.y = 0.95;
  Q.scene.add(m);
  Q.fx.push({ m, t0: performance.now(), dur: 1600, kind: "ripple" });
}

function step(now) {
  const t = now / 1000;
  Q.pulse = Math.max(0, Q.pulse - 0.03);
  Q.core.material.emissiveIntensity = 1.1 + (reduceMotion ? 0.4 : 1.4) * Q.pulse;
  Q.glow.intensity = 5 + 10 * Q.pulse;
  const wave = reduceMotion ? 0 : 1;  // reduced motion: same poses, nothing oscillates
  Q.seats.forEach((s) => {
    const u = s.userData, mk = u.mark, ring = u.ring.material;
    const resting = u.state === "asleep" || u.state === "pacing";
    mk.position.y = resting ? 0.5 : 0.62 + wave * (u.state === "busy"
      ? Math.sin(t * 2.2 + u.angle) * 0.03 : 0);
    mk.scale.setScalar(resting ? 0.86 : 1);
    mk.material.emissiveIntensity = u.state === "busy" ? 0.28 + wave * 0.18 * Math.sin(t * 2.2 + u.angle)
      : u.state === "needs" ? 0.2 : resting ? 0 : 0.06;
    ring.opacity = u.state === "needs" ? 0.75 + wave * 0.25 * Math.sin(t * 4)
      : u.state === "stuck" ? 0.7 : u.state === "busy" ? 0.35 + wave * 0.15 * Math.sin(t * 2.2 + u.angle) : 0;
  });
  Q.fx = Q.fx.filter((f) => {
    const k = Math.min(1, (now - f.t0) / f.dur);
    if (f.kind === "ripple") {
      const s = 1 + k * (R / 0.72 - 1);
      f.m.scale.set(s, s, 1);
      f.m.material.opacity = 0.7 * (1 - k);
    } else {
      f.m.position.lerpVectors(f.a, f.b, k);
      f.m.position.y += Math.sin(k * Math.PI) * (f.kind === "arc" ? 1.0 : 0.7);
    }
    if (k < 1) return true;
    Q.scene.remove(f.m);
    f.m.geometry.dispose();
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
  // A host measured at 0 isn't narrow, it isn't laid out yet: the resize observer sizes it.
  const narrow = host.clientWidth > 0 && host.clientWidth < NARROW;
  if (phone.matches || narrow) {
    Q.flatReason = phone.matches ? "" : "host is " + host.clientWidth + "px wide";
    return goFlat(host);
  }
  try {
    if (!THREE) THREE = await import("/vendor/three.module.min.js");
    build(host);
  } catch (err) {  // no WebGL (or the library failed): the flat table still works
    Q.flatReason = String(err && err.message || err);
    console.error("quorum: 3D view unavailable", err);
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
