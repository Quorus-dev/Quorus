// The hero's second beat: the Quorum view's own stage. quorum-stage.js and
// quorum-totems.js are the files the app ships (quorus/web), copied in at build time
// with three.js r180, so what renders here is the real view's code, not a mock-up.
// It seats the demo run's three members and plays the same script the hero
// replays (run.ts). Desktop only, loaded after the page is idle and the band is in
// view; phones, no-WebGL and reduced-motion keep the still. No relay, no data path.
import { RUN } from "./run.ts";

interface Vec3 { x: number; y: number; z: number; clone(): Vec3; setY(y: number): Vec3; }
interface Seat { position: Vec3 & { set(x: number, y: number, z: number): void }; userData: { state: string; angle: number }; }
interface Stage {
  monolith: Vec3;
  add(obj: unknown, pickable?: boolean): void;
  effect(fx: unknown): void;
  pulse(k: number): void;
  project(v: Vec3): { x: number; y: number; behind: boolean };
  resize(): void;
  start(): void;
  stop(): void;
}
interface StageModule {
  SEAT_R: number;
  palette(dark: boolean, accent: string, bg: string): unknown;
  create(host: HTMLElement, palette: unknown,
    hooks: { onFrame(t: number, dt: number): void; onPick(name: string): void; reduceMotion: boolean }): Stage;
}
interface TotemModule {
  makeTotem(name: string, vendor: string | null, color: string, palette: unknown): Seat;
  updateTotem(seat: Seat, t: number, dt: number, reduce: boolean, accent: string, stage: Stage): void;
  arc(stage: Stage, a: Vec3, b: Vec3, colors: string[], now: number): unknown;
  burst(stage: Stage, from: Vec3, color: string, now: number): unknown;
}

// The three members of the demo run, coloured as the view colours them: by the
// harness in the name, a person in warm white. No logos.
const MEMBERS = [
  { name: "maya", vendor: null, color: "#e8e4da" },
  { name: "maya-claude", vendor: "claude", color: "#d97757" },
  { name: "maya-codex", vendor: "codex", color: "#10a37f" },
];
const PUBLISHED = /\(quorus\) published \d+ commit/;
const OK = /✅|\bapproved?\b/;

function cssVar(name: string, fallback: string): string {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim() || fallback;
}

function plaque(layer: HTMLElement, name: string, color: string): HTMLElement {
  const t = document.createElement("div");
  t.className = "qtag";
  const who = document.createElement("b"), status = document.createElement("span");
  who.textContent = "@" + name;
  status.className = "qs";
  t.append(who, status);
  t.style.setProperty("--seat", color);
  layer.append(t);
  return t;
}

export async function mountStage(host: HTMLElement): Promise<void> {
  // Runtime URLs, not bundled: the files are copied from quorus/web by `npm run build`.
  const shipped = (file: string) => "/quorum/" + file;
  const [S, T] = (await Promise.all([
    import(/* @vite-ignore */ shipped("quorum-stage.js")),
    import(/* @vite-ignore */ shipped("quorum-totems.js")),
  ])) as [StageModule, TotemModule];
  const accent = cssVar("--accent", "#3fd2b0");
  const P = S.palette(true, accent, cssVar("--bg", "#0f1412"));
  const layer = document.createElement("div");
  layer.className = "qtags";
  const seats = new Map<string, Seat>(), tags = new Map<string, HTMLElement>();
  let live = false;

  // Plaques follow their seats in screen space, as in the app; three seats never clash.
  const placeTags = () => {
    const w = host.clientWidth, h = host.clientHeight;
    seats.forEach((s, name) => {
      const t = tags.get(name)!;
      const p = stage.project(s.position.clone().setY(2.25));
      t.hidden = p.behind;
      const left = Math.max(4, Math.min(w - t.offsetWidth - 4, p.x - t.offsetWidth / 2));
      const top = Math.max(4, Math.min(h - t.offsetHeight - 4, p.y - t.offsetHeight));
      t.style.transform = `translate(${Math.round(left)}px, ${Math.round(top)}px)`;
    });
  };
  const stage = S.create(host, P, {
    reduceMotion: false,
    onPick: () => undefined,
    onFrame(t, dt) {
      seats.forEach((s) => T.updateTotem(s, t, dt, false, accent, stage));
      placeTags();
      if (!live) { live = true; host.classList.add("live"); }  // the still fades out
    },
  });
  host.append(layer);
  MEMBERS.forEach((m, i) => {
    const seat = T.makeTotem(m.name, m.vendor, m.color, P);
    const a = (i / MEMBERS.length) * Math.PI * 2 + Math.PI / 2;  // the person in front
    seat.position.set(Math.cos(a) * S.SEAT_R, 0, Math.sin(a) * S.SEAT_R);
    seat.userData.angle = a;
    seat.userData.state = "idle";
    stage.add(seat, false);
    seats.set(m.name, seat);
    tags.set(m.name, plaque(layer, m.name, m.color));
  });
  const colourOf = (name: string) => MEMBERS.find((m) => m.name === name)?.color ?? accent;
  const setState = (name: string, state: string, text: string) => {
    const seat = seats.get(name), tag = tags.get(name);
    if (seat) seat.userData.state = state;
    if (tag) { tag.dataset.state = state; tag.children[1].textContent = text; }
  };

  // The script: the same beats as the hero, read as the view reads room traffic.
  // An @-mention is an arc from sender to the named seat, "(quorus) published" a
  // burst to the monolith, an approval a two-colour braid back to the author.
  let timers: number[] = [];
  const reset = () => MEMBERS.forEach((m) => setState(m.name, m.vendor ? "idle" : "human", m.vendor ? "idle" : ""));
  const play = () => {
    timers.forEach(clearTimeout);
    timers = [];
    reset();
    for (const b of RUN) {
      timers.push(window.setTimeout(() => {
        const now = performance.now();
        if (b.kind === "end") { play(); return; }
        if (b.kind === "banner") { stage.pulse(1); return; }
        const who = b.who.slice(1), from = seats.get(who);
        if (b.kind === "working") { setState(who, "busy", "working"); return; }
        stage.pulse(0.6);
        if (!from) return;
        const body = b.lines.join("\n");
        if (b.who !== "@maya") setState(who, "idle", OK.test(body) ? "✅ " + (PUBLISHED.test(body) ? "shipped" : "approved") : "idle");
        if (PUBLISHED.test(body)) stage.effect(T.burst(stage, from.position, colourOf(who), now));
        const review = OK.test(body) && b.who === "@maya-codex";
        for (const tag of body.match(/@[\w-]+/g) ?? []) {
          const to = seats.get(tag.slice(1));
          if (to && to !== from) stage.effect(T.arc(stage, from.position, to.position, [colourOf(who)], now));
        }
        if (review) {
          const author = seats.get("maya-claude")!;
          stage.effect(T.arc(stage, from.position, author.position, [colourOf(who), colourOf("maya-claude")], now));
        }
      }, b.at));
    }
  };
  const stop = () => { timers.forEach(clearTimeout); timers = []; stage.stop(); };

  new ResizeObserver(() => { stage.resize(); placeTags(); }).observe(host);
  // Draw only while the band is on screen and the tab is visible.
  let inView = false;
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    inView = e.isIntersecting;
    if (inView) { stage.start(); play(); } else stop();
  }), { threshold: 0.15 });
  io.observe(host);
  document.addEventListener("visibilitychange", () => {
    if (document.hidden) stop(); else if (inView) { stage.start(); play(); }
  });
}
