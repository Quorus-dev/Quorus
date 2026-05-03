import type { CSSProperties, ReactNode } from "react";
import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import { Link } from "react-router-dom";
import Spotlight from "../effects/Spotlight";

const EASE = [0.16, 1, 0.3, 1] as const;
const MONO = "'JetBrains Mono', ui-monospace, monospace";

/**
 * BentoCard — shared surface for the dark bento grid on the home page.
 *
 * - Entire card is clickable (Link wraps the surface) and keyboard-focusable.
 * - `wide` cards lay out illustration left, copy right; default cards stack
 *   illustration on top of copy.
 * - Hover lifts the card 4px and brightens the border + accent on the link.
 * - Reduced-motion users get the static end-state.
 *
 * Styling references CSS variables from src/styles/tokens.css — never raw
 * hex (with two exceptions: the rgba shadow and the focus ring fallback,
 * neither of which has a token).
 */
export type BentoCardProps = {
  /** Stable identifier — also used for analytics keys + aria-labelledby. */
  id: string;
  /** Card heading (h3). */
  title: string;
  /** Body paragraph — keep to 2 lines at the target widths. */
  description: string;
  /** Internal route; passed to react-router Link. */
  href: string;
  /** Hand-built SVG illustration (~200x200, stroke-based). */
  illustration: ReactNode;
  /** When true, illustration sits on the LEFT and copy on the right (desktop). */
  wide?: boolean;
  /** Optional eyebrow label rendered above the title in mono caps. */
  eyebrow?: string;
  /** CSS Grid area name — set by the parent grid. */
  area: string;
  /** Stagger index for entrance animation. */
  index?: number;
};

