import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoSse — a horizontal heartbeat trace. The pulse animates left-to-right
 * across the line at low frequency. A latency tag in the corner reinforces
 * the "sub-50ms" promise.
 */
export default function BentoSse() {
  // Heartbeat path — flat line with a single QRS-style spike near the middle.
  // Coordinates are in the 200x200 viewBox.
  const TRACE_D =
    "M 10 100 L 60 100 L 70 100 L 78 80 L 86 130 L 94 70 L 102 110 L 110 100 L 130 100 L 190 100";

  const prefersReduced = useReducedMotion();

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="A heartbeat trace with a moving pulse, representing real-time server-sent events"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Faint background grid — telemetry-monitor vibe. Vertical hairlines
          every 20 units, slightly offset so the QRS lands between them. */}
      {Array.from({ length: 9 }).map((_, i) => (
        <line
          key={`v-${i}`}
          x1={20 + i * 20}
          y1="40"
          x2={20 + i * 20}
          y2="160"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="0.8"
          opacity="0.08"
        />
      ))}
      {/* Horizontal centerline marker */}
      <line
        x1="10"
        y1="100"
        x2="190"
        y2="100"
        stroke="var(--color-text-on-ink-secondary)"
        strokeWidth="0.8"
        strokeDasharray="2 4"
        opacity="0.18"
      />

      {/* Static trace — the full heartbeat at low opacity, so the moving
          highlight has something to "ride along" on. */}
      <path
        d={TRACE_D}
        fill="none"
        stroke="var(--color-text-on-ink-secondary)"
        strokeWidth="1.3"
        strokeLinecap="round"
        strokeLinejoin="round"
        opacity="0.35"
      />

      {/* Animated highlight — same path, drawn left-to-right via pathLength. */}
      <motion.path
        d={TRACE_D}
        fill="none"
        stroke="var(--color-accent-on-ink)"
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
        initial={{ pathLength: 0, opacity: 0 }}
        animate={
          prefersReduced
            ? { pathLength: 1, opacity: 0.85 }
            : { pathLength: [0, 1, 1], opacity: [0, 1, 0] }
        }
        transition={{
          duration: 2.4,
          times: [0, 0.7, 1],
          repeat: prefersReduced ? 0 : Infinity,
          repeatDelay: 0.8,
          ease: EASE,
        }}
      />

      {/* Latency tag — bottom-right. Sells the "<50ms" promise. */}
      <g transform="translate(126 168)">
        <rect
          x="0"
          y="-12"
          width="62"
          height="18"
          rx="9"
          fill="var(--color-ink)"
          stroke="var(--color-border-dark-strong)"
          strokeWidth="1"
        />
        <circle cx="10" cy="-3" r="2.4" fill="var(--color-accent-on-ink)" />
        <text
          x="18"
          y="0"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          fill="var(--color-text-on-ink-secondary)"
          letterSpacing="0.08em"
        >
          &lt; 50ms
        </text>
      </g>

      {/* "stream" label — top-left annotation tying this to SSE. */}
      <g transform="translate(12 36)">
        <text
          x="0"
          y="0"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          fill="var(--color-text-on-ink-secondary)"
          opacity="0.65"
          letterSpacing="0.18em"
        >
          STREAM · sse
        </text>
      </g>
    </svg>
  );
}
