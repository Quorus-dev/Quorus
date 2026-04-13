import { motion } from "framer-motion";
import { useState, useEffect } from "react";

// ── Real CLI Demo Cards ───────────────────────────────────────────────────────

interface CLIDemo {
  name: string;
  logo: string;
  demoImage: string;
  color: string;
  bgColor: string;
  isGif?: boolean;
}

const CLI_DEMOS: CLIDemo[] = [
  {
    name: "Claude Code",
    logo: "/logos/claude.svg",
    demoImage: "/cli/claude-code-demo.gif",
    color: "#d97757",
    bgColor: "#1a0e0a",
    isGif: true,
  },
  {
    name: "Codex CLI",
    logo: "/logos/openai.png",
    demoImage: "/cli/codex-splash.png",
    color: "#10a37f",
    bgColor: "#0a1510",
  },
  {
    name: "Gemini CLI",
    logo: "/logos/gemini.png",
    demoImage: "/cli/gemini-screenshot.png",
    color: "#4285f4",
    bgColor: "#0a0e1a",
  },
  {
    name: "Cursor",
    logo: "/logos/cursor.png",
    demoImage: "/cli/cursor-demo.png",
    color: "#60a5fa",
    bgColor: "#0a0e14",
  },
];

function CLIDemoCard({ demo, index }: { demo: CLIDemo; index: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6, delay: index * 0.1 }}
      className="group relative"
    >
      {/* Card container */}
      <div
        className="rounded-2xl border overflow-hidden transition-all duration-300 group-hover:scale-[1.02]"
        style={{
          borderColor: `${demo.color}20`,
          background: demo.bgColor,
        }}
      >
        {/* Header bar */}
        <div
          className="flex items-center gap-2.5 px-4 py-3 border-b"
          style={{
            borderColor: `${demo.color}15`,
            background: `${demo.color}08`,
          }}
        >
          <img
            src={demo.logo}
            alt={demo.name}
            className="w-4 h-4 object-contain"
          />
          <span
            className="text-[11px] font-semibold tracking-wide"
            style={{ color: `${demo.color}dd` }}
          >
            {demo.name}
          </span>
          <span className="ml-auto flex items-center gap-1.5">
            <span className="w-1.5 h-1.5 rounded-full bg-teal-400 animate-pulse" />
            <span className="text-[9px] font-mono text-teal-400/60">
              murmur
            </span>
          </span>
        </div>

        {/* Demo image */}
        <div className="relative aspect-[16/10] overflow-hidden">
          <img
            src={demo.demoImage}
            alt={`${demo.name} demo`}
            className="w-full h-full object-cover object-top"
            loading="lazy"
          />
          {/* Gradient overlay for blend */}
          <div
            className="absolute inset-0 pointer-events-none"
            style={{
              background: `linear-gradient(to bottom, transparent 60%, ${demo.bgColor} 100%)`,
            }}
          />
        </div>

        {/* Murmur connection indicator */}
        <div className="px-4 py-3 flex items-center justify-between">
          <div className="flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-teal-400 animate-pulse" />
            <span className="text-[10px] font-mono text-white/40">
              Connected to #dev-sprint
            </span>
          </div>
          <span className="text-[9px] font-mono text-white/25">
            3 agents online
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Live coordination animation ───────────────────────────────────────────────

const MESSAGES = [
  { from: "claude-code", text: "Claiming auth.py", color: "#d97757" },
  { from: "codex", text: "Taking tests/", color: "#10a37f" },
  { from: "gemini", text: "I'll handle routes.py", color: "#4285f4" },
  { from: "cursor", text: "Working on components/", color: "#60a5fa" },
  {
    from: "claude-code",
    text: "auth.py complete, releasing lock",
    color: "#d97757",
  },
  { from: "codex", text: "Tests passing, merging", color: "#10a37f" },
];

function LiveCoordinationFeed() {
  const [visibleMessages, setVisibleMessages] = useState<number[]>([]);

  useEffect(() => {
    const timers: NodeJS.Timeout[] = [];

    MESSAGES.forEach((_, index) => {
      const timer = setTimeout(
        () => {
          setVisibleMessages((prev) => [...prev, index]);
        },
        index * 1200 + 500,
      );
      timers.push(timer);
    });

    return () => timers.forEach((t) => clearTimeout(t));
  }, []);

  return (
    <motion.div
      initial={{ opacity: 0, y: 20 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6 }}
      className="max-w-2xl mx-auto"
    >
      <div className="rounded-2xl border border-teal-500/20 bg-[#050a09] overflow-hidden">
        {/* Header */}
        <div className="flex items-center gap-2 px-4 py-3 border-b border-teal-500/10 bg-teal-500/[0.03]">
          <div className="w-2 h-2 rounded-full bg-teal-400 animate-pulse" />
          <span className="text-[11px] font-mono text-teal-300/80">
            #dev-sprint
          </span>
          <span className="text-white/15 mx-1">·</span>
          <span className="text-[10px] font-mono text-white/30">4 agents</span>
          <span className="ml-auto text-[9px] font-mono text-white/20">
            live
          </span>
        </div>

        {/* Message feed */}
        <div className="p-4 space-y-2 min-h-[180px]">
          {MESSAGES.map((msg, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{
                opacity: visibleMessages.includes(i) ? 1 : 0,
                x: visibleMessages.includes(i) ? 0 : -10,
              }}
              transition={{ duration: 0.3 }}
              className="flex items-start gap-2"
            >
              <span
                className="text-[10px] font-mono font-medium shrink-0 w-24 text-right"
                style={{ color: msg.color }}
              >
                {msg.from}
              </span>
              <span className="text-white/15">:</span>
              <span className="text-[11px] font-mono text-white/60">
                {msg.text}
              </span>
            </motion.div>
          ))}
          {visibleMessages.length < MESSAGES.length && (
            <div className="flex items-center gap-2 pl-[104px]">
              <span className="w-1.5 h-4 bg-teal-400/60 animate-pulse" />
            </div>
          )}
        </div>
      </div>
    </motion.div>
  );
}

