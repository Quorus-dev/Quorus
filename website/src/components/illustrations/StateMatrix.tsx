import { motion, useReducedMotion } from "framer-motion";

/**
 * Shared State Matrix — a 6x4 grid of cells that represent room state.
 * A handful of cells "fill in" on a slow loop to suggest live updates.
 */
export function StateMatrixIllustration() {
  const prefersReduced = useReducedMotion();
  const cols = 6;
  const rows = 4;
  const cellW = 32;
  const cellH = 24;
  const gap = 4;
  const offsetX = 38;
  const offsetY = 32;

  // Cells that animate on the loop (deterministic pattern, not random).
  const activeCells = new Set([
    "0-1",
    "1-3",
    "2-2",
    "3-0",
    "4-2",
    "5-1",
    "5-3",
  ]);

  return (
    <svg
      viewBox="0 0 290 180"
      width="100%"
      height="100%"
      role="img"
      aria-label="Grid of cells representing shared room state with active cells highlighted"
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

      {/* Column headers — JetBrains Mono */}
      {["k1", "k2", "k3", "k4", "k5", "k6"].map((k, i) => (
        <text
          key={k}
          x={offsetX + i * (cellW + gap) + cellW / 2}
          y={offsetY - 8}
          fontSize="8"
          fontFamily="JetBrains Mono, monospace"
          fill="#0a0a0f"
          opacity="0.4"
          textAnchor="middle"
        >
          {k}
        </text>
      ))}

      {/* Row labels */}
      {["agent.a", "agent.b", "agent.c", "agent.d"].map((label, i) => (
        <text
          key={label}
          x={offsetX - 6}
          y={offsetY + i * (cellH + gap) + cellH / 2 + 3}
          fontSize="8"
          fontFamily="JetBrains Mono, monospace"
          fill="#0a0a0f"
          opacity="0.45"
          textAnchor="end"
        >
          {label}
        </text>
      ))}

      {/* Grid cells */}
      {Array.from({ length: cols }).map((_, c) =>
        Array.from({ length: rows }).map((_, r) => {
          const key = `${c}-${r}`;
          const x = offsetX + c * (cellW + gap);
          const y = offsetY + r * (cellH + gap);
          const isActive = activeCells.has(key);

          if (!isActive) {
            return (
              <rect
                key={key}
                x={x}
                y={y}
                width={cellW}
                height={cellH}
                rx="2"
                fill="#0a0a0f"
                fillOpacity="0.04"
                stroke="#0a0a0f"
                strokeOpacity="0.08"
                strokeWidth="0.8"
              />
            );
          }

          // Active cell — accent fill that pulses
          return (
            <g key={key}>
              <rect
                x={x}
                y={y}
                width={cellW}
                height={cellH}
                rx="2"
                fill="#0a0a0f"
                fillOpacity="0.04"
              />
              <motion.rect
                x={x}
                y={y}
                width={cellW}
                height={cellH}
                rx="2"
                fill="#0d4d4a"
                initial={{ fillOpacity: 0.15 }}
                animate={
                  prefersReduced
                    ? { fillOpacity: 0.5 }
                    : { fillOpacity: [0.15, 0.85, 0.5] }
                }
                transition={{
                  duration: 2.4,
                  delay: ((c + r) % 4) * 0.4,
                  repeat: prefersReduced ? 0 : Infinity,
                  repeatDelay: 1.6,
                  ease: [0.16, 1, 0.3, 1],
                }}
              />
            </g>
          );
        }),
      )}

      {/* "live" indicator at top-right */}
      <text
        x="262"
        y="22"
        fontSize="8"
        fontFamily="JetBrains Mono, monospace"
        fill="#0d4d4a"
        textAnchor="end"
        opacity="0.7"
      >
        live · 7 keys
      </text>
    </svg>
  );
}
