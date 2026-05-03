import { useReducedMotion } from "framer-motion";
import type { CSSProperties, ReactNode } from "react";

/**
 * Marquee — continuous-scroll horizontal track inspired by magicui.design.
 *
 * Architecture:
 *   - Children are rendered TWICE inside a flex track so the second copy
 *     seamlessly takes over when the first translates 50% off-screen.
 *   - Animation is pure CSS (`marquee-left` / `marquee-right` keyframes
 *     defined in src/index.css), so paint stays on the compositor — 60fps
 *     even on a busy main thread.
 *   - Edge fade is a `mask-image` linear gradient (10% → 90%), giving the
 *     impression of items materializing out of fog rather than clipping
 *     against a hard edge.
 *   - Hover pauses the animation via `[--marquee-state]` so users can
 *     read a logo without chasing it.
 *   - `useReducedMotion()` collapses the track to a single static row.
 *
 * Why two children, not `Array(n).fill`:
 *   The pattern "render content twice + translate -50%" is the canonical
 *   marquee trick — it guarantees a perfect loop regardless of content
 *   width, no JS measurement required, no jank at the seam.
 */
type MarqueeProps = {
  children: ReactNode;
  /** Scroll direction. `left` translates content right→left. */
  direction?: "left" | "right";
  /** Seconds per full revolution. Slower = calmer. Default 40s. */
  durationSeconds?: number;
  /** Pause animation when the user hovers the row. Default true. */
  pauseOnHover?: boolean;
  className?: string;
};

export default function Marquee({
  children,
  direction = "left",
  durationSeconds = 40,
  pauseOnHover = true,
  className = "",
}: MarqueeProps) {
  const prefersReduced = useReducedMotion();

  const animationName = direction === "left" ? "marquee-left" : "marquee-right";

  // Mask fade: transparent → black 10% → black 90% → transparent. Applied on
  // both -webkit and standard properties so Safari renders the fade too.
  const maskImage =
    "linear-gradient(to right, transparent 0%, black 10%, black 90%, transparent 100%)";

  const trackStyle: CSSProperties = prefersReduced
    ? {}
    : {
        animation: `${animationName} ${durationSeconds}s linear infinite`,
        animationPlayState: "var(--marquee-state, running)" as string,
      };

  const wrapperStyle: CSSProperties = {
    maskImage,
    WebkitMaskImage: maskImage,
  };

  // Reduced motion: render a single static row, no duplication, no animation.
  if (prefersReduced) {
    return (
      <div
        className={`relative overflow-hidden ${className}`}
        style={wrapperStyle}
      >
        <div className="flex w-max items-center gap-12">{children}</div>
      </div>
    );
  }

  return (
    <div
      className={`group relative overflow-hidden ${className}`}
      style={wrapperStyle}
      onMouseEnter={
        pauseOnHover
          ? (e) =>
              e.currentTarget.style.setProperty("--marquee-state", "paused")
          : undefined
      }
      onMouseLeave={
        pauseOnHover
          ? (e) =>
              e.currentTarget.style.setProperty("--marquee-state", "running")
          : undefined
      }
    >
      <div className="flex w-max items-center gap-12" style={trackStyle}>
        {/* Two copies of children — required for seamless loop. */}
        <div className="flex shrink-0 items-center gap-12">{children}</div>
        <div className="flex shrink-0 items-center gap-12" aria-hidden="true">
          {children}
        </div>
      </div>
    </div>
  );
}
