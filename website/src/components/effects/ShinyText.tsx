import { useReducedMotion } from "framer-motion";
import type { CSSProperties, ElementType, ReactNode } from "react";

/**
 * ShinyText — subtle gradient sweep across text, inspired by magicui
 * Animated Shiny Text.
 *
 * Mechanism:
 *   A 3-stop linear gradient sits behind the glyphs (background-clip: text,
 *   color: transparent). The gradient is twice the box width. We slide
 *   `background-position-x` from 200% to -200% over 3.5s, repeating forever.
 *   The eye reads this as a glint sweeping the word once per cycle.
 *
 * Tone:
 *   - "light" — for ink/dark backgrounds. Cream base with a teal-300 glint.
 *   - "dark"  — for cream/light backgrounds. Ink base with a teal-700 glint.
 *
 * Performance:
 *   `background-position-x` animation runs on the compositor for solid
 *   colors but composites the text layer on each frame. For our use case
 *   (1-3 word eyebrows, never paragraphs) this is well below the budget.
 *
 * Reduced motion:
 *   Renders the base color with no gradient and no animation. The shimmer
 *   is decorative — the text remains fully legible.
 */
type ShinyTextProps = {
  children: ReactNode;
  /** Element tag, defaults to span. */
  as?: ElementType;
  /** Background tone of the surface this lives on. Default light. */
  tone?: "light" | "dark";
  className?: string;
  /** Seconds per shimmer cycle. Default 3.5s. */
  durationSeconds?: number;
};

// Shared keyframes registered once on first render. We use a global style
// tag rather than declaring keyframes in CSS so the component is portable —
// drop it into any project, no CSS import required.
const KEYFRAMES_ID = "quorus-shiny-text-keyframes";

function ensureKeyframes() {
  if (typeof document === "undefined") return;
  if (document.getElementById(KEYFRAMES_ID)) return;
  const style = document.createElement("style");
  style.id = KEYFRAMES_ID;
  style.textContent = `@keyframes quorus-shiny-text-sweep {
    0% { background-position: 200% center; }
    100% { background-position: -200% center; }
  }`;
  document.head.appendChild(style);
}

export default function ShinyText({
  children,
  as: Tag = "span",
  tone = "light",
  className,
  durationSeconds = 3.5,
}: ShinyTextProps) {
  const prefersReduced = useReducedMotion();
  ensureKeyframes();

  const Component = Tag as ElementType;

  // Static path: render the natural text color of the surrounding context.
  // No clip, no gradient, no animation — accessible and zero-cost.
  if (prefersReduced) {
    return <Component className={className}>{children}</Component>;
  }

  // Tone-specific gradients. Ratios (30 / 50 / 70) match magicui's shiny
  // sweep — narrow glint, broad base.
  const baseColor =
    tone === "light"
      ? "var(--color-text-on-cream)"
      : "var(--color-text-on-ink)";
  const glintColor =
    tone === "light"
      ? "rgba(94, 179, 168, 0.55)" // teal-300 at 55%
      : "rgba(94, 179, 168, 0.75)";

  const style: CSSProperties = {
    backgroundImage: `linear-gradient(110deg, ${baseColor} 30%, ${glintColor} 50%, ${baseColor} 70%)`,
    backgroundSize: "200% 100%",
    backgroundClip: "text",
    WebkitBackgroundClip: "text",
    color: "transparent",
    WebkitTextFillColor: "transparent",
    animation: `quorus-shiny-text-sweep ${durationSeconds}s linear infinite`,
    // Ensures the gradient repaints crisply when the parent re-renders.
    backgroundRepeat: "no-repeat",
  };

  return (
    <Component className={className} style={style}>
      {children}
    </Component>
  );
}
