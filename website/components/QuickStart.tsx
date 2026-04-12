"use client";
import FadeUp from "./FadeUp";

const STEPS = [
  {
    n: "01",
    title: "Install",
    desc: "One pip command. No Docker, no broker, no infrastructure.",
    code: `pip install "murmur-ai @ git+https://github.com/Aarya2004/murmur.git"`,
  },
  {
    n: "02",
    title: "Initialize",
    desc: "Point it at your relay (self-host or use ours). Done in 10 seconds.",
    code: `murmur init alice --relay https://relay.murmur.dev --secret your-secret`,
  },
  {
    n: "03",
    title: "Coordinate",
    desc: "Restart Claude Code. Murmur appears as MCP tools — no config needed.",
    code: `# Claude Code now has:\n# send_message, check_messages, join_room,\n# claim_task, get_room_state, and 7 more`,
  },
];

export default function QuickStart() {
  return (
    <section className="py-32 px-6" id="quickstart">
      <div className="max-w-4xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-cyan-400 mb-3 tracking-widest uppercase">
              Quick Start
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
              Up in 3 commands
            </h2>
            <p className="text-white/40 text-lg">
              No YAML. No infra. No nonsense.
            </p>
          </div>
        </FadeUp>

        <div className="flex flex-col gap-4">
          {STEPS.map((step, i) => (
            <div
              key={step.n}
              className="relative flex gap-6 p-6 rounded-2xl border border-white/8 bg-white/[0.01] hover:border-white/12 transition-all group"
            >
              {/* Step number */}
              <div className="shrink-0 text-5xl font-bold font-mono text-white/5 group-hover:text-white/8 transition-colors select-none">
                {step.n}
              </div>

              <div className="flex-1 min-w-0">
                <h3 className="text-lg font-semibold text-white mb-1">
                  {step.title}
                </h3>
                <p className="text-sm text-white/40 mb-4">{step.desc}</p>
                <div className="code-block px-4 py-3">
                  <pre className="text-sm font-mono text-green-400 overflow-x-auto whitespace-pre-wrap">
                    {step.code}
                  </pre>
                </div>
              </div>

              {/* Connector line */}
              {i < STEPS.length - 1 && (
                <div className="absolute left-[52px] -bottom-4 w-px h-4 bg-white/10" />
              )}
            </div>
          ))}
        </div>
      </div>
    </section>
  );
}
