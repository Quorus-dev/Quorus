import { motion, useReducedMotion, useScroll, useSpring } from "framer-motion";

/**
 * ScrollProgress — hairline scroll-progress bar pinned to the very top edge
 * of the viewport. Tracks document scroll position 0 → 1 and drives a
 * scaleX-only transform for GPU-cheap motion. The track is a 1px line in
 * `--color-border-light`; the fill is `--color-accent`.
 *
 * Reduced-motion: renders a static (non-animated) bar at scroll position 0.
 * The container always exists in the DOM so layout is stable; only the
 * spring-driven motion is removed.
 *
 * Z-index sits above NavV2 (which uses z-50) so the indicator is never
 * occluded by a sticky header.
 */
export default function ScrollProgress() {
  const prefersReduced = !!useReducedMotion();
  const { scrollYProgress } = useScroll();

  // Smooth out the raw progress with a critically-tuned spring. damping=30
  // and stiffness=120 keeps it responsive on fast scrolls but visually
  // settled — no oscillation past the cursor position.
  const scaleX = useSpring(scrollYProgress, {
    stiffness: 120,
    damping: 30,
    restDelta: 0.001,
  });

  return (
    <div
      aria-hidden
      className="pointer-events-none fixed inset-x-0 top-0 z-[60] h-px"
      style={{ backgroundColor: "var(--color-border-light)" }}
    >
      <motion.div
        className="h-full origin-left"
        style={{
          backgroundColor: "var(--color-accent)",
          // Static under reduced motion (rendered at progress=0 — the user's
          // own scroll bar already conveys position; we just don't animate).
          scaleX: prefersReduced ? 0 : scaleX,
        }}
      />
    </div>
  );
}
