import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoContext — a `.quorus/context.md` file glyph being mirrored from a
 * room state node. A short stream of dotted glyphs travels along a curved
 * path from the room dot down into the file, suggesting continuous sync.
 *
 * Visually pairs with BentoState (which shows the matrix itself) and
 * BentoCodeSync (which the previous version of this card had occupied).
 * The motion is slow and deliberate — never a marquee.
 */
export default function BentoContext() {
  const prefersReduced = useReducedMotion();

  // Three lines of mirrored content rendered inside the file body. We bias
  // toward the kinds of facts a room actually holds — goal, claim, decision —
  // so the glyph reads as a real artifact, not a generic doc.
  const lines: ReadonlyArray<{ y: number; width: number; accent?: boolean }> = [
    { y: 64, width: 60, accent: true },
    { y: 78, width: 78 },
    { y: 92, width: 48 },
    { y: 106, width: 70, accent: true },
  ];

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="Room state mirroring into a context.md file in the repo"
      preserveAspectRatio="xMidYMid meet"
    >
      {/* Room source — a soft accent disc with a faint ring, anchored
          top-right. This is the "room" the file mirrors. */}
      <g transform="translate(150 30)">
        <motion.circle
          cx="0"
          cy="0"
          r="14"
          fill="none"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="1.2"
          opacity="0.35"
          initial={false}
          animate={
            prefersReduced
              ? undefined
              : { r: [14, 18, 14], opacity: [0.35, 0.05, 0.35] }
          }
          transition={{
            duration: 3.6,
            repeat: prefersReduced ? 0 : Infinity,
            ease: EASE,
          }}
        />
        <circle
          cx="0"
          cy="0"
          r="6"
          fill="var(--color-accent-on-ink)"
          opacity="0.85"
        />
        <text
          x="-4"
          y="-22"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          fill="var(--color-text-on-ink-secondary)"
          opacity="0.7"
          letterSpacing="0.06em"
          textAnchor="middle"
        >
          room
        </text>
      </g>

      {/* Sync path — curved Bezier from the room down into the file's top
          edge. Drawn first as a faint guide so the moving dot reads against
          a track instead of empty space. */}
      <path
        id="ctx-sync-path"
        d="M 150 36 C 140 80, 110 70, 90 96"
        fill="none"
        stroke="var(--color-accent-on-ink)"
        strokeWidth="1"
        strokeDasharray="2 4"
        opacity="0.35"
      />

      {/* Two pulse dots staggered along the path — implies a steady stream. */}
      {[0, 1.6].map((delay, idx) => (
        <motion.circle
          key={idx}
          r="2.4"
          fill="var(--color-accent-on-ink)"
          initial={false}
          animate={
            prefersReduced
              ? { offsetDistance: "100%" }
              : { offsetDistance: ["0%", "100%"] }
          }
          transition={{
            duration: 3.2,
            delay,
            repeat: prefersReduced ? 0 : Infinity,
            repeatDelay: 0.4,
            ease: EASE,
          }}
          style={{
            offsetPath: "path('M 150 36 C 140 80, 110 70, 90 96')",
            offsetRotate: "0deg",
          }}
        />
      ))}

      {/* File glyph — the .quorus/context.md target. Same paper-with-fold
          treatment as BentoLocks, recoloured against the file path label. */}
      <g transform="translate(28 90)">
        {/* Sheet outline */}
        <path
          d="M 0 8 Q 0 0 8 0 L 78 0 L 108 30 L 108 96 Q 108 104 100 104 L 8 104 Q 0 104 0 96 Z"
          fill="var(--color-ink-2)"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />

        {/* Folded corner */}
        <path
          d="M 78 0 L 78 22 Q 78 30 86 30 L 108 30"
          fill="none"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.5"
          strokeLinejoin="round"
        />
        <path
          d="M 78 0 L 108 30 L 86 30 Q 78 30 78 22 Z"
          fill="var(--color-text-on-ink-secondary)"
          fillOpacity="0.08"
        />

        {/* Filename — sits on the file's top-left so the glyph is identifiable
            without a separate caption. */}
        <text
          x="10"
          y="20"
          fontSize="9"
          fontFamily="JetBrains Mono, monospace"
          fill="var(--color-accent-on-ink)"
          letterSpacing="0.04em"
          opacity="0.95"
        >
          .quorus/context.md
        </text>

        {/* Hairline content rules — the mirrored lines. Accent rows pulse on
            the same beat as the path dots so the eye links source → sink. */}
        {lines.map((line, i) => (
          <motion.line
            key={i}
            x1="14"
            y1={line.y - 50}
            x2={14 + line.width}
            y2={line.y - 50}
            stroke={
              line.accent
                ? "var(--color-accent-on-ink)"
                : "var(--color-text-on-ink-secondary)"
            }
            strokeWidth="1.4"
            strokeLinecap="round"
            opacity={line.accent ? 0.85 : 0.55}
            initial={false}
            animate={
              line.accent && !prefersReduced
                ? { opacity: [0.55, 0.95, 0.55] }
                : undefined
            }
            transition={
              line.accent && !prefersReduced
                ? {
                    duration: 3.2,
                    delay: i * 0.4,
                    repeat: Infinity,
                    ease: EASE,
                  }
                : undefined
            }
          />
        ))}
      </g>
    </svg>
  );
}