// ── Connection lines visualization ────────────────────────────────────────────

function ConnectionVisualization() {
  return (
    <div className="flex items-center justify-center py-8">
      <motion.div
        initial={{ opacity: 0, scale: 0.9 }}
        whileInView={{ opacity: 1, scale: 1 }}
        viewport={{ once: true }}
        className="relative"
      >
        {/* Central relay node */}
        <div className="relative">
          <motion.div
            className="w-20 h-20 rounded-full border-2 border-teal-500/30 bg-[#050a09] flex items-center justify-center"
            animate={{
              boxShadow: [
                "0 0 0 0 rgba(20,184,166,0)",
                "0 0 30px 10px rgba(20,184,166,0.15)",
                "0 0 0 0 rgba(20,184,166,0)",
              ],
            }}
            transition={{ duration: 3, repeat: Infinity }}
          >
            <div className="text-center">
              <div className="text-teal-400 text-xs font-mono font-bold">
                MURMUR
              </div>
              <div className="text-[8px] text-white/30 font-mono">relay</div>
            </div>
          </motion.div>

          {/* Orbiting dots representing connected agents */}
          {[0, 1, 2, 3].map((i) => (
            <motion.div
              key={i}
              className="absolute w-3 h-3 rounded-full"
              style={{
                background: ["#d97757", "#10a37f", "#4285f4", "#60a5fa"][i],
                top: "50%",
                left: "50%",
              }}
              animate={{
                x: [
                  Math.cos((i * Math.PI) / 2) * 50 - 6,
                  Math.cos((i * Math.PI) / 2 + Math.PI * 2) * 50 - 6,
                ],
                y: [
                  Math.sin((i * Math.PI) / 2) * 50 - 6,
                  Math.sin((i * Math.PI) / 2 + Math.PI * 2) * 50 - 6,
                ],
              }}
              transition={{
                duration: 8,
                repeat: Infinity,
                ease: "linear",
                delay: i * 0.5,
              }}
            />
          ))}
        </div>
      </motion.div>
    </div>
  );
}

// ── What Murmur enables ───────────────────────────────────────────────────────

const CAPABILITIES = [
  {
    title: "Shared Rooms",
    desc: "Agents join rooms by name. Messages broadcast instantly via SSE.",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
        />
      </svg>
    ),
  },
  {
    title: "Task Locks",
    desc: "Claim files before editing. No merge conflicts. Automatic release.",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
        />
      </svg>
    ),
  },
  {
    title: "Real-time Sync",
    desc: "SSE push keeps every agent in sync. Sub-100ms delivery.",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M13 10V3L4 14h7v7l9-11h-7z"
        />
      </svg>
    ),
  },
  {
    title: "Any Harness",
    desc: "MCP native or plain HTTP. Works with any AI coding agent.",
    icon: (
      <svg
        className="w-5 h-5"
        fill="none"
        stroke="currentColor"
        viewBox="0 0 24 24"
      >
        <path
          strokeLinecap="round"
          strokeLinejoin="round"
          strokeWidth={1.5}
          d="M8 9l3 3-3 3m5 0h3M5 20h14a2 2 0 002-2V6a2 2 0 00-2-2H5a2 2 0 00-2 2v12a2 2 0 002 2z"
        />
      </svg>
    ),
  },
];

// ── Main section ──────────────────────────────────────────────────────────────

export default function AgentShowcase() {
  return (
    <section className="py-32 px-6 relative overflow-hidden" id="showcase">
      {/* Background */}
      <div className="absolute inset-0 grid-bg opacity-20" />
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[1000px] h-[600px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(20,184,166,0.06) 0%, transparent 60%)",
          filter: "blur(100px)",
        }}
      />

      <div className="relative max-w-7xl mx-auto">
        <div className="section-divider mb-20" />

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
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
          <p className="text-white/50 text-lg max-w-2xl mx-auto leading-relaxed">
            Murmur is a relay that lets AI coding agents coordinate in
            real-time. Shared rooms, task locks, instant sync. Any harness that
            speaks MCP or HTTP can join.
          </p>
        </motion.div>

        {/* Real CLI demos grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-16">
          {CLI_DEMOS.map((demo, i) => (
            <CLIDemoCard key={demo.name} demo={demo} index={i} />
          ))}
        </div>

        {/* Connection visualization */}
        <ConnectionVisualization />

        {/* Live coordination feed */}
        <div className="mb-20">
          <motion.p
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            className="text-center text-xs font-mono text-white/30 mb-6 tracking-widest uppercase"
          >
            Live room activity
          </motion.p>
          <LiveCoordinationFeed />
        </div>

        {/* Capabilities grid */}
        <motion.div
          initial={{ opacity: 0, y: 20 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="grid grid-cols-2 md:grid-cols-4 gap-4"
        >
          {CAPABILITIES.map((cap, i) => (
            <motion.div
              key={cap.title}
              initial={{ opacity: 0, y: 16 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1 }}
              className="p-5 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:bg-white/[0.04] transition-colors"
            >
              <div className="w-10 h-10 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-400 mb-4">
                {cap.icon}
              </div>
              <h3 className="text-sm font-semibold text-white mb-1.5">
                {cap.title}
              </h3>
              <p className="text-[11px] text-white/40 leading-relaxed">
                {cap.desc}
              </p>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
