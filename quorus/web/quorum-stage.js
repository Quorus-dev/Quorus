// The Quorum stage (2026-10-18): renderer, image-based light, the table and the
// "main" monolith, a camera that drifts and dollies, and a small in-house bloom.
// quorum.js owns the data; this file only draws. Everything here is procedural
// (no textures, no external assets) so the relay's CSP stays `script-src 'self'`.
import * as THREE from "/vendor/three.module.min.js";

export const R = 3.0;            // table radius
export const SEAT_R = R + 0.62;  // seats sit just outside the rim
const TARGET = new THREE.Vector3(0, 0.55, 0);

// Bloom: bright pass → two separable blurs at half and quarter size → add back.
// The composite runs to the screen, so three's own ACES + sRGB chunks apply.
const QUAD_VS = "varying vec2 vUv; void main(){ vUv = uv; gl_Position = vec4(position.xy, 0.0, 1.0); }";
const BRIGHT_FS = `uniform sampler2D tex; uniform float threshold; varying vec2 vUv;
void main(){ vec4 c = texture2D(tex, vUv); float l = dot(c.rgb, vec3(0.2126, 0.7152, 0.0722));
  gl_FragColor = vec4(c.rgb * smoothstep(threshold, threshold + 0.6, l), 1.0); }`;
const BLUR_FS = `uniform sampler2D tex; uniform vec2 dir; varying vec2 vUv;
void main(){ vec4 s = texture2D(tex, vUv) * 0.227027;
  s += (texture2D(tex, vUv + dir * 1.384615) + texture2D(tex, vUv - dir * 1.384615)) * 0.316216;
  s += (texture2D(tex, vUv + dir * 3.230769) + texture2D(tex, vUv - dir * 3.230769)) * 0.070270;
  gl_FragColor = s; }`;
const COMPOSITE_FS = `uniform sampler2D scene; uniform sampler2D bloomA; uniform sampler2D bloomB;
uniform float strength; varying vec2 vUv;
void main(){ vec3 c = texture2D(scene, vUv).rgb;
  vec3 b = texture2D(bloomA, vUv).rgb * 0.6 + texture2D(bloomB, vUv).rgb * 0.8;
  gl_FragColor = vec4(c + b * strength, 1.0);
  #include <tonemapping_fragment>
  #include <colorspace_fragment>
}`;

function quadMaterial(fragment, uniforms) {
  return new THREE.ShaderMaterial({ vertexShader: QUAD_VS, fragmentShader: fragment, uniforms,
    depthTest: false, depthWrite: false });
}

// A small lit room, baked once into a PMREM: a warm key panel upper left, a cool
// fill to the right, a rim strip behind. Glossy surfaces reflect it softly.
function environment(renderer) {
  const pm = new THREE.PMREMGenerator(renderer);
  const room = new THREE.Scene();
  // The walls are a mid grey in linear light (an sRGB hex here would be near black).
  room.add(new THREE.Mesh(new THREE.BoxGeometry(16, 10, 16),
    new THREE.MeshBasicMaterial({ color: new THREE.Color(0.22, 0.25, 0.24), side: THREE.BackSide })));
  const panel = (w, h, color, k, pos) => {
    const m = new THREE.Mesh(new THREE.PlaneGeometry(w, h),
      new THREE.MeshBasicMaterial({ color: new THREE.Color(color).multiplyScalar(k), side: THREE.DoubleSide }));
    m.position.set(...pos);
    m.lookAt(0, 0, 0);
    room.add(m);
  };
  panel(6, 3.5, "#fff1e0", 9, [-5, 5, 3]);
  panel(3, 3, "#9fb8ff", 4, [5.5, 2.5, -2]);
  panel(9, 0.8, "#bfffe9", 11, [0, 5.5, -6.5]);
  panel(8, 8, "#dfe6e2", 1.6, [0, -4.9, 0]);  // a soft floor bounce
  const tex = pm.fromScene(room, 0.04).texture;
  pm.dispose();
  return tex;
}

export function palette(dark, accent, bg) {
  return dark
    ? { bg, accent, floorColor: "#181f1c", table: "#222b27", rim: "#6a7872",
        stem: "#242d29", mono: "#0e1311", key: "#fff1e0", rimLight: "#bfffe9", exposure: 1.15 }
    : { bg, accent, floorColor: "#e3e8e5", table: "#cfd6d2", rim: "#8a968f",
        stem: "#c3cbc6", mono: "#dfe5e1", key: "#ffffff", rimLight: "#ffffff", exposure: 1.0 };
}

