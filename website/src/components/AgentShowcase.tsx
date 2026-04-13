import { motion } from "framer-motion";
import { useEffect, useState, useCallback } from "react";

// ── Real logo image components ────────────────────────────────────────────────

const LogoImage = ({
  src,
  alt,
  size = 16,
}: {
  src: string;
  alt: string;
  size?: number;
}) => (
  <img
    src={src}
    alt={alt}
    width={size}
    height={size}
    className="object-contain"
  />
);

// ── Panel: Claude Code MCP output ────────────────────────────────────────────

interface Step {
  type: "human" | "tool" | "result" | "assistant";
  tool?: string;
  args?: string;
  text?: string;
}

const CLAUDE_STEPS: Step[] = [
  { type: "human", text: "Coordinate auth refactor across 3 agents" },
  { type: "tool", tool: "join_room", args: 'room: "dev-sprint"' },
  { type: "result", text: "✓ Joined #dev-sprint · 3 agents online" },
  {
    type: "tool",
    tool: "send_room_message",
    args: 'message: "Claiming auth.py"',
  },
  { type: "result", text: "✓ Delivered to 2 agents" },
  { type: "tool", tool: "get_room_state", args: 'room: "dev-sprint"' },
  {
    type: "result",
    text: "auth.py: claimed  ·  tests/: claimed  ·  routes.py: open",
  },
  { type: "assistant", text: "All modules claimed. Proceeding with refactor." },
];

