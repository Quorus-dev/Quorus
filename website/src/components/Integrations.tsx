import { motion } from "framer-motion";

// ── Agent platform data with real logo paths ─────────────────────────────────

interface AgentPlatform {
  name: string;
  logo: string;
  invert?: boolean; // Invert colors for light logos on light bg
}

const AGENT_PLATFORMS: AgentPlatform[] = [
  { name: "Claude Code", logo: "/logos/claude.svg" },
  { name: "Codex CLI", logo: "/logos/openai.png", invert: true },
  { name: "Gemini CLI", logo: "/logos/gemini.png" },
  { name: "Cursor", logo: "/logos/cursor.png" },
  { name: "Windsurf", logo: "/logos/windsurf.svg" },
];

// ── Integration feature cards ─────────────────────────────────────────────────

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
    code: `"quorus": { "command": "quorus-mcp" }`,
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
    title: "Plain HTTP works too",
    desc: "No MCP support? Any agent that can make an HTTP request can join a room and coordinate.",
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
    title: "Switch agents anytime",
    desc: "Swap agents mid-sprint. The relay persists. History, state, and locks survive.",
    code: `# durable, agent-agnostic rooms`,
  },
];

// ── Agent badge with real logo ────────────────────────────────────────────────

function AgentBadge({ agent }: { agent: AgentPlatform }) {
  return (
    <div className="flex flex-col items-center gap-2 px-4 py-3 rounded-xl border border-gray-200 bg-white min-w-[80px] cursor-default select-none hover:scale-105 hover:bg-gray-50 hover:border-gray-300 transition-all duration-200 shadow-sm">
      <img
        src={agent.logo}
        alt={agent.name}
        className="w-7 h-7 object-contain"
        style={agent.invert ? { filter: "invert(1)" } : undefined}
      />
      <span className="text-[9px] font-mono text-center leading-tight text-gray-500">
        {agent.name}
      </span>
    </div>
  );
}

// ── Stats row ─────────────────────────────────────────────────────────────────

const STATS = [
  { value: "11", label: "MCP tools" },
  { value: "5", label: "agent platforms" },
  { value: "100%", label: "open source" },
  { value: "MIT", label: "license" },
];

// ── Main section ──────────────────────────────────────────────────────────────

export default function Integrations() {
  return (
    <section className="py-40 px-6 overflow-hidden bg-white" id="integrations">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <p className="text-xs font-mono text-teal-600 mb-4 tracking-widest uppercase">
            Works with every harness
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-5 text-gray-900">
            Any harness. Any model.
            <br />
            <span className="text-teal-600">One shared room.</span>
          </h2>
          <p className="text-gray-500 text-lg max-w-2xl mx-auto leading-relaxed">
            Claude Code, Cursor, Codex, Gemini, Windsurf. If it speaks MCP or
            HTTP, it can join the group chat and build together.
          </p>
        </motion.div>

        {/* Agent platform badges with real logos */}
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6, delay: 0.2 }}
          className="flex flex-wrap justify-center gap-3 mb-20"
        >
          {AGENT_PLATFORMS.map((agent, i) => (
            <motion.div
              key={agent.name}
              initial={{ opacity: 0, y: 12 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.4, delay: 0.1 + i * 0.05 }}
            >
              <AgentBadge agent={agent} />
            </motion.div>
          ))}
        </motion.div>

        {/* Feature cards */}
        <div className="grid grid-cols-1 md:grid-cols-3 gap-5 mb-20">
          {INTEGRATION_CARDS.map((card, i) => (
            <motion.div
              key={card.tag}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.5, delay: i * 0.1 }}
              className="rounded-2xl p-6 bg-white border border-gray-200 hover:border-gray-300 hover:shadow-lg transition-all shadow-sm"
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="w-9 h-9 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-600">
                  {card.icon}
                </div>
                <span className="text-[10px] font-mono text-teal-600 tracking-widest uppercase">
                  {card.tag}
                </span>
              </div>
              <h3 className="text-lg font-semibold text-gray-900 mb-2">
                {card.title}
              </h3>
              <p className="text-sm text-gray-500 leading-relaxed mb-4">
                {card.desc}
              </p>
              <div className="px-3 py-2 rounded-lg bg-gray-900 border border-gray-800">
                <code className="text-[11px] font-mono text-teal-400">
                  {card.code}
                </code>
              </div>
            </motion.div>
          ))}
        </div>

        {/* Stats row */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5 }}
          className="flex flex-wrap justify-center gap-8 md:gap-16"
        >
          {STATS.map((stat) => (
            <div key={stat.label} className="text-center">
              <div className="text-3xl font-bold text-teal-600 mb-1">
                {stat.value}
              </div>
              <div className="text-[10px] font-mono text-gray-400 tracking-widest uppercase">
                {stat.label}
              </div>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
