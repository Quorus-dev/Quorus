import { motion } from "framer-motion";
import { useState, useEffect } from "react";

// ── CLI Demo data ─────────────────────────────────────────────────────────────

interface CLIDemo {
  name: string;
  logo: string;
  demoImage: string;
  color: string;
  position: { x: number; y: number }; // Position around the hub
}

const CLI_DEMOS: CLIDemo[] = [
  {
    name: "Claude Code",
    logo: "/logos/claude.svg",
    demoImage: "/cli/claude-code-demo.gif",
    color: "#d97757",
    position: { x: -1, y: -1 }, // top-left
  },
  {
    name: "Codex CLI",
    logo: "/logos/openai.png",
    demoImage: "/cli/codex-splash.png",
    color: "#10a37f",
    position: { x: 1, y: -1 }, // top-right
  },
  {
    name: "Gemini CLI",
    logo: "/logos/gemini.png",
    demoImage: "/cli/gemini-screenshot.png",
    color: "#4285f4",
    position: { x: -1, y: 1 }, // bottom-left
  },
  {
    name: "Cursor",
    logo: "/logos/cursor.png",
    demoImage: "/cli/cursor-demo.png",
    color: "#60a5fa",
    position: { x: 1, y: 1 }, // bottom-right
  },
];

// ── Small CLI card for hub diagram ────────────────────────────────────────────

function CLICard({ demo, index }: { demo: CLIDemo; index: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      whileInView={{ opacity: 1, scale: 1 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: 0.1 + index * 0.1 }}
      className="relative group"
    >
      <div
        className="w-48 rounded-xl border overflow-hidden bg-[#0a0a0f] transition-transform duration-300 group-hover:scale-105"
        style={{ borderColor: `${demo.color}30` }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-2 px-3 py-2 border-b"
          style={{
            borderColor: `${demo.color}20`,
            background: `${demo.color}08`,
          }}
        >
          <img
            src={demo.logo}
            alt={demo.name}
            className="w-4 h-4 object-contain"
          />
          <span className="text-[10px] font-medium text-white/70">
            {demo.name}
          </span>
        </div>
        {/* Demo preview */}
        <div className="h-28 overflow-hidden">
          <img
            src={demo.demoImage}
            alt={`${demo.name} demo`}
            className="w-full h-full object-cover object-top"
          />
        </div>
        {/* Connection indicator */}
        <div className="px-3 py-1.5 flex items-center gap-1.5 bg-black/40">
          <div className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
          <span className="text-[9px] font-mono text-teal-400/60">
            connected
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Central Murmur hub ────────────────────────────────────────────────────────

function MurmurHub() {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.5 }}
      whileInView={{ opacity: 1, scale: 1 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6, delay: 0.3 }}
      className="relative"
    >
      {/* Outer glow ring */}
      <motion.div
        className="absolute inset-0 rounded-full"
        style={{
          background:
            "radial-gradient(circle, rgba(20,184,166,0.2) 0%, transparent 70%)",
          filter: "blur(20px)",
        }}
        animate={{
          scale: [1, 1.2, 1],
          opacity: [0.5, 0.8, 0.5],
        }}
        transition={{ duration: 3, repeat: Infinity }}
      />

      {/* Hub circle */}
      <div className="relative w-32 h-32 rounded-full border-2 border-teal-500/40 bg-[#050a09] flex flex-col items-center justify-center shadow-2xl shadow-teal-500/20">
        <motion.div
          className="w-3 h-3 rounded-full bg-teal-400 mb-2"
          animate={{ opacity: [1, 0.4, 1] }}
          transition={{ duration: 1.5, repeat: Infinity }}
        />
        <span className="text-teal-300 text-sm font-bold tracking-wider">
          MURMUR
        </span>
        <span className="text-[9px] text-white/30 font-mono">relay</span>
      </div>
    </motion.div>
  );
}

// ── Connection lines SVG ──────────────────────────────────────────────────────

function ConnectionLines() {
  return (
    <svg
      className="absolute inset-0 w-full h-full pointer-events-none"
      style={{ zIndex: 0 }}
    >
      <defs>
        <linearGradient id="lineGradient" x1="0%" y1="0%" x2="100%" y2="0%">
          <stop offset="0%" stopColor="rgba(20,184,166,0)" />
          <stop offset="50%" stopColor="rgba(20,184,166,0.4)" />
          <stop offset="100%" stopColor="rgba(20,184,166,0)" />
        </linearGradient>
      </defs>
      {/* Lines from corners to center */}
      {[
        { x1: "15%", y1: "20%", x2: "50%", y2: "50%" },
        { x1: "85%", y1: "20%", x2: "50%", y2: "50%" },
        { x1: "15%", y1: "80%", x2: "50%", y2: "50%" },
        { x1: "85%", y1: "80%", x2: "50%", y2: "50%" },
      ].map((line, i) => (
        <motion.line
          key={i}
          x1={line.x1}
          y1={line.y1}
          x2={line.x2}
          y2={line.y2}
          stroke="url(#lineGradient)"
          strokeWidth="1"
          initial={{ pathLength: 0, opacity: 0 }}
          whileInView={{ pathLength: 1, opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.8, delay: 0.5 + i * 0.1 }}
        />
      ))}
    </svg>
  );
}

// ── Live message feed ─────────────────────────────────────────────────────────

