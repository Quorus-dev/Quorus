"use client";
import { useRef, useEffect, useState } from "react";
import { motion, useInView } from "framer-motion";
import FadeUp from "./FadeUp";

/* ─────────────────────────────────────────────
   Animated count-up counter
───────────────────────────────────────────── */
function Counter({
  value,
  suffix = "",
  decimals = 0,
}: {
  value: number;
  suffix?: string;
  decimals?: number;
}) {
  const ref = useRef<HTMLSpanElement>(null);
  const inView = useInView(ref, { once: true });
  const [display, setDisplay] = useState(0);

  useEffect(() => {
    if (!inView) return;
    const duration = 1500;
    const start = Date.now();
    const tick = () => {
      const elapsed = Date.now() - start;
      const progress = Math.min(elapsed / duration, 1);
      const eased = 1 - Math.pow(1 - progress, 3); // ease-out cubic
      const current = eased * value;
      setDisplay(current);
      if (progress < 1) requestAnimationFrame(tick);
      else setDisplay(value);
    };
    requestAnimationFrame(tick);
  }, [inView, value]);

  const formatted =
    decimals > 0 ? display.toFixed(decimals) : Math.floor(display).toString();

  return (
    <span ref={ref}>
      {formatted}
      {suffix}
    </span>
  );
}

/* ─────────────────────────────────────────────
   Travelling dot along a horizontal connector
───────────────────────────────────────────── */
function FlowPulse({
  color,
  delay,
  reverse = false,
}: {
  color: "violet" | "cyan";
  delay: number;
  reverse?: boolean;
}) {
  const bg = color === "violet" ? "bg-violet-400" : "bg-cyan-400";
  const from = reverse ? 80 : 0;
  const to = reverse ? 0 : 80;

  return (
    <motion.div
      className={`absolute top-1/2 -translate-y-1/2 w-1.5 h-1.5 rounded-full ${bg}`}
      style={{ left: 0 }}
      animate={{ x: [from, to], opacity: [0, 1, 1, 0] }}
      transition={{
        duration: 1.5,
        repeat: Infinity,
        ease: "linear",
        delay,
      }}
    />
  );
}

/* ─────────────────────────────────────────────
   Main section
───────────────────────────────────────────── */
export default function Architecture() {
  const agents = ["Claude Code", "Cursor / Codex", "Gemini / Ollama"];
  const inboxes = ["agent-1 inbox", "agent-2 inbox", "agent-3 inbox"];

  return (
    <section className="py-32 px-6 relative overflow-hidden" id="architecture">
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
            {/* ── Agents column ── */}
            <div className="flex flex-col gap-3">
              {agents.map((name, i) => (
                <motion.div
                  key={name}
                  initial={{ opacity: 0, x: -20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{
                    duration: 0.4,
                    delay: i * 0.12,
                    ease: "easeOut",
                  }}
                  className="px-4 py-3 rounded-xl border border-violet-500/20 bg-violet-500/5 text-sm font-mono text-violet-300 text-center min-w-[140px]"
                >
                  {name}
                </motion.div>
              ))}
              <div className="text-center text-white/20 text-xs font-mono mt-1">
                agents
              </div>
            </div>

            {/* ── Connector: agents → relay ── */}
            <div className="flex flex-col items-center gap-2 text-white/20">
              <div className="hidden md:block text-xs font-mono">
                HTTP / MCP
              </div>
              <div className="flex items-center gap-2">
                {/* Animated line with travelling dots */}
                <div className="hidden md:block relative w-16 h-px bg-gradient-to-r from-white/10 to-violet-500/40">
                  {[0, 0.5, 1.0].map((delay, i) => (
                    <FlowPulse key={i} color="violet" delay={delay} />
                  ))}
                </div>
                <svg
                  className="w-4 h-4 text-violet-400 md:rotate-0 rotate-90"
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

            {/* ── Relay box ── */}
            <div className="flex flex-col items-center gap-3">
              <motion.div
                animate={{
                  boxShadow: [
                    "0 0 20px rgba(124,58,237,0.2)",
                    "0 0 40px rgba(124,58,237,0.4)",
                    "0 0 20px rgba(124,58,237,0.2)",
                  ],
                }}
                transition={{
                  duration: 2,
                  repeat: Infinity,
                  ease: "easeInOut",
                }}
                className="relative px-6 py-5 rounded-2xl border border-violet-500/30 bg-violet-500/10"
              >
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
              </motion.div>
              <div className="text-center text-white/20 text-xs font-mono">
                relay server
              </div>
            </div>

            {/* ── Connector: relay → inboxes ── */}
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
                {/* Animated line with travelling dots */}
                <div className="hidden md:block relative w-16 h-px bg-gradient-to-r from-cyan-500/40 to-white/10">
                  {[0.25, 0.75, 1.25].map((delay, i) => (
                    <FlowPulse key={i} color="cyan" delay={delay} />
                  ))}
                </div>
              </div>
            </div>

            {/* ── Inboxes column ── */}
            <div className="flex flex-col gap-3">
              {inboxes.map((name, i) => (
                <motion.div
                  key={name}
                  initial={{ opacity: 0, x: 20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{
                    duration: 0.4,
                    delay: i * 0.12,
                    ease: "easeOut",
                  }}
                  className="px-4 py-3 rounded-xl border border-cyan-500/20 bg-cyan-500/5 text-sm font-mono text-cyan-300 text-center min-w-[130px]"
                >
                  {name}
                </motion.div>
              ))}
              <div className="text-center text-white/20 text-xs font-mono mt-1">
                inboxes
              </div>
            </div>
          </div>

          {/* ── Stats bar ── */}
          <div className="mt-8 pt-6 border-t border-white/5 grid grid-cols-3 gap-4">
            <FadeUp delay={0}>
              <div className="text-center">
                <div className="text-2xl font-bold gradient-text-subtle">
                  <Counter value={3.6} suffix="ms" decimals={1} />
                </div>
                <div className="text-xs text-white/30 mt-1 font-mono">
                  p50 latency
                </div>
              </div>
            </FadeUp>
            <FadeUp delay={0.1}>
              <div className="text-center">
                <div className="text-2xl font-bold gradient-text-subtle">
                  <Counter value={281} suffix=" msg/s" />
                </div>
                <div className="text-xs text-white/30 mt-1 font-mono">
                  throughput
                </div>
              </div>
            </FadeUp>
            <FadeUp delay={0.2}>
              <div className="text-center">
                <div className="text-2xl font-bold gradient-text-subtle">
                  <Counter value={866} suffix="+" />
                </div>
                <div className="text-xs text-white/30 mt-1 font-mono">
                  tests passing
                </div>
              </div>
            </FadeUp>
          </div>
        </div>
      </div>
    </section>
  );
}