export function create(host, P, hooks) {
  const S = { host, hooks, pick: [], fx: [], t0: performance.now(), last: 0, raf: 0, on: false,
              drift: !hooks.reduceMotion, focus: null, k: 0, pulse: 0, bloom: true, d: 12,
              // The screenshot harness runs on software GL and must keep full quality.
              lockQuality: sessionStorage.getItem("qq") === "lock" };
  S.scene = new THREE.Scene();
  S.scene.background = new THREE.Color(P.bg);
  S.scene.fog = new THREE.Fog(P.bg, 20, 30);
  S.cam = new THREE.PerspectiveCamera(30, 1, 0.1, 60);
  S.renderer = new THREE.WebGLRenderer({ antialias: true, powerPreference: "high-performance" });
  S.renderer.setPixelRatio(Math.min(devicePixelRatio || 1, 2));
  S.renderer.outputColorSpace = THREE.SRGBColorSpace;
  S.renderer.toneMapping = THREE.ACESFilmicToneMapping;
  S.renderer.toneMappingExposure = P.exposure;
  S.renderer.shadowMap.enabled = true;
  S.renderer.shadowMap.type = THREE.PCFSoftShadowMap;
  host.prepend(S.renderer.domElement);
  S.scene.environment = environment(S.renderer);
  S.scene.environmentIntensity = 1.4;

  // Direct light only for shadows and a touch of key: the environment does the rest.
  const key = new THREE.DirectionalLight(P.key, 1.9);
  key.position.set(-5, 9, 6);
  key.castShadow = true;
  key.shadow.mapSize.set(2048, 2048);
  key.shadow.radius = 8;
  key.shadow.bias = -0.0004;
  Object.assign(key.shadow.camera, { left: -6, right: 6, top: 6, bottom: -6, near: 1, far: 30 });
  const rim = new THREE.DirectionalLight(P.rimLight, 0.8);
  rim.position.set(0, 5, -9);
  S.glow = new THREE.PointLight(P.accent, 4, 6, 2);
  S.glow.position.set(0, 1.4, 0);
  S.scene.add(key, rim, S.glow);

  const floor = new THREE.Mesh(new THREE.CircleGeometry(12, 96),
    new THREE.MeshPhysicalMaterial({ color: P.floorColor, roughness: 0.92, metalness: 0 }));
  floor.rotation.x = -Math.PI / 2;
  floor.receiveShadow = true;
  // A glossy dark slab: clearcoat gives the faint reflection of the room above it.
  const top = new THREE.Mesh(new THREE.CylinderGeometry(R, R * 0.985, 0.12, 160),
    new THREE.MeshPhysicalMaterial({ color: P.table, roughness: 0.16, metalness: 0.02,
      clearcoat: 1, clearcoatRoughness: 0.1, envMapIntensity: 2.0 }));
  top.position.y = 0.87;
  top.castShadow = top.receiveShadow = true;
  const edge = new THREE.Mesh(new THREE.TorusGeometry(R, 0.024, 12, 200),
    new THREE.MeshPhysicalMaterial({ color: P.rim, roughness: 0.25, metalness: 0.9 }));
  edge.rotation.x = Math.PI / 2;
  edge.position.y = 0.93;
  const stem = new THREE.Mesh(new THREE.CylinderGeometry(0.34, 0.5, 0.81, 48),
    new THREE.MeshPhysicalMaterial({ color: P.stem, roughness: 0.5 }));
  stem.position.y = 0.405;
  const base = new THREE.Mesh(new THREE.CylinderGeometry(1.3, 1.4, 0.06, 64),
    new THREE.MeshPhysicalMaterial({ color: P.stem, roughness: 0.5 }));
  base.position.y = 0.03;
  base.receiveShadow = true;
  S.scene.add(floor, top, edge, stem, base);

  // "main": a wide dark-glass slab at the centre, turned off-axis, with two teal
  // seams and a soft pool of light at its foot that pulses when anyone speaks.
  // (A thin bar inside a ring read as a power button from the default camera.)
  const mono = new THREE.Group();
  const glass = new THREE.Mesh(new THREE.BoxGeometry(1.0, 1.4, 0.2),
    new THREE.MeshPhysicalMaterial({ color: P.mono, roughness: 0.12, metalness: 0.05, clearcoat: 1,
      clearcoatRoughness: 0.08, envMapIntensity: 1.4 }));
  glass.position.y = 0.93 + 0.7;
  glass.castShadow = true;
  S.seam = new THREE.MeshStandardMaterial({ color: P.accent, emissive: P.accent, emissiveIntensity: 1.4,
    roughness: 0.3 });
  [-0.3, 0.3].forEach((x) => {
    const f = new THREE.Mesh(new THREE.BoxGeometry(0.025, 1.2, 0.02), S.seam);
    f.position.set(x, glass.position.y, 0.105);
    const b = f.clone();
    b.position.z = -0.105;
    mono.add(f, b);
  });
  const cap = new THREE.Mesh(new THREE.BoxGeometry(1.02, 0.012, 0.22), S.seam);
  cap.position.y = glass.position.y + 0.705;
  S.core = new THREE.Mesh(new THREE.CircleGeometry(0.95, 72),
    new THREE.MeshBasicMaterial({ color: P.accent, transparent: true, opacity: 0.22, depthWrite: false,
      blending: THREE.AdditiveBlending }));
  S.core.rotation.x = -Math.PI / 2;
  S.core.position.y = 0.935;
  mono.add(glass, cap, S.core);
  mono.rotation.y = -0.55;
  S.scene.add(mono);
  S.monolith = new THREE.Vector3(0, 1.45, 0);

  // Bloom targets, sized on resize.
  S.quadScene = new THREE.Scene();
  S.quadCam = new THREE.OrthographicCamera(-1, 1, 1, -1, 0, 1);
  S.quad = new THREE.Mesh(new THREE.PlaneGeometry(2, 2));
  S.quadScene.add(S.quad);
  S.bright = quadMaterial(BRIGHT_FS, { tex: { value: null }, threshold: { value: 0.72 } });
  S.blur = quadMaterial(BLUR_FS, { tex: { value: null }, dir: { value: new THREE.Vector2() } });
  S.comp = quadMaterial(COMPOSITE_FS, { scene: { value: null }, bloomA: { value: null },
    bloomB: { value: null }, strength: { value: 0.55 } });
  S.ray = new THREE.Raycaster();
  S.renderer.domElement.addEventListener("click", (ev) => onClick(S, ev));
  resize(S);
  const handle = api(S);
  host.quorumStage = handle;  // for devtools and the screenshot harness; not a global
  return handle;
}

