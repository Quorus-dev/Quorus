"use client";

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
      <span className="cursor-blink text-violet-400/80">|</span>
    </span>
  );
}

// ── Product preview terminal ──────────────────────────────────────────────────

const PREVIEW_MSGS = [
  {
    agent: "claude-code",
    color: "#a78bfa",
    content: "Claiming src/auth.py — starting refactor",
    badge: "CLAIM",
  },
  {
    agent: "cursor-1",
    color: "#60a5fa",
    content: "On it — grabbing tests/ directory",
    badge: null,
  },
  {
    agent: "codex-1",
    color: "#34d399",
    content: "LOCK acquired: api/routes.py",
    badge: "LOCK",
  },
  {
    agent: "claude-code",
    color: "#a78bfa",
    content: "Auth middleware rewritten. Tests passing ✓",
    badge: "DONE",
  },
  {
    agent: "cursor-1",
    color: "#60a5fa",
    content: "PR ready — 14 files changed, 0 conflicts",
    badge: null,
  },
];

function HeroTerminal() {
  const [visible, setVisible] = useState(0);

  useEffect(() => {
    if (visible >= PREVIEW_MSGS.length) {
      const reset = setTimeout(() => setVisible(0), 3000);
      return () => clearTimeout(reset);
    }
    const t = setTimeout(() => setVisible((v) => v + 1), 900);
    return () => clearTimeout(t);
  }, [visible]);

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
            "radial-gradient(ellipse 70% 60% at 50% 100%, rgba(124,58,237,0.22) 0%, transparent 70%)",
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
          <div className="flex-1 text-center">
            <span className="text-[11px] font-mono text-white/30">
              murmur console
            </span>
            <span className="text-white/15 mx-2">·</span>
            <span className="text-[11px] font-mono text-violet-400/60">
              #dev-room
            </span>
          </div>
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot" />
            <span className="text-[10px] font-mono text-green-400/60">
              3 agents
            </span>
          </div>
        </div>

        {/* Messages */}
        <div className="px-4 py-4 space-y-3 min-h-[180px]">
          <AnimatePresence>
            {PREVIEW_MSGS.slice(0, visible).map((msg, i) => (
              <motion.div
                key={i}
                initial={{ opacity: 0, x: -8 }}
                animate={{ opacity: 1, x: 0 }}
                transition={{ duration: 0.25 }}
                className="flex items-start gap-3"
              >
                <span
                  className="w-1.5 h-1.5 rounded-full mt-[5px] shrink-0"
                  style={{ background: msg.color }}
                />
                <div className="min-w-0 flex-1">
                  <div className="flex items-center gap-2 mb-0.5">
                    <span
                      className="text-[12px] font-mono font-semibold"
                      style={{ color: msg.color }}
                    >
                      {msg.agent}
                    </span>
                    {msg.badge && (
                      <span
                        className="text-[9px] font-mono px-1.5 py-0.5 rounded border"
                        style={{
                          color: msg.color,
                          borderColor: `${msg.color}40`,
                          background: `${msg.color}15`,
                        }}
                      >
                        {msg.badge}
                      </span>
                    )}
                  </div>
                  <p className="text-[13px] text-white/55 font-mono">
                    {msg.content}
                  </p>
                </div>
              </motion.div>
            ))}
          </AnimatePresence>

          {/* Typing indicator */}
          {visible < PREVIEW_MSGS.length && visible > 0 && (
            <motion.div
              initial={{ opacity: 0 }}
              animate={{ opacity: 1 }}
              className="flex items-center gap-2 pl-[18px]"
            >
              <span className="flex gap-1">
                {[0, 1, 2].map((i) => (
                  <span
                    key={i}
                    className="w-1 h-1 rounded-full bg-white/20"
                    style={{
                      animation: `pulse-dot 1.2s ease-in-out ${i * 0.2}s infinite`,
                    }}
                  />
                ))}
              </span>
            </motion.div>
          )}
        </div>

        {/* Bottom bar */}
        <div className="px-4 py-2 border-t border-white/[0.04] flex items-center gap-2">
          <span className="text-[10px] font-mono text-violet-400/50 flex items-center gap-1.5">
            <span className="w-1 h-1 rounded-full bg-violet-400/50" />
            murmur relay · 3.6ms p50
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Floating agent nodes (desktop only) ───────────────────────────────────────

const AGENT_NODES = [
  {
    id: "a1",
    label: "claude-code",
    status: "building auth…",
    locked: true,
    x: -310,
    y: -40,
    delay: 0,
  },
  {
    id: "a2",
    label: "cursor-1",
    status: "reviewing PR",
    locked: false,
    x: 310,
    y: -50,
    delay: 0.3,
  },
  {
    id: "a3",
    label: "codex-1",
    status: "tests passing",
    locked: false,
    x: 0,
    y: 200,
    delay: 0.6,
  },
];

function FloatingAgentNode({ node }: { node: (typeof AGENT_NODES)[0] }) {
  // Outer div handles positioning + centering (static CSS, no Framer Motion transforms)
  // Inner motion.div handles fade-in + float (no transform conflicts)
  return (
    <div
      className="absolute hidden xl:block pointer-events-none"
      style={{
        left: `calc(50% + ${node.x}px)`,
        top: `calc(50% + ${node.y}px)`,
        transform: "translate(-50%, -50%)",
      }}
    >
      <motion.div
        initial={{ opacity: 0, scale: 0.8, y: 0 }}
        animate={{
          opacity: 1,
          scale: 1,
          y: [0, -10, 0],
        }}
        transition={{
          opacity: { delay: 1.4 + node.delay, duration: 0.7, ease: "easeOut" },
          scale: { delay: 1.4 + node.delay, duration: 0.7, ease: "easeOut" },
          y: {
            delay: 1.6 + node.delay,
            duration: 3.2 + node.delay * 0.5,
            repeat: Infinity,
            ease: "easeInOut",
            repeatType: "mirror",
          },
        }}
        className="px-3 py-2 rounded-xl border border-white/[0.09] bg-black/60 backdrop-blur-xl text-xs font-mono min-w-[148px] shadow-xl shadow-black/50"
      >
        <div className="flex items-center justify-between gap-2 mb-1">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot shrink-0" />
            <span className="text-white/75 font-semibold">{node.label}</span>
          </div>
          {node.locked && (
            <span className="text-[9px] px-1.5 py-0.5 rounded bg-violet-500/20 border border-violet-500/30 text-violet-300">
              locked
            </span>
          )}
        </div>
        <span className="text-white/30 text-[10px]">{node.status}</span>
      </motion.div>
    </div>
  );
}

// ── Main Hero ─────────────────────────────────────────────────────────────────

export default function Hero() {
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Particle field
  useEffect(() => {
    const canvas = canvasRef.current;
    if (!canvas) return;
    const ctx = canvas.getContext("2d");
    if (!ctx) return;

    const resize = () => {
      canvas.width = canvas.offsetWidth;
      canvas.height = canvas.offsetHeight;
    };
    resize();
    window.addEventListener("resize", resize);

    const particles = Array.from({ length: 55 }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.25,
      vy: (Math.random() - 0.5) * 0.25,
      size: Math.random() * 1.8 + 0.4,
      alpha: Math.random() * 0.35 + 0.08,
      hue: Math.random() > 0.4 ? 270 : 285,
    }));

    let animFrame: number;
    const draw = () => {
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
        ctx.fillStyle = `hsla(${p.hue}, 85%, 72%, ${p.alpha})`;
        ctx.fill();
      }
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const d = Math.sqrt(dx * dx + dy * dy);
          if (d < 90) {
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = `rgba(124,58,237,${0.07 * (1 - d / 90)})`;
            ctx.lineWidth = 0.5;
            ctx.stroke();
          }
        }
      }
      animFrame = requestAnimationFrame(draw);
    };
    draw();
    return () => {
      cancelAnimationFrame(animFrame);
      window.removeEventListener("resize", resize);
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

      {/* ── Background glow stack ── */}
      {/* Big violet orb — center-top */}
      <motion.div
        className="absolute pointer-events-none"
        style={{
          width: 900,
          height: 650,
          top: "-5%",
          left: "50%",
          translateX: "-50%",
          background:
            "radial-gradient(ellipse, rgba(124,58,237,0.38) 0%, rgba(109,40,217,0.14) 45%, transparent 70%)",
          filter: "blur(50px)",
        }}
        animate={{
          scale: [1, 1.06, 0.97, 1.03, 1],
          opacity: [0.85, 1, 0.88, 1, 0.85],
        }}
        transition={{ duration: 10, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* Inner tight headline halo */}
      <div
        className="absolute pointer-events-none"
        style={{
          width: 600,
          height: 360,
          top: "12%",
          left: "50%",
          transform: "translateX(-50%)",
          background:
            "radial-gradient(ellipse, rgba(139,92,246,0.22) 0%, transparent 70%)",
          filter: "blur(70px)",
        }}
      />
      {/* Left accent — violet variant */}
      <motion.div
        className="absolute pointer-events-none"
        style={{
          width: 480,
          height: 380,
          top: "28%",
          left: "2%",
          background:
            "radial-gradient(ellipse, rgba(109,40,217,0.12) 0%, transparent 70%)",
          filter: "blur(45px)",
        }}
        animate={{
          scale: [1, 1.18, 1, 1.08, 1],
          x: [0, 18, 0, -10, 0],
          opacity: [0.4, 0.7, 0.5, 0.75, 0.4],
        }}
        transition={{
          duration: 14,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 1.5,
        }}
      />
      {/* Pink/magenta right accent — new warmth */}
      <motion.div
        className="absolute pointer-events-none"
        style={{
          width: 320,
          height: 280,
          top: "20%",
          right: "5%",
          background:
            "radial-gradient(ellipse, rgba(236,72,153,0.10) 0%, transparent 70%)",
          filter: "blur(40px)",
        }}
        animate={{ scale: [1, 1.15, 0.92, 1.18, 1], y: [0, -20, 10, -8, 0] }}
        transition={{
          duration: 14,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 3,
        }}
      />
      {/* Bottom beam — horizontal streak */}
      <div
        className="absolute pointer-events-none"
        style={{
          height: 1,
          width: "80%",
          top: "58%",
          left: "10%",
          background:
            "linear-gradient(90deg, transparent 0%, rgba(124,58,237,0.35) 25%, rgba(167,139,250,0.55) 50%, rgba(124,58,237,0.35) 75%, transparent 100%)",
          boxShadow: "0 0 60px 12px rgba(124,58,237,0.18)",
        }}
      />

      {/* Floating agent nodes */}
      {AGENT_NODES.map((node) => (
        <FloatingAgentNode key={node.id} node={node} />
      ))}

      {/* ── Main content ── */}
      <div className="relative z-10 flex flex-col items-center text-center px-6 w-full max-w-6xl mx-auto pt-36">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: -12 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="inline-flex items-center gap-2 px-3.5 py-1.5 rounded-full border border-violet-500/25 bg-violet-500/8 text-xs text-violet-300 mb-10 backdrop-blur-sm"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 pulse-dot" />
          Private beta · Limited spots open now
        </motion.div>

        {/* ── HEADLINE — much bigger ── */}
        <motion.h1
          className="font-bold tracking-tight leading-[0.95] mb-7"
          style={{ fontSize: "clamp(46px, 7.5vw, 96px)" }}
          initial={{ opacity: 0, y: 24 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.75, delay: 0.08 }}
        >
          <span className="block text-white">
            The <span className="text-shimmer">Communication</span>
          </span>
          <span className="block text-white">
            Layer for <TypewriterWord />
          </span>
        </motion.h1>

        {/* Sub-heading */}
        <motion.p
          className="text-lg md:text-xl text-white/60 max-w-2xl mb-10 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.18 }}
        >
          Claude Code, Cursor, Codex, Gemini — any agent, any model, any
          machine.
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
          <Waitlist size="lg" label="Request access" className="w-full" />
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
