// Totems and light effects for the Quorum view (2026-10-18). Each vendor family
// has its own sculpted abstract form, coloured by its hue; a person is a luminous
// pillar. State is motion: working glows and streams particles toward the table's
// centre, waiting breathes dim, needs-you pulses a ring and raises a beacon, stuck
// flickers amber. Shipped work bursts toward the "main" monolith; an @-mention
// draws an arc between seats and a review a two-colour braid. No logos, nothing
// borrowed; all geometry is procedural.
import * as THREE from "/vendor/three.module.min.js";

export const AMBER = "#e8a63a", TOMATO = "#f2776e";
const UP = 1.0;    // the height of a mark's centre above its seat
const SCALE = 1.8; // marks were ~40px from the default camera and read as icons (review)

// Soft trails: each stream is a comet of points whose size and alpha fall off
// behind the head (the `fade` attribute), with a soft radial edge; bloom does the rest.
const PARTICLE_VS = `attribute float fade; varying float vFade;
void main(){ vFade = fade; vec4 mv = modelViewMatrix * vec4(position, 1.0);
  gl_PointSize = (0.12 + 0.3 * fade) * (300.0 / -mv.z); gl_Position = projectionMatrix * mv; }`;
const PARTICLE_FS = `uniform vec3 color; varying float vFade;
void main(){ float d = length(gl_PointCoord - 0.5); if (d > 0.5) discard;
  float soft = smoothstep(0.5, 0.0, d); gl_FragColor = vec4(color, soft * soft * vFade * 0.85); }`;

function physical(color, extra) {
  return new THREE.MeshPhysicalMaterial(Object.assign({ color, roughness: 0.3, metalness: 0.05,
    clearcoat: 0.6, clearcoatRoughness: 0.25, envMapIntensity: 0.8, emissive: color,
    emissiveIntensity: 0.12 }, extra || {}));
}

function rod(len, mat) {
  return new THREE.Mesh(new THREE.BoxGeometry(0.028, 0.028, len), mat);
}

// One form per vendor family. Returns the parts to add to the mark group plus a
// per-frame `spin(t, dt)` that animates them.
function form(vendor, mat) {
  const g = new THREE.Group();
  let spin = (t) => { g.rotation.y = t * 0.25; };
  if (vendor === "claude") {  // faceted crystal
    const m = new THREE.Mesh(new THREE.OctahedronGeometry(0.3, 0), mat);
    m.scale.set(1, 1.55, 1);
    mat.flatShading = true;
    g.add(m);
  } else if (vendor === "codex") {  // interlocking rings
    const a = new THREE.Mesh(new THREE.TorusGeometry(0.27, 0.045, 24, 72), mat);
    const b = a.clone();
    a.rotation.x = Math.PI / 2;
    b.rotation.y = Math.PI / 2;
    g.add(a, b);
    spin = (t) => { a.rotation.z = t * 0.5; b.rotation.x = -t * 0.4; g.rotation.y = t * 0.15; };
  } else if (vendor === "gemini") {  // twin orbs
    const a = new THREE.Mesh(new THREE.SphereGeometry(0.15, 40, 28), mat);
    const b = a.clone();
    g.add(a, b);
    spin = (t) => {
      a.position.set(Math.cos(t * 0.9) * 0.18, Math.sin(t * 0.9) * 0.08, Math.sin(t * 0.9) * 0.18);
      b.position.set(-a.position.x, -a.position.y, -a.position.z);
    };
  } else if (vendor === "cursor") {  // helix
    g.add(new THREE.Mesh(new THREE.TorusKnotGeometry(0.19, 0.045, 96, 12, 2, 3), mat));
    spin = (t) => { g.rotation.y = t * 0.3; g.rotation.x = Math.sin(t * 0.2) * 0.2; };
  } else if (vendor === "cline") {  // stacked discs
    [-0.13, 0, 0.13].forEach((y, i) => {
      const d = new THREE.Mesh(new THREE.CylinderGeometry(0.26 - i * 0.03, 0.26 - i * 0.03, 0.05, 48), mat);
      d.position.y = y;
      g.add(d);
    });
    spin = (t) => { g.children.forEach((d, i) => { d.rotation.y = t * (0.2 + i * 0.15); d.rotation.z = Math.sin(t * 0.7 + i) * 0.08; }); };
  } else {  // lattice cube (opencode and any new harness)
    const h = 0.21;
    [[1, 1], [1, -1], [-1, 1], [-1, -1]].forEach(([a, b]) => {
      const z = rod(0.45, mat); z.position.set(a * h, b * h, 0);
      const x = rod(0.45, mat); x.rotation.y = Math.PI / 2; x.position.set(0, a * h, b * h);
      const y = rod(0.45, mat); y.rotation.x = Math.PI / 2; y.position.set(a * h, 0, b * h);
      g.add(z, x, y);
    });
    g.rotation.z = 0.615;
    spin = (t) => { g.rotation.y = t * 0.3; };
  }
  return { g, spin };
}

