"use client";

import { useState, useEffect, useRef } from "react";
import {
  motion,
  useMotionValue,
  useSpring,
  AnimatePresence,
} from "framer-motion";

const INSTALL_CMD = `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`;

const TYPEWRITER_WORDS = [
  "AI Swarms",
  "Agent Teams",
  "Claude Agents",
  "Distributed AI",
  "Your Swarm",
];

function TypewriterWord() {
  const [index, setIndex] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [deleting, setDeleting] = useState(false);
  const word = TYPEWRITER_WORDS[index];

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    if (!deleting && displayed.length < word.length) {
      timeout = setTimeout(
        () => setDisplayed(word.slice(0, displayed.length + 1)),
        80,
      );
    } else if (!deleting && displayed.length === word.length) {
      timeout = setTimeout(() => setDeleting(true), 2200);
    } else if (deleting && displayed.length > 0) {
      timeout = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 40);
    } else if (deleting && displayed.length === 0) {
      setDeleting(false);
      setIndex((i) => (i + 1) % TYPEWRITER_WORDS.length);
    }
    return () => clearTimeout(timeout);
  }, [displayed, deleting, word]);

  return (
    <span className="gradient-text">
      {displayed}
      <span className="cursor-blink text-violet-400">|</span>
    </span>
  );
}

const SUBHEADLINES = [
  "Any model. Any machine. Real-time coordination.",
  "3 lines of Python. Infinite agent reach.",
  "SSE push, shared state, distributed locks — built in.",
  "The missing communication layer for AI swarms.",
];

function TypewriterSubheadline() {
  const [index, setIndex] = useState(0);
  const [displayed, setDisplayed] = useState("");
  const [phase, setPhase] = useState<"typing" | "holding" | "erasing">(
    "typing",
  );
  const text = SUBHEADLINES[index];

  useEffect(() => {
    let timeout: ReturnType<typeof setTimeout>;
    if (phase === "typing" && displayed.length < text.length) {
      timeout = setTimeout(
        () => setDisplayed(text.slice(0, displayed.length + 1)),
        38,
      );
    } else if (phase === "typing" && displayed.length === text.length) {
      timeout = setTimeout(() => setPhase("holding"), 2800);
    } else if (phase === "holding") {
      timeout = setTimeout(() => setPhase("erasing"), 0);
    } else if (phase === "erasing" && displayed.length > 0) {
      timeout = setTimeout(() => setDisplayed(displayed.slice(0, -1)), 22);
    } else if (phase === "erasing" && displayed.length === 0) {
      setIndex((i) => (i + 1) % SUBHEADLINES.length);
      setPhase("typing");
    }
    return () => clearTimeout(timeout);
  }, [displayed, phase, text]);

  return (
    <span>
      {displayed}
      <span className="text-violet-400 opacity-70">|</span>
    </span>
  );
}

const AGENT_NODES = [
  {
    id: "a1",
    label: "claude-1",
    status: "building auth...",
    locked: true,
    x: -290,
    y: -70,
    delay: 0,
  },
  {
    id: "a2",
    label: "claude-2",
    status: "reviewing PR",
    locked: false,
    x: 290,
    y: -80,
    delay: 0.3,
  },
  {
    id: "a3",
    label: "codex-3",
    status: "running tests",
    locked: false,
    x: 0,
    y: 130,
    delay: 0.6,
  },
];

function FloatingAgentNode({ node }: { node: (typeof AGENT_NODES)[0] }) {
  const yOffset = useMotionValue(0);
  const springY = useSpring(yOffset, { stiffness: 25, damping: 8 });

  useEffect(() => {
    let frame: number;
    const start = Date.now() + node.delay * 1000;
    const tick = () => {
      const t = (Date.now() - start) / 1000;
      yOffset.set(Math.sin(t * 0.5 + node.delay) * 14);
      frame = requestAnimationFrame(tick);
    };
    frame = requestAnimationFrame(tick);
    return () => cancelAnimationFrame(frame);
  }, [node.delay, yOffset]);

  return (
    <motion.div
      className="absolute hidden lg:flex flex-col gap-1 pointer-events-none"
      style={{
        left: `calc(50% + ${node.x}px)`,
        top: `calc(50% + ${node.y}px)`,
        y: springY,
        translateX: "-50%",
        translateY: "-50%",
      }}
      initial={{ opacity: 0, scale: 0.75 }}
      animate={{ opacity: 1, scale: 1 }}
      transition={{ delay: 1.2 + node.delay, duration: 0.7, ease: "easeOut" }}
    >
      <div className="px-3 py-2.5 rounded-xl border border-white/10 bg-black/70 backdrop-blur-lg text-xs font-mono min-w-[140px] shadow-lg shadow-black/40">
        <div className="flex items-center justify-between gap-2 mb-1.5">
          <div className="flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot shrink-0" />
            <span className="text-white/80 font-semibold">{node.label}</span>
          </div>
          {node.locked && (
            <span className="text-[9px] px-1.5 py-0.5 rounded-md bg-violet-500/20 border border-violet-500/30 text-violet-300">
              locked
            </span>
          )}
        </div>
        <span className="text-white/35 text-[10px]">{node.status}</span>
      </div>
    </motion.div>
  );
}

