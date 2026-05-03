import { motion, useReducedMotion } from "framer-motion";
import type { ComponentType, ElementType, ReactNode } from "react";

/**
 * BlurFadeIn — opacity + blur + lift on enter, inspired by magicui Blur Fade.
 *
 * Why blur instead of just opacity:
 *   The 8px → 0px blur tween creates the impression of focus pulling in,
 *   which reads as "intentional reveal" rather than "thing appeared".
 *   It's the same trick film editors use for soft cuts. Pairs well with
 *   the cream/ink palette because the blurred state retains color, never
 *   ghostly grey.
 *
 * Reduced motion:
 *   Renders the final state immediately. No transform, no filter — the
 *   underlying element pays no animation cost and the page reflows once.
 *
 * Performance:
 *   `viewport={{ once: true }}` means we animate exactly once per scroll,
 *   never thrashing on re-entry. Tween (not spring) keeps the timeline
 *   deterministic for the design QA pass.
 */
type BlurFadeInProps = {
  children: ReactNode;
  /** Seconds before animation starts. Use to choreograph siblings. */
  delay?: number;
  /** Seconds the tween runs. Default 0.6s — matches --motion-duration-slow. */
  duration?: number;
  /** Set false to skip viewport gating and animate on mount. */
  inView?: boolean;
  /** Element tag, defaults to div. Use span inside a paragraph. */
  as?: ElementType;
  className?: string;
};

const EASE_OUT_EXPO = [0.16, 1, 0.3, 1] as const;

export default function BlurFadeIn({
  children,
  delay = 0,
  duration = 0.6,
  inView = true,
  as = "div",
  className,
}: BlurFadeInProps) {
  const prefersReduced = useReducedMotion();

  // Static render path — no Framer hooks, no IntersectionObserver, no cost.
  if (prefersReduced) {
    const Tag = as as ElementType;
    return <Tag className={className}>{children}</Tag>;
  }

  // Hidden / shown variants.
  const hidden = { opacity: 0, filter: "blur(8px)", y: 12 };
  const shown = { opacity: 1, filter: "blur(0px)", y: 0 };

  // Pick the right animation trigger. `whileInView` defers to the user's
  // scroll position; `animate` runs immediately. Both feed the same target
  // so layout is stable either way.
  const motionProps = inView
    ? {
        initial: hidden,
        whileInView: shown,
        viewport: { once: true, amount: 0.3 },
      }
    : {
        initial: hidden,
        animate: shown,
      };

  // Framer's `motion()` factory accepts any element type but the resulting
  // component is generic over `unknown`. We cast to a permissive component
  // type so callers can pass className/style without TS losing its mind.
  // Functionally identical — the runtime motion component already accepts
  // every HTML/SVG attribute its underlying tag does.
  const MotionTag = motion(as) as ComponentType<Record<string, unknown>>;

  return (
    <MotionTag
      className={className}
      {...motionProps}
      transition={{ duration, delay, ease: EASE_OUT_EXPO }}
    >
      {children}
    </MotionTag>
  );
}
