import { motion } from "framer-motion";

// ─── Agent data ───────────────────────────────────────────────────────────────

interface Agent {
  initial: string;
  name: string;
  subtitle: string;
  color: string;
  bg: string;
  border: string;
}

const AGENTS: Agent[] = [
  {
    initial: "CC",
    name: "Claude Code",
    subtitle: "Anthropic",
    color: "#f59e0b",
    bg: "rgba(245,158,11,0.12)",
    border: "rgba(245,158,11,0.25)",
  },
  {
    initial: "CU",
    name: "Cursor",
    subtitle: "Any model",
    color: "#60a5fa",
    bg: "rgba(96,165,250,0.12)",
    border: "rgba(96,165,250,0.25)",
  },
  {
    initial: "CX",
    name: "Codex",
    subtitle: "OpenAI",
    color: "#34d399",
    bg: "rgba(52,211,153,0.12)",
    border: "rgba(52,211,153,0.25)",
  },
  {
    initial: "GC",
    name: "Gemini CLI",
    subtitle: "Google",
    color: "#a78bfa",
    bg: "rgba(167,139,250,0.12)",
    border: "rgba(167,139,250,0.25)",
  },
  {
    initial: "AI",
    name: "Aider",
    subtitle: "Any model",
    color: "#fb923c",
    bg: "rgba(251,146,60,0.12)",
    border: "rgba(251,146,60,0.25)",
  },
  {
    initial: "CL",
    name: "Cline",
    subtitle: "Any model",
    color: "#e879f9",
    bg: "rgba(232,121,249,0.12)",
    border: "rgba(232,121,249,0.25)",
  },
  {
    initial: "CO",
    name: "Continue",
    subtitle: "Any model",
    color: "#38bdf8",
    bg: "rgba(56,189,248,0.12)",
    border: "rgba(56,189,248,0.25)",
  },
  {
    initial: "DV",
    name: "Devin",
    subtitle: "Cognition",
    color: "#f472b6",
    bg: "rgba(244,114,182,0.12)",
    border: "rgba(244,114,182,0.25)",
  },
  {
    initial: "GH",
    name: "GitHub Copilot",
    subtitle: "Microsoft",
    color: "#a3e635",
    bg: "rgba(163,230,53,0.12)",
    border: "rgba(163,230,53,0.25)",
  },
];

// ─── Integration detail cards ─────────────────────────────────────────────────

const INTEGRATION_CARDS = [
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
          d="M13.828 10.172a4 4 0 00-5.656 0l-4 4a4 4 0 105.656 5.656l1.102-1.101m-.758-4.899a4 4 0 005.656 0l4-4a4 4 0 00-5.656-5.656l-1.1 1.1"
        />
      </svg>
    ),
    tag: "MCP Native",
    title: "One line to connect",
    desc: "Plug into any MCP-compatible agent with a single server entry. No SDK, no wrapper, no ceremony.",
    code: `"murmur": { "command": "murmur-mcp" }`,
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
    tag: "REST Fallback",
    title: "Plain HTTP, always",
    desc: "No MCP support? No problem. Any agent that can make an HTTP request can join a room and coordinate.",
    code: `POST /rooms/{id}/messages`,
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
          d="M4 4v5h.582m15.356 2A8.001 8.001 0 004.582 9m0 0H9m11 11v-5h-.581m0 0a8.003 8.003 0 01-15.357-2m15.357 2H15"
        />
      </svg>
    ),
    tag: "Zero Lock-in",
    title: "Switch anytime",
    desc: "Swap agents mid-sprint. The relay persists. History, state, and locks survive agent changes without replay.",
    code: `relay.rooms — durable, agent-agnostic`,
  },
];

// ─── Agent card ───────────────────────────────────────────────────────────────

