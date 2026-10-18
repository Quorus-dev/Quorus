// Motion for quorus.dev, loaded after first paint by main.ts. Everything here is
// transform/opacity (plus SVG stroke drawing), driven by the Web Animations API with
// one easing family, and it never runs under prefers-reduced-motion: main.ts only
// imports this module when motion is allowed. Text is built with textContent only.

import { RUN, type Beat } from "./run.ts";

const EASE = "cubic-bezier(0.16, 1, 0.3, 1)"; // out-expo, no bounce
const el = (tag: string, cls = "", text?: string) => {
  const e = document.createElement(tag);
  if (cls) e.className = cls;
  if (text !== undefined) e.textContent = text;
  return e;
};

// ---- the hero: a replay of the real demo run (the script lives in run.ts) ----------
function messageEl(b: Extract<Beat, { kind: "msg" }>): HTMLElement {
  const m = el("div", "tmsg" + (b.me ? " me" : ""));
  const head = el("div", "twho");
  head.append(el("span", "dot " + (b.who === "@maya-codex" ? "codex" : b.who === "@maya-claude" ? "claude" : "human")), el("b", "", b.who), el("time", "", b.time));
  m.append(head);
  for (const line of b.lines) {
    const p = el("p", line === "…" ? "ellipsis" : line.startsWith("(quorus)") ? "sys" : "", line);
    m.append(p);
  }
  if (b.me) m.append(el("span", "delivered", "Delivered"));
  return m;
}

function workingEl(who: string): HTMLElement {
  const w = el("div", "tworking");
  w.append(el("span", "", "⋯ " + who + " working"));
  const dots = el("span", "dots");
  for (let i = 0; i < 3; i++) dots.append(el("i"));
  w.append(dots);
  return w;
}

function enter(node: HTMLElement, dy = 10, dur = 520) {
  node.animate([{ opacity: 0, transform: `translateY(${dy}px)` }, { opacity: 1, transform: "none" }],
    { duration: dur, easing: EASE, fill: "both" });
}

export function replay(log: HTMLElement, banner: HTMLElement): () => void {
  // The markup ships with the finished run; it is what reduced-motion and no-JS
  // visitors see, and what the log goes back to whenever the replay is stopped.
  const finished = Array.from(log.children).map((n) => n.cloneNode(true));
  const showFinished = () => { log.replaceChildren(...finished.map((n) => n.cloneNode(true))); banner.classList.add("in"); };
  let timers: number[] = [];
  let working: HTMLElement | null = null;
  let running = false, inView = false;
  const stop = () => {
    timers.forEach(clearTimeout); timers = [];
    if (running) { running = false; working = null; showFinished(); }
  };
  const run = () => {
    timers.forEach(clearTimeout); timers = [];
    running = true;
    let fresh = true; // the log clears when the first message lands, never before
    for (const b of RUN) {
      timers.push(window.setTimeout(() => {
        if (fresh) { fresh = false; log.replaceChildren(); banner.classList.remove("in"); }
        if (working && b.kind !== "end") { working.remove(); working = null; }
        if (b.kind === "msg") { const m = messageEl(b); log.append(m); enter(m, 12); }
        else if (b.kind === "working") { working = workingEl(b.who); log.append(working); enter(working, 6, 300); }
        else if (b.kind === "banner") { banner.classList.add("in"); }
        else if (b.kind === "end") { run(); }
        log.scrollTop = log.scrollHeight;
      }, b.at));
    }
  };
  // Only animate while the hero is on screen; a hidden tab or a scrolled-away hero
  // shows the finished run instead. Half the terminal must be in view: on a phone
  // its top edge arrives first, and messages land at the bottom.
  const io = new IntersectionObserver((es) => es.forEach((e) => {
    inView = e.isIntersecting;
    if (inView) run(); else stop();
  }), { threshold: 0.5 });
  io.observe(log.closest(".term") ?? log);
  document.addEventListener("visibilitychange", () => (document.hidden ? stop() : inView && !running ? run() : undefined));
  return stop;
}

