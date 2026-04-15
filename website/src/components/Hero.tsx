import { motion, AnimatePresence } from "framer-motion";
import { useEffect, useRef, useState } from "react";

import Waitlist from "./Waitlist";

// ── Typewriter ────────────────────────────────────────────────────────────────

const TYPEWRITER_WORDS = [
  "AI Swarms",
  "Agent Teams",
  "Cursor Agents",
  "Codex Agents",
  "Gemini Agents",
  "Your Swarm",
];

function TypewriterWord() {
  const [index, setIndex] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [deleting, setDeleting] = useState(false);
  const word = TYPEWRITER_WORDS[index];

  useEffect(() => {
    let t: ReturnType<typeof setTimeout>;
    if (!deleting && displayed.length < word.length) {
      t = setTimeout(
        () => setDisplayed(word.slice(0, displayed.length + 1)),
        75,
      );
    } else if (!deleting && displayed.length === word.length) {
      t = setTimeout(() => setDeleting(true), 2000);
    } else if (deleting && displayed.length > 0) {
      t = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 38);
    } else {
      setDeleting(false);
      setIndex((i) => (i + 1) % TYPEWRITER_WORDS.length);
    }
    return () => clearTimeout(t);
  }, [displayed, deleting, word]);

  return (
    <span className="gradient-text">
      {displayed}
      <span className="cursor-blink text-teal-400/80">|</span>
    </span>
  );
}

// ── Product preview terminal ──────────────────────────────────────────────────

type StepType = "human" | "tool-call" | "tool-result";

interface TermStep {
  type: StepType;
  text?: string;
  tool?: string;
  args?: string;
  delay: number;
}

const CLAUDE_CODE_STEPS: TermStep[] = [
  { type: "human", text: "Coordinate auth refactor across 3 agents", delay: 0 },
  {
    type: "tool-call",
    tool: "join_room",
    args: 'room: "dev-sprint"',
    delay: 0,
  },
  { type: "tool-result", text: "✓ Joined · 3 agents online", delay: 400 },
  {
    type: "tool-call",
    tool: "send_room_message",
    args: 'message: "Claiming auth.py"',
    delay: 800,
  },
  { type: "tool-result", text: "✓ Delivered to 2 agents", delay: 1200 },
  {
    type: "tool-call",
    tool: "get_room_state",
    args: 'room: "dev-sprint"',
    delay: 1600,
  },
  {
    type: "tool-result",
    text: "✓ auth.py: claimed · tests/: claimed · routes.py: open",
    delay: 2000,
  },
];

const ClaudeLogo = () => (
  <img
    src="/logos/claude.svg"
    alt="Claude"
    width={14}
    height={14}
    className="object-contain"
  />
);

function HeroTerminal() {
  const [visibleCount, setVisibleCount] = useState(0);

  useEffect(() => {
    // Stop at the end - don't loop (looping causes page scroll jank)
    if (visibleCount >= CLAUDE_CODE_STEPS.length) return;
    const t = setTimeout(() => setVisibleCount((v) => v + 1), 750);
    return () => clearTimeout(t);
  }, [visibleCount]);

  const visibleSteps = CLAUDE_CODE_STEPS.slice(0, visibleCount);

  return (
    <motion.div
      initial={{ opacity: 0, y: 40 }}
      animate={{ opacity: 1, y: 0 }}
      transition={{ duration: 0.9, delay: 0.9, ease: [0.16, 1, 0.3, 1] }}
      className="relative w-full max-w-3xl mx-auto mt-16"
    >
      {/* Glow under terminal */}
      <div
        className="absolute -inset-4 rounded-3xl pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 70% 60% at 50% 100%, rgba(20,184,166,0.2) 0%, transparent 70%)",
          filter: "blur(20px)",
        }}
      />

      {/* Terminal card */}
      <div className="relative rounded-2xl border border-white/10 bg-[#080812]/90 backdrop-blur-xl overflow-hidden shadow-2xl shadow-black/60">
        {/* Title bar */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-white/[0.06] bg-white/[0.02]">
          <div className="flex gap-1.5">
            <span className="w-3 h-3 rounded-full bg-[#ff5f57]" />
            <span className="w-3 h-3 rounded-full bg-[#febc2e]" />
            <span className="w-3 h-3 rounded-full bg-[#28c840]" />
          </div>
          <div className="flex-1 flex items-center justify-center gap-2">
            <ClaudeLogo />
            <span className="text-[11px] font-mono text-white/40">
              Claude Code
            </span>
            <span className="text-white/15 mx-1">·</span>
            <span className="text-[10px] font-mono text-teal-400/50">
              claude-sonnet-4-6
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot" />
            <span className="text-[10px] font-mono text-green-400/60">
              3 agents
            </span>
          </div>
        </div>

        {/* Steps */}
        <div className="px-4 py-4 space-y-2.5 min-h-[200px] font-mono text-[12px]">
          <AnimatePresence>
            {visibleSteps.map((step, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -6 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.2 }}
              >
                {step.type === "human" && (
                  <div className="text-white/35 text-[11px] mb-1">
                    {step.text}
                  </div>
                )}
                {step.type === "tool-call" && (
                  <div className="flex items-start gap-2">
                    <span className="text-teal-400 mt-0.5 shrink-0">●</span>
                    <div>
                      <span className="text-teal-300 font-semibold">
                        {step.tool}
                      </span>
                      <span className="text-white/25 ml-2">{step.args}</span>
                    </div>
                  </div>
                )}
                {step.type === "tool-result" && (
                  <div className="text-green-400/80 flex items-center gap-1.5 pl-4">
                    <span>{step.text}</span>
                  </div>
                )}
              </motion.div>
            ))}
          </AnimatePresence>

          {/* Blinking cursor */}
          {visibleCount < CLAUDE_CODE_STEPS.length && visibleCount > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="pl-4 flex items-center"
            >
              <span className="w-2 h-3.5 bg-teal-400/70 animate-pulse inline-block" />
            </motion.div>
          )}
        </div>

        {/* Bottom bar */}
        <div className="px-4 py-2 border-t border-white/[0.04] flex items-center gap-2">
          <span className="text-[10px] font-mono text-teal-400/50 flex items-center gap-1.5">
            <span className="w-1 h-1 rounded-full bg-teal-400/50" />
            quorus relay · connected
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// Floating nodes removed - they were overlapping with text and causing visual clutter

