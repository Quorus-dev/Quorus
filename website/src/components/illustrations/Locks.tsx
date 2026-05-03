import { motion, useReducedMotion } from "framer-motion";

/**
 * Distributed Locks — three file rows with lock icons in different states.
 * The middle one transitions from "claimed" to "released" on a slow loop.
 */
export function LocksIllustration() {
  const prefersReduced = useReducedMotion();

  const rows = [
    { name: "auth.py", owner: "claude-1", state: "claimed" as const },
    { name: "tests/", owner: "—", state: "open" as const },
    { name: "routes.py", owner: "cursor-2", state: "transition" as const },
  ];

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Three file rows showing distributed lock state"
    >
      {/* Frame */}
      <rect
        x="14"
        y="14"
        width="262"
        height="152"
        rx="14"
        fill="none"
        stroke="#0a0a0f"
        strokeOpacity="0.12"
        strokeWidth="1"
      />

      {rows.map((row, i) => {
        const y = 38 + i * 42;
        return (
          <g key={row.name}>
            {/* Row separator (after first row) */}
            {i > 0 && (
              <line
                x1="28"
                x2="262"
                y1={y - 10}
                y2={y - 10}
                stroke="#0a0a0f"
                strokeOpacity="0.06"
                strokeWidth="1"
              />
            )}

            {/* File icon — small folder-like shape */}
            <path
              d={`M 32 ${y - 6} L 40 ${y - 6} L 42 ${y - 4} L 56 ${y - 4} L 56 ${y + 8} L 32 ${y + 8} Z`}
              fill="none"
              stroke="#0a0a0f"
              strokeOpacity="0.55"
              strokeWidth="1.2"
              strokeLinejoin="round"
            />

            {/* Filename */}
            <text
              x="68"
              y={y + 2}
              fontSize="11"
              fontFamily="JetBrains Mono, monospace"
              fill="#0a0a0f"
              opacity="0.85"
            >
              {row.name}
            </text>

            {/* Owner */}
            <text
              x="68"
              y={y + 14}
              fontSize="9"
              fontFamily="JetBrains Mono, monospace"
              fill="#0a0a0f"
              opacity="0.4"
            >
              {row.owner}
            </text>

            {/* Lock state */}
            <g transform={`translate(232 ${y - 6})`}>
              {row.state === "claimed" && (
                <ClosedLock fill="#0d4d4a" stroke="#0d4d4a" />
              )}
              {row.state === "open" && (
                <OpenLock stroke="#0a0a0f" strokeOpacity={0.35} />
              )}
              {row.state === "transition" && (
                <TransitionLock prefersReduced={prefersReduced ?? false} />
              )}
            </g>
          </g>
        );
      })}
    </svg>
  );
}

function ClosedLock({ fill, stroke }: { fill: string; stroke: string }) {
  return (
    <g>
      <path
        d="M 4 6 L 4 4 A 4 4 0 0 1 12 4 L 12 6"
        fill="none"
        stroke={stroke}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <rect x="2" y="6" width="12" height="10" rx="2" fill={fill} />
    </g>
  );
}

function OpenLock({
  stroke,
  strokeOpacity,
}: {
  stroke: string;
  strokeOpacity: number;
}) {
  return (
    <g>
      <path
        d="M 4 6 L 4 4 A 4 4 0 0 1 12 4 L 12 6 L 12 2"
        fill="none"
        stroke={stroke}
        strokeOpacity={strokeOpacity}
        strokeWidth="1.4"
        strokeLinecap="round"
      />
      <rect
        x="2"
        y="6"
        width="12"
        height="10"
        rx="2"
        fill="none"
        stroke={stroke}
        strokeOpacity={strokeOpacity}
        strokeWidth="1.2"
      />
    </g>
  );
}

function TransitionLock({ prefersReduced }: { prefersReduced: boolean }) {
  // Pulses between closed and open every 3.5 seconds.
  return (
    <motion.g
      animate={prefersReduced ? { opacity: 0.7 } : { opacity: [1, 0.4, 1] }}
      transition={{
        duration: 3.5,
        repeat: prefersReduced ? 0 : Infinity,
        ease: [0.16, 1, 0.3, 1],
      }}
    >
      <ClosedLock fill="#5eb3a8" stroke="#0d4d4a" />
    </motion.g>
  );
}
