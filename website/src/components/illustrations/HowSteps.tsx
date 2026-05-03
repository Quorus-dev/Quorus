import { motion, useReducedMotion, AnimatePresence } from "framer-motion";
import { useEffect, useState } from "react";

/**
 * HowSteps — three hand-built illustrations for the "How it works" band.
 *
 * Each function returns a 280×280 SVG composition with a subtle looping
 * Framer animation. All strokes 1.5px, palette is the cream surface family
 * with teal-700 / teal-300 accents (matching tokens.css).
 *
 * Reduced-motion: every animation collapses to a static end state.
 *
 * Exports (all named, no default):
 *   - Step1Join   — agents flow into a shared room
 *   - Step2Lock   — files acquire lock badges sequentially
 *   - Step3Stream — state matrix with a pulse line + revision counter
 */

const TEAL_700 = "#0d4d4a";
const TEAL_300 = "#5eb3a8";
const INK = "#0a0a0f";
const CREAM = "#f5f1ea";

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "JetBrains Mono, monospace";
const SECONDARY = "rgba(10,10,15,0.55)";
const FAINT = "rgba(10,10,15,0.18)";
const HAIRLINE = "rgba(10,10,15,0.10)";

/* ──────────────────────────────────────────────────────────────────────────
 * Shared SVG primitives
 * ────────────────────────────────────────────────────────────────────────── */

function Avatar({
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
        fill={ring === TEAL_700 ? TEAL_700 : INK}
        textAnchor="middle"
      >
        {label.charAt(0).toUpperCase()}
      </text>
    </>
  );
}

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

/* ──────────────────────────────────────────────────────────────────────────
 * Step 1 — Join a room
 * Four agent avatars settle inside a shared room. The latest avatar slides
 * in from the left on a Framer loop while the previous three sit settled.
 * ────────────────────────────────────────────────────────────────────────── */

const ROOM_AGENTS: Array<{ x: number; y: number; label: string }> = [
  { x: 110, y: 120, label: "claude-1" },
  { x: 165, y: 120, label: "cursor-2" },
  { x: 110, y: 175, label: "codex-3" },
  { x: 165, y: 175, label: "gemini-4" },
];

