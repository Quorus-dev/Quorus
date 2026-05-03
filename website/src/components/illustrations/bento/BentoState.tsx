import { useEffect, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoState — a 4-quadrant grid where each cell shows a key + revision
 * counter. Revisions tick up on a slow loop (40 → 41 → 42, cycling). The
 * "winning" cell holds an accent dot to show last-writer-wins.
 *
 * Wide-card illustration: this lays out as a 2x2 matrix sized to fit the
 * 200x200 viewBox comfortably.
 */
export default function BentoState() {
  const prefersReduced = useReducedMotion();
  const [tick, setTick] = useState(0);

  // Drive the counters from a single shared tick. setInterval lives only as
  // long as the component is mounted and is paused for reduced-motion users.
  useEffect(() => {
    if (prefersReduced) return;
    const id = window.setInterval(() => {
      setTick((t) => (t + 1) % 60);
    }, 1100);
    return () => window.clearInterval(id);
  }, [prefersReduced]);

  // Quadrant definitions. `phase` shifts the counter so they don't all tick
  // in lockstep. `winner` flags the cell that owns the latest revision.
  const cells: ReadonlyArray<{
    x: number;
    y: number;
    key: string;
    base: number;
    phase: number;
  }> = [
    { x: 22, y: 32, key: "agent.a", base: 40, phase: 0 },
    { x: 104, y: 32, key: "agent.b", base: 40, phase: 1 },
    { x: 22, y: 104, key: "agent.c", base: 40, phase: 2 },
    { x: 104, y: 104, key: "agent.d", base: 40, phase: 3 },
  ];

  // The winning cell rotates each cycle — keeps the eye moving without
  // forcing the viewer to stare at one corner.
  const winnerIdx = Math.floor(tick / 4) % 4;

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="A 2 by 2 grid of agent keys with incrementing revision numbers, illustrating last-writer-wins replication"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Outer frame hint — quarter-corner marks instead of a full border so
          the matrix reads as a viewport, not a card. */}
      {[
        { x: 8, y: 8, sx: 1, sy: 1 },
        { x: 192, y: 8, sx: -1, sy: 1 },
        { x: 8, y: 192, sx: 1, sy: -1 },
        { x: 192, y: 192, sx: -1, sy: -1 },
      ].map((c, i) => (
        <g
          key={i}
          transform={`translate(${c.x} ${c.y}) scale(${c.sx} ${c.sy})`}
        >
          <line
            x1="0"
            y1="0"
            x2="10"
            y2="0"
            stroke="var(--color-text-on-ink-secondary)"
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.5"
          />
          <line
            x1="0"
            y1="0"
            x2="0"
            y2="10"
            stroke="var(--color-text-on-ink-secondary)"
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity="0.5"
          />
        </g>
      ))}

      {cells.map((cell, i) => {
        const isWinner = i === winnerIdx;
        const rev = cell.base + ((tick + cell.phase) % 12);
        return (
          <g key={cell.key}>
            {/* Cell background */}
            <rect
              x={cell.x}
              y={cell.y}
              width="74"
              height="64"
              rx="8"
              fill="var(--color-ink-2)"
              stroke={
                isWinner
                  ? "var(--color-accent-on-ink)"
                  : "var(--color-border-dark-strong)"
              }
              strokeWidth="1.4"
              opacity={isWinner ? 0.95 : 0.85}
              style={{
                transition: "stroke 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
              }}
            />

            {/* Key label */}
            <text
              x={cell.x + 10}
              y={cell.y + 18}
              fontSize="9"
              fontFamily="JetBrains Mono, monospace"
              fill="var(--color-text-on-ink-secondary)"
              opacity="0.7"
              letterSpacing="0.04em"
            >
              {cell.key}
            </text>

            {/* Revision counter — animated AnimatePresence-free swap so the
                whole number changes atomically each tick. */}
            <motion.text
              x={cell.x + 10}
              y={cell.y + 44}
              fontSize="20"
              fontFamily="JetBrains Mono, monospace"
              fontWeight="600"
              fill={
                isWinner
                  ? "var(--color-accent-on-ink)"
                  : "var(--color-text-on-ink)"
              }
              key={`${cell.key}-${rev}`}
              initial={prefersReduced ? false : { opacity: 0, y: cell.y + 50 }}
              animate={{ opacity: 1, y: cell.y + 44 }}
              transition={{ duration: 0.35, ease: EASE }}
            >
              r{rev}
            </motion.text>

            {/* Winner dot — top-right corner. */}
            {isWinner && (
              <motion.circle
                cx={cell.x + 64}
                cy={cell.y + 12}
                r="3"
                fill="var(--color-accent-on-ink)"
                initial={prefersReduced ? false : { scale: 0, opacity: 0 }}
                animate={{ scale: 1, opacity: 1 }}
                transition={{ duration: 0.4, ease: EASE }}
              />
            )}
          </g>
        );
      })}

      {/* Cross-hair connector — implies these cells are part of one matrix. */}
      <line
        x1="100"
        y1="36"
        x2="100"
        y2="164"
        stroke="var(--color-border-dark-strong)"
        strokeWidth="1"
        strokeDasharray="2 4"
        opacity="0.5"
      />
      <line
        x1="22"
        y1="100"
        x2="178"
        y2="100"
        stroke="var(--color-border-dark-strong)"
        strokeWidth="1"
        strokeDasharray="2 4"
        opacity="0.5"
      />
    </svg>
  );
}