function pillar(color) {
  // Half the glow it had: a person is the one lit figure, not the brightest thing on
  // stage, or the eye never reaches the agents (review of 3083def4).
  const mat = physical(color, { emissiveIntensity: 0.28, roughness: 0.35, clearcoat: 0.4 });
  const g = new THREE.Group();
  const body = new THREE.Mesh(new THREE.CylinderGeometry(0.085, 0.12, 0.95, 40), mat);
  const cap = new THREE.Mesh(new THREE.SphereGeometry(0.085, 32, 20), mat);
  cap.position.y = 0.475;
  g.add(body, cap);
  return { g, spin: () => {}, mat };
}

// A member's seat: a low pedestal, the mark above it, a status ring and a contact
// shadow on the floor. Everything else about the seat (name, model, status text)
// lives in the HTML plaque that quorum.js owns.
export function makeTotem(name, vendor, color, P) {
  const seat = new THREE.Group();
  const pedestal = new THREE.Mesh(new THREE.CylinderGeometry(0.44, 0.48, 0.12, 64),
    new THREE.MeshPhysicalMaterial({ color: P.stem, roughness: 0.55 }));
  pedestal.position.y = 0.06;
  pedestal.castShadow = pedestal.receiveShadow = true;
  const mat = vendor ? physical(color) : null;
  const { g: mark, spin, mat: pmat } = vendor ? form(vendor, mat) : pillar(color);
  mark.position.y = UP;
  mark.scale.setScalar(SCALE);
  mark.traverse((o) => { o.castShadow = true; });
  const ring = new THREE.Mesh(new THREE.TorusGeometry(0.56, 0.022, 10, 96),
    new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0, depthWrite: false }));
  ring.rotation.x = Math.PI / 2;
  ring.position.y = 0.135;
  const beam = new THREE.Mesh(new THREE.CylinderGeometry(0.04, 0.06, 3.0, 24, 1, true),
    new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0, depthWrite: false,
      blending: THREE.AdditiveBlending, side: THREE.DoubleSide }));
  beam.position.y = 1.6;
  const spark = new THREE.Mesh(new THREE.SphereGeometry(0.05, 16, 12),
    new THREE.MeshBasicMaterial({ color: AMBER, transparent: true, opacity: 0 }));
  seat.add(pedestal, mark, ring, beam, spark);
  seat.traverse((o) => { o.userData.seat = name; });
  const u = { name, vendor, mark, mat: mat || pmat, ring, beam, spark, state: "", angle: 0, spin,
              particles: null, base: vendor ? 0.12 : 0.28, color: new THREE.Color(color) };
  seat.userData = u;
  return seat;
}