function targets(S, w, h) {
  const dpr = S.renderer.getPixelRatio();
  const mk = (div) => new THREE.WebGLRenderTarget(Math.max(2, Math.floor(w * dpr / div)),
    Math.max(2, Math.floor(h * dpr / div)), { type: THREE.HalfFloatType, depthBuffer: div === 1 });
  [S.rtScene, S.rtA, S.rtA2, S.rtB, S.rtB2].forEach((rt) => rt && rt.dispose());
  S.rtScene = mk(1);
  S.rtA = mk(2); S.rtA2 = mk(2);   // half size: tight glow
  S.rtB = mk(4); S.rtB2 = mk(4);   // quarter size: wide halo
}

export function resize(S) {
  const w = S.host.clientWidth || 600, h = S.host.clientHeight || 420;
  S.renderer.setSize(w, h);
  S.cam.aspect = w / h;
  targets(S, w, h);
  frame(S);
}

// Fit the whole ring of seats (plus their marks) for the host's aspect: a fixed
// camera cropped the front seats and shrank the back ones.
function frame(S) {
  const pts = [];
  for (let i = 0; i < 24; i++) {
    const a = (i / 24) * Math.PI * 2;
    pts.push(new THREE.Vector3(Math.cos(a) * (SEAT_R + 0.45), 0, Math.sin(a) * (SEAT_R + 0.45)));
    pts.push(new THREE.Vector3(Math.cos(a) * (SEAT_R + 0.2), 2.4, Math.sin(a) * (SEAT_R + 0.2)));
  }
  let d = 12;
  for (let pass = 0; pass < 4; pass++) {
    place(S, d, 0.82, 0, TARGET);
    let m = 0;
    pts.forEach((p) => { const v = p.clone().project(S.cam); m = Math.max(m, Math.abs(v.x), Math.abs(v.y) * 1.08); });
    d *= m / 0.92;
  }
  S.d = d;
  S.scene.fog.near = d + 3.5;
  S.scene.fog.far = d + 11;
}

function place(S, d, elev, az, target) {
  S.cam.position.set(target.x + Math.sin(az) * Math.cos(elev) * d, target.y + Math.sin(elev) * d,
    target.z + Math.cos(az) * Math.cos(elev) * d);
  S.cam.lookAt(target);
  S.cam.updateProjectionMatrix();
  S.cam.updateMatrixWorld();
}

