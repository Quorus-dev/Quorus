import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoLocks — a file glyph with a chain/key overlay. The lock body sits
 * snug against the file; the shackle pulses closed on a slow loop, then
 * eases open, suggesting an atomic claim cycle.
 */
export default function BentoLocks() {
  const prefersReduced = useReducedMotion();

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="A file with a padlock that closes and opens, representing an atomic distributed lock"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* File body — corner-folded sheet, slightly tilted for visual interest. */}
      <g transform="translate(40 30) rotate(-4 60 70)">
        {/* Sheet outline */}
        <path
          d="M 0 8 Q 0 0 8 0 L 80 0 L 110 30 L 110 132 Q 110 140 102 140 L 8 140 Q 0 140 0 132 Z"
          fill="var(--color-ink-2)"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />

        {/* Folded corner — the small triangle that signals "this is paper". */}
        <path
          d="M 80 0 L 80 22 Q 80 30 88 30 L 110 30"
          fill="none"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M 80 0 L 110 30 L 88 30 Q 80 30 80 22 Z"
          fill="var(--color-text-on-ink-secondary)"
          fillOpacity="0.08"
        />

        {/* Hairline content rules — implies file content without showing it. */}
        <line
          x1="14"
          y1="56"
          x2="80"
          y2="56"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1"
          strokeLinecap="round"
          opacity="0.4"
        />
        <line
          x1="14"
          y1="68"
          x2="92"
          y2="68"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1"
          strokeLinecap="round"
          opacity="0.4"
        />
        <line
          x1="14"
          y1="80"
          x2="58"
          y2="80"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1"
          strokeLinecap="round"
          opacity="0.4"
        />
      </g>

      {/* Padlock — overlaps the bottom-right of the file. The shackle is the
          animated element; the body stays put. */}
      <g transform="translate(118 110)">
        {/* Drop shadow — subtle separation from the file. */}
        <ellipse cx="22" cy="60" rx="22" ry="3" fill="rgba(0,0,0,0.35)" />

        {/* Shackle — the animated path. PathLength toggles between open and
            closed shape to suggest the snap-shut motion. */}
        <motion.path
          d="M 9 24 L 9 16 A 13 13 0 0 1 35 16 L 35 24"
          fill="none"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="2.4"
          strokeLinecap="round"
          initial={false}
          animate={
            prefersReduced
              ? undefined
              : { pathLength: [0.55, 1, 1, 0.55], opacity: [0.55, 1, 1, 0.55] }
          }
          transition={{
            duration: 4,
            times: [0, 0.3, 0.85, 1],
            repeat: prefersReduced ? 0 : Infinity,
            repeatDelay: 1.4,
            ease: EASE,
          }}
        />

        {/* Lock body */}
        <rect
          x="2"
          y="22"
          width="40"
          height="34"
          rx="5"
          fill="var(--color-ink-2)"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="1.8"
        />
        {/* Keyhole */}
        <circle cx="22" cy="36" r="3" fill="var(--color-accent-on-ink)" />
        <rect
          x="20.6"
          y="36"
          width="2.8"
          height="9"
          rx="1.2"
          fill="var(--color-accent-on-ink)"
        />
      </g>

      {/* "owner" tag — small mono caption that ties this back to the lock
          taxonomy. Not animated — pure annotation. */}
      <g transform="translate(34 174)">
        <circle cx="3" cy="-3" r="2.2" fill="var(--color-accent-on-ink)" />
        <text
          x="10"
          y="0"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          fill="var(--color-text-on-ink-secondary)"
          opacity="0.75"
          letterSpacing="0.06em"
        >
          owner: claude-1
        </text>
      </g>
    </svg>
  );
}
