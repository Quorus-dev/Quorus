import { useRef, useEffect, useState } from "react";
import { motion, useInView } from "framer-motion";
import FadeUp from "./FadeUp";

/* ─── Counter ─────────────────────────────────────────────────────────────── */

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
      const eased = 1 - Math.pow(1 - progress, 3);
      setDisplay(eased * value);
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

/* ─── Architecture diagram lines ──────────────────────────────────────────── */

interface DiagramLine {
  label: string;
  color: string;
  prefix: string;
}

const DIAGRAM_LINES: DiagramLine[] = [
  { label: "Claude Code", color: "#d97757", prefix: "CC" },
  { label: "Cursor", color: "#60a5fa", prefix: "CU" },
  { label: "Codex", color: "#10a37f", prefix: "CX" },
];

/* ─── Primitives ──────────────────────────────────────────────────────────── */

const PRIMITIVES = [
  {
    label: "Rooms",
    desc: "Fan-out to all members in <1ms",
    color: "#2dd4bf",
  },
  {
    label: "SSE Push",
    desc: "Zero polling. 3.6ms p50 delivery",
    color: "#8b5cf6",
  },
  {
    label: "Shared State",
    desc: "GET /state: full swarm snapshot",
    color: "#14b8a6",
  },
  {
    label: "Mutex Locks",
    desc: "TTL-gated. Auto-expire on crash",
    color: "#0f766e",
  },
];

/* ─── Text architecture diagram ──────────────────────────────────────────── */

function ArchDiagram() {
  const ref = useRef<HTMLDivElement>(null);
  const inView = useInView(ref, { once: true });
  const [visibleLines, setVisibleLines] = useState(0);

  const lines = [
    {
      text: "Claude Code ──┐",
      highlights: [{ from: 0, to: 11, color: "#d97757" }],
    },
    {
      text: "Cursor ────────┼──► murmur relay ◄──► SSE push ──► all agents",
      highlights: [
        { from: 0, to: 6, color: "#60a5fa" },
        { from: 19, to: 31, color: "#2dd4bf" },
        { from: 37, to: 45, color: "#14b8a6" },
        { from: 51, to: 61, color: "#34d399" },
      ],
    },
    {
      text: "Codex ─────────┘         │",
      highlights: [{ from: 0, to: 5, color: "#10a37f" }],
    },
    {
      text: "                    shared state",
      highlights: [{ from: 20, to: 32, color: "#2dd4bf" }],
    },
    {
      text: "                    mutex locks",
      highlights: [{ from: 20, to: 31, color: "#8b5cf6" }],
    },
    {
      text: "                    room fanout",
      highlights: [{ from: 20, to: 31, color: "#14b8a6" }],
    },
  ];

  useEffect(() => {
    if (!inView) return;
    let i = 0;
    const interval = setInterval(() => {
      i++;
      setVisibleLines(i);
      if (i >= lines.length) clearInterval(interval);
    }, 180);
    return () => clearInterval(interval);
  }, [inView, lines.length]);

  function renderLine(line: (typeof lines)[0], lineIdx: number) {
    const text = line.text;
    const hl = line.highlights;

    // Build segments
    const segments: { text: string; color: string }[] = [];
    let cursor = 0;

    const sorted = [...hl].sort((a, b) => a.from - b.from);
    for (const h of sorted) {
      if (cursor < h.from) {
        segments.push({
          text: text.slice(cursor, h.from),
          color: "rgba(255,255,255,0.35)",
        });
      }
      segments.push({ text: text.slice(h.from, h.to), color: h.color });
      cursor = h.to;
    }
    if (cursor < text.length) {
      segments.push({
        text: text.slice(cursor),
        color: "rgba(255,255,255,0.35)",
      });
    }

    return (
      <motion.div
        key={lineIdx}
        initial={{ opacity: 0, x: -8 }}
        animate={
          lineIdx < visibleLines ? { opacity: 1, x: 0 } : { opacity: 0, x: -8 }
        }
        transition={{ duration: 0.25 }}
        className="leading-relaxed"
      >
        {segments.map((seg, si) => (
          <span key={si} style={{ color: seg.color }}>
            {seg.text}
          </span>
        ))}
      </motion.div>
    );
  }

  return (
    <div
      ref={ref}
      className="rounded-2xl border border-teal-500/20 bg-[#07070f] p-6 lg:p-8 font-mono text-[13px] leading-7 overflow-x-auto"
    >
      <div className="flex items-center gap-2 mb-5 pb-4 border-b border-white/[0.05]">
        <div className="flex gap-1.5">
          <div className="w-2.5 h-2.5 rounded-full bg-red-500/50" />
          <div className="w-2.5 h-2.5 rounded-full bg-yellow-500/50" />
          <div className="w-2.5 h-2.5 rounded-full bg-green-500/50" />
        </div>
        <span className="text-[10px] text-white/20 font-mono ml-2">
          architecture.txt
        </span>
      </div>

      <div className="space-y-0.5 whitespace-pre">
        {lines.map((line, i) => renderLine(line, i))}
      </div>

      {/* Agent badges row */}
      <div className="flex gap-3 mt-6 pt-5 border-t border-white/[0.05] flex-wrap">
        {DIAGRAM_LINES.map((agent) => (
          <div
            key={agent.label}
            className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border text-[10px] font-mono"
            style={{
              borderColor: `${agent.color}30`,
              background: `${agent.color}0a`,
              color: agent.color,
            }}
          >
            <span
              className="w-1.5 h-1.5 rounded-full shrink-0"
              style={{ background: agent.color }}
            />
            {agent.label}
          </div>
        ))}
        <div className="flex items-center gap-1.5 px-2.5 py-1 rounded-lg border border-teal-500/20 bg-teal-500/[0.06] text-[10px] font-mono text-teal-300">
          <span className="w-1.5 h-1.5 rounded-full bg-teal-400 shrink-0" />
          murmur relay
        </div>
      </div>
    </div>
  );
}

