// The Quorum cast (2026-10-18, after Arav: "mini agents, a real cartoon"). One
// original chibi robot per harness, built here from primitives: a capsule body, a
// big head with a dark glossy visor and two lit eyes that carry the expression, a
// small chest badge. Nothing is a vendor's mascot or a stock model; the shape and
// colour only nod to the harness (a warm pebble with an antenna, a teal pill with
// ring ears, a slim two-tone blue with twin orbs, a grey lattice cube, a violet
// wedge, an amber stack of discs). People are taller warm-white figures in a cap.
// The badge is a coloured initial until each vendor's mark and license is cleared;
// then it becomes that mark, as identification only. Light effects (arcs, bursts)
// stay in quorum-totems.js and are re-exported so quorum.js has one import.
import * as THREE from "/vendor/three.module.min.js";
export { AMBER, TOMATO, arc, burst, ripple } from "/quorum-totems.js";

const AMBER_C = "#e8a63a", TOMATO_C = "#f2776e";
const EYE = "#eafff8";
const BADGE = {  // fallback initials; marks replace these once cleared per vendor
  claude: "C", codex: "Cx", gemini: "G", opencode: "oc", cursor: "Cu", cline: "Cl",
};

function pbr(color, extra) {
  return new THREE.MeshPhysicalMaterial(Object.assign({ color, roughness: 0.38, metalness: 0.02,
    clearcoat: 0.55, clearcoatRoughness: 0.3, envMapIntensity: 0.9 }, extra || {}));
}

// A box with rounded edges from an extruded rounded rectangle (no addons needed).
function roundedBox(w, h, d, r) {
  const s = new THREE.Shape(), x = -w / 2, y = -h / 2;
  s.moveTo(x + r, y); s.lineTo(x + w - r, y); s.quadraticCurveTo(x + w, y, x + w, y + r);
  s.lineTo(x + w, y + h - r); s.quadraticCurveTo(x + w, y + h, x + w - r, y + h);
  s.lineTo(x + r, y + h); s.quadraticCurveTo(x, y + h, x, y + h - r);
  s.lineTo(x, y + r); s.quadraticCurveTo(x, y, x + r, y);
  const g = new THREE.ExtrudeGeometry(s, { depth: Math.max(0.01, d - 2 * r), bevelEnabled: true,
    bevelThickness: r, bevelSize: r, bevelSegments: 5, curveSegments: 6 });
  g.center();
  return g;
}

// The badge: a pale disc with the harness initial(s) in its colour, drawn once to a
// canvas. Text only; no vendor artwork until cleared.
function badgeTexture(text, color) {
  const c = document.createElement("canvas");
  c.width = c.height = 256;
  const g = c.getContext("2d");
  g.fillStyle = "#f3f6f4";
  g.beginPath(); g.arc(128, 128, 118, 0, Math.PI * 2); g.fill();
  g.fillStyle = color;
  g.font = `700 ${text.length > 1 ? 120 : 150}px "Plus Jakarta Sans", system-ui, sans-serif`;
  g.textAlign = "center"; g.textBaseline = "middle";
  g.fillText(text, 128, 136);
  const t = new THREE.CanvasTexture(c);
  t.colorSpace = THREE.SRGBColorSpace;
  t.anisotropy = 8;
  return t;
}

function eye(mat) {
  const e = new THREE.Mesh(new THREE.CircleGeometry(0.055, 24), mat);
  return e;
}

