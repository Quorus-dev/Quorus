import { motion } from "framer-motion";

// ── Tool logos ────────────────────────────────────────────────────────────────

const GitHubLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path
      d="M12 2C6.48 2 2 6.48 2 12c0 4.42 2.87 8.17 6.84 9.49.5.09.68-.22.68-.48v-1.69c-2.78.6-3.37-1.34-3.37-1.34-.45-1.16-1.1-1.46-1.1-1.46-.9-.62.07-.6.07-.6 1 .07 1.53 1.03 1.53 1.03.89 1.52 2.34 1.08 2.91.83.09-.65.35-1.08.63-1.33-2.22-.25-4.55-1.11-4.55-4.94 0-1.09.39-1.98 1.03-2.68-.1-.25-.45-1.27.1-2.64 0 0 .84-.27 2.75 1.02A9.56 9.56 0 0 1 12 6.8c.85 0 1.71.11 2.51.33 1.91-1.29 2.75-1.02 2.75-1.02.55 1.37.2 2.39.1 2.64.64.7 1.03 1.59 1.03 2.68 0 3.84-2.34 4.69-4.57 4.93.36.31.68.92.68 1.85v2.74c0 .27.18.58.69.48A10.01 10.01 0 0 0 22 12C22 6.48 17.52 2 12 2z"
      fill="#e2e8f0"
    />
  </svg>
);

const SlackLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path d="M9 8a2 2 0 1 1-2-2h2v2z" fill="#e01e5a" />
    <path d="M10 8a2 2 0 0 1 2-2v5h-2V8z" fill="#e01e5a" />
    <path d="M15 10a2 2 0 1 1 2 2h-2v-2z" fill="#36c5f0" />
    <path d="M14 11a2 2 0 0 1-2 2H7v-2h7z" fill="#36c5f0" />
    <path d="M16 15a2 2 0 1 1-2 2v-2h2z" fill="#2eb67d" />
    <path d="M14 16a2 2 0 0 1-2 2v-5h2v3z" fill="#2eb67d" />
    <path d="M9 14a2 2 0 1 1-2-2h2v2z" fill="#ecb22e" />
    <path d="M10 15a2 2 0 0 1 2-2v5h-2v-3z" fill="#ecb22e" />
  </svg>
);

const NotionLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path
      d="M4.5 3h10.7l4.8 4.8V21H4.5V3z"
      stroke="#e2e8f0"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path
      d="M14.5 3v5.5H20"
      stroke="#e2e8f0"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path
      d="M8 10h8M8 13h6M8 16h4"
      stroke="#e2e8f0"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

const LinearLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path d="M4 4l16 8-8 8L4 4z" fill="#5e6ad2" />
    <path d="M4 4l8 16" stroke="#5e6ad2" strokeWidth="1.5" />
  </svg>
);

const JiraLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path
      d="M12 2L4 12l8 10 8-10L12 2z"
      fill="none"
      stroke="#0052cc"
      strokeWidth="1.5"
      strokeLinejoin="round"
    />
    <path d="M12 2l4 5-4 5-4-5 4-5z" fill="#0052cc" />
  </svg>
);

const FigmaLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <rect x="8" y="2" width="8" height="6" rx="2" fill="#f24e1e" />
    <rect x="8" y="9" width="8" height="6" rx="2" fill="#ff7262" />
    <rect x="8" y="16" width="8" height="6" rx="2" fill="#0acf83" />
    <circle cx="16" cy="12" r="4" fill="#1abcfe" />
    <rect
      x="8"
      y="2"
      width="8"
      height="6"
      rx="2"
      fill="#f24e1e"
      opacity="0.8"
    />
  </svg>
);

const GDriveLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path d="M12 4L4 18h8l8-14z" fill="none" />
    <path d="M4 18l4-7h12l-4 7H4z" fill="#4285f4" />
    <path d="M8 11L12 4l4 7H8z" fill="#fbbc04" />
    <path d="M16 11l4 7H8l4-7h4z" fill="#0f9d58" />
  </svg>
);

const VercelLogo = () => (
  <svg viewBox="0 0 24 24" fill="none" className="w-full h-full">
    <path d="M12 3L22 21H2L12 3z" fill="#e2e8f0" />
  </svg>
);

// ── Tool logo data ────────────────────────────────────────────────────────────

interface ToolLogo {
  name: string;
  logo: React.ReactNode;
  bg: string;
  border: string;
}