/* ─── Main section ────────────────────────────────────────────────────────── */

export default function Architecture() {
  return (
    <section className="py-32 px-6 relative overflow-hidden" id="architecture">
      {/* Background */}
      <div className="absolute inset-0 grid-bg opacity-40" />
      <div
        className="absolute top-1/2 left-1/2 -translate-x-1/2 -translate-y-1/2 w-[700px] h-[420px] rounded-full pointer-events-none"
        style={{
          background:
            "radial-gradient(ellipse, rgba(20,184,166,0.05) 0%, transparent 70%)",
          filter: "blur(80px)",
        }}
      />

      <div className="relative max-w-6xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-teal-400 mb-3 tracking-widest uppercase">
              Architecture
            </p>
            <h2 className="text-5xl md:text-6xl font-bold tracking-tight mb-4">
              Dead simple by design
            </h2>
            <p className="text-white/55 text-lg max-w-xl mx-auto">
              One relay at the center. Every agent on the ring. No broker
              lock-in.
            </p>
          </div>
        </FadeUp>

        <div className="grid grid-cols-1 lg:grid-cols-2 gap-10 items-center">
          {/* Text architecture diagram */}
          <ArchDiagram />

          {/* Primitives list + stats */}
          <div className="space-y-8">
            <div className="space-y-3">
              {PRIMITIVES.map((item, i) => (
                <motion.div
                  key={item.label}
                  initial={{ opacity: 0, x: 20 }}
                  whileInView={{ opacity: 1, x: 0 }}
                  viewport={{ once: true }}
                  transition={{
                    delay: i * 0.08,
                    duration: 0.45,
                    ease: "easeOut",
                  }}
                  className="flex items-center gap-4 px-4 py-3.5 rounded-xl border border-white/[0.06] bg-white/[0.02] hover:border-white/[0.1] transition-colors group cursor-default"
                >
                  <div
                    className="w-2 h-2 rounded-full shrink-0 transition-transform group-hover:scale-125"
                    style={{
                      background: item.color,
                      boxShadow: `0 0 8px ${item.color}80`,
                    }}
                  />
                  <div className="flex-1 min-w-0">
                    <span className="text-sm font-semibold text-white font-mono">
                      {item.label}
                    </span>
                    <span className="text-xs text-white/50 ml-2 group-hover:text-white/70 transition-colors">
                      {item.desc}
                    </span>
                  </div>
                </motion.div>
              ))}
            </div>

            {/* Stats */}
            <div className="grid grid-cols-3 gap-3">
              {[
                { value: 3.6, suffix: "ms", decimals: 1, label: "p50 latency" },
                { value: 281, suffix: "/s", label: "throughput" },
                { value: 99.9, suffix: "%", decimals: 1, label: "uptime" },
              ].map((s, i) => (
                <motion.div
                  key={s.label}
                  initial={{ opacity: 0, y: 12 }}
                  whileInView={{ opacity: 1, y: 0 }}
                  viewport={{ once: true }}
                  transition={{ delay: 0.3 + i * 0.1, duration: 0.45 }}
                  className="text-center px-3 py-4 rounded-xl border border-white/[0.05] bg-white/[0.015]"
                >
                  <div className="text-xl font-bold gradient-text-subtle tabular-nums">
                    <Counter
                      value={s.value}
                      suffix={s.suffix}
                      decimals={s.decimals}
                    />
                  </div>
                  <div className="text-[10px] text-white/25 mt-1 font-mono">
                    {s.label}
                  </div>
                </motion.div>
              ))}
            </div>
          </div>
        </div>
      </div>
    </section>
  );
}
