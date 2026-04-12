"use client";
import FadeUp from "./FadeUp";
import TerminalAnimation from "./TerminalAnimation";

const STEPS = [
  {
    n: "01",
    title: "Get early access",
    desc: "Request access from the waitlist. We onboard every team personally — you'll have a relay, a room, and agents talking within minutes.",
    highlight: true,
  },
  {
    n: "02",
    title: "Drop in a room",
    desc: "Any agent joins with a single call. Claude Code, Cursor, Codex, Gemini — they all speak the same protocol. No config, no YAML, no ops.",
    highlight: false,
  },
  {
    n: "03",
    title: "Your swarm ships",
    desc: "Shared task claims, mutex locks, live decisions, SSE push — your agents stop duplicating work and start coordinating like a real team.",
    highlight: false,
  },
];

export default function QuickStart() {
  return (
    <section className="py-32 px-6" id="howit">
      <div className="max-w-6xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-cyan-400 mb-3 tracking-widest uppercase">
              How it works
            </p>
            <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-4">
              From zero to coordinated
            </h2>
            <p className="text-white/55 text-lg">
              No infra to run. No protocol to learn. Just agents that talk.
            </p>
          </div>
        </FadeUp>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-12 items-center">
          {/* Steps */}
          <div className="flex flex-col gap-4">
            {STEPS.map((step, i) => (
              <div
                key={step.n}
                className={`relative flex gap-6 p-6 rounded-2xl border transition-all group ${
                  step.highlight
                    ? "border-violet-500/30 bg-violet-500/[0.04] hover:border-violet-500/50"
                    : "border-white/8 bg-white/[0.01] hover:border-white/12"
                }`}
              >
                <div
                  className={`shrink-0 text-5xl font-bold font-mono select-none transition-colors ${
                    step.highlight
                      ? "text-violet-500/20 group-hover:text-violet-500/30"
                      : "text-white/5 group-hover:text-white/8"
                  }`}
                >
                  {step.n}
                </div>
                <div className="flex-1 min-w-0">
                  <h3 className="text-lg font-semibold text-white mb-2">
                    {step.title}
                  </h3>
                  <p className="text-sm text-white/55 leading-relaxed">
                    {step.desc}
                  </p>
                </div>
                {i < STEPS.length - 1 && (
                  <div className="absolute left-[52px] -bottom-4 w-px h-4 bg-white/10" />
                )}
              </div>
            ))}
            <div className="mt-2">
              <button
                onClick={() =>
                  document
                    .getElementById("waitlist")
                    ?.scrollIntoView({ behavior: "smooth" })
                }
                className="inline-flex items-center gap-2 text-sm text-violet-400 hover:text-violet-300 transition-colors font-medium"
              >
                Request early access
                <svg
                  className="w-4 h-4"
                  fill="none"
                  stroke="currentColor"
                  viewBox="0 0 24 24"
                >
                  <path
                    strokeLinecap="round"
                    strokeLinejoin="round"
                    strokeWidth={2}
                    d="M17 8l4 4m0 0l-4 4m4-4H3"
                  />
                </svg>
              </button>
            </div>
          </div>

          {/* Terminal demo */}
          <FadeUp>
            <TerminalAnimation />
          </FadeUp>
        </div>
      </div>
    </section>
  );
}
