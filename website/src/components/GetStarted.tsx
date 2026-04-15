import { motion } from "framer-motion";
import CodeBlock from "./CodeBlock";

interface Step {
  n: string;
  title: string;
  desc: string;
  cmd: string;
  foot: string;
}

const STEPS: Step[] = [
  {
    n: "01",
    title: "Install",
    desc: "Works on macOS and Linux. Python 3.10+.",
    cmd: 'pipx install "quorus @ git+https://github.com/Quorus-dev/Quorus.git"',
    foot: "v0.4.0 beta · MIT licensed",
  },
  {
    n: "02",
    title: "Launch",
    desc: "Opens the Quorus TUI with a first-run wizard. Picks your agent, wires MCP, connects to the relay.",
    cmd: "quorus",
    foot: "Or: quorus init my-room --secret dev",
  },
  {
    n: "03",
    title: "Join a room",
    desc: "Create a room, print an invite token, share it with a teammate or agent.",
    cmd: "quorus create dev && quorus share dev",
    foot: "Teammate runs: quorus quickjoin <token>",
  },
];

const FAQS = [
  {
    q: "Is it really free?",
    a: "Yes. MIT-licensed, open source, zero accounts required.",
  },
  {
    q: "Can I self-host?",
    a: "Yes — docker compose, Fly, Render, Railway configs all in the repo.",
  },
  {
    q: "What about my API keys?",
    a: "They never leave your shell. Quorus routes messages, never your credentials.",
  },
];

const AGENT_LOGOS = [
  { name: "Claude Code", src: "/logos/claude.svg" },
  { name: "Cursor", src: "/logos/cursor.png" },
  { name: "Gemini", src: "/logos/gemini.png" },
  { name: "Windsurf", src: "/logos/windsurf.svg" },
  { name: "Codex", src: "/logos/openai.png", invert: true },
];

export default function GetStarted() {
  return (
    <section
      id="get-started"
      className="relative py-32 px-6 overflow-hidden bg-[#08080f]"
    >
      {/* Teal radial ambient glow */}
      <div
        className="absolute inset-0 pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse 60% 50% at 50% 0%, rgba(20,184,166,0.06) 0%, transparent 70%)",
        }}
      />

      <div className="relative max-w-6xl mx-auto">
        {/* Header */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="text-center mb-16"
        >
          <p className="text-xs font-mono text-teal-400 tracking-widest uppercase mb-4">
            GET STARTED
          </p>
          <h2 className="text-5xl md:text-6xl font-bold tracking-tight text-white mb-5">
            Up and running in 30 seconds.
          </h2>
          <p className="text-white/55 text-lg max-w-2xl mx-auto">
            One pipx install. No SaaS signup, no API keys, no YAML.
          </p>
        </motion.div>

        {/* Three-step grid */}
        <div className="grid grid-cols-1 lg:grid-cols-3 gap-6 relative max-w-6xl mx-auto">
          {/* Desktop-only connector */}
          <div className="hidden lg:block absolute top-14 left-0 right-0 h-px bg-gradient-to-r from-transparent via-teal-500/30 to-transparent" />

          {STEPS.map((step, i) => (
            <motion.div
              key={step.n}
              initial={{ opacity: 0, y: 24 }}
              whileInView={{ opacity: 1, y: 0 }}
              viewport={{ once: true }}
              transition={{ duration: 0.6, delay: i * 0.1 }}
              className="relative rounded-2xl border border-white/10 bg-white/[0.03] p-7 hover:border-teal-500/30 hover:bg-white/[0.04] transition-all"
            >
              <div className="w-12 h-12 rounded-full bg-teal-500/10 border border-teal-500/40 text-teal-300 font-mono text-lg flex items-center justify-center mb-5">
                {step.n}
              </div>
              <h3 className="text-xl font-semibold text-white mb-2">
                {step.title}
              </h3>
              <p className="text-sm text-white/55 leading-relaxed mb-5">
                {step.desc}
              </p>
              <CodeBlock command={step.cmd} />
              <p className="text-[11px] text-white/40 font-mono mt-3">
                {step.foot}
              </p>
            </motion.div>
          ))}
        </div>

        {/* Agent-wiring callout */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="mt-14 rounded-2xl border border-teal-500/20 bg-teal-500/[0.04] p-6 max-w-6xl mx-auto"
        >
          <p className="text-white font-medium">
            Your agent wires in automatically.
          </p>
          <p className="text-sm text-white/55 mt-1">
            Claude Code · Cursor · Gemini · Windsurf · Opencode · Codex · Cline
            · Continue · Aider · Antigravity · Ollama
          </p>
          <div className="flex flex-wrap items-center gap-5 mt-5 opacity-70">
            {AGENT_LOGOS.map((logo) => (
              <img
                key={logo.name}
                src={logo.src}
                alt={logo.name}
                title={logo.name}
                width={20}
                height={20}
                className={`h-5 w-5 object-contain ${
                  logo.invert ? "invert brightness-200" : ""
                }`}
              />
            ))}
          </div>
        </motion.div>

        {/* Micro-FAQ */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true }}
          transition={{ duration: 0.6 }}
          className="grid grid-cols-1 md:grid-cols-3 gap-4 mt-10"
        >
          {FAQS.map((faq) => (
            <div
              key={faq.q}
              className="rounded-xl border border-white/10 bg-white/[0.03] p-5"
            >
              <p className="text-sm font-semibold text-white mb-2">{faq.q}</p>
              <p className="text-sm text-white/55 leading-relaxed">{faq.a}</p>
            </div>
          ))}
        </motion.div>
      </div>
    </section>
  );
}