// Working agents stream soft trails toward the monolith along a lifted arc:
// a few comets, each a head with a tail of fading points behind it.
function particles(seat, color, to) {
  const streams = 5, tail = 14, n = streams * tail;
  const geo = new THREE.BufferGeometry();
  geo.setAttribute("position", new THREE.Float32BufferAttribute(new Float32Array(n * 3), 3));
  const fade = new Float32Array(n);
  for (let k = 0; k < streams; k++) for (let j = 0; j < tail; j++) fade[k * tail + j] = 1 - j / tail;
  geo.setAttribute("fade", new THREE.Float32BufferAttribute(fade, 1));
  const pos = geo.attributes.position.array;  // the attribute's own buffer, not a copy
  const heads = new Float32Array(streams);
  for (let k = 0; k < streams; k++) heads[k] = k / streams;
  const pts = new THREE.Points(geo, new THREE.ShaderMaterial({ transparent: true, depthWrite: false,
    blending: THREE.AdditiveBlending, uniforms: { color: { value: new THREE.Color(color) } },
    vertexShader: PARTICLE_VS, fragmentShader: PARTICLE_FS }));
  const a = seat.position.clone().setY(UP + 0.2);
  const mid = a.clone().lerp(to, 0.5).setY(UP + 1.3);
  const curve = new THREE.QuadraticBezierCurve3(a, mid, to);
  pts.userData.step = (dt) => {
    for (let k = 0; k < streams; k++) {
      heads[k] = (heads[k] + dt * (0.16 + k * 0.012)) % 1;
      for (let j = 0; j < tail; j++) {
        const i = k * tail + j, q = heads[k] - j * 0.011;
        const p = curve.getPoint(q < 0 ? q + 1 : q);
        const wob = Math.sin(q * 14 + k * 2) * 0.03;
        pos[i * 3] = p.x + wob; pos[i * 3 + 1] = p.y + Math.cos(q * 11 + k) * 0.03; pos[i * 3 + 2] = p.z + wob;
      }
    }
    geo.attributes.position.needsUpdate = true;
  };
  return pts;
}

// Rank from board.js → the mark's pose for this frame. `reduce` freezes every
// oscillation but keeps the state's colour and level: a static premium still.
export function updateTotem(seat, t, dt, reduce, accent, stage) {
  const u = seat.userData, s = u.state, w = reduce ? 0 : 1;
  if (!reduce) u.spin(t, dt);
  const breathe = 0.97 + w * 0.03 * Math.sin(t * 1.9 + u.angle);
  u.mark.scale.setScalar(SCALE * (s === "asleep" || s === "pacing" ? 0.9 * breathe : 1));
  u.mark.position.y = UP - (s === "asleep" || s === "pacing" ? 0.12 : 0)
    + w * (s === "busy" ? Math.sin(t * 1.6 + u.angle) * 0.04 : 0);
  const stuckFlicker = reduce ? 0.45 : (Math.sin(t * 23) > 0.2 ? 0.7 : 0.15) + Math.random() * 0.1;
  u.mat.emissive.set(s === "stuck" ? AMBER : u.color);
  u.mat.emissiveIntensity = s === "busy" ? 0.38 + w * 0.22 * Math.sin(t * 1.6 + u.angle)
    : s === "needs" ? 0.3 : s === "stuck" ? stuckFlicker
    : s === "asleep" || s === "pacing" ? 0.03 : u.base;
  u.ring.material.color.set(s === "stuck" ? TOMATO : s === "busy" ? accent : AMBER);
  u.ring.material.opacity = s === "needs" ? 0.7 + w * 0.3 * Math.sin(t * 4)
    : s === "stuck" ? 0.55 : s === "busy" ? 0.3 + w * 0.12 * Math.sin(t * 1.6 + u.angle) : 0;
  u.beam.material.opacity = s === "needs" ? 0.16 + w * 0.06 * Math.sin(t * 3) : 0;
  u.spark.material.opacity = s === "needs" ? 0.9 : 0;
  if (s === "needs") u.spark.position.y = reduce ? 2.0 : 0.4 + ((t * 0.9 + u.angle) % 2.4);
  if (s === "busy" && !reduce) {
    if (!u.particles) { u.particles = particles(seat, u.color, stage.monolith); stage.add(u.particles); }
    u.particles.userData.step(dt);
  } else if (u.particles) { stage.remove(u.particles); u.particles.geometry.dispose(); u.particles = null; }
}

