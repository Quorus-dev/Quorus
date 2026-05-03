import { motion, useReducedMotion } from "framer-motion";

/**
 * Code-aware Sync — two stacked editor panes with line markers showing the
 * same code in two agents' contexts. A small "diff" indicator pulses.
 */
export function CodeSyncIllustration() {
  const prefersReduced = useReducedMotion();

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Two editor panes showing synchronized code with diff markers"
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
        strokeOpacity="0.1"
        strokeWidth="1"
      />

      {/* Top editor pane */}
      <EditorPane
        x={28}
        y={28}
        label="claude · auth.py"
        lines={[
          { kind: "neutral", w: 110 },
          { kind: "neutral", w: 80 },
          { kind: "added", w: 130 },
          { kind: "neutral", w: 92 },
        ]}
      />

      {/* Sync arrow connecting the two panes */}
      <g transform="translate(135 96)">
        <motion.line
          x1="0"
          x2="0"
          y1="-4"
          y2="14"
          stroke="#0d4d4a"
          strokeWidth="1.4"
          strokeLinecap="round"
          markerEnd="url(#arrowhead-down)"
          initial={{ pathLength: 0 }}
          animate={
            prefersReduced ? { pathLength: 1 } : { pathLength: [0, 1, 1, 0] }
          }
          transition={{
            duration: 3,
            repeat: prefersReduced ? 0 : Infinity,
            repeatDelay: 1.5,
            ease: [0.16, 1, 0.3, 1],
          }}
        />
        <motion.line
          x1="0"
          x2="0"
          y1="14"
          y2="-4"
          stroke="#0d4d4a"
          strokeWidth="1.4"
          strokeLinecap="round"
          markerEnd="url(#arrowhead-up)"
          initial={{ pathLength: 0 }}
          animate={
            prefersReduced ? { pathLength: 1 } : { pathLength: [0, 0, 1, 1] }
          }
          transition={{
            duration: 3,
            repeat: prefersReduced ? 0 : Infinity,
            repeatDelay: 1.5,
            ease: [0.16, 1, 0.3, 1],
          }}
        />

        <defs>
          <marker
            id="arrowhead-down"
            markerWidth="6"
            markerHeight="6"
            refX="3"
            refY="5"
            orient="auto"
          >
            <path d="M 0 0 L 6 0 L 3 5 Z" fill="#0d4d4a" />
          </marker>
          <marker
            id="arrowhead-up"
            markerWidth="6"
            markerHeight="6"
            refX="3"
            refY="1"
            orient="auto"
          >
            <path d="M 0 6 L 6 6 L 3 1 Z" fill="#0d4d4a" />
          </marker>
        </defs>
      </g>

      {/* Bottom editor pane */}
      <EditorPane
        x={28}
        y={114}
        label="cursor · auth.py"
        lines={[
          { kind: "neutral", w: 110 },
          { kind: "neutral", w: 80 },
          { kind: "added", w: 130 },
          { kind: "neutral", w: 92 },
        ]}
      />

      {/* Right side — sync status */}
      <g transform="translate(214 70)">
        <text
          x="0"
          y="0"
          fontSize="8"
          fontFamily="JetBrains Mono, monospace"
          fill="#0a0a0f"
          opacity="0.5"
        >
          delta · 1 line
        </text>
        <text
          x="0"
          y="14"
          fontSize="8"
          fontFamily="JetBrains Mono, monospace"
          fill="#0d4d4a"
          opacity="0.85"
        >
          synced 0.4s
        </text>
      </g>
    </svg>
  );
}

type LineKind = "neutral" | "added" | "removed";

function EditorPane({
  x,
  y,
  label,
  lines,
}: {
  x: number;
  y: number;
  label: string;
  lines: Array<{ kind: LineKind; w: number }>;
}) {
  return (
    <g>
      {/* Pane bg */}
      <rect
        x={x}
        y={y}
        width="170"
        height="56"
        rx="4"
        fill="#0a0a0f"
        fillOpacity="0.025"
        stroke="#0a0a0f"
        strokeOpacity="0.1"
        strokeWidth="0.8"
      />
      {/* Label tab */}
      <text
        x={x + 8}
        y={y + 11}
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fill="#0a0a0f"
        opacity="0.55"
      >
        {label}
      </text>
      {/* Code lines (rectangles representing tokens) */}
      {lines.map((line, i) => {
        const ly = y + 22 + i * 8;
        const fill =
          line.kind === "added"
            ? "#0d4d4a"
            : line.kind === "removed"
              ? "#0a0a0f"
              : "#0a0a0f";
        const opacity =
          line.kind === "added" ? 0.55 : line.kind === "neutral" ? 0.18 : 0.4;
        return (
          <g key={i}>
            <text
              x={x + 8}
              y={ly + 3}
              fontSize="7"
              fontFamily="JetBrains Mono, monospace"
              fill="#0a0a0f"
              opacity="0.3"
            >
              {(i + 1).toString().padStart(2, "0")}
            </text>
            <rect
              x={x + 22}
              y={ly - 2}
              width={line.w}
              height="4"
              rx="1"
              fill={fill}
              fillOpacity={opacity}
            />
          </g>
        );
      })}
    </g>
  );
}
