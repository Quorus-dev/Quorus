"use client";

import { useState, useEffect, useRef } from "react";
import { motion, useMotionValue, useSpring } from "framer-motion";
import Waitlist from "./Waitlist";

function HeroWaitlist() {
  return <Waitlist size="lg" label="Request access" className="w-full" />;
}

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

const AGENT_NODES = [
  {
    id: "a1",
    label: "claude-code",
    status: "building auth...",
    locked: true,
    x: -290,
    y: -70,
    delay: 0,
  },
  {
    id: "a2",
    label: "cursor-1",
    status: "reviewing PR",
    locked: false,
    x: 290,
    y: -80,
    delay: 0.3,
  },
  {
    id: "a3",
    label: "codex-1",
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
      {/* Primary violet orb — centered behind headline */}
      <motion.div
        className="absolute w-[900px] h-[600px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(124,58,237,0.28) 0%, rgba(109,40,217,0.10) 45%, transparent 70%)",
          top: "10%",
          left: "50%",
          translateX: "-50%",
          filter: "blur(40px)",
        }}
        animate={{
          scale: [1, 1.07, 0.96, 1.04, 1],
          opacity: [0.8, 1, 0.85, 1, 0.8],
        }}
        transition={{ duration: 9, repeat: Infinity, ease: "easeInOut" }}
      />
      {/* Secondary tight glow — headline halo */}
      <div
        className="absolute w-[500px] h-[280px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(139,92,246,0.18) 0%, transparent 70%)",
          top: "22%",
          left: "50%",
          transform: "translateX(-50%)",
          filter: "blur(60px)",
        }}
      />
      {/* Cyan accent — left */}
      <motion.div
        className="absolute w-[500px] h-[400px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(6,182,212,0.12) 0%, transparent 70%)",
          top: "30%",
          left: "5%",
          filter: "blur(30px)",
        }}
        animate={{
          scale: [1, 1.2, 1, 1.1, 1],
          x: [0, 25, 0, -15, 0],
          opacity: [0.5, 0.85, 0.6, 0.95, 0.5],
        }}
        transition={{
          duration: 11,
          repeat: Infinity,
          ease: "easeInOut",
          delay: 1.5,
        }}
      />
      {/* Purple accent — right */}
      <motion.div
        className="absolute w-[380px] h-[300px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(167,139,250,0.14) 0%, transparent 70%)",
          top: "45%",
          left: "68%",
          filter: "blur(30px)",
        }}
        animate={{
          scale: [1, 1.12, 0.93, 1.18, 1],
          y: [0, -18, 8, -10, 0],
          opacity: [0.45, 0.8, 0.5, 0.88, 0.45],
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
      <div className="relative z-10 flex flex-col items-center text-center px-6 max-w-5xl mx-auto pt-32">
        {/* Badge */}
        <motion.div
          initial={{ opacity: 0, y: -10 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.6 }}
          className="inline-flex items-center gap-2 px-3 py-1.5 rounded-full border border-white/10 bg-white/5 text-xs text-white/60 mb-8 backdrop-blur-sm"
        >
          <span className="w-1.5 h-1.5 rounded-full bg-violet-400 pulse-dot" />
          Private beta · Limited spots
        </motion.div>

        {/* Headline */}
        <motion.h1
          className="text-5xl md:text-7xl lg:text-8xl font-bold tracking-tight leading-[1.05] mb-6"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.1 }}
        >
          <span className="text-white block">
            The&nbsp;
            <span className="text-shimmer">Communication</span>
          </span>
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
          Claude Code, Cursor, Codex, Gemini — any agent, any model, any
          machine.
          <br className="hidden md:block" />
          Rooms, SSE push, shared state, distributed locks. Zero config.
        </motion.p>

        {/* Waitlist form */}
        <motion.div
          className="w-full max-w-lg mb-5"
          initial={{ opacity: 0, y: 20 }}
          animate={{ opacity: 1, y: 0 }}
          transition={{ duration: 0.7, delay: 0.3 }}
        >
          <HeroWaitlist />
        </motion.div>

        {/* Scroll hint */}
        <motion.div
          className="mb-14"
          initial={{ opacity: 0 }}
          animate={{ opacity: 1 }}
          transition={{ duration: 0.6, delay: 0.45 }}
        >
          <a
            href="#features"
            className="text-sm text-white/30 hover:text-white/60 transition-colors flex items-center gap-1.5"
          >
            See how it works
            <svg
              className="w-3.5 h-3.5"
              fill="none"
              stroke="currentColor"
              viewBox="0 0 24 24"
            >
              <path
                strokeLinecap="round"
                strokeLinejoin="round"
                strokeWidth={2}
                d="M19 9l-7 7-7-7"
              />
            </svg>
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
            "Any model",
            "Any machine",
            "Real-time state",
            "Distributed locks",
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