export function disposeTotem(seat, stage) {
  if (seat.userData.particles) { stage.remove(seat.userData.particles); seat.userData.particles.geometry.dispose(); }
  stage.remove(seat);
}

// --- one-shot light effects -------------------------------------------------

function fade(mesh, k) { mesh.material.opacity = 0.9 * (1 - Math.max(0, (k - 0.6) / 0.4)); }

// An @-mention: a thin arc of the sender's colour revealed from seat to seat, then
// fading. A review (approve/block) braids two arcs in both members' colours.
export function arc(stage, a, b, colors, now) {
  const tubes = colors.map((color, i) => {
    const side = colors.length > 1 ? (i ? 0.1 : -0.1) : 0;
    const dir = b.clone().sub(a).normalize();
    const norm = new THREE.Vector3(-dir.z, 0, dir.x).multiplyScalar(side);
    const mid = a.clone().lerp(b, 0.5).add(norm).setY(UP + 1.25);
    const curve = new THREE.QuadraticBezierCurve3(a.clone().setY(UP + 0.2), mid, b.clone().setY(UP + 0.2));
    const geo = new THREE.TubeGeometry(curve, 56, 0.012, 6, false);
    const m = new THREE.Mesh(geo, new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.9,
      depthWrite: false, blending: THREE.AdditiveBlending }));
    m.userData.count = geo.index.count;
    geo.setDrawRange(0, 0);
    stage.add(m);
    return m;
  });
  return { update(n) {
    const k = Math.min(1, (n - now) / 1700);
    tubes.forEach((m) => { m.geometry.setDrawRange(0, Math.floor(m.userData.count * Math.min(1, k / 0.55))); fade(m, k); });
    return k < 1;
  }, dispose() { tubes.forEach((m) => { stage.remove(m); m.geometry.dispose(); }); } };
}

// Shipped: a burst of the vendor's colour travels to the monolith, which flashes,
// and a ripple runs out across the table.
export function burst(stage, from, color, now) {
  const m = new THREE.Mesh(new THREE.SphereGeometry(0.08, 20, 14),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 1 }));
  const a = from.clone().setY(UP + 0.1), b = stage.monolith.clone();
  const mid = a.clone().lerp(b, 0.5).setY(UP + 1.6);
  const curve = new THREE.QuadraticBezierCurve3(a, mid, b);
  stage.add(m);
  let landed = false;
  return { update(n) {
    const k = Math.min(1, (n - now) / 1300), e = 1 - Math.pow(1 - k, 3);
    m.position.copy(curve.getPoint(e));
    m.scale.setScalar(1 + e * 0.8);
    if (k >= 1 && !landed) { landed = true; stage.pulse(1); stage.effect(ripple(stage, color, n)); }
    return k < 1;
  }, dispose() { stage.remove(m); m.geometry.dispose(); } };
}

export function ripple(stage, color, now) {
  const m = new THREE.Mesh(new THREE.TorusGeometry(0.72, 0.014, 8, 140),
    new THREE.MeshBasicMaterial({ color, transparent: true, opacity: 0.8, depthWrite: false }));
  m.rotation.x = Math.PI / 2;
  m.position.y = 0.95;
  stage.add(m);
  return { update(n) {
    const k = Math.min(1, (n - now) / 1600), s = 1 + k * 3.0;
    m.scale.set(s, s, 1);
    m.material.opacity = 0.8 * (1 - k);
    return k < 1;
  }, dispose() { stage.remove(m); m.geometry.dispose(); } };
}
