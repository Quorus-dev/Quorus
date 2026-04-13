import { motion } from "framer-motion";

// ── AI Agent platform logos ───────────────────────────────────────────────────

const AnthropicLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <path
      d="M18.87 7H14.5L7 25h4.5l1.6-4h7.8l1.6 4H27L18.87 7zm-4.2 10.5L16.7 11l2.03 6.5h-4.06z"
      fill="#d97757"
    />
  </svg>
);

const OpenAILogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <path
      d="M28.2 13.1a7.7 7.7 0 0 0-.66-6.32 7.8 7.8 0 0 0-8.38-3.74A7.8 7.8 0 0 0 13.3 1a7.8 7.8 0 0 0-7.43 5.4 7.8 7.8 0 0 0-5.2 3.77 7.8 7.8 0 0 0 .96 9.14 7.8 7.8 0 0 0 .66 6.32 7.8 7.8 0 0 0 8.38 3.74A7.8 7.8 0 0 0 16.54 31a7.8 7.8 0 0 0 7.44-5.4 7.8 7.8 0 0 0 5.2-3.77 7.8 7.8 0 0 0-.98-9.13z"
      fill="#10a37f"
    />
  </svg>
);

const GoogleLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <path
      d="M30.3 16.3c0-1.1-.1-2.1-.3-3.1H16v5.8h8.1a6.9 6.9 0 0 1-3 4.6v3.8h4.8c2.8-2.6 4.4-6.4 4.4-11.1z"
      fill="#4285f4"
    />
    <path
      d="M16 31c4.1 0 7.5-1.4 10-3.7l-4.8-3.8c-1.4.9-3.1 1.5-5.2 1.5-4 0-7.3-2.7-8.5-6.3H2.6v3.9A15 15 0 0 0 16 31z"
      fill="#34a853"
    />
    <path
      d="M7.5 18.7A9 9 0 0 1 7 16c0-.9.2-1.8.5-2.7V9.4H2.6A15 15 0 0 0 1 16c0 2.4.6 4.7 1.6 6.6l4.9-3.9z"
      fill="#fbbc05"
    />
    <path
      d="M16 7c2.3 0 4.3.8 5.9 2.3l4.4-4.4C23.5 2.4 20 1 16 1A15 15 0 0 0 2.6 9.4l4.9 3.9C8.7 9.7 12 7 16 7z"
      fill="#ea4335"
    />
  </svg>
);

const CursorLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <path d="M6 4l20 12-10.5 2.5L12 28 6 4z" fill="#60a5fa" />
  </svg>
);

const GitHubLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path
      d="M12 2C6.48 2 2 6.48 2 12c0 4.42 2.87 8.17 6.84 9.49.5.09.68-.22.68-.48v-1.69c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.1-1.46-1.1-1.46-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.56 9.56 0 0 1 12 6.8c.85 0 1.71.11 2.51.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10.01 10.01 0 0 0 22 12C22 6.48 17.52 2 12 2z"
      fill="#e2e8f0"
    />
  </svg>
);

const AiderLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <rect x="4" y="4" width="24" height="24" rx="4" fill="#22c55e" />
    <path d="M16 8l6 12H10l6-12z" fill="#000" fillOpacity="0.3" />
    <text
      x="16"
      y="22"
      textAnchor="middle"
      fill="#fff"
      fontSize="10"
      fontWeight="bold"
      fontFamily="monospace"
    >
      A
    </text>
  </svg>
);

const ContinueLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <circle cx="16" cy="16" r="12" fill="#8b5cf6" />
    <path d="M13 11l8 5-8 5V11z" fill="#fff" />
  </svg>
);

const WindsurfLogo = () => (
  <svg viewBox="0 0 32 32" fill="none" className="w-full h-full">
    <path d="M6 24c4-8 8-12 12-12s8 4 8 8-4 8-8 8-8-4-12 4z" fill="#0ea5e9" />
    <path d="M6 24c4-8 8-12 12-12" stroke="#fff" strokeWidth="2" />
  </svg>
);

// ── Agent platform data ───────────────────────────────────────────────────────