const TOOL_LOGOS: ToolLogo[] = [
  {
    name: "GitHub",
    logo: <GitHubLogo />,
    bg: "bg-[#0d0d0d]",
    border: "border-white/15",
  },
  {
    name: "Slack",
    logo: <SlackLogo />,
    bg: "bg-[#1a0a10]",
    border: "border-[#e01e5a]/25",
  },
  {
    name: "Notion",
    logo: <NotionLogo />,
    bg: "bg-[#0d0d0d]",
    border: "border-white/15",
  },
  {
    name: "Linear",
    logo: <LinearLogo />,
    bg: "bg-[#0a0b1a]",
    border: "border-[#5e6ad2]/30",
  },
  {
    name: "Jira",
    logo: <JiraLogo />,
    bg: "bg-[#0a0e1a]",
    border: "border-[#0052cc]/30",
  },
  {
    name: "Figma",
    logo: <FigmaLogo />,
    bg: "bg-[#1a0e0a]",
    border: "border-[#f24e1e]/25",
  },
  {
    name: "Google Drive",
    logo: <GDriveLogo />,
    bg: "bg-[#0a0e1a]",
    border: "border-[#4285f4]/25",
  },
  {
    name: "Vercel",
    logo: <VercelLogo />,
    bg: "bg-[#0d0d0d]",
    border: "border-white/15",
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
    title: "Plain HTTP, always",
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
    title: "Switch anytime",
    desc: "Swap agents mid-sprint. The relay persists. History, state, and locks survive without replay.",
    code: `relay.rooms — durable, agent-agnostic`,
  },
];

// ── Tool badge ────────────────────────────────────────────────────────────────

function ToolBadge({ tool }: { tool: ToolLogo }) {
  return (
    <div
      className={`flex flex-col items-center gap-2 px-4 py-3 rounded-xl border ${tool.bg} ${tool.border} min-w-[72px] cursor-default select-none hover:scale-105 transition-transform duration-200`}
    >
      <div className="w-6 h-6">{tool.logo}</div>
      <span className="text-[9px] font-mono text-white/35 text-center leading-tight">
        {tool.name}
      </span>
    </div>
  );
}

// ── Stats row ─────────────────────────────────────────────────────────────────

const STATS = [
  { value: "9", label: "agent platforms" },
  { value: "250+", label: "teams" },
  { value: "3.6ms", label: "p50 latency" },
  { value: "866+", label: "tests passing" },
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
          viewport={{ once: true, margin: "-50px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="text-center mb-14"
        >
          <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
            Integrations
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-5">
            Every tool your agents use,
            <br />
            available in every room.
          </h2>
          <p className="text-white/50 text-base max-w-lg mx-auto">
            Murmur coordinates the agents. The agents coordinate around your
            tools. No adapters needed.
          </p>
        </motion.div>

        {/* Tool logos grid */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.1 }}
          className="flex flex-wrap justify-center gap-3 mb-20"
        >
          {TOOL_LOGOS.map((tool, i) => (
            <motion.div
              key={tool.name}
              initial={{ opacity: 0, scale: 0.9 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true }}
              transition={{ duration: 0.3, delay: i * 0.05 }}
            >
              <ToolBadge tool={tool} />
            </motion.div>
          ))}
        </motion.div>

        {/* Feature cards */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, margin: "-60px" }}
          transition={{ duration: 0.6, ease: [0.21, 0.47, 0.32, 0.98] }}
          className="grid grid-cols-1 md:grid-cols-3 gap-4 mb-16"
        >
          {INTEGRATION_CARDS.map((card, i) => (
            <motion.div
              key={card.tag}
              initial={{ opacity: 0, y: 20 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.1, duration: 0.5 }}
              className="card-gradient-border group rounded-2xl p-6 flex flex-col gap-4 hover:border-teal-500/30 transition-all duration-300"
            >
              <div className="flex items-center gap-3">
                <div className="p-2.5 rounded-xl bg-teal-500/10 text-teal-400 group-hover:bg-teal-500/15 transition-colors duration-200">
                  {card.icon}
                </div>
                <span className="text-xs font-mono text-teal-400/80 tracking-widest uppercase">
                  {card.tag}
                </span>
              </div>

              <div>
                <h3 className="text-base font-semibold text-white mb-1.5">
                  {card.title}
                </h3>
                <p className="text-sm text-white/50 leading-relaxed">
                  {card.desc}
                </p>
              </div>

              <div className="mt-auto rounded-lg bg-black/50 border border-white/[0.06] px-3 py-2">
                <code className="text-[11px] font-mono text-teal-300/80 break-all">
                  {card.code}
                </code>
              </div>
            </motion.div>
          ))}
        </motion.div>

        {/* Stats row */}
        <motion.div
          initial={{ opacity: 0, y: 16 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.2 }}
          className="flex flex-wrap justify-center items-center gap-8 py-8 border-t border-white/[0.05]"
        >
          {STATS.map((stat, i) => (
            <motion.div
              key={stat.label}
              initial={{ opacity: 0 }}
              whileInView={{ opacity: 1 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.08, duration: 0.4 }}
              className="text-center"
            >
              <div className="text-2xl font-bold gradient-text-subtle tabular-nums">
                {stat.value}
              </div>
              <div className="text-[11px] font-mono text-white/30 mt-0.5">
                {stat.label}
              </div>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