// Each frame: a slow cinematic drift (off under reduced motion), a dolly toward the
// focused seat, the core's pulse, the caller's totem/fx updates, then the bloom chain.
function tick(S, now) {
  if (!S.on || document.hidden) { S.raf = 0; return; }
  S.raf = requestAnimationFrame((n) => tick(S, n));
  const dt = Math.min(0.1, (now - (S.last || now)) / 1000);
  S.last = now;
  const t = (now - S.t0) / 1000;
  budget(S, dt, t);
  const goal = S.focus ? 1 : 0;
  S.k += (goal - S.k) * Math.min(1, dt * 5);
  const target = TARGET.clone();
  if (S.focus) target.lerp(S.focus.clone().setY(0.6), S.k);
  const az = S.drift ? Math.sin(t * Math.PI * 2 / 46) * 0.11 : 0;
  const elev = 0.82 + (S.drift ? Math.sin(t * Math.PI * 2 / 31) * 0.012 : 0);
  place(S, S.d * (1 - 0.48 * S.k), elev, az, target);
  S.pulse = Math.max(0, S.pulse - dt * 1.6);
  S.seam.emissiveIntensity = 1.4 + 2.2 * S.pulse;
  S.core.material.opacity = 0.22 + 0.5 * S.pulse;
  S.glow.intensity = 4 + 14 * S.pulse;
  S.fx = S.fx.filter((f) => f.update(now) || (f.dispose(), false));
  S.hooks.onFrame(t, dt);
  render(S);
}

// The frame budget is 60 fps on a laptop. If frames run long for a while, step
// the quality down once each: first the bloom goes, then the pixel ratio drops to
// 1. Never back up (that would oscillate), and the first two seconds don't count
// (shader compiles). Reduced motion renders on demand and never gets here.
function budget(S, dt, t) {
  if (t < 2 || dt <= 0 || S.lockQuality) return;
  S.ema = S.ema === undefined ? dt : S.ema * 0.9 + dt * 0.1;
  if (S.ema < 0.024) { S.slowSince = 0; return; }
  S.slowSince = S.slowSince || t;
  if (t - S.slowSince < 1.5) return;  // slow for a moment is fine; slow for 1.5 s is not
  S.slowSince = 0;
  S.ema = 0.016;
  if (S.bloom) { S.bloom = false; S.quality = "no-bloom"; return; }
  if (S.renderer.getPixelRatio() > 1) {
    S.renderer.setPixelRatio(1);
    S.quality = "no-bloom, 1x";
    resize(S);
  }
}

function render(S) {
  const r = S.renderer;
  if (!S.bloom) { r.setRenderTarget(null); r.render(S.scene, S.cam); return; }
  r.setRenderTarget(S.rtScene);
  r.render(S.scene, S.cam);
  const pass = (mat, from, to, setup) => {
    S.quad.material = mat; mat.uniforms.tex.value = from.texture;
    if (setup) setup(to);
    r.setRenderTarget(to); r.render(S.quadScene, S.quadCam);
  };
  pass(S.bright, S.rtScene, S.rtA);
  const blur = (a, b) => {
    pass(S.blur, a, b, (to) => S.blur.uniforms.dir.value.set(1 / to.width, 0));
    pass(S.blur, b, a, (to) => S.blur.uniforms.dir.value.set(0, 1 / to.height));
  };
  blur(S.rtA, S.rtA2);
  pass(S.bright, S.rtScene, S.rtB);
  blur(S.rtB, S.rtB2); blur(S.rtB, S.rtB2);
  S.quad.material = S.comp;
  S.comp.uniforms.scene.value = S.rtScene.texture;
  S.comp.uniforms.bloomA.value = S.rtA.texture;
  S.comp.uniforms.bloomB.value = S.rtB.texture;
  r.setRenderTarget(null);
  r.render(S.quadScene, S.quadCam);
}

function onClick(S, ev) {
  const b = S.renderer.domElement.getBoundingClientRect();
  const p = new THREE.Vector2(((ev.clientX - b.left) / b.width) * 2 - 1, -((ev.clientY - b.top) / b.height) * 2 + 1);
  S.ray.setFromCamera(p, S.cam);
  const hit = S.ray.intersectObjects(S.pick, true).find((h) => h.object.userData.seat);
  S.hooks.onPick(hit ? hit.object.userData.seat : "");
}

function api(S) {
  return {
    THREE,
    scene: S.scene,
    monolith: S.monolith,
    add(obj, pickable) { S.scene.add(obj); if (pickable) S.pick.push(obj); },
    remove(obj) { S.scene.remove(obj); S.pick = S.pick.filter((o) => o !== obj); },
    effect(f) { S.fx.push(f); },
    pulse(k) { S.pulse = Math.max(S.pulse, k); },
    focus(pos) { S.focus = pos ? pos.clone() : null; },
    project(v) {
      const p = v.clone().project(S.cam), w = S.host.clientWidth, h = S.host.clientHeight;
      return { x: (p.x + 1) / 2 * w, y: (1 - p.y) / 2 * h, behind: p.z > 1 };
    },
    resize() { resize(S); },
    start() { S.on = true; if (!S.raf) { S.last = 0; S.raf = requestAnimationFrame((n) => tick(S, n)); } },
    stop() { S.on = false; },
    once() { S.hooks.onFrame((performance.now() - S.t0) / 1000, 0); render(S); },
    setBloom(on) { S.bloom = on; },
    quality() { return S.quality || "full"; },
  };
}
