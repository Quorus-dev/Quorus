import type { CSSProperties } from "react";
import { useCallback, useEffect, useRef } from "react";
import { useReducedMotion } from "framer-motion";

/**
 * Spotlight — Magic-UI-style cursor-tracked radial gradient.
 *
 * Renders an absolutely-positioned overlay that paints a soft radial gradient
 * at the cursor position whenever the pointer is inside the parent element.
 * The parent must be `position: relative` and `overflow: hidden`.
 *
 * Implementation notes:
 *   - Tracks the cursor through CSS custom properties (`--spot-x`, `--spot-y`)
 *     written directly to the overlay's style. Zero React re-renders on move.
 *   - mousemove is throttled to one update per animation frame via rAF.
 *   - Opacity is driven by `--spot-opacity` so the fade is a CSS transition,
 *     not a state-driven re-render.
 *   - Honors `prefers-reduced-motion` — returns null entirely so neither the
 *     overlay nor the listeners cost anything.
 *
 * The overlay uses `pointer-events: none` so it never intercepts clicks,
 * focus, or hover from the parent's interactive children.
 */
export type SpotlightProps = {
  /** Radius of the spotlight in pixels. Default 200. */
  size?: number;
  /** Accent color (rgba). Defaults to Quorus teal at 10% opacity. */
  color?: string;
  /** Fade-in duration in ms. Default 200. */
  fadeIn?: number;
  /** Fade-out duration in ms. Default 300. */
  fadeOut?: number;
  /** Optional className passthrough. */
  className?: string;
};

export default function Spotlight({
  size = 200,
  color = "rgba(94, 179, 168, 0.10)",
  fadeIn = 200,
  fadeOut = 300,
  className,
}: SpotlightProps) {
  const prefersReduced = useReducedMotion();
  const overlayRef = useRef<HTMLDivElement | null>(null);
  const rafRef = useRef<number | null>(null);
  const pendingRef = useRef<{ x: number; y: number } | null>(null);
  // Track current fade duration so enter/leave transitions feel asymmetric.
  const fadeRef = useRef<number>(fadeIn);

  const flush = useCallback(() => {
    rafRef.current = null;
    const node = overlayRef.current;
    const next = pendingRef.current;
    if (!node || !next) return;
    node.style.setProperty("--spot-x", `${next.x}px`);
    node.style.setProperty("--spot-y", `${next.y}px`);
  }, []);

  useEffect(() => {
    if (prefersReduced) return;
    const overlay = overlayRef.current;
    if (!overlay) return;
    // The hoverable surface is the overlay's parent (the bento card surface).
    const parent = overlay.parentElement;
    if (!parent) return;

    const onMove = (e: MouseEvent) => {
      const rect = parent.getBoundingClientRect();
      pendingRef.current = {
        x: e.clientX - rect.left,
        y: e.clientY - rect.top,
      };
      if (rafRef.current == null) {
        rafRef.current = requestAnimationFrame(flush);
      }
    };

    const onEnter = (e: MouseEvent) => {
      const rect = parent.getBoundingClientRect();
      // Seed the position so the first paint is at the cursor, not (0,0).
      overlay.style.setProperty("--spot-x", `${e.clientX - rect.left}px`);
      overlay.style.setProperty("--spot-y", `${e.clientY - rect.top}px`);
      fadeRef.current = fadeIn;
      overlay.style.transitionDuration = `${fadeIn}ms`;
      overlay.style.setProperty("--spot-opacity", "1");
    };

    const onLeave = () => {
      fadeRef.current = fadeOut;
      overlay.style.transitionDuration = `${fadeOut}ms`;
      overlay.style.setProperty("--spot-opacity", "0");
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
      pendingRef.current = null;
    };

    parent.addEventListener("mousemove", onMove);
    parent.addEventListener("mouseenter", onEnter);
    parent.addEventListener("mouseleave", onLeave);
    return () => {
      parent.removeEventListener("mousemove", onMove);
      parent.removeEventListener("mouseenter", onEnter);
      parent.removeEventListener("mouseleave", onLeave);
      if (rafRef.current != null) {
        cancelAnimationFrame(rafRef.current);
        rafRef.current = null;
      }
    };
  }, [prefersReduced, fadeIn, fadeOut, flush]);

  if (prefersReduced) return null;

  // Initial position is offscreen so a stray paint before mouseenter is invisible.
  const style: CSSProperties = {
    background: `radial-gradient(circle ${size}px at var(--spot-x) var(--spot-y), ${color}, transparent 70%)`,
    opacity: "var(--spot-opacity, 0)" as unknown as number,
    transitionProperty: "opacity",
    transitionTimingFunction: "cubic-bezier(0.16, 1, 0.3, 1)",
    transitionDuration: `${fadeIn}ms`,
    // Seed values so the var() resolves before the first mouseenter writes.
    ["--spot-x" as string]: "-9999px",
    ["--spot-y" as string]: "-9999px",
  };

  return (
    <div
      ref={overlayRef}
      aria-hidden
      className={[
        "pointer-events-none absolute inset-0 z-[1]",
        className ?? "",
      ].join(" ")}
      style={style}
    />
  );
}