export default function Hero() {
  const [copied, setCopied] = useState(false);
  const canvasRef = useRef<HTMLCanvasElement>(null);

  // Aurora particle field
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

    type Particle = {
      x: number;
      y: number;
      vx: number;
      vy: number;
      size: number;
      alpha: number;
      hue: number;
    };
    const particles: Particle[] = Array.from({ length: 60 }, () => ({
      x: Math.random() * canvas.width,
      y: Math.random() * canvas.height,
      vx: (Math.random() - 0.5) * 0.3,
      vy: (Math.random() - 0.5) * 0.3,
      size: Math.random() * 2 + 0.5,
      alpha: Math.random() * 0.4 + 0.1,
      hue: Math.random() > 0.5 ? 270 : 190, // violet or cyan
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
        ctx.fillStyle = `hsla(${p.hue}, 80%, 70%, ${p.alpha})`;
        ctx.fill();
      }
      // Draw connecting lines between nearby particles
      for (let i = 0; i < particles.length; i++) {
        for (let j = i + 1; j < particles.length; j++) {
          const dx = particles[i].x - particles[j].x;
          const dy = particles[i].y - particles[j].y;
          const dist = Math.sqrt(dx * dx + dy * dy);
          if (dist < 100) {
            ctx.beginPath();
            ctx.moveTo(particles[i].x, particles[i].y);
            ctx.lineTo(particles[j].x, particles[j].y);
            ctx.strokeStyle = `rgba(124, 58, 237, ${0.08 * (1 - dist / 100)})`;
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

  const copy = () => {
    navigator.clipboard.writeText(INSTALL_CMD);
    setCopied(true);
    setTimeout(() => setCopied(false), 2000);
  };

  return (
    <section className="relative min-h-screen flex flex-col items-center justify-center overflow-hidden">
      {/* Grid background */}
      <div className="absolute inset-0 grid-bg opacity-60" />

      {/* Particle canvas */}
      <canvas
        ref={canvasRef}
        className="absolute inset-0 w-full h-full pointer-events-none"
      />

      {/* Aurora mesh — layered animated blobs */}
      <motion.div
        className="absolute w-[1100px] h-[700px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(124,58,237,0.15) 0%, rgba(109,40,217,0.06) 40%, transparent 70%)",
          top: "15%",
          left: "50%",
          translateX: "-50%",
        }}
        animate={{
          scale: [1, 1.08, 0.97, 1.05, 1],
          opacity: [0.7, 1, 0.8, 1, 0.7],
        }}
        transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
      />
      <motion.div
        className="absolute w-[600px] h-[500px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(6,182,212,0.09) 0%, transparent 70%)",
          top: "35%",
          left: "20%",
        }}
        animate={{
          scale: [1, 1.25, 1, 1.1, 1],
          x: [0, 30, 0, -20, 0],
          opacity: [0.5, 0.9, 0.6, 1, 0.5],
        }}
        transition={{
          duration: 11,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 1.5,
        }}
      />
      <motion.div
        className="absolute w-[450px] h-[350px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(167,139,250,0.08) 0%, transparent 70%)",
          top: "50%",
          left: "65%",
        }}
        animate={{
          scale: [1, 1.15, 0.95, 1.2, 1],
          y: [0, -20, 10, -10, 0],
          opacity: [0.4, 0.8, 0.5, 0.9, 0.4],
        }}
        transition={{
          duration: 13,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 3,
        }}
      />
      {/* Mesh shimmer overlay */}
      <div
        className="absolute inset-0 pointer-events-none opacity-[0.03]"
        style={{
          backgroundImage:
            "linear-gradient(rgba(124,58,237,0.8) 1px, transparent 1px), linear-gradient(90deg, rgba(124,58,237,0.8) 1px, transparent 1px)",
          backgroundSize: "80px 80px",
          maskImage:
            "radial-gradient(ellipse 80% 60% at 50% 40%, black 0%, transparent 100%)",
        }}
      />

      {/* Floating agent nodes */}
      {AGENT_NODES.map((node) => (
        <FloatingAgentNode key={node.id} node={node} />
      ))}

      {/* Main content */}
      <div className="relative z-10 flex flex-col items-center text-center px-6 max-w-5xl mx-auto pt-24">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-white/10 bg-white/5 text-xs text-white/60 mb-8 backdrop-blur-sm"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 pulse-dot" />
          Open source · MIT licensed · 780+ tests
        </motion.div>

        {/* Headline */}
        <motion.h1
          className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight leading-[1.05] mb-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.1 }}
        >
          <span className="text-white block">The Communication</span>
          <span className="text-white">Layer for </span>
          <TypewriterWord />
        </motion.h1>

        {/* Subheading */}
        <motion.p
          className="text-lg md:text-xl text-white/50 max-w-2xl mb-10 leading-relaxed"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.2 }}
        >
          Any model. Any machine. Real-time coordination.
          <br className="hidden md:block" />
          Rooms, SSE push, shared state, distributed locks — for every AI agent.
        </motion.p>

        {/* Install command */}
        <motion.div
          className="w-full max-w-2xl mb-8"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.3 }}
        >
          <div
            className="code-block flex items-center justify-between px-5 py-3.5 group cursor-pointer relative overflow-hidden"
            onClick={copy}
          >
            {/* Shimmer on hover */}
            <div
              className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-500 pointer-events-none"
              style={{
                background:
                  "linear-gradient(90deg, transparent, rgba(124,58,237,0.06), transparent)",
              }}
            />
            <div className="flex items-center gap-3 min-w-0 relative z-10">
              <span className="text-white/30 font-mono text-sm shrink-0">
                $
              </span>
              <span className="font-mono text-sm text-green-400 truncate">
                {INSTALL_CMD}
              </span>
            </div>
            <button className="ml-4 shrink-0 p-1.5 rounded-md text-white/30 hover:text-white/80 hover:bg-white/10 transition-all relative z-10">
              <AnimatePresence mode="wait">
                {copied ? (
                  <motion.svg
                    key="check"
                    className="w-4 h-4 text-green-400"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    exit={{ scale: 0 }}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M5 13l4 4L19 7"
                    />
                  </motion.svg>
                ) : (
                  <motion.svg
                    key="copy"
                    className="w-4 h-4"
                    fill="none"
                    stroke="currentColor"
                    viewBox="0 0 24 24"
                    initial={{ scale: 0 }}
                    animate={{ scale: 1 }}
                    exit={{ scale: 0 }}
                  >
                    <path
                      strokeLinecap="round"
                      strokeLinejoin="round"
                      strokeWidth={2}
                      d="M8 16H6a2 2 0 01-2-2V6a2 2 0 012-2h8a2 2 0 012 2v2m-6 12h8a2 2 0 002-2v-8a2 2 0 00-2-2h-8a2 2 0 00-2 2v8a2 2 0 002 2z"
                    />
                  </motion.svg>
                )}
              </AnimatePresence>
            </button>
          </div>
        </motion.div>

        {/* CTAs */}
        <motion.div
          className="flex flex-col sm:flex-row gap-3 mb-16"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.4 }}
        >
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="relative px-6 py-3 rounded-full bg-violet-600 hover:bg-violet-500 text-white text-sm font-medium transition-all duration-200 hover:shadow-lg hover:shadow-violet-500/30 overflow-hidden group"
          >
            <span className="relative z-10">Read the Docs →</span>
            <div
              className="absolute inset-0 opacity-0 group-hover:opacity-100 transition-opacity duration-300 pointer-events-none"
              style={{
                background:
                  "linear-gradient(135deg, rgba(255,255,255,0.1), transparent)",
              }}
            />
          </a>
          <a
            href="https://github.com/Aarya2004/murmur"
            target="_blank"
            rel="noopener noreferrer"
            className="px-6 py-3 rounded-full border border-white/10 hover:border-white/25 bg-white/5 hover:bg-white/8 text-white text-sm font-medium transition-all duration-200 hover:shadow-lg hover:shadow-white/5"
          >
            View on GitHub
          </a>
        </motion.div>

        {/* Floating badges */}
        <motion.div
          className="flex flex-wrap items-center justify-center gap-2 text-xs text-white/40"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.7, delay: 0.6 }}
        >
          {[
            "SSE push delivery",
            "Zero polling",
            "At-least-once ACK",
            "Redis-backed",
            "MIT licensed",
            "780 tests",
          ].map((badge, i) => (
            <motion.span
              key={badge}
              className="px-3 py-1 rounded-full border border-white/8 bg-white/3 hover:border-white/15 hover:text-white/60 transition-all cursor-default"
              initial={{ opacity: 0, scale: 0.9 }}
              animate={{ opacity: 1, scale: 1 }}
              transition={{ delay: 0.7 + i * 0.05 }}
            >
              {badge}
            </motion.span>
          ))}
        </motion.div>
      </div>

      {/* Scroll indicator */}
      <motion.div
        className="absolute bottom-8 left-1/2 -translate-x-1/2 flex flex-col items-center gap-2 text-white/20"
        initial={{ opacity: 0 }}
        animate={{ opacity: 1 }}
        transition={{ delay: 1.5 }}
      >
        <motion.div
          className="w-px h-10 bg-gradient-to-b from-transparent via-white/20 to-transparent"
          animate={{ scaleY: [1, 1.3, 1], opacity: [0.3, 0.7, 0.3] }}
          transition={{ duration: 2, repeat: Infinity, ease: "easeInOut" }}
        />
      </motion.div>
    </section>
  );
}
