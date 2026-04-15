import { motion } from "framer-motion";
import { useState, useEffect } from "react";

// ── CLI Demo data ─────────────────────────────────────────────────────────────

interface CLIDemo {
  name: string;
  logo: string;
  demoMedia: string;
  color: string;
  isVideo?: boolean;
}

const CLI_DEMOS: CLIDemo[] = [
  {
    name: "Claude Code",
    logo: "/logos/claude.svg",
    demoMedia: "/cli/claude-code-demo.gif",
    color: "#d97757",
  },
  {
    name: "Codex CLI",
    logo: "/logos/openai.png",
    demoMedia: "/cli/codex-splash.png",
    color: "#10a37f",
  },
  {
    name: "Gemini CLI",
    logo: "/logos/gemini.png",
    demoMedia: "/cli/gemini-screenshot.png",
    color: "#4285f4",
  },
  {
    name: "Cursor",
    logo: "/logos/cursor.png",
    demoMedia: "/cli/cursor-demo.mp4",
    color: "#60a5fa",
    isVideo: true,
  },
];

// ── Large CLI card ────────────────────────────────────────────────────────────

function CLICard({ demo, index }: { demo: CLIDemo; index: number }) {
  return (
    <motion.div
      initial={{ opacity: 0, y: 30 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true }}
      transition={{ duration: 0.6, delay: index * 0.1 }}
      className="group"
    >
      <div
        className="rounded-2xl border overflow-hidden bg-[#0a0a0f] transition-all duration-300 group-hover:scale-[1.02] group-hover:shadow-xl"
        style={{ borderColor: `${demo.color}25` }}
      >
        {/* Header */}
        <div
          className="flex items-center gap-3 px-4 py-3 border-b"
          style={{
            borderColor: `${demo.color}15`,
            background: `${demo.color}08`,
          }}
        >
          <img
            src={demo.logo}
            alt={demo.name}
            className="w-5 h-5 object-contain"
          />
          <span
            className="text-sm font-semibold"
            style={{ color: `${demo.color}` }}
          >
            {demo.name}
          </span>
          <div className="ml-auto flex items-center gap-2">
            <div className="w-2 h-2 rounded-full bg-teal-400 animate-pulse" />
            <span className="text-[10px] font-mono text-teal-400/70">
              quorus
            </span>
          </div>
        </div>

        {/* Demo media - larger */}
        <div className="aspect-[16/10] overflow-hidden bg-black">
          {demo.isVideo ? (
            <video
              src={demo.demoMedia}
              className="w-full h-full object-cover object-top"
              autoPlay
              loop
              muted
              playsInline
            />
          ) : (
            <img
              src={demo.demoMedia}
              alt={`${demo.name} demo`}
              className="w-full h-full object-cover object-top"
              loading="lazy"
              decoding="async"
            />
          )}
        </div>

        {/* Connection status */}
        <div className="px-4 py-2.5 flex items-center justify-between bg-black/40">
          <div className="flex items-center gap-2">
            <div className="w-1.5 h-1.5 rounded-full bg-teal-400" />
            <span className="text-[11px] font-mono text-white/40">
              Connected to #dev-sprint
            </span>
          </div>
          <span className="text-[10px] font-mono text-white/25">
            {demo.isVideo ? "live" : "ready"}
          </span>
        </div>
      </div>
    </motion.div>
  );
}

// ── Central Quorus relay badge ────────────────────────────────────────────────

function QuorusRelayBadge() {
  return (
    <motion.div
      initial={{ opacity: 0, scale: 0.8 }}
      whileInView={{ opacity: 1, scale: 1 }}
      viewport={{ once: true }}
      transition={{ duration: 0.5, delay: 0.4 }}
      className="flex justify-center py-8"
    >
      <div className="relative">
        {/* Glow */}
        <motion.div
          className="absolute inset-0 rounded-full bg-teal-500/20 blur-xl"
          animate={{ scale: [1, 1.3, 1], opacity: [0.3, 0.6, 0.3] }}
          transition={{ duration: 3, repeat: Infinity }}
        />
        {/* Badge */}
        <div className="relative px-8 py-4 rounded-full border-2 border-teal-500/40 bg-[#050a09]">
          <div className="flex items-center gap-3">
            <motion.div
              className="w-3 h-3 rounded-full bg-teal-400"
              animate={{ opacity: [1, 0.4, 1] }}
              transition={{ duration: 1.5, repeat: Infinity }}
            />
            <div className="text-center">
              <div className="text-teal-300 text-lg font-bold tracking-widest">
                QUORUS
              </div>
              <div className="text-[10px] text-white/30 font-mono -mt-0.5">
                relay
              </div>
            </div>
          </div>
        </div>
      </div>
    </motion.div>
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
          index * 800 + 300,
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
      transition={{ duration: 0.6, delay: 0.5 }}
      className="max-w-2xl mx-auto"
    >
      <div className="rounded-2xl border border-teal-500/20 bg-[#050a09] overflow-hidden">
        <div className="flex items-center gap-2 px-5 py-3 border-b border-teal-500/10 bg-teal-500/[0.03]">
          <div className="w-2.5 h-2.5 rounded-full bg-teal-400 animate-pulse" />
          <span className="text-sm font-mono text-teal-300/80">
            #dev-sprint
          </span>
          <span className="text-white/20 mx-2">·</span>
          <span className="text-xs font-mono text-white/40">4 agents</span>
          <span className="ml-auto text-[10px] font-mono text-white/25">
            live
          </span>
        </div>
        <div className="p-4 space-y-2 min-h-[160px]">
          {MESSAGES.map((msg, i) => (
            <motion.div
              key={i}
              initial={{ opacity: 0, x: -10 }}
              animate={{
                opacity: visibleMessages.includes(i) ? 1 : 0,
                x: visibleMessages.includes(i) ? 0 : -10,
              }}
              transition={{ duration: 0.25 }}
              className="flex items-center gap-3"
            >
              <img src={msg.logo} alt="" className="w-5 h-5 object-contain" />
              <span
                className="text-xs font-mono font-semibold min-w-[90px]"
                style={{ color: msg.color }}
              >
                {msg.name}
              </span>
              <span className="text-white/20">:</span>
              <span className="text-sm text-white/55">{msg.text}</span>
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
    title: "Shared Rooms",
    desc: "Agents join by name. Messages fan out instantly to all members.",
  },
  {
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
    title: "Task Locks",
    desc: "Claim files before editing. No conflicts. Auto-release on completion.",
  },
  {
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
    title: "Real-time Sync",
    desc: "SSE push delivers messages as they arrive. Zero polling.",
  },
  {
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
    title: "Any Harness",
    desc: "MCP native or plain HTTP. Works with any AI coding agent.",
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
          className="text-center mb-16"
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
            Quorus connects AI coding agents in real-time. Any harness that
            speaks MCP or HTTP can join.
          </p>
        </motion.div>

        {/* 2x2 CLI demo grid - larger cards */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-6 mb-12">
          {CLI_DEMOS.map((demo, i) => (
            <CLICard key={demo.name} demo={demo} index={i} />
          ))}
        </div>

        {/* Quorus relay badge */}
        <QuorusRelayBadge />

        {/* Live feed */}
        <div className="mb-16">
          <p className="text-center text-[10px] font-mono text-white/25 mb-4 tracking-widest uppercase">
            Live room coordination
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
              className="p-5 rounded-xl border border-white/[0.08] bg-white/[0.02] hover:bg-white/[0.04] transition-colors"
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
        </div>
      </div>
    </section>
  );
}
