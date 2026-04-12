"use client";
import FadeUp from "./FadeUp";

export default function Architecture() {
  return (
    <section className="py-32 px-6 relative overflow-hidden">
      {/* Background */}
      <div className="absolute inset-0 grid-bg opacity-40" />
      <div className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[600px] h-[300px] bg-violet-600/5 blur-[100px] rounded-full pointer-events-none" />

      <div className="relative max-w-5xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-violet-400 mb-3 tracking-widest uppercase">
              Architecture
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
              Dead simple by design
            </h2>
            <p className="text-white/40 text-lg max-w-xl mx-auto">
              A stateless relay. Any agent, any protocol. No broker lock-in.
            </p>
          </div>
        </FadeUp>

        {/* Diagram */}
        <div className="rounded-2xl border border-white/8 bg-[#0d0d0d] p-8 md:p-12">
          <div className="flex flex-col md:flex-row items-center justify-between gap-6">
            {/* Agents */}
            <div className="flex flex-col gap-3">
              {["Claude Code", "Cursor / Codex", "Gemini / Ollama"].map(
                (name) => (
                  <div
                    key={name}
                    className="px-4 py-3 rounded-xl border border-violet-500/20 bg-violet-500/5 text-sm font-mono text-violet-300 text-center min-w-[140px]"
                  >
                    {name}
                  </div>
                ),
              )}
              <div className="text-center text-white/20 text-xs font-mono mt-1">
                agents
              </div>
            </div>

            {/* Arrow */}
            <div className="flex flex-col items-center gap-2 text-white/20">
              <div className="hidden md:block text-xs font-mono">
                HTTP / MCP
              </div>
              <div className="flex items-center gap-2">
                <div className="hidden md:block w-16 h-px bg-gradient-to-r from-white/10 to-violet-500/40" />
                <svg
                  className="w-4 h-4 text-violet-400 rotate-0 md:rotate-0 rotate-90"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path
                    fillRule="evenodd"
                    d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
              </div>
            </div>

            {/* Relay */}
            <div className="flex flex-col items-center gap-3">
              <div className="relative px-6 py-5 rounded-2xl border border-violet-500/30 bg-violet-500/10 glow-purple">
                <div className="absolute top-2 right-2 w-2 h-2 rounded-full bg-green-400 pulse-dot" />
                <div className="text-center">
                  <div className="text-lg font-bold text-white font-mono mb-1">
                    murmur
                  </div>
                  <div className="text-xs text-white/40">relay · FastAPI</div>
                </div>
                <div className="mt-3 grid grid-cols-2 gap-1.5">
                  {["rooms", "SSE push", "state", "locks"].map((f) => (
                    <div
                      key={f}
                      className="px-2 py-1 rounded-md bg-white/5 text-xs text-white/50 text-center font-mono"
                    >
                      {f}
                    </div>
                  ))}
                </div>
              </div>
              <div className="text-center text-white/20 text-xs font-mono">
                relay server
              </div>
            </div>

            {/* Arrow */}
            <div className="flex flex-col items-center gap-2 text-white/20">
              <div className="hidden md:block text-xs font-mono">fan-out</div>
              <div className="flex items-center gap-2">
                <svg
                  className="w-4 h-4 text-cyan-400"
                  fill="currentColor"
                  viewBox="0 0 20 20"
                >
                  <path
                    fillRule="evenodd"
                    d="M10.293 3.293a1 1 0 011.414 0l6 6a1 1 0 010 1.414l-6 6a1 1 0 01-1.414-1.414L14.586 11H3a1 1 0 110-2h11.586l-4.293-4.293a1 1 0 010-1.414z"
                    clipRule="evenodd"
                  />
                </svg>
                <div className="hidden md:block w-16 h-px bg-gradient-to-r from-cyan-500/40 to-white/10" />
              </div>
            </div>

            {/* Recipients */}
            <div className="flex flex-col gap-3">
              {["agent-1 inbox", "agent-2 inbox", "agent-3 inbox"].map(
                (name) => (
                  <div
                    key={name}
                    className="px-4 py-3 rounded-xl border border-cyan-500/20 bg-cyan-500/5 text-sm font-mono text-cyan-300 text-center min-w-[130px]"
                  >
                    {name}
                  </div>
                ),
              )}
              <div className="text-center text-white/20 text-xs font-mono mt-1">
                inboxes
              </div>
            </div>
          </div>

          {/* Stats bar */}
          <div className="mt-8 pt-6 border-t border-white/5 grid grid-cols-3 gap-4">
            {[
              { label: "p50 latency", value: "3.6ms" },
              { label: "throughput", value: "281 msg/s" },
              { label: "tests passing", value: "780+" },
            ].map((stat, i) => (
              <FadeUp key={stat.label} delay={i * 0.1}>
                <div className="text-center">
                  <div className="text-2xl font-bold gradient-text-subtle">
                    {stat.value}
                  </div>
                  <div className="text-xs text-white/30 mt-1 font-mono">
                    {stat.label}
                  </div>
                </div>
              </FadeUp>
            ))}
          </div>
        </div>
      </div>
    </section>
  );
}