export default function BentoCard({
  id,
  title,
  description,
  href,
  illustration,
  wide = false,
  eyebrow,
  area,
  index = 0,
}: BentoCardProps) {
  const prefersReduced = useReducedMotion();
  const [hover, setHover] = useState(false);

  const titleId = `bento-${id}-title`;

  // Inline style covers what Tailwind cannot express cleanly:
  // grid-area assignment + the hover-driven box-shadow + the border swap.
  const surfaceStyle: CSSProperties = {
    gridArea: area,
    backgroundColor: "var(--color-ink-2)",
    borderColor: hover
      ? "var(--color-border-dark-strong)"
      : "var(--color-border-dark)",
    // On hover we layer three shadows: the lift, the existing inset bloom,
    // and a 1px teal halo that rides just outside the border for the glow.
    boxShadow: hover
      ? "0 24px 60px rgba(0, 0, 0, 0.45), 0 0 0 1px rgba(94, 179, 168, 0.20), 0 0 0 1px rgba(94, 179, 168, 0.06) inset"
      : "0 1px 2px rgba(0, 0, 0, 0.25)",
    transitionProperty: "border-color, box-shadow",
    transitionDuration: "0.4s",
    transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
  };

  return (
    <motion.div
      style={surfaceStyle}
      className="group relative h-full overflow-hidden rounded-2xl border"
      initial={{ opacity: 0, y: 16 }}
      whileInView={{ opacity: 1, y: 0 }}
      viewport={{ once: true, amount: 0.2 }}
      transition={{
        duration: 0.55,
        delay: 0.06 * index,
        ease: EASE,
      }}
      whileHover={prefersReduced ? undefined : { y: -4 }}
      onHoverStart={() => setHover(true)}
      onHoverEnd={() => setHover(false)}
    >
      {/* Cursor-tracked spotlight. Listens on this motion.div (its parent),
          sits behind the link content via z-index, never blocks pointer events. */}
      <Spotlight />

      <Link
        to={href}
        aria-labelledby={titleId}
        onFocus={() => setHover(true)}
        onBlur={() => setHover(false)}
        className="relative z-[2] flex h-full flex-col outline-none"
      >
        {/* Two layouts: wide (illustration left, copy right) vs stacked. */}
        <div
          className={
            wide
              ? "grid h-full grid-cols-1 gap-6 p-7 sm:p-8 md:grid-cols-[minmax(0,140px)_1fr] md:items-center md:gap-7"
              : "flex h-full flex-col p-7 sm:p-8"
          }
        >
          {/* Illustration well */}
          <div
            aria-hidden
            className={
              wide
                ? "relative flex aspect-[5/4] w-full items-center justify-center md:aspect-auto md:h-[200px]"
                : "relative flex h-[140px] w-full items-center justify-center"
            }
          >
            <IllustrationFrame hover={hover}>{illustration}</IllustrationFrame>
          </div>

          {/* Copy column */}
          <div
            className={
              wide ? "flex h-full flex-col" : "mt-6 flex flex-1 flex-col"
            }
          >
            {eyebrow && (
              <span
                className="mb-3 text-[11px] uppercase"
                style={{
                  color: "var(--color-accent-on-ink)",
                  fontFamily: MONO,
                  letterSpacing: "0.22em",
                }}
              >
                {eyebrow}
              </span>
            )}

            <h3
              id={titleId}
              className="text-balance"
              style={{
                color: "var(--color-text-on-ink)",
                fontWeight: 600,
                letterSpacing: "-0.018em",
                lineHeight: 1.15,
                fontSize: wide ? "clamp(19px, 1.4vw, 22px)" : "19px",
                hyphens: "manual",
              }}
            >
              {title}
            </h3>

            <p
              className="mt-3 text-[14.5px] leading-[1.55]"
              style={{ color: "var(--color-text-on-ink-secondary)" }}
            >
              {description}
            </p>

            {/* Bottom-aligned learn-more affordance */}
            <span
              className="mt-auto inline-flex items-center gap-1.5 pt-6 text-[12.5px]"
              style={{
                color: hover
                  ? "var(--color-accent-on-ink)"
                  : "var(--color-text-on-ink-secondary)",
                fontFamily: MONO,
                letterSpacing: "0.04em",
                transition: "color 0.3s cubic-bezier(0.16, 1, 0.3, 1)",
              }}
            >
              Learn more
              <ArrowGlyph
                hover={hover}
                prefersReduced={prefersReduced ?? false}
              />
            </span>
          </div>
        </div>
      </Link>
    </motion.div>
  );
}

/**
 * Wraps the illustration in a hairline frame matching the card's surface so
 * the SVG reads as an embedded artifact, not a free-floating shape.
 */
function IllustrationFrame({
  hover,
  children,
}: {
  hover: boolean;
  children: ReactNode;
}) {
  return (
    <div
      className="relative flex h-full w-full items-center justify-center overflow-hidden rounded-xl"
      style={{
        background:
          "radial-gradient(ellipse at 50% 30%, rgba(94,179,168,0.05), transparent 70%), rgba(255,255,255,0.015)",
        border: `1px solid ${
          hover ? "rgba(255,255,255,0.08)" : "rgba(255,255,255,0.04)"
        }`,
        transition: "border-color 0.4s cubic-bezier(0.16, 1, 0.3, 1)",
      }}
    >
      <div className="h-full w-full p-3">{children}</div>
    </div>
  );
}

function ArrowGlyph({
  hover,
  prefersReduced,
}: {
  hover: boolean;
  prefersReduced: boolean;
}) {
  return (
    <motion.svg
      width="11"
      height="11"
      viewBox="0 0 12 12"
      fill="none"
      aria-hidden
      animate={prefersReduced ? undefined : { x: hover ? 3 : 0 }}
      transition={{ duration: 0.3, ease: EASE }}
    >
      <path
        d="M3 9l6-6M9 3H4.5M9 3v4.5"
        stroke="currentColor"
        strokeWidth="1.4"
        strokeLinecap="round"
        strokeLinejoin="round"
      />
    </motion.svg>
  );
}
