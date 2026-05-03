import { useId } from "react";
import { motion, useReducedMotion } from "framer-motion";

/**
 * HarnessFlow — animated 4-vendor → 1-relay flow diagram for the
 * Cross-Harness Compatibility band.
 *
 * Layout:
 *   Desktop  — four vendor cards across the top, single QUORUS relay
 *              centered below, curved SVG paths connecting each vendor to
 *              the relay. Paths light up in sequence (1s gap, looping).
 *   Mobile   — vendor cards stack vertically, paths become short downward
 *              arrows between them and the relay block at the bottom.
 *
 * Honors prefers-reduced-motion: paths render at full opacity, no sequence
 * animation, no dash flow.
 *
 * Wordmarks only — no logo image files. Pure SVG + text.
 */

const EASE = [0.16, 1, 0.3, 1] as const;

const VENDORS = [
  { id: "claude", label: "Claude Code" },
  { id: "cursor", label: "Cursor" },
  { id: "gemini", label: "Gemini CLI" },
  { id: "codex", label: "Codex CLI" },
] as const;

// Diagram canvas in viewBox units. 1100x420 keeps the 4-up grid spaced
// without crowding the relay node, and stays under 3:1 aspect.
const VB_W = 1100;
const VB_H = 420;

// Vendor card geometry
const CARD_W = 200;
const CARD_H = 92;
const CARD_Y = 24;
const CARD_GAP = (VB_W - VENDORS.length * CARD_W) / (VENDORS.length + 1);

// Relay geometry
const RELAY_W = 260;
const RELAY_H = 96;
const RELAY_X = (VB_W - RELAY_W) / 2;
const RELAY_Y = VB_H - RELAY_H - 24;

function vendorCenter(i: number): { x: number; y: number } {
  const x = CARD_GAP + CARD_W / 2 + i * (CARD_W + CARD_GAP);
  const y = CARD_Y + CARD_H;
  return { x, y };
}

function relayTopAt(targetX: number): { x: number; y: number } {
  // Anchor each connecting line on the relay's top edge near the vendor's
  // x position so the bundle splays naturally instead of stacking.
  const minX = RELAY_X + 32;
  const maxX = RELAY_X + RELAY_W - 32;
  const clamped = Math.max(minX, Math.min(maxX, targetX));
  return { x: clamped, y: RELAY_Y };
}

function curvePath(i: number): string {
  const start = vendorCenter(i);
  const end = relayTopAt(start.x);
  // Single cubic Bezier — eases the line so it feels like a signal, not a
  // straight wire. Control points at 60% and 80% of vertical span keep the
  // curve gentle on the outer columns and tight on the middle two.
  const c1y = start.y + (end.y - start.y) * 0.55;
  const c2y = start.y + (end.y - start.y) * 0.85;
  return `M ${start.x} ${start.y} C ${start.x} ${c1y}, ${end.x} ${c2y}, ${end.x} ${end.y}`;
}

function VendorCard({
  vendor,
  index,
}: {
  vendor: (typeof VENDORS)[number];
  index: number;
}): JSX.Element {
  const x = CARD_GAP + index * (CARD_W + CARD_GAP);
  return (
    <g>
      <rect
        x={x}
        y={CARD_Y}
        width={CARD_W}
        height={CARD_H}
        rx={10}
        fill="var(--color-ink-2)"
        stroke="var(--color-border-dark-strong)"
        strokeWidth={1}
      />
      {/* Wordmark */}
      <text
        x={x + CARD_W / 2}
        y={CARD_Y + 38}
        textAnchor="middle"
        fill="var(--color-text-on-ink)"
        fontFamily="var(--font-sans)"
        fontWeight={600}
        fontSize={16}
        letterSpacing="-0.01em"
      >
        {vendor.label}
      </text>
      {/* Status pill — small dot + label */}
      <g>
        <circle
          cx={x + CARD_W / 2 - 44}
          cy={CARD_Y + 64}
          r={3}
          fill="var(--color-accent-on-ink)"
        />
        <text
          x={x + CARD_W / 2 - 34}
          y={CARD_Y + 68}
          textAnchor="start"
          fill="var(--color-text-on-ink-muted)"
          fontFamily="var(--font-mono)"
          fontSize={10.5}
          letterSpacing="0.12em"
        >
          COMPATIBLE
        </text>
      </g>
    </g>
  );
}

