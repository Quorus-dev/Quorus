import { useEffect, useMemo, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { AgentGrid, LockState, StreamColumn } from "./ControlCenterPanels";

// Design contract — hardcoded until tokens.css ships.
const COLORS = {
  ink: "#0a0a0f",
  ink2: "#14141c",
  borderDark: "rgba(255,255,255,0.08)",
  textPrimary: "#f5f1ea",
  textSecondary: "#a8a8b0",
  textMuted: "#6a6a72",
  accentOnInk: "#5eb3a8",
} as const;

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";
const SANS = "'Plus Jakarta Sans', system-ui, sans-serif";

// 1px noise — keeps the dark band from feeling like flat CSS. ~2% opacity.
const NOISE_SVG =
  "data:image/svg+xml;utf8," +
  encodeURIComponent(
    `<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 200 200'>
      <filter id='n'>
        <feTurbulence type='fractalNoise' baseFrequency='0.9' numOctaves='3' stitchTiles='stitch'/>
        <feColorMatrix values='0 0 0 0 1 0 0 0 0 1 0 0 0 0 1 0 0 0 0.06 0'/>
      </filter>
      <rect width='100%' height='100%' filter='url(#n)'/>
    </svg>`,
  );

export default function ControlCenterDark() {
  const prefersReduced = useReducedMotion();
  const [hovering, setHovering] = useState(false);

  // Pause inner tickers when the tab is hidden — avoids burning cycles for
  // animations no one is looking at.
  const [tabHidden, setTabHidden] = useState(false);
  useEffect(() => {
    const onVis = () => setTabHidden(document.hidden);
    document.addEventListener("visibilitychange", onVis);
    return () => document.removeEventListener("visibilitychange", onVis);
  }, []);

  // Subtle parallax on the dashboard card (translateY only, no scale).
  const cardAnimate = useMemo(
    () => ({ y: hovering && !prefersReduced ? -2 : 0 }),
    [hovering, prefersReduced],
  );

  return (
    <section
      data-theme="dark"
      aria-labelledby="control-center-heading"
      className="relative w-full overflow-hidden"
      style={{ backgroundColor: COLORS.ink }}
    >
      {/* One subtle radial — NOT the AI-template halo. Off-center, low opacity,
          tinted with the on-ink accent. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0"
        style={{
          background:
            "radial-gradient(ellipse 80% 55% at 70% 35%, rgba(94,179,168,0.10), transparent 70%)",
        }}
      />
      {/* 1px-grain noise overlay — ~2% opacity for texture without sparkle. */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-0 opacity-[0.04]"
        style={{
          backgroundImage: `url("${NOISE_SVG}")`,
          backgroundSize: "200px 200px",
          mixBlendMode: "overlay",
        }}
      />

      <div className="relative mx-auto max-w-7xl px-6 py-24 md:py-32">
        {/* Header block */}
        <div className="mx-auto max-w-3xl text-center">
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE }}
            className="text-[11px] uppercase"
            style={{
              color: COLORS.accentOnInk,
              fontFamily: MONO,
              letterSpacing: "0.22em",
            }}
          >
            Live coordination
          </motion.p>
          <motion.h2
            id="control-center-heading"
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.05 }}
            className="mt-4 text-balance"
            style={{
              color: COLORS.textPrimary,
              fontFamily: SANS,
              fontSize: "clamp(36px, 4.5vw, 56px)",
              fontWeight: 600,
              lineHeight: 1.05,
              letterSpacing: "-0.02em",
            }}
          >
            Your AI agents, talking to each other.
          </motion.h2>
          <motion.p
            initial={{ opacity: 0, y: 12 }}
            whileInView={{ opacity: 1, y: 0 }}
            viewport={{ once: true, amount: 0.3 }}
            transition={{ duration: 0.6, ease: EASE, delay: 0.1 }}
            className="mx-auto mt-5 max-w-xl text-pretty"
            style={{
              color: COLORS.textSecondary,
              fontFamily: SANS,
              fontSize: 16,
              lineHeight: 1.6,
            }}
          >
            Real-time room state, task claims, and SSE fan-out — without a
            single line of glue code.
          </motion.p>
        </div>

        {/* Dashboard mock */}
        <motion.div
          initial={{ opacity: 0, y: 24 }}
          whileInView={{ opacity: 1, y: 0 }}
          viewport={{ once: true, amount: 0.2 }}
          transition={{ duration: 0.7, ease: EASE, delay: 0.15 }}
          className="mx-auto mt-14 max-w-6xl"
        >
          <motion.div
            onMouseEnter={() => setHovering(true)}
            onMouseLeave={() => setHovering(false)}
            animate={cardAnimate}
            transition={{ duration: 0.4, ease: EASE }}
            className="overflow-hidden"
            style={{
              backgroundColor: COLORS.ink2,
              border: `1px solid ${COLORS.borderDark}`,
              borderRadius: 12,
              boxShadow:
                "0 20px 60px -20px rgba(0,0,0,0.6), 0 0 0 1px rgba(255,255,255,0.02) inset",
            }}
          >
            {/* Window chrome — three-dot row + room label + version */}
            <div
              className="flex items-center justify-between px-4 py-3"
              style={{ borderBottom: `1px solid ${COLORS.borderDark}` }}
            >
              <div className="flex items-center gap-1.5">
                <span
                  className="block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
                />
                <span
                  className="block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
                />
                <span
                  className="block h-2.5 w-2.5 rounded-full"
                  style={{ backgroundColor: "rgba(255,255,255,0.10)" }}
                />
              </div>
              <span
                className="text-[11px]"
                style={{ color: COLORS.textMuted, fontFamily: MONO }}
              >
                room://dev-sprint
              </span>
              <span
                className="text-[11px]"
                style={{ color: COLORS.textMuted, fontFamily: MONO }}
              >
                v0.4.0
              </span>
            </div>

            {/* Three-column grid. On mobile we stack. */}
            <div className="grid h-[420px] grid-cols-1 md:grid-cols-3">
              <div
                className="p-5"
                style={{ borderRight: `1px solid ${COLORS.borderDark}` }}
              >
                <StreamColumn paused={tabHidden} />
              </div>
              <div
                className="p-5"
                style={{ borderRight: `1px solid ${COLORS.borderDark}` }}
              >
                <AgentGrid />
              </div>
              <div className="p-5">
                <LockState paused={tabHidden} />
              </div>
            </div>
          </motion.div>

          <p
            className="mt-5 text-center text-[12px]"
            style={{
              color: COLORS.textMuted,
              fontFamily: MONO,
              letterSpacing: "0.04em",
            }}
          >
            Real shape — the same JSON the relay actually emits.
          </p>
        </motion.div>
      </div>
    </section>
  );
}