interface AgentPlatform {
  name: string;
  logo: React.ReactNode;
  bg: string;
  border: string;
}

const AGENT_PLATFORMS: AgentPlatform[] = [
  {
    name: "Claude Code",
    logo: <AnthropicLogo />,
    bg: "bg-[#1a0e0a]",
    border: "border-[#d97757]/30",
  },
  {
    name: "Codex CLI",
    logo: <OpenAILogo />,
    bg: "bg-[#0a1510]",
    border: "border-[#10a37f]/30",
  },
  {
    name: "Gemini CLI",
    logo: <GoogleLogo />,
    bg: "bg-[#0a0e1a]",
    border: "border-[#4285f4]/30",
  },
  {
    name: "Cursor",
    logo: <CursorLogo />,
    bg: "bg-[#0a0e14]",
    border: "border-[#60a5fa]/30",
  },
  {
    name: "Copilot",
    logo: <GitHubLogo />,
    bg: "bg-[#0d0d0d]",
    border: "border-white/15",
  },
  {
    name: "Aider",
    logo: <AiderLogo />,
    bg: "bg-[#0a140a]",
    border: "border-[#22c55e]/30",
  },
  {
    name: "Continue",
    logo: <ContinueLogo />,
    bg: "bg-[#0f0a14]",
    border: "border-[#8b5cf6]/30",
  },
  {
    name: "Windsurf",
    logo: <WindsurfLogo />,
    bg: "bg-[#0a1014]",
    border: "border-[#0ea5e9]/30",
  },
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

// ── Agent badge ───────────────────────────────────────────────────────────────

function AgentBadge({ agent }: { agent: AgentPlatform }) {
  return (
    <div
      className={`flex flex-col items-center gap-2 px-4 py-3 rounded-xl border ${agent.bg} ${agent.border} min-w-[80px] cursor-default select-none hover:scale-105 transition-transform duration-200`}
    >
      <div className="w-7 h-7">{agent.logo}</div>
      <span className="text-[9px] font-mono text-white/40 text-center leading-tight">
        {agent.name}
      </span>
    </div>
  );
}

// ── Stats row ─────────────────────────────────────────────────────────────────

const STATS = [
  { value: "11", label: "MCP tools" },
  { value: "8+", label: "agent platforms" },
  { value: "870+", label: "tests passing" },
  { value: "MIT", label: "license" },
];

// ── Main section ──────────────────────────────────────────────────────────────

export default function Integrations() {
  return (
    <section className="py-40 px-6 overflow-hidden" id="integrations">
      <div className="max-w-7xl mx-auto">
        {/* Section divider */}
        <div className="section-divider mb-20" />

        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
            Works with every agent
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-5">
            Any harness. Any model.
            <br />
            <span className="gradient-text">One shared room.</span>
          </h2>
          <p className="text-white/50 text-lg max-w-2xl mx-auto leading-relaxed">
            Claude Code, Cursor, Codex, Gemini, Copilot, Aider, Continue,
            Windsurf. If it speaks MCP or HTTP, it can join the swarm.
          </p>
        </motion.div>

        {/* Agent platform badges */}
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
              className="card-gradient-border rounded-2xl p-6"
            >
              <div className="flex items-center gap-3 mb-4">
                <div className="w-9 h-9 rounded-lg bg-teal-500/10 border border-teal-500/20 flex items-center justify-center text-teal-400">
                  {card.icon}
                </div>
                <span className="text-[10px] font-mono text-teal-400/70 tracking-widest uppercase">
                  {card.tag}
                </span>
              </div>
              <h3 className="text-lg font-semibold text-white mb-2">
                {card.title}
              </h3>
              <p className="text-sm text-white/45 leading-relaxed mb-4">
                {card.desc}
              </p>
              <div className="px-3 py-2 rounded-lg bg-black/40 border border-white/[0.06]">
                <code className="text-[11px] font-mono text-teal-300/70">
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
              <div className="text-3xl font-bold text-white mb-1">
                {stat.value}
              </div>
              <div className="text-[10px] font-mono text-white/30 tracking-widest uppercase">
                {stat.label}
              </div>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
