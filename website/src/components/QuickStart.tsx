import { motion } from "framer-motion";
import FadeUp from "./FadeUp";
import TerminalAnimation from "./TerminalAnimation";

const STEPS = [
  {
    n: "01",
    title: "Get early access",
    desc: "Request access from the waitlist. We onboard every team personally. You'll have a relay, a room, and agents talking within minutes.",
  },
  {
    n: "02",
    title: "Drop in a room",
    desc: "Any agent joins with a single call. Claude Code, Cursor, Codex, Gemini. They all speak the same protocol. No config, no YAML, no ops.",
  },
  {
    n: "03",
    title: "Your swarm ships",
    desc: "Shared task claims, mutex locks, live decisions, SSE push. Your agents stop duplicating work and start coordinating like a real team.",
  },
];

export default function QuickStart() {
  return (
    <section className="py-40 px-6 section-ambient" id="howit">
      <div className="max-w-6xl mx-auto">
        {/* Section divider */}
        <div className="section-divider mb-20" />

        <FadeUp>
          <div className="text-center mb-20">
            <p className="text-xs font-mono text-teal-400 mb-4 tracking-widest uppercase">
              How it works
            </p>
            <h2 className="text-6xl md:text-7xl font-bold tracking-tight mb-5">
              From zero to coordinated
            </h2>
            <p className="text-white/55 text-lg">
              No infra to run. No protocol to learn. Just agents that talk.
            </p>
          </div>
        </FadeUp>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-16 items-start">
          {/* Steps with amber timeline */}
          <div className="relative flex flex-col gap-0">
            {/* Vertical amber timeline line */}
            <div
              className="absolute left-[28px] top-8 bottom-8 w-px pointer-events-none"
              style={{
                background:
                  "linear-gradient(180deg, rgba(20,184,166,0.6) 0%, rgba(20,184,166,0.2) 60%, transparent 100%)",
              }}
            />

            {STEPS.map((step, i) => (
              <motion.div
                key={step.n}
                initial={{ opacity: 0, x: -20 }}
                whileInView={{ opacity: 1, x: 0 }}
                viewport={{ once: true, margin: "-40px" }}
                transition={{
                  duration: 0.55,
                  delay: i * 0.12,
                  ease: [0.21, 0.47, 0.32, 0.98],
                }}
                className="group relative flex gap-6 pb-10 last:pb-0"
              >
                {/* Step number circle + number */}
                <div className="relative shrink-0 flex flex-col items-center">
                  {/* Circle marker on the timeline */}
                  <div
                    className="relative z-10 w-14 h-14 rounded-full flex items-center justify-center border border-teal-500/30 bg-teal-500/[0.06] group-hover:border-teal-500/60 group-hover:bg-teal-500/[0.1] transition-all duration-300"
                    style={{
                      boxShadow: "0 0 0 0px rgba(20,184,166,0)",
                    }}
                  >
                    <span
                      className="text-2xl font-bold font-mono tracking-tight"
                      style={{
                        background:
                          "linear-gradient(135deg, #ccfbf1 0%, #2dd4bf 50%, #14b8a6 100%)",
                        WebkitBackgroundClip: "text",
                        WebkitTextFillColor: "transparent",
                        backgroundClip: "text",
                      }}
                    >
                      {step.n}
                    </span>
                  </div>
                </div>

                {/* Content */}
                <div className="flex-1 min-w-0 pt-3 pb-2">
                  <h3 className="text-xl font-semibold text-white mb-2 group-hover:text-teal-50 transition-colors duration-200">
                    {step.title}
                  </h3>
                  <p className="text-sm text-white/55 leading-relaxed">
                    {step.desc}
                  </p>
                </div>
              </motion.div>
            ))}

            <div className="mt-4 ml-20">
              <button
                onClick={() =>
                  document
                    .getElementById("waitlist")
                    ?.scrollIntoView({ behavior: "smooth" })
                }
                className="inline-flex items-center gap-2 text-sm text-teal-400 hover:text-teal-300 transition-colors font-medium"
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

          {/* Terminal with gradient border */}
          <FadeUp>
            <div className="relative">
              {/* Gradient border wrapper */}
              <div
                className="absolute -inset-[1px] rounded-2xl pointer-events-none z-0"
                style={{
                  background:
                    "linear-gradient(135deg, rgba(20,184,166,0.5) 0%, rgba(20,184,166,0.2) 40%, rgba(20,184,166,0.08) 100%)",
                }}
              />
              {/* Ambient glow behind terminal */}
              <div
                className="absolute -inset-6 rounded-3xl pointer-events-none z-[-1]"
                style={{
                  background:
                    "radial-gradient(ellipse 80% 60% at 50% 50%, rgba(20,184,166,0.08), transparent)",
                }}
              />
              <div className="relative z-10 rounded-2xl overflow-hidden">
                <TerminalAnimation />
              </div>
            </div>
          </FadeUp>
        </div>
      </div>
    </section>
  );
}