// ── Main Hero ─────────────────────────────────────────────────────────────────

export default function Hero() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Subtle ambient dot field. Respect prefers-reduced-motion (accessibility)
  // and pause when the hero scrolls out of view (battery + INP).
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const prefersReducedMotion = window.matchMedia(
      "(prefers-reduced-motion: reduce)",
    ).matches;
    if (prefersReducedMotion) return; // Render a static canvas, no animation.

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const particles = Array.from({ length: 30 }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.15,
      vy: (Math.random() - 0.5) * 0.15,
      size: Math.random() * 1.2 + 0.3,
      alpha: Math.random() * 0.18 + 0.04,
    }));

    let running = true;
    let animFrame: number;
    const draw = () => {
      if (!running) return;
      ctx.clearRect(0, 0, canvas.width, canvas.height);
      for (const p of particles) {
        p.x += p.vx;
        p.y += p.vy;
        if (p.x < 0) p.x = canvas.width;
        if (p.x > canvas.width) p.x = 0;
        if (p.y < 0) p.y = canvas.height;
        if (p.y > canvas.height) p.y = 0;
        ctx.beginPath();
        ctx.arc(p.x, p.y, p.size, 0, Math.PI * 2);
        ctx.fillStyle = `rgba(45,212,191,${p.alpha})`;
        ctx.fill();
      }
      animFrame = requestAnimationFrame(draw);
    };

    // Pause when the canvas leaves the viewport (battery, INP)
    const observer = new IntersectionObserver(
      (entries) => {
        const visible = entries[0]?.isIntersecting ?? true;
        if (visible && !running) {
          running = true;
          draw();
        } else if (!visible) {
          running = false;
          cancelAnimationFrame(animFrame);
        }
      },
      { threshold: 0 },
    );
    observer.observe(canvas);

    draw();
    return () => {
      running = false;
      cancelAnimationFrame(animFrame);
      window.removeEventListener("resize", resize);
      observer.disconnect();
    };
  }, []);

  return (
    <section className="relative min-h-screen flex flex-col items-center justify-start overflow-hidden pb-24">
      {/* Grid */}
      <div className="absolute inset-0 grid-bg opacity-50" />

      {/* Particles */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
      />

      {/* ── Single clean ambient gradient ── */}
      <div
        className="absolute pointer-events-none"
        style={{
          width: "100%",
          height: "70%",
          top: 0,
          left: 0,
          background:
            "radial-gradient(ellipse 65% 55% at 50% 0%, rgba(20,184,166,0.12) 0%, transparent 70%)",
        }}
      />

      {/* ── Main content ── */}
      <div className="relative z-10 flex flex-col items-center text-center px-6 w-full max-w-6xl mx-auto pt-48">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-teal-500/25 bg-teal-500/[0.08] text-xs text-teal-300 mb-10 backdrop-blur-sm"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-teal-400 pulse-dot" />
          Private beta · Limited spots open now
        </motion.div>

        {/* ── HEADLINE ── */}
        <motion.h1
          className="font-bold tracking-[-0.03em] leading-[0.92] mb-8"
          style={{ fontSize: "clamp(52px, 8.5vw, 108px)" }}
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.75, delay: 0.08 }}
        >
          <span
            className="block"
            style={{
              background:
                "linear-gradient(135deg, #ccfbf1 0%, #2dd4bf 50%, #14b8a6 100%)",
              WebkitBackgroundClip: "text",
              WebkitTextFillColor: "transparent",
              backgroundClip: "text",
            }}
          >
            Quorus
          </span>
          <span className="block text-white/95 text-[0.55em]">
            Coordination Layer for <TypewriterWord />
          </span>
        </motion.h1>

        {/* Sub-heading */}
        <motion.p
          className="text-lg md:text-xl text-white/60 max-w-2xl mb-10 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.18 }}
        >
          Claude Code, Cursor, Codex, Gemini. Any agent, any model, any machine.
          <br className="hidden md:block" />
          Rooms, SSE push, shared state, distributed locks.{" "}
          <span className="text-white/80">Zero config.</span>
        </motion.p>

        {/* Waitlist */}
        <motion.div
          className="w-full max-w-md mb-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.26 }}
        >
          <Waitlist
            size="lg"
            label="Request access"
            variant="dark"
            className="w-full"
          />
        </motion.div>

        {/* Install command — copyable */}
        <motion.div
          initial={{ opacity: 0, y: 12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6, delay: 0.32 }}
          className="w-full max-w-2xl mb-6"
        >
          <div className="group flex items-center gap-3 px-4 py-3 rounded-xl border border-white/10 bg-white/[0.03] backdrop-blur-sm font-mono text-xs sm:text-sm overflow-x-auto">
            <span className="text-teal-400 shrink-0" aria-hidden="true">
              $
            </span>
            <code className="text-white/80 whitespace-nowrap">
              pipx install &quot;quorus @
              git+https://github.com/Quorus-dev/Quorus.git&quot;
            </code>
            <button
              type="button"
              aria-label="Copy install command"
              onClick={() =>
                navigator.clipboard
                  .writeText(
                    'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"',
                  )
                  .catch(() => {})
              }
              className="ml-auto shrink-0 text-white/40 hover:text-teal-300 transition-colors focus-visible:outline-2 focus-visible:outline-teal-400 focus-visible:outline-offset-2 rounded"
            >
              <svg
                className="w-4 h-4"
                fill="none"
                stroke="currentColor"
                viewBox="0 0 24 24"
                aria-hidden="true"
              >
                <path
                  strokeLinecap="round"
                  strokeLinejoin="round"
                  strokeWidth={1.5}
                  d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                />
              </svg>
            </button>
          </div>
          <p className="text-center text-[11px] text-white/45 mt-2 font-mono">
            Then just type <span className="text-teal-400">quorus</span> in your
            terminal · v0.4.0 beta · MIT
          </p>
        </motion.div>

        {/* Capability pills */}
        <motion.div
          className="flex flex-wrap items-center justify-center gap-2"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 0.4 }}
        >
          {[
            "SSE push",
            "Zero polling",
            "Distributed locks",
            "Any model",
            "Real-time state",
            "MCP native",
          ].map((pill, i) => (
            <motion.span
              key={pill}
              initial={{ opacity: 0, scale: 0.88 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.45 + i * 0.05 }}
              className="px-3 py-1 rounded-full border border-white/[0.08] bg-white/[0.03] text-[11px] text-white/35 hover:border-white/15 hover:text-white/55 transition-all cursor-default"
            >
              {pill}
            </motion.span>
          ))}
        </motion.div>

        {/* ── Product preview ── */}
        <HeroTerminal />

        {/* Scroll hint */}
        <motion.button
          onClick={() =>
            document
              .getElementById("features")
              ?.scrollIntoView({ behavior: "smooth" })
          }
          className="mt-12 text-sm text-white/25 hover:text-white/55 transition-colors flex flex-col items-center gap-2"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ delay: 1.8 }}
        >
          <motion.div
            className="w-px h-8 bg-gradient-to-b from-transparent via-white/20 to-transparent"
            animate={{ scaleY: [1, 1.4, 1], opacity: [0.3, 0.7, 0.3] }}
            transition={{ duration: 2, repeat: Infinity }}
          />
          <span className="text-xs font-mono tracking-widest">SCROLL</span>
        </motion.button>
      </div>
    </section>
  );
}