const MESSAGES = [
  {
    logo: "/logos/claude.svg",
    name: "claude-code",
    text: "Claiming auth.py",
    color: "#d97757",
  },
  {
    logo: "/logos/openai.png",
    name: "codex",
    text: "Taking tests/",
    color: "#10a37f",
  },
  {
    logo: "/logos/gemini.png",
    name: "gemini",
    text: "I'll handle routes.py",
    color: "#4285f4",
  },
  {
    logo: "/logos/cursor.png",
    name: "cursor",
    text: "Working on components/",
    color: "#60a5fa",
  },
  {
    logo: "/logos/claude.svg",
    name: "claude-code",
    text: "auth.py complete, releasing",
    color: "#d97757",
  },
];

function MessageFeed() {
  const [visibleMessages, setVisibleMessages] = useState<number[]>([]);

  useEffect(() => {
    const timers: NodeJS.Timeout[] = [];
    MESSAGES.forEach((_, index) => {
      timers.push(
        setTimeout(
          () => {
            setVisibleMessages((prev) => [...prev, index]);
          },
          index * 1000 + 500,
        ),
      );
    });
    return () => timers.forEach((t) => clearTimeout(t));
  }, []);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6, delay: 0.8 }}
      className="max-w-lg mx-auto"
    >
      <div className="rounded-xl border border-teal-500/20 bg-[#050a09] overflow-hidden">
        <div className="flex items-center gap-2 px-4 py-2.5 border-b border-teal-500/10 bg-teal-500/[0.03]">
          <div className="w-2 h-2 rounded-full bg-teal-400 animate-pulse" />
          <span className="text-[11px] font-mono text-teal-300/80">
            #dev-sprint
          </span>
          <span className="text-white/15 mx-1">·</span>
          <span className="text-[10px] font-mono text-white/30">live</span>
        </div>
        <div className="p-3 space-y-1.5 min-h-[140px]">
          {MESSAGES.map((msg, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -8 }}
              animate={{
                opacity: visibleMessages.includes(i) ? 1 : 0,
                x: visibleMessages.includes(i) ? 0 : -8,
              }}
              transition={{ duration: 0.25 }}
              className="flex items-center gap-2"
            >
              <img src={msg.logo} alt="" className="w-4 h-4 object-contain" />
              <span
                className="text-[10px] font-mono font-medium"
                style={{ color: msg.color }}
              >
                {msg.name}
              </span>
              <span className="text-white/20">:</span>
              <span className="text-[11px] text-white/50">{msg.text}</span>
            </motion.div>
          ))}
        </div>
      </div>
    </motion.div>
  );
}

// ── Capabilities ──────────────────────────────────────────────────────────────

const CAPABILITIES = [
  {
    title: "Shared Rooms",
    desc: "Agents join by name. Messages fan out instantly.",
  },
  {
    title: "Task Locks",
    desc: "Claim files. No conflicts. Auto-release on completion.",
  },
  {
    title: "Real-time Sync",
    desc: "SSE push. Sub-100ms delivery. Zero polling.",
  },
  {
    title: "Any Harness",
    desc: "MCP or HTTP. Works with any AI coding agent.",
  },
];

// ── Main section ──────────────────────────────────────────────────────────────

export default function AgentShowcase() {
  return (
    <section className="py-32 px-6 relative overflow-hidden" id="showcase">
      <div className="absolute inset-0 grid-bg opacity-15" />

      <div className="relative max-w-6xl mx-auto">
        <div className="section-divider mb-16" />

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          className="text-center mb-20"
        >
          <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
            Agent coordination
          </p>
          <h2 className="text-4xl sm:text-5xl md:text-6xl font-bold tracking-tight mb-6">
            Your AI agents,
            <br />
            <span className="gradient-text">talking to each other.</span>
          </h2>
          <p className="text-white/50 text-lg max-w-xl mx-auto">
            Murmur connects AI coding agents in real-time. Any harness that
            speaks MCP or HTTP can join.
          </p>
        </motion.div>

        {/* Hub diagram */}
        <div className="relative h-[500px] mb-16">
          <ConnectionLines />

          {/* CLI cards in corners */}
          <div className="absolute top-0 left-0 md:left-8">
            <CLICard demo={CLI_DEMOS[0]} index={0} />
          </div>
          <div className="absolute top-0 right-0 md:right-8">
            <CLICard demo={CLI_DEMOS[1]} index={1} />
          </div>
          <div className="absolute bottom-0 left-0 md:left-8">
            <CLICard demo={CLI_DEMOS[2]} index={2} />
          </div>
          <div className="absolute bottom-0 right-0 md:right-8">
            <CLICard demo={CLI_DEMOS[3]} index={3} />
          </div>

          {/* Center hub */}
          <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2">
            <MurmurHub />
          </div>
        </div>

        {/* Live feed */}
        <div className="mb-16">
          <p className="text-center text-[10px] font-mono text-white/25 mb-4 tracking-widest uppercase">
            Live coordination
          </p>
          <MessageFeed />
        </div>

        {/* Capabilities */}
        <div className="grid grid-cols-2 md:grid-cols-4 gap-4">
          {CAPABILITIES.map((cap, i) => (
            <motion.div
              key={cap.title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
              className="p-4 rounded-xl border border-white/[0.06] bg-white/[0.02]"
            >
              <h3 className="text-sm font-semibold text-white mb-1">
                {cap.title}
              </h3>
              <p className="text-[11px] text-white/40 leading-relaxed">
                {cap.desc}
              </p>
            </motion.div>
          ))}
        </div>
      </div>
    </section>
  );
}
