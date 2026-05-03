import { motion, useReducedMotion } from "framer-motion";

/**
 * Rooms & Fan-out — concentric room boundary with agents arranged inside,
 * a single connecting line pulsing between them on a slow loop.
 *
 * Hand-built. No Lucide. No emoji. Stroke-based.
 */
export function RoomsIllustration() {
  const prefersReduced = useReducedMotion();

  // Agent positions inside the room (rough trapezoid layout)
  const agents: Array<{ cx: number; cy: number; label: string }> = [
    { cx: 60, cy: 70, label: "C" },
    { cx: 145, cy: 50, label: "X" },
    { cx: 230, cy: 80, label: "K" },
    { cx: 100, cy: 130, label: "G" },
    { cx: 190, cy: 135, label: "M" },
  ];

  // Connection lines that fan out from the relay (center) to each agent
  const relayCx = 145;
  const relayCy = 95;

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Five agent nodes inside a room boundary connected to a central relay"
    >
      {/* Room boundary — soft rounded rect, dashed outline */}
      <rect
        x="14"
        y="14"
        width="262"
        height="152"
        rx="14"
        fill="none"
        stroke="#0a0a0f"
        strokeOpacity="0.16"
        strokeWidth="1.2"
        strokeDasharray="4 5"
      />
      {/* Inner ambient cream tint */}
      <rect
        x="14"
        y="14"
        width="262"
        height="152"
        rx="14"
        fill="#0a0a0f"
        fillOpacity="0.015"
      />

      {/* Room label tab */}
      <rect
        x="22"
        y="6"
        width="120"
        height="16"
        rx="3"
        fill="#f5f1ea"
        stroke="#0a0a0f"
        strokeOpacity="0.16"
        strokeWidth="1"
      />
      <text
        x="30"
        y="17"
        fontSize="9"
        fontFamily="JetBrains Mono, monospace"
        fill="#0a0a0f"
        opacity="0.6"
      >
        room://dev-sprint
      </text>

      {/* Fan-out connection lines (relay → each agent) */}
      {agents.map((a, i) => (
        <motion.line
          key={`line-${i}`}
          x1={relayCx}
          y1={relayCy}
          x2={a.cx}
          y2={a.cy}
          stroke="#0d4d4a"
          strokeWidth="0.8"
          strokeLinecap="round"
          initial={{ pathLength: 0, opacity: 0.2 }}
          animate={
            prefersReduced
              ? { pathLength: 1, opacity: 0.45 }
              : { pathLength: [0.2, 1, 1], opacity: [0.2, 0.6, 0.45] }
          }
          transition={{
            duration: 2.4,
            delay: i * 0.15,
            repeat: prefersReduced ? 0 : Infinity,
            repeatDelay: 4,
            ease: [0.16, 1, 0.3, 1],
          }}
        />
      ))}

      {/* Agent nodes */}
      {agents.map((a, i) => (
        <g key={`agent-${i}`}>
          <circle
            cx={a.cx}
            cy={a.cy}
            r="11"
            fill="#f5f1ea"
            stroke="#0a0a0f"
            strokeOpacity="0.85"
            strokeWidth="1.2"
          />
          <text
            x={a.cx}
            y={a.cy + 3}
            fontSize="9"
            fontFamily="JetBrains Mono, monospace"
            fontWeight="600"
            fill="#0a0a0f"
            textAnchor="middle"
          >
            {a.label}
          </text>
        </g>
      ))}

      {/* Central relay — accent ring */}
      <circle
        cx={relayCx}
        cy={relayCy}
        r="14"
        fill="none"
        stroke="#0d4d4a"
        strokeWidth="1.5"
      />
      <circle cx={relayCx} cy={relayCy} r="6" fill="#0d4d4a" />
      <circle cx={relayCx} cy={relayCy} r="2.5" fill="#f5f1ea" />
    </svg>
  );
}
