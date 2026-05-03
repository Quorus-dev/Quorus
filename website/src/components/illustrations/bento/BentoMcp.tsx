import { motion, useReducedMotion } from "framer-motion";

const EASE = [0.16, 1, 0.3, 1] as const;

/**
 * BentoMcp — a plug-and-socket connecting two systems via a fiber line.
 * Data dots flow continuously through the fiber, slow enough not to
 * distract on idle but unmistakably alive on hover.
 */
export default function BentoMcp() {
  const prefersReduced = useReducedMotion();

  // Fiber path — left socket (system A) to right plug (system B), curving
  // gently so it reads as cable rather than circuit trace.
  const fiberD = "M 38 100 Q 100 70, 162 100";

  // Dots travel from t=0 to t=1 along the path. Three dots at staggered
  // offsets give the impression of continuous flow.
  const dotOffsets = [0, 0.33, 0.66];

  return (
    <svg
      viewBox="0 0 200 200"
      width="100%"
      height="100%"
      role="img"
      aria-label="Two systems connected by a fiber-optic line carrying data, representing Model Context Protocol"
      preserveAspectRatio="xMidYMid meet"
    >
      <defs>
        {/* Path used both for the visible fiber and as the motion reference
            for the data dots. */}
        <path id="bento-mcp-fiber" d={fiberD} />
      </defs>

      {/* Left system — the host (e.g. Claude Code). Rounded rect with an
          inset socket on the right edge. */}
      <g>
        <rect
          x="6"
          y="68"
          width="48"
          height="64"
          rx="8"
          fill="var(--color-ink-2)"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.5"
        />
        {/* Faux header pip */}
        <circle
          cx="14"
          cy="76"
          r="1.6"
          fill="var(--color-text-on-ink-secondary)"
          opacity="0.6"
        />
        <circle
          cx="20"
          cy="76"
          r="1.6"
          fill="var(--color-text-on-ink-secondary)"
          opacity="0.4"
        />
        {/* Socket cutout */}
        <rect
          x="38"
          y="92"
          width="14"
          height="16"
          rx="2"
          fill="var(--color-ink)"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.2"
        />
        {/* Socket pins */}
        <line
          x1="42"
          y1="96"
          x2="42"
          y2="104"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.2"
          strokeLinecap="round"
        />
        <line
          x1="46"
          y1="96"
          x2="46"
          y2="104"
          stroke="var(--color-text-on-ink-secondary)"
          strokeWidth="1.2"
          strokeLinecap="round"
        />
      </g>

      {/* Right system — the MCP server. Same chassis, mirrored. */}
      <g>
        <rect
          x="146"
          y="68"
          width="48"
          height="64"
          rx="8"
          fill="var(--color-ink-2)"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="1.5"
        />
        {/* Status pip — accent, marks this as the MCP side. */}
        <circle cx="186" cy="76" r="1.8" fill="var(--color-accent-on-ink)" />
        {/* Plug body protruding left */}
        <rect
          x="148"
          y="92"
          width="14"
          height="16"
          rx="2"
          fill="var(--color-accent-on-ink)"
          opacity="0.18"
        />
        <rect
          x="148"
          y="92"
          width="14"
          height="16"
          rx="2"
          fill="none"
          stroke="var(--color-accent-on-ink)"
          strokeWidth="1.2"
        />
      </g>

      {/* Fiber — visible cable. Two strokes layered so the inner accent reads
          as the live wire and the outer stroke reads as cladding. */}
      <use
        href="#bento-mcp-fiber"
        fill="none"
        stroke="var(--color-text-on-ink-secondary)"
        strokeWidth="3"
        strokeOpacity="0.18"
        strokeLinecap="round"
      />
      <use
        href="#bento-mcp-fiber"
        fill="none"
        stroke="var(--color-accent-on-ink)"
        strokeWidth="1.4"
        strokeOpacity="0.6"
        strokeLinecap="round"
      />

      {/* Data dots — animateMotion along the fiber path. SVG-native motion
          here because Framer's MotionPath doesn't support text/circle along
          an arbitrary path without extra deps. Each dot traverses the full
          path (keyPoints 0;1) but enters at a staggered begin time, giving
          the illusion of continuous flow. (Earlier we offset keyPoints with
          values >1, which is invalid per SVG spec and printed a console
          warning in Chromium.) */}
      {!prefersReduced &&
        dotOffsets.map((offset, i) => (
          <circle key={i} r="2.4" fill="var(--color-accent-on-ink)">
            <animateMotion
              dur="2.6s"
              repeatCount="indefinite"
              begin={`${(-offset * 2.6).toFixed(3)}s`}
              keyPoints="0;1"
              keyTimes="0;1"
              calcMode="linear"
            >
              <mpath href="#bento-mcp-fiber" />
            </animateMotion>
          </circle>
        ))}

      {/* Caption — mono protocol label sitting below the fiber. */}
      <motion.text
        x="100"
        y="156"
        fontSize="10"
        fontFamily="JetBrains Mono, monospace"
        fill="var(--color-text-on-ink-secondary)"
        textAnchor="middle"
        letterSpacing="0.18em"
        opacity="0.75"
        initial={false}
        animate={prefersReduced ? undefined : { opacity: [0.5, 0.85, 0.5] }}
        transition={{
          duration: 4,
          repeat: prefersReduced ? 0 : Infinity,
          ease: EASE,
        }}
      >
        MCP · stdio
      </motion.text>
    </svg>
  );
}
