import { motion } from "framer-motion";

/**
 * Private helpers for HowSteps.tsx illustrations.
 * Underscore prefix marks this as not for direct external import.
 *
 * Kept out of HowSteps.tsx so the main file stays under the 500-line cap
 * after the project's prettier formatter expands SVG attribute lists.
 */

export const TEAL_700 = "#0d4d4a";
export const TEAL_300 = "#5eb3a8";
export const INK = "#0a0a0f";
export const CREAM = "#f5f1ea";

export const EASE = [0.16, 1, 0.3, 1] as const;
export const MONO = "JetBrains Mono, monospace";
export const SECONDARY = "rgba(10,10,15,0.55)";
export const FAINT = "rgba(10,10,15,0.18)";
export const HAIRLINE = "rgba(10,10,15,0.10)";

/* Step 1 helpers ──────────────────────────────────────────────────────── */

export function Avatar({
  cx,
  cy,
  label,
  ring,
}: {
  cx: number;
  cy: number;
  label: string;
  ring: string;
}): JSX.Element {
  const fill = ring === TEAL_700 ? TEAL_700 : INK;
  return (
    <>
      <circle
        cx={cx}
        cy={cy}
        r="14"
        fill={CREAM}
        stroke={ring}
        strokeWidth="1.5"
      />
      <text
        x={cx}
        y={cy + 3}
        fontSize="9"
        fontFamily={MONO}
        fontWeight="600"
        fill={fill}
        textAnchor="middle"
      >
        {label.charAt(0).toUpperCase()}
      </text>
    </>
  );
}

/* Step 2 helpers ──────────────────────────────────────────────────────── */

function LockBadge({ cx, cy }: { cx: number; cy: number }): JSX.Element {
  return (
    <>
      <circle cx={cx} cy={cy} r="14" fill={TEAL_700} />
      <rect
        x={cx - 6}
        y={cy - 3}
        width="12"
        height="9"
        rx="1.5"
        fill="none"
        stroke={CREAM}
        strokeWidth="1.5"
      />
      <path
        d={`M ${cx - 4} ${cy - 3} L ${cx - 4} ${cy - 6} A 4 4 0 0 1 ${cx + 4} ${cy - 6} L ${cx + 4} ${cy - 3}`}
        fill="none"
        stroke={CREAM}
        strokeWidth="1.5"
        strokeLinecap="round"
      />
    </>
  );
}

export function FileRow({
  name,
  y,
  locked,
}: {
  name: string;
  y: number;
  locked: boolean;
}): JSX.Element {
  return (
    <g>
      <rect
        x="60"
        y={y}
        width="160"
        height="50"
        rx="8"
        fill={CREAM}
        stroke={SECONDARY}
        strokeWidth="1.5"
      />
      <g
        transform={`translate(74, ${y + 13})`}
        stroke={SECONDARY}
        strokeWidth="1.5"
        fill="none"
      >
        <path d="M0 0 L14 0 L20 6 L20 24 L0 24 Z" strokeLinejoin="round" />
        <path d="M14 0 L14 6 L20 6" strokeLinejoin="round" />
        <line x1="4" y1="13" x2="16" y2="13" strokeWidth="1.2" />
        <line x1="4" y1="18" x2="13" y2="18" strokeWidth="1.2" />
      </g>
      <text
        x="108"
        y={y + 30}
        fontSize="13"
        fontFamily={MONO}
        fontWeight="500"
        fill={INK}
      >
        {name}
      </text>
      <motion.g
        initial={false}
        animate={{ opacity: locked ? 1 : 0, scale: locked ? 1 : 0.6 }}
        transition={{ duration: 0.45, ease: EASE }}
        style={{ transformOrigin: `194px ${y + 25}px` }}
      >
        <LockBadge cx={194} cy={y + 25} />
      </motion.g>
    </g>
  );
}

/* Step 3 helpers ──────────────────────────────────────────────────────── */

export const REV_START = 41;
export const CELL_W = 50;
export const CELL_H = 38;
export const COLS = 4;
export const ROWS = 2;
export const GAP = 8;
export const GRID_W = COLS * CELL_W + (COLS - 1) * GAP;
export const GRID_X = (280 - GRID_W) / 2;
export const GRID_Y = 100;

export function Cell({
  x,
  y,
  hot,
}: {
  x: number;
  y: number;
  hot: boolean;
}): JSX.Element {
  return (
    <g>
      <rect
        x={x}
        y={y}
        width={CELL_W}
        height={CELL_H}
        rx="6"
        fill={CREAM}
        stroke={SECONDARY}
        strokeWidth="1.5"
      />
      <line
        x1={x + 8}
        y1={y + 14}
        x2={x + CELL_W - 18}
        y2={y + 14}
        stroke={SECONDARY}
        strokeWidth="1.2"
        strokeOpacity="0.6"
      />
      <line
        x1={x + 8}
        y1={y + 22}
        x2={x + CELL_W - 26}
        y2={y + 22}
        stroke={SECONDARY}
        strokeWidth="1.2"
        strokeOpacity="0.4"
      />
      <motion.circle
        cx={x + CELL_W - 10}
        cy={y + 10}
        r="3"
        fill={TEAL_300}
        initial={false}
        animate={{ opacity: hot ? 1 : 0, scale: hot ? 1 : 0.4 }}
        transition={{ duration: 0.35, ease: EASE }}
      />
    </g>
  );
}