// Head shapes per harness, each returning the mesh and a hat/antenna group.
function headFor(vendor, mat, accent) {
  const g = new THREE.Group();
  let head;
  if (vendor === "opencode") {           // lattice cube: a rounded cube with etched grooves
    head = new THREE.Mesh(roundedBox(0.66, 0.6, 0.6, 0.09), mat);
    const groove = new THREE.MeshPhysicalMaterial({ color: "#5b646a", roughness: 0.6 });
    [-0.11, 0.11].forEach((o) => {
      const v = new THREE.Mesh(new THREE.BoxGeometry(0.02, 0.5, 0.02), groove); v.position.set(o, 0, 0.3);
      const h = new THREE.Mesh(new THREE.BoxGeometry(0.5, 0.02, 0.02), groove); h.position.set(0, o, 0.3);
      g.add(v, h);
    });
  } else if (vendor === "cursor") {      // wedge: a four-sided taper, turned to a diamond front
    head = new THREE.Mesh(new THREE.CylinderGeometry(0.22, 0.36, 0.6, 4, 1), mat);
    head.rotation.y = Math.PI / 4;
  } else {                                // the round family
    head = new THREE.Mesh(new THREE.SphereGeometry(0.34, 48, 32), mat);
    head.scale.set(1, 0.92, 0.94);
  }
  head.castShadow = true;
  g.add(head);
  const thin = pbr("#2a3330", { roughness: 0.5 });
  const bright = new THREE.MeshStandardMaterial({ color: accent, emissive: accent, emissiveIntensity: 0.9, roughness: 0.4 });
  if (vendor === "claude") {              // one antenna with a lit tip
    const stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.014, 0.014, 0.22, 10), thin);
    stalk.position.y = 0.4;
    const tip = new THREE.Mesh(new THREE.SphereGeometry(0.045, 20, 14), bright);
    tip.position.y = 0.52;
    g.add(stalk, tip);
  } else if (vendor === "codex") {        // twin ring ears
    [-1, 1].forEach((s) => {
      const ring = new THREE.Mesh(new THREE.TorusGeometry(0.1, 0.028, 14, 40), mat);
      ring.position.set(s * 0.36, 0.02, 0);
      ring.rotation.y = Math.PI / 2;
      g.add(ring);
    });
  } else if (vendor === "gemini") {       // twin orbs on stalks
    [-1, 1].forEach((s) => {
      const stalk = new THREE.Mesh(new THREE.CylinderGeometry(0.012, 0.012, 0.2, 10), thin);
      stalk.position.set(s * 0.14, 0.4, 0); stalk.rotation.z = -s * 0.35;
      const orb = new THREE.Mesh(new THREE.SphereGeometry(0.04, 20, 14), bright);
      orb.position.set(s * 0.21, 0.5, 0);
      g.add(stalk, orb);
    });
  } else if (vendor === "cline") {        // a cap of three stacked discs
    [0, 1, 2].forEach((i) => {
      const d = new THREE.Mesh(new THREE.CylinderGeometry(0.26 - i * 0.07, 0.26 - i * 0.07, 0.045, 40), mat);
      d.position.y = 0.33 + i * 0.05;
      g.add(d);
    });
  }
  return { g, head };
}

// A person: taller, warm white, a cap instead of a visor, two simple dark eyes.
function person(color) {
  const mat = pbr(color, { roughness: 0.5, clearcoat: 0.2 });
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.23, 0.55, 8, 24), mat);
  body.position.y = 0.6;
  const head = new THREE.Mesh(new THREE.SphereGeometry(0.27, 40, 28), mat);
  head.position.y = 1.3;
  const capMat = pbr("#2d3835", { roughness: 0.6 });
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.285, 32, 16, 0, Math.PI * 2, 0, Math.PI / 2.4), capMat);
  cap.position.y = 1.32;
  const brim = new THREE.Mesh(new THREE.CylinderGeometry(0.3, 0.3, 0.03, 32), capMat);
  brim.position.set(0, 1.36, 0.14); brim.scale.z = 1.3;
  const eyeMat = new THREE.MeshStandardMaterial({ color: "#1a2220", roughness: 0.4 });
  const eyes = [-1, 1].map((s) => { const e = eye(eyeMat); e.scale.setScalar(0.55); e.position.set(s * 0.09, 1.3, 0.262); return e; });
  g.add(body, head, cap, brim, ...eyes);
  g.traverse((o) => { o.castShadow = true; });
  return { g, mat, eyes, head, visor: null, arms: [], badge: null, height: 1.6 };
}