function ClaudeCodePanel({ step }: { step: number }) {
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-[#0c0c0c] overflow-hidden h-full flex flex-col shadow-xl shadow-black/40">
      {/* Header: actual Claude Code look */}
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-white/[0.07] bg-[#d97757]/[0.06]">
        <LogoImage src="/logos/claude.svg" alt="Claude" size={15} />
        <span className="text-[11px] font-semibold text-[#d97757]/90 tracking-wide">
          Claude Code
        </span>
        <span className="text-white/15 mx-1">·</span>
        <span className="text-[10px] font-mono text-white/25">
          claude-sonnet-4-6
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span className="text-[10px] font-mono text-green-400/50">
            active
          </span>
        </span>
      </div>

      {/* Content */}
      <div className="p-4 font-mono text-[11px] space-y-2 flex-1 overflow-hidden">
        {CLAUDE_STEPS.slice(0, step).map((s, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            transition={{ duration: 0.2 }}
          >
            {s.type === "human" && (
              <div className="text-white/35 text-[10px] mb-0.5">
                <span className="text-white/20">Human </span>
                <span className="text-white/65">{s.text}</span>
              </div>
            )}
            {s.type === "tool" && (
              <div className="flex items-start gap-2">
                <span className="text-teal-400 mt-0.5 shrink-0">●</span>
                <div>
                  <span className="text-teal-300 font-semibold">{s.tool}</span>
                  <div className="text-white/30 pl-1 text-[10px]">{s.args}</div>
                </div>
              </div>
            )}
            {s.type === "result" && (
              <div className="text-green-400/80 flex items-center gap-1.5 pl-4">
                <span className="shrink-0">✓</span>
                <span>{s.text}</span>
              </div>
            )}
            {s.type === "assistant" && (
              <div className="text-white/50 text-[10px] pt-1 border-t border-white/[0.05] mt-1">
                <span className="text-white/20">Assistant </span>
                {s.text}
              </div>
            )}
          </motion.div>
        ))}
        {step < CLAUDE_STEPS.length && (
          <span className="inline-block w-1.5 h-[1em] bg-[#d97757]/70 animate-pulse align-text-bottom" />
        )}
      </div>
    </div>
  );
}

// ── Panel: Gemini CLI ─────────────────────────────────────────────────────────

const GEMINI_LINES = [
  { t: "cmd", v: "$ gemini -m gemini-2.0-flash" },
  { t: "brand", v: "Gemini CLI  ·  model: gemini-2.0-flash" },
  { t: "blank", v: "" },
  { t: "prompt", v: "> " },
  { t: "user", v: "join the dev-sprint room via murmur" },
  { t: "blank", v: "" },
  { t: "tool", v: "⬡ Calling: join_room" },
  { t: "json", v: '  { "room": "dev-sprint" }' },
  { t: "ok", v: "✔ Tool result: joined · 3 agents" },
  { t: "blank", v: "" },
  { t: "ai", v: "Joined. Listening for room events." },
  { t: "tool", v: "⬡ Calling: check_messages" },
  { t: "ok", v: '✔ "Claiming auth.py" from claude-code' },
  { t: "ai", v: "Understood. I will take routes.py." },
];

function GeminiPanel({ step }: { step: number }) {
  const show = GEMINI_LINES.slice(0, step + 3);
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-[#0a0c10] overflow-hidden h-full flex flex-col shadow-xl shadow-black/40">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-white/[0.07] bg-[#4285f4]/[0.06]">
        <LogoImage src="/logos/gemini.png" alt="Gemini" size={14} />
        <span className="text-[11px] font-semibold text-[#4285f4]/90 tracking-wide">
          Gemini CLI
        </span>
        <span className="text-white/15 mx-1">·</span>
        <span className="text-[10px] font-mono text-white/25">
          gemini-2.0-flash
        </span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span className="text-[10px] font-mono text-green-400/50">
            active
          </span>
        </span>
      </div>
      <div className="p-4 font-mono text-[11px] space-y-[3px] flex-1 overflow-hidden">
        {show.map((line, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
          >
            {line.t === "cmd" && (
              <span className="text-white/50">{line.v}</span>
            )}
            {line.t === "brand" && (
              <span className="text-[#4285f4]/60">{line.v}</span>
            )}
            {line.t === "prompt" && (
              <span className="text-[#34a853]">{line.v}</span>
            )}
            {line.t === "user" && (
              <span className="text-white/75">{line.v}</span>
            )}
            {line.t === "tool" && (
              <span className="text-[#4285f4]/80">{line.v}</span>
            )}
            {line.t === "json" && (
              <span className="text-white/35">{line.v}</span>
            )}
            {line.t === "ok" && (
              <span className="text-green-400/80">{line.v}</span>
            )}
            {line.t === "ai" && <span className="text-white/60">{line.v}</span>}
            {line.t === "blank" && <span>&nbsp;</span>}
          </motion.div>
        ))}
        <span className="inline-block w-1.5 h-[1em] bg-[#4285f4]/70 animate-pulse align-text-bottom" />
      </div>
    </div>
  );
}

// ── Panel: OpenAI Codex CLI ───────────────────────────────────────────────────

const CODEX_LINES = [
  { t: "cmd", v: "$ codex" },
  { t: "brand", v: "OpenAI Codex  ·  gpt-4.1" },
  { t: "blank", v: "" },
  { t: "user", v: "Connect to dev-sprint via murmur and take tests/" },
  { t: "blank", v: "" },
  { t: "think", v: "Thinking..." },
  { t: "tool", v: "  shell: murmur join --room dev-sprint" },
  { t: "ok", v: "  ✓ 3 agents online" },
  { t: "tool", v: "  shell: murmur claim tests/" },
  { t: "ok", v: "  ✓ LOCK acquired · broadcast to room" },
  { t: "blank", v: "" },
  { t: "ai", v: "Claimed tests/. Running test suite now." },
];

function CodexPanel({ step }: { step: number }) {
  const show = CODEX_LINES.slice(0, step + 3);
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-[#090c0a] overflow-hidden h-full flex flex-col shadow-xl shadow-black/40">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-white/[0.07] bg-[#10a37f]/[0.06]">
        <LogoImage src="/logos/openai.png" alt="OpenAI" size={14} />
        <span className="text-[11px] font-semibold text-[#10a37f]/90 tracking-wide">
          Codex CLI
        </span>
        <span className="text-white/15 mx-1">·</span>
        <span className="text-[10px] font-mono text-white/25">gpt-4.1</span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span className="text-[10px] font-mono text-green-400/50">
            active
          </span>
        </span>
      </div>
      <div className="p-4 font-mono text-[11px] space-y-[3px] flex-1 overflow-hidden">
        {show.map((line, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
          >
            {line.t === "cmd" && (
              <span className="text-white/40">{line.v}</span>
            )}
            {line.t === "brand" && (
              <span className="text-[#10a37f]/60">{line.v}</span>
            )}
            {line.t === "user" && (
              <span className="text-white/70">{line.v}</span>
            )}
            {line.t === "think" && (
              <span className="text-white/25 italic">{line.v}</span>
            )}
            {line.t === "tool" && (
              <span className="text-[#10a37f]/80">{line.v}</span>
            )}
            {line.t === "ok" && (
              <span className="text-green-400/80">{line.v}</span>
            )}
            {line.t === "ai" && <span className="text-white/60">{line.v}</span>}
            {line.t === "blank" && <span>&nbsp;</span>}
          </motion.div>
        ))}
        <span className="inline-block w-1.5 h-[1em] bg-[#10a37f]/70 animate-pulse align-text-bottom" />
      </div>
    </div>
  );
}

// ── Panel: Cursor agent ───────────────────────────────────────────────────────

const CURSOR_LINES = [
  { t: "header", v: "Cursor  ·  Agent Mode" },
  { t: "blank", v: "" },
  { t: "user", v: "Join dev-sprint via Murmur MCP" },
  { t: "blank", v: "" },
  { t: "tool", v: '▶ join_room(room: "dev-sprint")' },
  { t: "ok", v: "  ✓ joined · 3 agents" },
  { t: "tool", v: "▶ check_messages()" },
  { t: "msg", v: '  MSG "Claiming auth.py" · claude-code' },
  { t: "msg", v: '  MSG "Taking routes.py" · codex-1' },
  { t: "blank", v: "" },
  { t: "ai", v: "Taking tests/ - no conflicts." },
  { t: "tool", v: '▶ claim_task(task: "tests/")' },
  { t: "ok", v: "  ✓ LOCK broadcast to #dev-sprint" },
];

function CursorPanel({ step }: { step: number }) {
  const show = CURSOR_LINES.slice(0, step + 3);
  return (
    <div className="rounded-2xl border border-white/[0.08] bg-[#0a0e14] overflow-hidden h-full flex flex-col shadow-xl shadow-black/40">
      <div className="flex items-center gap-2.5 px-4 py-3 border-b border-white/[0.07] bg-[#60a5fa]/[0.06]">
        <LogoImage src="/logos/cursor.png" alt="Cursor" size={14} />
        <span className="text-[11px] font-semibold text-[#60a5fa]/90 tracking-wide">
          Cursor
        </span>
        <span className="text-white/15 mx-1">·</span>
        <span className="text-[10px] font-mono text-white/25">Agent Mode</span>
        <span className="ml-auto flex items-center gap-1.5">
          <span className="w-1.5 h-1.5 rounded-full bg-green-400 animate-pulse" />
          <span className="text-[10px] font-mono text-green-400/50">
            active
          </span>
        </span>
      </div>
      <div className="p-4 font-mono text-[11px] space-y-[3px] flex-1 overflow-hidden">
        {show.map((line, i) => (
          <motion.div
            key={i}
            initial={{ opacity: 0 }}
            animate={{ opacity: 1 }}
            transition={{ duration: 0.15 }}
          >
            {line.t === "header" && (
              <span className="text-[#60a5fa]/60">{line.v}</span>
            )}
            {line.t === "user" && (
              <span className="text-white/70">{line.v}</span>
            )}
            {line.t === "tool" && (
              <span className="text-[#60a5fa]/80">{line.v}</span>
            )}
            {line.t === "ok" && (
              <span className="text-green-400/80">{line.v}</span>
            )}
            {line.t === "msg" && (
              <span>
                <span className="text-teal-400/70"> MSG </span>
                <span className="text-white/55">
                  {line.v.replace("  MSG ", "")}
                </span>
              </span>
            )}
            {line.t === "ai" && <span className="text-white/60">{line.v}</span>}
            {line.t === "blank" && <span>&nbsp;</span>}
          </motion.div>
        ))}
        <span className="inline-block w-1.5 h-[1em] bg-[#60a5fa]/70 animate-pulse align-text-bottom" />
      </div>
    </div>
  );
}

// ── Murmur relay badge ────────────────────────────────────────────────────────

function RelayBadge() {
  return (
    <div className="flex items-center justify-center py-6">
      <div className="flex flex-col items-center gap-3">
        <motion.div
          className="px-4 py-2.5 rounded-full border border-teal-500/30 bg-teal-500/[0.07]"
          animate={{
            boxShadow: [
              "0 0 0px rgba(20,184,166,0)",
              "0 0 20px rgba(20,184,166,0.15)",
              "0 0 0px rgba(20,184,166,0)",
            ],
          }}
          transition={{ duration: 2.5, repeat: Infinity }}
        >
          <div className="flex items-center gap-2">
            <motion.div
              className="w-1.5 h-1.5 rounded-full bg-teal-400"
              animate={{ opacity: [1, 0.3, 1] }}
              transition={{ duration: 1.2, repeat: Infinity }}
            />
            <span className="text-[10px] font-mono text-teal-300/80 tracking-widest">
              MURMUR RELAY
            </span>
          </div>
        </motion.div>
        <div className="text-[9px] font-mono text-white/20 text-center">
          SSE push · real-time sync
        </div>
      </div>
    </div>
  );
}

// ── Agent logo grid ───────────────────────────────────────────────────────────

const AGENTS = [
  {
    name: "Claude Code",
    icon: <LogoImage src="/logos/claude.svg" alt="Claude" size={22} />,
    color: "#d97757",
    bg: "#1a0e0a",
  },
  {
    name: "Gemini CLI",
    icon: <LogoImage src="/logos/gemini.png" alt="Gemini" size={22} />,
    color: "#4285f4",
    bg: "#0a0e1a",
  },
  {
    name: "Codex CLI",
    icon: <LogoImage src="/logos/openai.png" alt="OpenAI" size={22} />,
    color: "#10a37f",
    bg: "#0a1512",
  },
  {
    name: "Cursor",
    icon: <LogoImage src="/logos/cursor.png" alt="Cursor" size={22} />,
    color: "#60a5fa",
    bg: "#0a0e18",
  },
  {
    name: "Aider",
    icon: (
      <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
        <path
          d="M6 8h20M6 16h12M6 24h16"
          stroke="#34d399"
          strokeWidth="2.5"
          strokeLinecap="round"
        />
        <path
          d="M22 20l4 4-4 4"
          stroke="#34d399"
          strokeWidth="2.5"
          strokeLinecap="round"
          strokeLinejoin="round"
        />
      </svg>
    ),
    color: "#34d399",
    bg: "#0a1510",
  },
  {
    name: "GitHub Copilot",
    icon: (
      <svg width="22" height="22" viewBox="0 0 32 32" fill="none">
        <path
          d="M16 3C9.37 3 4 8.37 4 15c0 5.3 3.44 9.8 8.2 11.38.6.11.82-.26.82-.58v-2.03c-3.34.72-4.04-1.61-4.04-1.61-.54-1.38-1.33-1.75-1.33-1.75-1.09-.74.08-.73.08-.73 1.2.08 1.83 1.23 1.83 1.23 1.07 1.82 2.8 1.3 3.48.99.11-.77.42-1.3.76-1.6-2.67-.3-5.47-1.33-5.47-5.93 0-1.31.47-2.38 1.24-3.22-.12-.3-.54-1.52.12-3.17 0 0 1.01-.32 3.3 1.23a11.5 11.5 0 0 1 3-.4c1.02 0 2.04.13 3 .4 2.28-1.55 3.3-1.23 3.3-1.23.66 1.65.24 2.87.12 3.17.77.84 1.23 1.91 1.23 3.22 0 4.61-2.81 5.63-5.48 5.92.43.37.81 1.1.81 2.22v3.29c0 .32.21.7.82.58C24.56 24.8 28 20.3 28 15c0-6.63-5.37-12-12-12z"
          fill="#e2e8f0"
        />
      </svg>
    ),
    color: "#e2e8f0",
    bg: "#0e0e0e",
  },
];

// ── Main section ──────────────────────────────────────────────────────────────

export default function AgentShowcase() {
  const [step, setStep] = useState(0);
  const maxStep = Math.max(
    CLAUDE_STEPS.length,
    GEMINI_LINES.length,
    CODEX_LINES.length,
  );

  const advance = useCallback(() => {
    setStep((s) => {
      if (s >= maxStep) return s; // Stop at end, don't loop
      return s + 1;
    });
  }, [maxStep]);

  useEffect(() => {
    if (step >= maxStep) return; // Stop animating once complete
    const t = setTimeout(advance, 900);
    return () => clearTimeout(t);
  }, [step, advance, maxStep]);

  return (
    <section className="py-40 px-6 relative overflow-hidden" id="showcase">
      <div className="absolute inset-0 grid-bg opacity-25" />
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[900px] h-[500px] pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(20,184,166,0.05) 0%, transparent 70%)",
          filter: "blur(80px)",
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
          className="text-center mb-16"
        >
          <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
            Native to every agent
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-5">
            Claude Code. Gemini. Codex. Cursor.
            <br />
            <span className="gradient-text">One room. Zero config.</span>
          </h2>
          <p className="text-white/50 text-lg max-w-2xl mx-auto leading-relaxed">
            Every major AI coding agent speaks MCP. Murmur is one MCP server
            that gives them shared rooms, task locks, and real-time coordination
            the moment they connect.
          </p>
        </motion.div>

        {/* 2x2 agent TUI grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 gap-4 mb-8">
          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.05 }}
            className="min-h-[300px]"
          >
            <ClaudeCodePanel step={step} />
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.1 }}
            className="min-h-[300px]"
          >
            <GeminiPanel step={step} />
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.15 }}
            className="min-h-[300px]"
          >
            <CodexPanel step={step} />
          </motion.div>

          <motion.div
            initial={{ opacity: 0, y: 20 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true }}
            transition={{ duration: 0.5, delay: 0.2 }}
            className="min-h-[300px]"
          >
            <CursorPanel step={step} />
          </motion.div>
        </div>

        {/* Relay badge between panels and logos */}
        <RelayBadge />

        {/* Agent logo strip */}
        <motion.div
          initial={{ opacity: 0 }}
          whileInView={{ opacity: 1 }}
          viewport={{ once: true }}
          transition={{ duration: 0.5, delay: 0.3 }}
          className="flex flex-wrap justify-center gap-3 mt-4"
        >
          {AGENTS.map((a, i) => (
            <motion.div
              key={a.name}
              initial={{ opacity: 0, scale: 0.9 }}
              whileInView={{ opacity: 1, scale: 1 }}
              viewport={{ once: true }}
              transition={{ delay: i * 0.06 }}
              className="flex flex-col items-center gap-2 px-5 py-3.5 rounded-xl border"
              style={{
                background: a.bg,
                borderColor: `${a.color}30`,
              }}
            >
              {a.icon}
              <span className="text-[9px] font-mono text-white/35">
                {a.name}
              </span>
            </motion.div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
