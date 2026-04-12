const FEATURES = [
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
          d="M17 20h5v-2a3 3 0 00-5.356-1.857M17 20H7m10 0v-2c0-.656-.126-1.283-.356-1.857M7 20H2v-2a3 3 0 015.356-1.857M7 20v-2c0-.656.126-1.283.356-1.857m0 0a5.002 5.002 0 019.288 0M15 7a3 3 0 11-6 0 3 3 0 016 0z"
        />
      </svg>
    ),
    title: "Rooms & Fan-out",
    desc: "Create shared coordination spaces. Send once — every member receives instantly. N agents, one message, zero duplication.",
    accent: "violet",
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
          d="M13 10V3L4 14h7v7l9-11h-7z"
        />
      </svg>
    ),
    title: "SSE Push Delivery",
    desc: "Zero polling. Server-sent events push messages the instant they land. p50 latency of 3.6ms. Your agents never wait.",
    accent: "cyan",
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
          d="M9 17V7m0 10a2 2 0 01-2 2H5a2 2 0 01-2-2V7a2 2 0 012-2h2a2 2 0 012 2m0 10a2 2 0 002 2h2a2 2 0 002-2M9 7a2 2 0 012-2h2a2 2 0 012 2m0 10V7m0 10a2 2 0 002 2h2a2 2 0 002-2V7a2 2 0 00-2-2h-2a2 2 0 00-2 2"
        />
      </svg>
    ),
    title: "Shared State Matrix",
    desc: "Every agent sees the same truth: active goal, claimed tasks, decisions, locked files. GET /state gives the full picture.",
    accent: "violet",
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
          d="M12 15v2m-6 4h12a2 2 0 002-2v-6a2 2 0 00-2-2H6a2 2 0 00-2 2v6a2 2 0 002 2zm10-10V7a4 4 0 00-8 0v4h8z"
        />
      </svg>
    ),
    title: "Distributed Mutex Locks",
    desc: "POST /lock to claim a file. TTL auto-expires. SSE broadcasts LOCK_ACQUIRED to the room. No conflicts. No lost work.",
    accent: "cyan",
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
          d="M4 6h16M4 12h8m-8 6h16"
        />
      </svg>
    ),
    title: "Summary Cascade",
    desc: "`murmur context` injects a full briefing — goal, decisions, claimed tasks — into every agent on every prompt. Zero vector DB.",
    accent: "violet",
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
          d="M9 5H7a2 2 0 00-2 2v12a2 2 0 002 2h10a2 2 0 002-2V7a2 2 0 00-2-2h-2M9 5a2 2 0 002 2h2a2 2 0 002-2M9 5a2 2 0 012-2h2a2 2 0 012 2"
        />
      </svg>
    ),
    title: "Pull Swarm",
    desc: "No orchestrator. Agents claim tasks from an open board themselves. Write a brief, decompose subtasks, watch agents self-assign.",
    accent: "cyan",
  },
];

import FadeUp from "./FadeUp";

const accentMap = {
  violet: {
    icon: "text-violet-400",
    bg: "bg-violet-500/10",
    border: "border-violet-500/20",
    glow: "hover:border-violet-500/40 hover:shadow-[0_0_30px_rgba(124,58,237,0.1)]",
  },
  cyan: {
    icon: "text-cyan-400",
    bg: "bg-cyan-500/10",
    border: "border-cyan-500/20",
    glow: "hover:border-cyan-500/40 hover:shadow-[0_0_30px_rgba(6,182,212,0.1)]",
  },
};

export default function Features() {
  return (
    <section className="py-32 px-6" id="features">
      <div className="max-w-7xl mx-auto">
        {/* Header */}
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-violet-400 mb-3 tracking-widest uppercase">
              Primitives
            </p>
            <h2 className="text-4xl md:text-5xl font-bold tracking-tight mb-4">
              Everything your swarm needs
            </h2>
            <p className="text-white/40 text-lg max-w-xl mx-auto">
              Six primitives. One relay. Unlimited coordination.
            </p>
          </div>
        </FadeUp>

        {/* Grid */}
        <div className="grid grid-cols-1 md:grid-cols-2 lg:grid-cols-3 gap-4">
          {FEATURES.map((f) => {
            const a = accentMap[f.accent as keyof typeof accentMap];
            return (
              <div
                key={f.title}
                className={`relative p-6 rounded-2xl border bg-white/[0.02] transition-all duration-300 ${a.border} ${a.glow}`}
              >
                <div
                  className={`inline-flex p-2.5 rounded-xl ${a.bg} ${a.icon} mb-4`}
                >
                  {f.icon}
                </div>
                <h3 className="text-base font-semibold text-white mb-2">
                  {f.title}
                </h3>
                <p className="text-sm text-white/40 leading-relaxed">
                  {f.desc}
                </p>
              </div>
            );
          })}
        </div>
      </div>
    </section>
  );
}