// A robot: capsule body, big head with visor and eyes, stub arms and feet, a badge.
function robot(vendor, color, accent) {
  const mat = pbr(color);
  const g = new THREE.Group();
  const slim = vendor === "gemini", wide = vendor === "codex";
  const body = new THREE.Mesh(new THREE.CapsuleGeometry(0.27, 0.3, 8, 28), mat);
  body.scale.set(slim ? 0.86 : wide ? 1.12 : 1, 1, slim ? 0.9 : 1);
  body.position.y = 0.52;
  const feet = [-1, 1].map((s) => {
    const f = new THREE.Mesh(new THREE.CapsuleGeometry(0.075, 0.1, 6, 16), mat);
    f.rotation.x = Math.PI / 2; f.position.set(s * 0.14, 0.08, 0.04); return f;
  });
  const arms = [-1, 1].map((s) => {
    const a = new THREE.Group();
    const upper = new THREE.Mesh(new THREE.CapsuleGeometry(0.055, 0.22, 6, 16), mat);
    upper.position.y = -0.14;
    a.add(upper);
    a.position.set(s * (slim ? 0.28 : 0.34), 0.72, 0);
    a.rotation.z = s * 0.18;
    return a;
  });
  const headMat = vendor === "gemini" ? pbr(new THREE.Color(color).lerp(new THREE.Color("#ffffff"), 0.35)) : mat;
  const { g: headG, head } = headFor(vendor, headMat, accent);
  headG.position.y = 1.18;
  // The visor: a dark glossy cap over the face, the eyes sit just in front of it.
  const visor = new THREE.Mesh(new THREE.SphereGeometry(0.3, 40, 24, -Math.PI / 2 - 0.95, 1.9, 0.75, 1.1),
    new THREE.MeshPhysicalMaterial({ color: "#0b100e", roughness: 0.08, metalness: 0.1, clearcoat: 1,
      clearcoatRoughness: 0.05, envMapIntensity: 1.6 }));
  visor.rotation.y = Math.PI;
  visor.position.set(0, 0.02, vendor === "opencode" ? 0.04 : vendor === "cursor" ? 0.06 : 0.055);
  visor.scale.set(1.06, 0.95, 1.06);
  const eyeMat = new THREE.MeshStandardMaterial({ color: EYE, emissive: EYE, emissiveIntensity: 1.1, roughness: 0.3 });
  const eyes = [-1, 1].map((s) => { const e = eye(eyeMat); e.position.set(s * 0.1, 0.04, 0.345); return e; });
  headG.add(visor, ...eyes);
  const badge = new THREE.Mesh(new THREE.CircleGeometry(0.085, 32),
    new THREE.MeshStandardMaterial({ map: badgeTexture(BADGE[vendor] || "?", color), roughness: 0.5 }));
  badge.position.set(0, 0.66, (slim ? 0.9 : wide ? 1 : 1) * 0.27 + 0.005);
  g.add(body, ...feet, ...arms, headG, badge);
  g.traverse((o) => { o.castShadow = true; });
  badge.castShadow = false;
  return { g, mat, eyes, head, headG, visor, arms, badge, height: 1.55 };
}

// A member's seat: a low pedestal, the figure on it, a status ring at the foot and a
// raised-hand spark for "needs you". Name and status text live in the HTML plaque.
export function makeAgent(name, vendor, color, P, accent) {
  const seat = new THREE.Group();
  const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.5, 0.54, 0.1, 64),
    new THREE.MeshPhysicalMaterial({ color: P.stem, roughness: 0.55 }));
  pedestal.position.y = 0.05;
  pedestal.castShadow = pedestal.receiveShadow = true;
  const fig = vendor ? robot(vendor, color, accent || "#3fd2b0") : person(color);
  fig.g.position.y = 0.1;
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.62, 0.022, 10, 96),
    new THREE.MeshBasicMaterial({ color: AMBER_C, transparent: true, opacity: 0, depthWrite: false }));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.115;
  const spark = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12),
    new THREE.MeshBasicMaterial({ color: AMBER_C, transparent: true, opacity: 0 }));
  seat.add(pedestal, fig.g, ring, spark);
  seat.traverse((o) => { o.userData.seat = name; });
  seat.userData = Object.assign({ name, vendor, state: "", angle: 0, color: new THREE.Color(color),
    ring, spark, fig, yaw: 0, face: null, hop: 0 }, {});
  return seat;
}

const lerp = (a, b, k) => a + (b - a) * k;