export default function HarnessFlow(): JSX.Element {
  const prefersReduced = useReducedMotion();
  const gradId = useId();
  const relayGlowId = useId();

  return (
    <div className="w-full">
      {/* Desktop diagram — full SVG flow */}
      <div className="hidden md:block">
        <svg
          aria-hidden
          viewBox={`0 0 ${VB_W} ${VB_H}`}
          preserveAspectRatio="xMidYMid meet"
          className="block h-auto w-full"
          xmlns="http://www.w3.org/2000/svg"
        >
          <defs>
            {/* Soft accent halo behind the relay node */}
            <radialGradient id={relayGlowId} cx="50%" cy="50%" r="60%">
              <stop
                offset="0%"
                stopColor="var(--color-accent-on-ink)"
                stopOpacity={0.18}
              />
              <stop
                offset="100%"
                stopColor="var(--color-accent-on-ink)"
                stopOpacity={0}
              />
            </radialGradient>
            {/* Gradient stroke for the lit-up state of each path */}
            <linearGradient id={gradId} x1="0" y1="0" x2="0" y2="1">
              <stop
                offset="0%"
                stopColor="var(--color-accent-on-ink)"
                stopOpacity={0.2}
              />
              <stop
                offset="100%"
                stopColor="var(--color-accent-on-ink)"
                stopOpacity={1}
              />
            </linearGradient>
          </defs>

          {/* Vendor cards */}
          {VENDORS.map((v, i) => (
            <VendorCard key={v.id} vendor={v} index={i} />
          ))}

          {/* Connector paths — base layer (dim, always visible) */}
          {VENDORS.map((v, i) => (
            <path
              key={`base-${v.id}`}
              d={curvePath(i)}
              stroke="var(--color-accent-on-ink)"
              strokeOpacity={0.4}
              strokeWidth={1}
              fill="none"
            />
          ))}

          {/* Connector paths — animated overlay (the "lighting up" sweep) */}
          {!prefersReduced &&
            VENDORS.map((v, i) => {
              // 4 vendors, 1s offset each, 4s loop -> staggered round-robin
              const delay = i * 1;
              const cycle = VENDORS.length;
              return (
                <motion.path
                  key={`pulse-${v.id}`}
                  d={curvePath(i)}
                  stroke={`url(#${gradId})`}
                  strokeWidth={2}
                  fill="none"
                  strokeDasharray="6 8"
                  initial={{ opacity: 0, strokeDashoffset: 0 }}
                  animate={{
                    opacity: [0, 0.95, 0.95, 0],
                    strokeDashoffset: [0, -120],
                  }}
                  transition={{
                    duration: 1.4,
                    repeat: Infinity,
                    repeatDelay: cycle - 1.4,
                    delay,
                    ease: "linear",
                    times: [0, 0.2, 0.8, 1],
                  }}
                />
              );
            })}

          {/* Relay halo */}
          <ellipse
            cx={VB_W / 2}
            cy={RELAY_Y + RELAY_H / 2}
            rx={RELAY_W * 0.85}
            ry={RELAY_H * 1.4}
            fill={`url(#${relayGlowId})`}
          />

          {/* Relay node — visually heavier */}
          <g>
            <rect
              x={RELAY_X}
              y={RELAY_Y}
              width={RELAY_W}
              height={RELAY_H}
              rx={12}
              fill="var(--color-ink-2)"
              stroke="var(--color-accent-on-ink)"
              strokeWidth={1.25}
            />
            <text
              x={VB_W / 2}
              y={RELAY_Y + 42}
              textAnchor="middle"
              fill="var(--color-text-on-ink)"
              fontFamily="var(--font-sans)"
              fontWeight={600}
              fontSize={22}
              letterSpacing="-0.01em"
            >
              QUORUS
            </text>
            <text
              x={VB_W / 2}
              y={RELAY_Y + 68}
              textAnchor="middle"
              fill="var(--color-accent-on-ink)"
              fontFamily="var(--font-mono)"
              fontSize={10.5}
              letterSpacing="0.18em"
            >
              COORDINATION RELAY
            </text>
          </g>
        </svg>
      </div>

      {/* Mobile diagram — stacked vendor pills, downward arrows, relay block */}
      <div className="md:hidden">
        <div className="flex flex-col items-center gap-3">
          {VENDORS.map((v, i) => (
            <div key={v.id} className="flex w-full flex-col items-center">
              <motion.div
                initial={{ opacity: 0, y: 8 }}
                whileInView={{ opacity: 1, y: 0 }}
                viewport={{ once: true, amount: 0.4 }}
                transition={{ duration: 0.5, ease: EASE, delay: i * 0.05 }}
                className="flex w-full max-w-xs items-center justify-between rounded-[10px] px-4 py-3"
                style={{
                  backgroundColor: "var(--color-ink-2)",
                  border: "1px solid var(--color-border-dark-strong)",
                }}
              >
                <span
                  className="text-[14px]"
                  style={{
                    color: "var(--color-text-on-ink)",
                    fontFamily: "var(--font-sans)",
                    fontWeight: 600,
                  }}
                >
                  {v.label}
                </span>
                <span
                  className="inline-flex items-center gap-1.5 text-[10px]"
                  style={{
                    color: "var(--color-text-on-ink-muted)",
                    fontFamily: "var(--font-mono)",
                    letterSpacing: "0.12em",
                  }}
                >
                  <span
                    className="block h-1.5 w-1.5 rounded-full"
                    style={{
                      backgroundColor: "var(--color-accent-on-ink)",
                    }}
                  />
                  COMPATIBLE
                </span>
              </motion.div>
              {/* Down arrow connector */}
              <svg
                aria-hidden
                width="14"
                height="22"
                viewBox="0 0 14 22"
                className="my-1"
              >
                <line
                  x1="7"
                  y1="0"
                  x2="7"
                  y2="16"
                  stroke="var(--color-accent-on-ink)"
                  strokeOpacity={0.5}
                  strokeWidth={1}
                  strokeDasharray="3 3"
                />
                <path
                  d="M3 14 L7 20 L11 14"
                  stroke="var(--color-accent-on-ink)"
                  strokeOpacity={0.7}
                  strokeWidth={1}
                  fill="none"
                  strokeLinecap="round"
                  strokeLinejoin="round"
                />
              </svg>
            </div>
          ))}
          {/* Relay */}
          <div
            className="flex w-full max-w-xs flex-col items-center rounded-[12px] px-4 py-4 text-center"
            style={{
              backgroundColor: "var(--color-ink-2)",
              border: "1px solid var(--color-accent-on-ink)",
            }}
          >
            <span
              className="text-[20px]"
              style={{
                color: "var(--color-text-on-ink)",
                fontFamily: "var(--font-sans)",
                fontWeight: 600,
                letterSpacing: "-0.01em",
              }}
            >
              QUORUS
            </span>
            <span
              className="mt-1 text-[10px]"
              style={{
                color: "var(--color-accent-on-ink)",
                fontFamily: "var(--font-mono)",
                letterSpacing: "0.18em",
              }}
            >
              COORDINATION RELAY
            </span>
          </div>
        </div>
      </div>
    </div>
  );
}