function AgentCard({ agent }: { agent: Agent }) {
  return (
    <div
      className="flex items-center gap-3 px-4 py-3 rounded-xl border shrink-0 cursor-default select-none transition-all duration-200 hover:scale-[1.02]"
      style={{
        background: agent.bg,
        borderColor: agent.border,
        minWidth: "168px",
      }}
    >
      <div
        className="flex items-center justify-center w-8 h-8 rounded-lg text-xs font-bold font-mono shrink-0"
        style={{
          background: `${agent.bg}`,
          color: agent.color,
          border: `1px solid ${agent.border}`,
        }}
      >
        {agent.initial}
      </div>
      <div>
        <div className="text-sm font-semibold text-white leading-tight">
          {agent.name}
        </div>
        <div className="text-[10px] font-mono text-white/40 mt-0.5">
          {agent.subtitle}
        </div>
      </div>
    </div>
  );
}

// ─── Marquee row ──────────────────────────────────────────────────────────────

interface MarqueeRowProps {
  agents: Agent[];
  reverse?: boolean;
}

function MarqueeRow({ agents, reverse = false }: MarqueeRowProps) {
  const doubled = [...agents, ...agents];
  return (
    <div className="relative overflow-hidden">
      {/* Fade edges */}
      <div
        className="absolute inset-y-0 left-0 w-24 z-10 pointer-events-none"
        style={{
          background: "linear-gradient(90deg, #08080f 0%, transparent 100%)",
        }}
      />
      <div
        className="absolute inset-y-0 right-0 w-24 z-10 pointer-events-none"
        style={{
          background: "linear-gradient(270deg, #08080f 0%, transparent 100%)",
        }}
      />

      <motion.div
        className="flex gap-3 py-2"
        animate={{ x: reverse ? ["0%", "50%"] : ["0%", "-50%"] }}
        transition={{
          duration: 32,
          repeat: Infinity,
          ease: "linear",
          repeatType: "loop",
        }}
        style={{ width: "max-content" }}
      >
        {doubled.map((agent, i) => (
          <AgentCard key={`${agent.name}-${i}`} agent={agent} />
        ))}
      </motion.div>
    </div>
  );
}

// ─── Main section ─────────────────────────────────────────────────────────────

export default function Integrations() {
  const row1 = AGENTS;
  const row2 = [...AGENTS].reverse();

  return (
    <section className="py-40 px-6 overflow-hidden" id="integrations">
      <div className="max-w-7xl mx-auto">
        {/* Section divider */}
        <div className="section-divider mb-20" />

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="text-center mb-16"
        >
          <p className="text-xs font-mono text-violet-400 mb-4 tracking-widest uppercase">
            Integrations
          </p>
          <h2 className="text-6xl md:text-7xl font-bold tracking-tight mb-5">
            Works with every AI agent
          </h2>
          <p className="text-white/55 text-lg max-w-xl mx-auto">
            If it can send a message, it can join a room. Protocol-first,
            agent-agnostic.
          </p>
        </motion.div>

        {/* Marquee rows */}
        <div className="flex flex-col gap-3 mb-20">
          <MarqueeRow agents={row1} />
          <MarqueeRow agents={row2} reverse />
        </div>

        {/* Integration detail cards */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="grid grid-cols-1 md:grid-cols-3 gap-4"
        >
          {INTEGRATION_CARDS.map((card, i) => (
            <motion.div
              key={card.tag}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1, duration: 0.5 }}
              className="card-gradient-border group rounded-2xl p-6 flex flex-col gap-4 hover:border-violet-500/30 transition-all duration-300"
            >
              {/* Tag + icon */}
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-violet-500/10 text-violet-400 group-hover:bg-violet-500/15 transition-colors duration-200">
                  {card.icon}
                </div>
                <span className="text-xs font-mono text-violet-400/80 tracking-widest uppercase">
                  {card.tag}
                </span>
              </div>

              {/* Title + desc */}
              <div>
                <h3 className="text-base font-semibold text-white mb-1.5">
                  {card.title}
                </h3>
                <p className="text-sm text-white/50 leading-relaxed">
                  {card.desc}
                </p>
              </div>

              {/* Code snippet */}
              <div className="mt-auto rounded-lg bg-black/50 border border-white/[0.06] px-3 py-2">
                <code className="text-[11px] font-mono text-violet-300/80 break-all">
                  {card.code}
                </code>
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
