import { useId } from "react";
import type { JSX } from "react";
import { useReducedMotion } from "framer-motion";
import { AnimatedBeam, BorderBeam } from "../effects/AnimatedBeam";
import {
  ClaudeMark,
  CursorMark,
  GeminiMark,
  CodexMark,
} from "../effects/VendorLogos";
import type { VendorMarkProps } from "../effects/VendorLogos";

/**
 * HarnessFlow — animated 4-vendor → 1-relay flow diagram for the
 * Cross-Harness Compatibility band.
 *
 * Layout:
 *   Desktop  — four vendor cards across the top, single QUORUS relay
 *              centered below, curved SVG paths connecting each vendor to
 *              the relay. A teal "comet" travels each path, firing in
 *              sequence (Claude → Cursor → Gemini → Codex → repeat). The
 *              relay node is wrapped by a slow border beam that orbits its
 *              perimeter.
 *   Mobile   — vendor cards stack vertically, paths become short downward
 *              arrows between them and the relay block at the bottom.
 *
 * Honors prefers-reduced-motion: only the dim base paths render, the
 * comet overlays and border beam are suppressed. Layout, typography,
 * relay halo, and ARIA semantics are unchanged from prior version.
 *
 * Wordmarks only — no logo image files. Pure SVG + text.
 */

// Wave-7: HarnessFlow keeps the 4-up grid for legibility but the band's
// caption now references all 7 (6 tier-A + 1 tier-B). The four shown here
// are the most visible vendor names; Opencode + Cline + Windsurf appear in
// the install switcher (CrossHarnessBand) and the comparison band copy.
type VendorMark = (props: VendorMarkProps) => JSX.Element;

const VENDORS: ReadonlyArray<{
  id: string;
  label: string;
  Mark: VendorMark;
}> = [
  { id: "claude", label: "Claude Code", Mark: ClaudeMark },
  { id: "cursor", label: "Cursor", Mark: CursorMark },
  { id: "gemini", label: "Gemini CLI", Mark: GeminiMark },
  { id: "codex", label: "Codex CLI", Mark: CodexMark },
];

// Diagram canvas in viewBox units. 1100x420 keeps the 4-up grid spaced
// without crowding the relay node, and stays under 3:1 aspect.
const VB_W = 1100;
const VB_H = 420;

// Vendor card geometry — taller now to fit brand mark + wordmark + status pill
const CARD_W = 200;
const CARD_H = 108;
const CARD_Y = 24;
const CARD_GAP = (VB_W - VENDORS.length * CARD_W) / (VENDORS.length + 1);

// Relay geometry
const RELAY_W = 260;
const RELAY_H = 96;
const RELAY_X = (VB_W - RELAY_W) / 2;
const RELAY_Y = VB_H - RELAY_H - 24;

// Beam timing — comet sweep duration and per-vendor stagger.
// 4 vendors × 0.6s offset = 2.4s between each vendor's own re-fire,
// which lines up cleanly with the 2.5s sweep so the band feels full
// without overlapping comets crashing into each other.
const BEAM_DURATION = 2.5;
const BEAM_STAGGER = 0.6;

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
  const { Mark } = vendor;
  // Brand-mark badge sits in the top portion of the card, wordmark below.
  // foreignObject is the cleanest way to render an HTML element (which lets
  // the mark's gradient defs and currentColor cascade work normally) inside
  // the SVG canvas without recomputing every path's offset.
  const MARK_SIZE = 26;
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
      {/* Brand mark — centered horizontally, sits at the top of the card */}
      <foreignObject
        x={x + CARD_W / 2 - MARK_SIZE / 2}
        y={CARD_Y + 12}
        width={MARK_SIZE}
        height={MARK_SIZE}
      >
        <div
          style={{
            width: MARK_SIZE,
            height: MARK_SIZE,
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            color: "var(--color-text-on-ink)",
          }}
        >
          <Mark size={MARK_SIZE} />
        </div>
      </foreignObject>
      {/* Wordmark — pushed below the mark */}
      <text
        x={x + CARD_W / 2}
        y={CARD_Y + 58}
        textAnchor="middle"
        fill="var(--color-text-on-ink)"
        fontFamily="var(--font-sans)"
        fontWeight={600}
        fontSize={14.5}
        letterSpacing="-0.01em"
      >
        {vendor.label}
      </text>
      {/* Status pill — small dot + label */}
      <g>
        <circle
          cx={x + CARD_W / 2 - 44}
          cy={CARD_Y + 78}
          r={3}
          fill="var(--color-accent-on-ink)"
        />
        <text
          x={x + CARD_W / 2 - 34}
          y={CARD_Y + 82}
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
  const animate = !prefersReduced;
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
          </defs>

          {/* Vendor cards */}
          {VENDORS.map((v, i) => (
            <VendorCard key={v.id} vendor={v} index={i} />
          ))}

          {/*
            Connector beams — each Magic-UI-style: dim base path + a teal
            comet that sweeps from vendor down to the relay. The four
            vendors are staggered by BEAM_STAGGER so the beams cascade
            instead of firing in unison, which would feel busy. Total
            cycle = BEAM_DURATION + (n-1) * BEAM_STAGGER ≈ 4.3s, slow
            enough to read as a heartbeat rather than a strobe.
          */}
          {VENDORS.map((v, i) => (
            <AnimatedBeam
              key={`beam-${v.id}`}
              d={curvePath(i)}
              animate={animate}
              duration={BEAM_DURATION}
              delay={i * BEAM_STAGGER}
              baseOpacity={0.2}
              baseWidth={1}
              beamWidth={2}
              cometSpan={0.18}
            />
          ))}

          {/* Relay halo — soft radial wash, not animated */}
          <ellipse
            cx={VB_W / 2}
            cy={RELAY_Y + RELAY_H / 2}
            rx={RELAY_W * 0.85}
            ry={RELAY_H * 1.4}
            fill={`url(#${relayGlowId})`}
          />

          {/* Relay node — visually heavier, with orbiting border beam */}
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
            {/* Border beam orbits the relay perimeter on a 4s loop. The
                static border above stays visible at all times; this layer
                is purely decorative. */}
            <BorderBeam
              x={RELAY_X}
              y={RELAY_Y}
              width={RELAY_W}
              height={RELAY_H}
              rx={12}
              animate={animate}
              duration={4}
              arcSpan={0.15}
              beamWidth={1.5}
              beamOpacity={0.75}
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
          {VENDORS.map((v) => (
            <div key={v.id} className="flex w-full flex-col items-center">
              <div
                className="flex w-full max-w-xs items-center justify-between rounded-[10px] px-4 py-3"
                style={{
                  backgroundColor: "var(--color-ink-2)",
                  border: "1px solid var(--color-border-dark-strong)",
                }}
              >
                <span
                  className="flex items-center gap-2.5 text-[14px]"
                  style={{
                    color: "var(--color-text-on-ink)",
                    fontFamily: "var(--font-sans)",
                    fontWeight: 600,
                  }}
                >
                  <v.Mark size={20} />
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
              </div>
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
