import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoRooms — central hub with five spokes radiating to agent dots.
 * One spoke pulses on a slow loop; on hover, the relay ring tightens.
 */
export default function BentoRooms() {
  const prefersReduced = useReducedMotion();

  // Five agents arranged loosely around the relay (not on a perfect circle —
  // a perfect circle reads like a stock-photo network diagram).
  const agents: ReadonlyArray<{ cx: number; cy: number; r: number }> = [
    { cx: 36, cy: 58, r: 5.5 },
    { cx: 158, cy: 30, r: 6 },
    { cx: 168, cy: 132, r: 5 },
    { cx: 50, cy: 150, r: 5.5 },
    { cx: 184, cy: 80, r: 5 },
  ];

  const relayCx = 100;
  const relayCy = 100;

  // Index of the spoke that pulses (deterministic, not random).
  const pulseIdx = 1;

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="A central relay node with five spokes radiating to agent endpoints"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Faint orbital ring — anchors the composition without dominating. */}
      <circle
        cx={relayCx}
        cy={relayCy}
        r="74"
        fill="none"
        stroke="var(--color-border-dark-strong)"
        strokeWidth="1"
        strokeDasharray="2 6"
        opacity="0.55"
      />

      {/* Spokes (relay → agent). The pulse spoke is rendered last so it
          overdraws its neighbors when it brightens. */}
      {agents.map((a, i) => {
        const isPulse = i === pulseIdx;
        return (
          <motion.line
            key={`spoke-${i}`}
            x1={relayCx}
            y1={relayCy}
            x2={a.cx}
            y2={a.cy}
            stroke="var(--color-text-on-ink-secondary)"
            strokeWidth="1.5"
            strokeLinecap="round"
            opacity={isPulse ? 0.9 : 0.32}
            initial={false}
            animate={
              prefersReduced || !isPulse
                ? undefined
                : { opacity: [0.32, 0.95, 0.45] }
            }
            transition={{
              duration: 2.6,
              repeat: prefersReduced ? 0 : Infinity,
              repeatDelay: 1.6,
              ease: EASE,
            }}
          />
        );
      })}

      {/* Travelling pulse — small dot riding the active spoke from relay to agent. */}
      {!prefersReduced && (
        <motion.circle
          r="2.6"
          fill="var(--color-accent-on-ink)"
          initial={{ cx: relayCx, cy: relayCy, opacity: 0 }}
          animate={{
            cx: [relayCx, agents[pulseIdx].cx],
            cy: [relayCy, agents[pulseIdx].cy],
            opacity: [0, 1, 0],
          }}
          transition={{
            duration: 2.6,
            repeat: Infinity,
            repeatDelay: 1.6,
            ease: EASE,
          }}
        />
      )}

      {/* Agent endpoints — open circles with hairline strokes. */}
      {agents.map((a, i) => (
        <g key={`agent-${i}`}>
          <circle
            cx={a.cx}
            cy={a.cy}
            r={a.r}
            fill="var(--color-ink-2)"
            stroke="var(--color-text-on-ink-secondary)"
            strokeWidth="1.4"
          />
          <circle
            cx={a.cx}
            cy={a.cy}
            r={a.r * 0.35}
            fill="var(--color-text-on-ink-secondary)"
            opacity={i === pulseIdx ? 0.95 : 0.45}
          />
        </g>
      ))}

      {/* Central relay — accent ring with solid core. */}
      <g>
        {/* Outer ring — gentle breathing motion to suggest a heartbeat. */}
        <motion.circle
          cx={relayCx}
          cy={relayCy}
          r="18"
          fill="none"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="1.5"
          opacity="0.9"
          initial={false}
          animate={
            prefersReduced
              ? undefined
              : { r: [18, 20, 18], opacity: [0.9, 0.55, 0.9] }
          }
          transition={{
            duration: 3.6,
            repeat: prefersReduced ? 0 : Infinity,
            ease: "easeInOut",
          }}
        />
        {/* Inner solid core */}
        <circle
          cx={relayCx}
          cy={relayCy}
          r="9"
          fill="var(--color-accent-on-ink)"
          opacity="0.18"
        />
        <circle
          cx={relayCx}
          cy={relayCy}
          r="4"
          fill="var(--color-accent-on-ink)"
        />
      </g>
    </svg>
  );
}
