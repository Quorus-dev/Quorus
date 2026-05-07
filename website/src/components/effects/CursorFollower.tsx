import { useEffect, useState } from "react";
import {
  motion,
  useMotionValue,
  useSpring,
  useReducedMotion,
} from "framer-motion";

/**
 * CursorFollower — a small accent dot that trails the cursor with spring
 * physics, scales up over interactive elements, and is hidden on touch
 * devices and when the user prefers reduced motion.
 *
 * This effect is layered on top of always-visible content. If JS fails or
 * the spring stalls, nothing about the page becomes unreadable — the
 * follower simply doesn't render.
 *
 * Magnetic targets: anything with `data-magnetic` attribute, plus
 * `<button>`, `<a>`, `[role="button"]`, and form inputs. When the cursor
 * is over one, the dot grows to ~36px and shifts blend mode so it reads
 * as a halo rather than a hard puck.
 */
export default function CursorFollower() {
  const prefersReduced = useReducedMotion();
  const [enabled, setEnabled] = useState(false);
  const [hovering, setHovering] = useState(false);
  const [visible, setVisible] = useState(false);

  // Raw motion values — updated synchronously from pointermove. The
  // displayed dot reads from spring-smoothed derivatives below, so the
  // physics looks like it has weight.
  const x = useMotionValue(0);
  const y = useMotionValue(0);
  const springX = useSpring(x, { stiffness: 200, damping: 30, mass: 0.6 });
  const springY = useSpring(y, { stiffness: 200, damping: 30, mass: 0.6 });

  // Gate on `(hover: hover)` — touch devices report `(hover: none)` and we
  // skip rendering entirely.
  useEffect(() => {
    if (typeof window === "undefined") return;
    const mq = window.matchMedia("(hover: hover) and (pointer: fine)");
    const update = () => setEnabled(mq.matches);
    update();
    mq.addEventListener?.("change", update);
    return () => mq.removeEventListener?.("change", update);
  }, []);

  useEffect(() => {
    if (!enabled || prefersReduced) return;

    const MAGNETIC_SELECTOR =
      'a, button, [role="button"], [data-magnetic], input, textarea, select, summary';

    const handleMove = (e: PointerEvent) => {
      x.set(e.clientX);
      y.set(e.clientY);
      if (!visible) setVisible(true);

      const target = e.target as Element | null;
      const interactive = target?.closest?.(MAGNETIC_SELECTOR);
      setHovering(Boolean(interactive));
    };

    const handleLeave = () => setVisible(false);
    const handleEnter = () => setVisible(true);

    window.addEventListener("pointermove", handleMove, { passive: true });
    document.addEventListener("pointerleave", handleLeave);
    document.addEventListener("pointerenter", handleEnter);

    return () => {
      window.removeEventListener("pointermove", handleMove);
      document.removeEventListener("pointerleave", handleLeave);
      document.removeEventListener("pointerenter", handleEnter);
    };
  }, [enabled, prefersReduced, visible, x, y]);

  if (!enabled || prefersReduced) return null;

  // The dot itself. `mix-blend-mode: multiply` keeps it readable on both
  // cream and dark surfaces — on cream it deepens to a forest-teal mark,
  // on the dark CTA band it mostly disappears into the ink (intentional —
  // we don't want a glowing puck dragged across the dark surface).
  return (
    <motion.div
      aria-hidden
      className="pointer-events-none fixed left-0 top-0 z-[60]"
      style={{
        x: springX,
        y: springY,
        opacity: visible ? 1 : 0,
        transition: "opacity 180ms ease",
      }}
    >
      <motion.div
        animate={{
          width: hovering ? 36 : 8,
          height: hovering ? 36 : 8,
          opacity: hovering ? 0.55 : 0.85,
        }}
        transition={{ type: "spring", stiffness: 320, damping: 28 }}
        style={{
          backgroundColor: "var(--color-accent)",
          borderRadius: "9999px",
          mixBlendMode: "multiply",
          // Center the dot under the actual cursor point.
          translateX: "-50%",
          translateY: "-50%",
        }}
      />
    </motion.div>
  );
}
