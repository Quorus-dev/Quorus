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

/* ─── Hub diagram data ────────────────────────────────────────────────────── */

const RING_AGENTS = [
  { label: "Claude Code", color: "#a78bfa", angle: -90 },
  { label: "Cursor", color: "#60a5fa", angle: -30 },
  { label: "OpenAI Codex", color: "#34d399", angle: 30 },
  { label: "Gemini", color: "#fb923c", angle: 90 },
  { label: "Ollama", color: "#f472b6", angle: 150 },
  { label: "Any HTTP", color: "#94a3b8", angle: 210 },
];

const PRIMITIVES = [
  {
    label: "Rooms",
    desc: "Fan-out to all members in <1ms",
    color: "#fbbf24",
  },
  {
    label: "SSE Push",
    desc: "Zero polling. 3.6ms p50 delivery",
    color: "#f59e0b",
  },
  {
    label: "Shared State",
    desc: "GET /state: full swarm snapshot",
    color: "#d97706",
  },
  {
    label: "Mutex Locks",
    desc: "TTL-gated. Auto-expire on crash",
    color: "#b45309",
  },
];

/* ─── Hub & spoke SVG diagram ─────────────────────────────────────────────── */

function HubDiagram() {
  const W = 480;
  const H = 360;
  const cx = W / 2;
  const cy = H / 2;
  const radius = 148;

  const nodes = RING_AGENTS.map((a) => {
    const rad = (a.angle * Math.PI) / 180;
    return {
      ...a,
      x: cx + radius * Math.cos(rad),
      y: cy + radius * Math.sin(rad),
    };
  });

  return (
    <div className="w-full">
      <svg
        viewBox={`0 0 ${W} ${H}`}
        className="w-full h-auto"
        style={{ maxHeight: 340, overflow: "visible" }}
      >
        <defs>
          <radialGradient id="rg-relay" cx="50%" cy="50%" r="50%">
            <stop offset="0%" stopColor="#d97706" stopOpacity="0.38" />
            <stop offset="100%" stopColor="#d97706" stopOpacity="0.06" />
          </radialGradient>
          <filter id="glow-node" x="-100%" y="-100%" width="300%" height="300%">
            <feGaussianBlur stdDeviation="3.5" result="blur" />
            <feMerge>
              <feMergeNode in="blur" />
              <feMergeNode in="SourceGraphic" />
            </feMerge>
          </filter>
        </defs>

        {/* Animated dashed connection lines */}
        {nodes.map((n, i) => (
          <line
            key={`line-${n.label}`}
            x1={n.x}
            y1={n.y}
            x2={cx}
            y2={cy}
            stroke={n.color}
            strokeWidth="0.85"
            strokeOpacity="0.22"
            strokeDasharray="3 6"
          >
            <animate
              attributeName="stroke-dashoffset"
              values={i % 2 === 0 ? "0;-27" : "0;27"}
              dur={`${1.1 + i * 0.1}s`}
              repeatCount="indefinite"
              calcMode="linear"
            />
          </line>
        ))}

        {/* Relay outer pulse ring */}
        <circle
          cx={cx}
          cy={cy}
          r="52"
          fill="none"
          stroke="rgba(217,119,6,0.18)"
          strokeWidth="1"
        >
          <animate
            attributeName="r"
            values="52;70;52"
            dur="3s"
            repeatCount="indefinite"
          />
          <animate
            attributeName="opacity"
            values="0.5;0;0.5"
            dur="3s"
            repeatCount="indefinite"
          />
        </circle>

        {/* Relay core */}
        <circle
          cx={cx}
          cy={cy}
          r="44"
          fill="url(#rg-relay)"
          stroke="rgba(217,119,6,0.4)"
          strokeWidth="1"
        />
        <circle cx={cx} cy={cy} r="34" fill="rgba(217,119,6,0.18)" />

        {/* Live indicator */}
        <circle cx={cx + 28} cy={cy - 28} r="3.5" fill="#4ade80">
          <animate
            attributeName="opacity"
            values="1;0.3;1"
            dur="2.2s"
            repeatCount="indefinite"
          />
        </circle>

        <text
          x={cx}
          y={cy - 4}
          textAnchor="middle"
          fill="#fcd34d"
          fontSize="11"
          fontFamily="ui-monospace, monospace"
          fontWeight="700"
        >
          murmur
        </text>
        <text
          x={cx}
          y={cy + 11}
          textAnchor="middle"
          fill="rgba(252,211,77,0.42)"
          fontSize="8.5"
          fontFamily="ui-monospace, monospace"
        >
          relay
        </text>

        {/* Agent nodes */}
        {nodes.map((n, i) => (
          <motion.g
            key={n.label}
            initial={{ opacity: 0 }}
            whileInView={{ opacity: 1 }}
            viewport={{ once: true }}
            transition={{ delay: 0.25 + i * 0.1, duration: 0.7 }}
          >
            {/* Node background */}
            <circle
              cx={n.x}
              cy={n.y}
              r="24"
              fill={`${n.color}12`}
              stroke={`${n.color}28`}
              strokeWidth="1"
            />
            {/* Node dot with glow */}
            <circle
              cx={n.x}
              cy={n.y}
              r="5"
              fill={n.color}
              opacity="0.9"
              filter="url(#glow-node)"
            />
            {/* Label */}
            <text
              x={n.x}
              y={n.y + 38}
              textAnchor="middle"
              fill={n.color}
              fontSize="7.5"
              fontFamily="ui-monospace, monospace"
              opacity="0.7"
            >
              {n.label}
            </text>
          </motion.g>
        ))}
      </svg>
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
            "radial-gradient(ellipse, rgba(217,119,6,0.05) 0%, transparent 70%)",
          filter: "blur(80px)",
        }}
      />

      <div className="relative max-w-6xl mx-auto">
        <FadeUp>
          <div className="text-center mb-16">
            <p className="text-sm font-mono text-amber-400 mb-3 tracking-widest uppercase">
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
          {/* Hub diagram */}
          <div className="rounded-2xl border border-white/[0.07] bg-[#0b0b14] p-6 lg:p-10 flex items-center justify-center">
            <HubDiagram />
          </div>

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