export function Step1Join(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const [tick, setTick] = useState(0);

  useEffect(() => {
    if (prefersReduced) return;
    const id = window.setInterval(() => setTick((t) => (t + 1) % 4), 1800);
    return () => window.clearInterval(id);
  }, [prefersReduced]);

  const incoming = ROOM_AGENTS[tick];

  return (
    <svg
      viewBox="0 0 280 280"
      width="100%"
      height="100%"
      role="img"
      aria-label="Four agents joining a shared coordination room"
      style={{ display: "block" }}
    >
      {/* Room boundary */}
      <rect
        x="78"
        y="76"
        width="140"
        height="140"
        rx="14"
        fill={CREAM}
        stroke={SECONDARY}
        strokeWidth="1.5"
        strokeDasharray="3 5"
        opacity="0.85"
      />

      {/* Room label tab */}
      <rect
        x="86"
        y="60"
        width="118"
        height="20"
        rx="4"
        fill={CREAM}
        stroke={FAINT}
      />
      <text x="95" y="74" fontSize="10" fontFamily={MONO} fill={SECONDARY}>
        room://dev-sprint
      </text>

      {/* Inflow lane — three faint dashes guiding agents in */}
      <g stroke={FAINT} strokeWidth="1" strokeDasharray="2 4">
        <line x1="20" y1="140" x2="78" y2="140" />
        <line x1="20" y1="155" x2="78" y2="155" />
        <line x1="20" y1="170" x2="78" y2="170" />
      </g>

      {/* Settled vs incoming agents */}
      {ROOM_AGENTS.map((a, i) => {
        const isIncoming = i === tick && !prefersReduced;
        return (
          <g key={a.label}>
            <AnimatePresence mode="wait">
              {isIncoming ? (
                <motion.g
                  key={`incoming-${tick}`}
                  initial={{ x: -60, opacity: 0 }}
                  animate={{ x: 0, opacity: 1 }}
                  exit={{ opacity: 0 }}
                  transition={{ duration: 0.7, ease: EASE }}
                >
                  <Avatar cx={a.x} cy={a.y} label={a.label} ring={TEAL_700} />
                </motion.g>
              ) : (
                <g>
                  <Avatar cx={a.x} cy={a.y} label={a.label} ring={INK} />
                </g>
              )}
            </AnimatePresence>
          </g>
        );
      })}

      {/* Caption pill — "<name> joined" */}
      <motion.g
        key={`caption-${tick}`}
        initial={prefersReduced ? false : { opacity: 0, y: 4 }}
        animate={{ opacity: 1, y: 0 }}
        transition={{ duration: 0.4, ease: EASE }}
      >
        <rect x="92" y="232" width="112" height="22" rx="11" fill={TEAL_700} />
        <text
          x="148"
          y="247"
          fontSize="10"
          fontFamily={MONO}
          fontWeight="500"
          fill={CREAM}
          textAnchor="middle"
        >
          {incoming.label} joined
        </text>
      </motion.g>
    </svg>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
 * Step 2 — Claim what you own
 * Three file rows; lock badges reveal sequentially. The first file briefly
 * shows a "claimed by claude-1" tooltip when all three are locked.
 * ────────────────────────────────────────────────────────────────────────── */

const FILES: Array<{ name: string; y: number }> = [
  { name: "auth.py", y: 60 },
  { name: "router.py", y: 130 },
  { name: "models.py", y: 200 },
];

function FileRow({
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
      {/* Folded-corner doc icon */}
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

export function Step2Lock(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const [phase, setPhase] = useState(0);

  useEffect(() => {
    if (prefersReduced) {
      setPhase(3);
      return;
    }
    const id = window.setInterval(() => setPhase((p) => (p + 1) % 5), 1100);
    return () => window.clearInterval(id);
  }, [prefersReduced]);

  const showTooltip = phase === 3;

  return (
    <svg
      viewBox="0 0 280 280"
      width="100%"
      height="100%"
      role="img"
      aria-label="Three files acquiring distributed locks in sequence"
      style={{ display: "block" }}
    >
      {/* Vertical guide between files */}
      <line
        x1="140"
        y1="40"
        x2="140"
        y2="240"
        stroke={HAIRLINE}
        strokeWidth="1"
        strokeDasharray="2 4"
      />

      {FILES.map((f, i) => (
        <FileRow key={f.name} name={f.name} y={f.y} locked={phase > i} />
      ))}

      {/* Tooltip on first file */}
      <AnimatePresence>
        {showTooltip ? (
          <motion.g
            key="tooltip"
            initial={{ opacity: 0, y: 4 }}
            animate={{ opacity: 1, y: 0 }}
            exit={{ opacity: 0, y: 4 }}
            transition={{ duration: 0.35, ease: EASE }}
          >
            <line
              x1="194"
              y1={FILES[0].y + 11}
              x2="232"
              y2={FILES[0].y - 4}
              stroke={TEAL_700}
              strokeWidth="1"
              strokeDasharray="2 3"
            />
            <rect
              x="148"
              y={FILES[0].y - 28}
              width="120"
              height="22"
              rx="4"
              fill={INK}
            />
            <text
              x="208"
              y={FILES[0].y - 13}
              fontSize="10"
              fontFamily={MONO}
              fill={CREAM}
              textAnchor="middle"
            >
              claimed by claude-1
            </text>
          </motion.g>
        ) : null}
      </AnimatePresence>
    </svg>
  );
}

/* ──────────────────────────────────────────────────────────────────────────
 * Step 3 — Watch state stream
 * 4×2 grid of state cells with a horizontal pulse line passing through;
 * revision counter cycles rev 41 → 42 → 43.
 * ────────────────────────────────────────────────────────────────────────── */

const REV_START = 41;
const CELL_W = 50;
const CELL_H = 38;
const COLS = 4;
const ROWS = 2;
const GAP = 8;
const GRID_W = COLS * CELL_W + (COLS - 1) * GAP;
const GRID_X = (280 - GRID_W) / 2;
const GRID_Y = 100;

function Cell({
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

export function Step3Stream(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const [rev, setRev] = useState(REV_START);

  useEffect(() => {
    if (prefersReduced) return;
    const id = window.setInterval(() => {
      setRev((r) => (r >= REV_START + 2 ? REV_START : r + 1));
    }, 1400);
    return () => window.clearInterval(id);
  }, [prefersReduced]);

  return (
    <svg
      viewBox="0 0 280 280"
      width="100%"
      height="100%"
      role="img"
      aria-label="State matrix with a real-time pulse line and revision counter"
      style={{ display: "block" }}
    >
      <text
        x="140"
        y="58"
        fontSize="9"
        fontFamily={MONO}
        fill={SECONDARY}
        letterSpacing="2"
        textAnchor="middle"
      >
        STATE MATRIX
      </text>

      <line
        x1={GRID_X}
        y1={GRID_Y - 14}
        x2={GRID_X + GRID_W}
        y2={GRID_Y - 14}
        stroke={HAIRLINE}
        strokeWidth="1"
      />

      {Array.from({ length: ROWS }).map((_, r) =>
        Array.from({ length: COLS }).map((_, c) => (
          <Cell
            key={`cell-${r}-${c}`}
            x={GRID_X + c * (CELL_W + GAP)}
            y={GRID_Y + r * (CELL_H + GAP)}
            hot={(r * COLS + c) % 3 === rev - REV_START}
          />
        )),
      )}

      <motion.line
        x1={GRID_X - 8}
        y1={GRID_Y + CELL_H + GAP / 2}
        x2={GRID_X + GRID_W + 8}
        y2={GRID_Y + CELL_H + GAP / 2}
        stroke={TEAL_700}
        strokeWidth="1.5"
        strokeLinecap="round"
        initial={{ pathLength: 0, opacity: 0.2 }}
        animate={
          prefersReduced
            ? { pathLength: 1, opacity: 0.6 }
            : { pathLength: [0, 1, 1], opacity: [0.2, 0.85, 0.4] }
        }
        transition={{
          duration: 1.4,
          ease: EASE,
          repeat: prefersReduced ? 0 : Infinity,
        }}
      />

      {/* Revision counter pill */}
      <g>
        <rect
          x="178"
          y="222"
          width="76"
          height="22"
          rx="11"
          fill="none"
          stroke={TEAL_700}
          strokeWidth="1.5"
        />
        <text
          x="190"
          y="237"
          fontSize="10"
          fontFamily={MONO}
          fontWeight="500"
          fill={TEAL_700}
        >
          rev
        </text>
        <AnimatePresence mode="wait">
          <motion.text
            key={`rev-${rev}`}
            x="216"
            y="237"
            fontSize="10"
            fontFamily={MONO}
            fontWeight="600"
            fill={TEAL_700}
            initial={prefersReduced ? false : { opacity: 0, y: -3 }}
            animate={{ opacity: 1, y: 0 }}
            exit={prefersReduced ? undefined : { opacity: 0, y: 3 }}
            transition={{ duration: 0.25, ease: EASE }}
          >
            {rev}
          </motion.text>
        </AnimatePresence>
        <circle cx="244" cy="233" r="2.5" fill={TEAL_300} />
      </g>

      <text x="26" y="237" fontSize="10" fontFamily={MONO} fill={SECONDARY}>
        sse · &lt;50ms
      </text>
    </svg>
  );
}