// Board rank → pose, every frame. `reduce` freezes all motion but keeps the pose.
export function updateAgent(seat, t, dt, reduce, accent, stage) {
  const u = seat.userData, f = u.fig, s = u.state, w = reduce ? 0 : 1;
  const k = reduce ? 1 : Math.min(1, dt * 6);
  // Face the table's centre, or whoever is being spoken to.
  const target = u.face || new THREE.Vector3(0, 0, 0);
  const want = Math.atan2(target.x - seat.position.x, target.z - seat.position.z);
  let d = want - u.yaw; d = Math.atan2(Math.sin(d), Math.cos(d));
  u.yaw += d * k;
  f.g.rotation.y = u.yaw;
  // Idle bob and a blink every few seconds; asleep and stuck slump instead.
  const slump = s === "stuck" || s === "asleep" || s === "pacing" ? 1 : 0;
  f.g.position.y = 0.1 + w * (slump ? 0 : Math.sin(t * 1.7 + u.angle) * 0.015) - slump * 0.05 + u.hop;
  u.hop = Math.max(0, u.hop - dt * 1.2);
  const headG = f.headG || f.head;
  if (headG) {
    headG.rotation.x = lerp(headG.rotation.x, slump ? 0.42 : s === "needs" ? -0.12 : 0, k);
    headG.rotation.z = lerp(headG.rotation.z, s === "stuck" ? 0.15 : 0, k);
  }
  const blink = !reduce && ((t * 0.37 + u.angle) % 4) < 0.12;
  const eyeY = s === "asleep" || s === "pacing" ? 0.12 : blink ? 0.08 : s === "stuck" ? 0.6 : 1;
  f.eyes.forEach((e) => { e.scale.y = lerp(e.scale.y, eyeY * (u.vendor ? 1 : 0.55), reduce ? 1 : 0.5); });
  if (u.vendor) {
    const em = f.eyes[0].material;
    em.emissive.set(s === "stuck" ? AMBER_C : EYE);
    em.emissiveIntensity = s === "busy" ? 1.6 + w * 0.3 * Math.sin(t * 5) : s === "asleep" || s === "pacing" ? 0.25 : 1.1;
    f.mat.emissive.set(s === "stuck" ? AMBER_C : u.color);
    f.mat.emissiveIntensity = s === "busy" ? 0.08 : s === "stuck" ? 0.12 + (reduce ? 0 : Math.random() * 0.05) : 0.03;
  }
  // Arms: typing while busy, one hand up on needs-you, hanging otherwise.
  f.arms.forEach((a, i) => {
    const side = i ? 1 : -1;
    let z = side * 0.18, x = 0;
    if (s === "busy") { x = -1.1 + w * Math.sin(t * 11 + i * 1.7) * 0.25; z = side * 0.05; }
    else if (s === "needs" && i === 1) { z = 2.6; x = 0; }
    a.rotation.x = lerp(a.rotation.x, x, k);
    a.rotation.z = lerp(a.rotation.z, z, k);
  });
  u.ring.material.color.set(s === "stuck" ? TOMATO_C : s === "busy" ? accent : AMBER_C);
  u.ring.material.opacity = s === "needs" ? 0.7 + w * 0.3 * Math.sin(t * 4)
    : s === "stuck" ? 0.55 : s === "busy" ? 0.3 + w * 0.12 * Math.sin(t * 1.6 + u.angle) : 0;
  u.spark.material.opacity = s === "needs" ? 0.9 : 0;
  if (s === "needs") {
    u.spark.position.set(0.42 * Math.cos(u.yaw), 1.75 + (reduce ? 0 : Math.sin(t * 3) * 0.05), -0.42 * Math.sin(u.yaw));
  }
  void stage;
}

export function disposeAgent(seat, stage) {
  stage.remove(seat);
  seat.traverse((o) => { if (o.geometry) o.geometry.dispose(); });
}

// A shipped commit: the author hops once; quorum.js also fires the burst.
export function celebrate(seat) { seat.userData.hop = 0.22; }
// Turn to face another seat for a while (an @-mention), then back to the table.
export function faceToward(seat, other, ms) {
  seat.userData.face = other ? other.position.clone() : null;
  if (other && ms) setTimeout(() => { if (seat.userData.face === null) return; seat.userData.face = null; }, ms);
}