// ---- scroll story -----------------------------------------------------------------
function drawWires(svg: SVGSVGElement) {
  svg.querySelectorAll<SVGPathElement>(".wires path").forEach((path, i) => {
    const len = path.getTotalLength();
    path.style.strokeDasharray = `${len}`;
    path.animate([{ strokeDashoffset: len }, { strokeDashoffset: 0 }],
      { duration: 900, delay: 120 + i * 60, easing: EASE, fill: "both" });
  });
}

function countUp(cell: HTMLElement) {
  const target = Number(cell.dataset.n ?? "0"), pct = cell.dataset.pct === "1";
  const t0 = performance.now(), dur = 900 + Math.min(600, target * 2);
  const tick = (now: number) => {
    const k = Math.min(1, (now - t0) / dur), e = 1 - Math.pow(1 - k, 3);
    cell.textContent = `${Math.round(target * e)}${pct ? "%" : ""}`;
    if (k < 1) requestAnimationFrame(tick);
  };
  requestAnimationFrame(tick);
}

function claimRow(row: HTMLElement) {
  row.animate([{ opacity: 0, transform: "translateX(-10px)" }, { opacity: 1, transform: "none" }],
    { duration: 600, delay: 500, easing: EASE, fill: "both" });
  row.animate([{ backgroundColor: "rgba(232,166,58,.28)" }, { backgroundColor: "rgba(232,166,58,0)" }],
    { duration: 1400, delay: 500, easing: "ease-out", fill: "both" });
}

export function story() {
  const once = new IntersectionObserver((entries) => {
    for (const e of entries) {
      if (!e.isIntersecting) continue;
      const t = e.target as HTMLElement;
      if (t.matches("svg")) drawWires(t as unknown as SVGSVGElement);
      else if (t.matches("table.score")) t.querySelectorAll<HTMLElement>("td.n").forEach(countUp);
      else if (t.matches("table.locks")) t.querySelectorAll<HTMLElement>("tr.claim").forEach(claimRow);
      once.unobserve(t);
    }
  }, { rootMargin: "0px 0px -15% 0px" });
  document.querySelectorAll(".compare svg, table.score, table.locks").forEach((n) => once.observe(n));
}

// ---- micro -----------------------------------------------------------------------
export function micro() {
  if (!matchMedia("(pointer: fine)").matches) return;
  // Magnetic copy button: leans toward the pointer within 90px, snaps back after.
  document.querySelectorAll<HTMLButtonElement>(".cmd button").forEach((btn) => {
    const box = btn.parentElement as HTMLElement;
    box.addEventListener("pointermove", (ev) => {
      const r = btn.getBoundingClientRect();
      const dx = ev.clientX - (r.left + r.width / 2), dy = ev.clientY - (r.top + r.height / 2);
      const d = Math.hypot(dx, dy);
      const k = d < 90 ? (1 - d / 90) * 6 : 0;
      btn.style.transform = k ? `translate(${(dx / d) * k}px, ${(dy / d) * k}px)` : "";
    });
    box.addEventListener("pointerleave", () => { btn.style.transform = ""; });
  });
  // A soft glow that follows the cursor, well below the content.
  const glow = document.querySelector<HTMLElement>(".cursor-glow");
  if (glow) {
    let x = innerWidth / 2, y = innerHeight / 3, tx = x, ty = y, raf = 0;
    const step = () => {
      x += (tx - x) * 0.12; y += (ty - y) * 0.12;
      glow.style.transform = `translate3d(${x - 300}px, ${y - 300}px, 0)`;
      raf = Math.abs(tx - x) + Math.abs(ty - y) > 0.5 ? requestAnimationFrame(step) : 0;
    };
    addEventListener("pointermove", (ev) => { tx = ev.clientX; ty = ev.clientY; glow.classList.add("on"); if (!raf) raf = requestAnimationFrame(step); }, { passive: true });
  }
}

export function start() {
  const log = document.getElementById("replayLog"), banner = document.getElementById("replayBanner");
  if (log && banner) replay(log, banner);
  story();
  micro();
}
