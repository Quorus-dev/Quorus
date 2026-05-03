import { useId } from "react";

/**
 * AnimatedBeam + BorderBeam — Magic-UI-style decorative effects.
 *
 * Both primitives render pure SVG. The "comet" highlight is a moving
 * `linearGradient` (transparent → accent → transparent) sliding along the
 * stroke of a path or rect via `<animate>` on `gradientTransform`.
 *
 * Why SVG `<animate>` rather than Framer Motion / CSS keyframes:
 *   - We are animating a gradient transform on a freshly-created
 *     `<linearGradient>`. SVG SMIL handles this with no JS, no extra
 *     bundle weight, and no React re-renders. The browser composites
 *     it on the GPU. Framer Motion does not have a primitive for
 *     animating SVG gradient transforms.
 *
 * Reduced-motion handling is the caller's job — pass `animate={false}`.
 * That way each consumer can call `useReducedMotion()` once at the top
 * and pipe the result down without these primitives needing the hook.
 *
 * Color scheme is fixed to `var(--color-accent-on-ink)` (teal #5eb3a8)
 * to keep the dark surfaces consistent. Caller controls only opacity,
 * width, timing, and the path / rect geometry.
 */

const ACCENT = "var(--color-accent-on-ink)";

export type AnimatedBeamProps = {
  /** SVG path `d` attribute — the curve the beam travels along. */
  d: string;
  /** Whether to render the moving comet overlay. Pass `false` for reduced-motion. */
  animate: boolean;
  /** Total loop duration in seconds (one comet pass + idle gap). */
  duration?: number;
  /** Seconds before this beam first fires. Stagger siblings by setting different values. */
  delay?: number;
  /** Stroke width of the dim base path. */
  baseWidth?: number;
  /** Opacity of the dim base path (0..1). */
  baseOpacity?: number;
  /** Stroke width of the moving comet overlay. */
  beamWidth?: number;
  /**
   * Fraction (0..1) of the path the comet's bright span covers. Smaller =
   * tighter pulse. Default 0.18 reads as a clear "packet" of light.
   */
  cometSpan?: number;
};

/**
 * One beam = one dim base path + one moving comet overlay.
 *
 * Implementation detail — the comet is a long, flat gradient (1×1 user
 * units) that we slide horizontally with a SMIL `<animate>` on its
 * `gradientTransform`. The path's `pathLength="1"` lets us reason about
 * `strokeDasharray` in 0..1 fractions, and `userSpaceOnUse` keeps the
 * gradient coords sane regardless of path bounding box.
 */
export function AnimatedBeam({
  d,
  animate,
  duration = 2.5,
  delay = 0,
  baseWidth = 1,
  baseOpacity = 0.2,
  beamWidth = 2,
  cometSpan = 0.18,
}: AnimatedBeamProps): JSX.Element {
  const gid = useId();
  // Dasharray draws only `cometSpan` worth of stroke and hides the rest.
  // `dashoffset` slides that visible chunk from one end of the path to
  // the other. With `pathLength="1"` we can reason in fractions.
  const dash = `${cometSpan} ${1 - cometSpan}`;
  // Full sweep travels the bright chunk from -cometSpan to 1 — that way
  // the comet enters and exits cleanly off-path on both ends.
  const offsetStart = cometSpan;
  const offsetEnd = -1;

  return (
    <g>
      {/* Base layer — always visible, dim teal */}
      <path
        d={d}
        stroke={ACCENT}
        strokeOpacity={baseOpacity}
        strokeWidth={baseWidth}
        fill="none"
        strokeLinecap="round"
      />
      {animate && (
        <>
          <defs>
            {/* The comet itself: a 3-stop gradient that fades in and out
                around a hot center. Slid via gradientTransform below. */}
            <linearGradient
              id={gid}
              gradientUnits="userSpaceOnUse"
              x1="0"
              y1="0"
              x2="1"
              y2="0"
            >
              <stop offset="0%" stopColor={ACCENT} stopOpacity="0" />
              <stop offset="50%" stopColor={ACCENT} stopOpacity="1" />
              <stop offset="100%" stopColor={ACCENT} stopOpacity="0" />
            </linearGradient>
          </defs>
          <path
            d={d}
            pathLength="1"
            stroke={`url(#${gid})`}
            strokeWidth={beamWidth}
            fill="none"
            strokeLinecap="round"
            strokeDasharray={dash}
            strokeDashoffset={offsetStart}
          >
            <animate
              attributeName="stroke-dashoffset"
              from={offsetStart}
              to={offsetEnd}
              dur={`${duration}s`}
              begin={`${delay}s`}
              repeatCount="indefinite"
              fill="freeze"
            />
          </path>
        </>
      )}
    </g>
  );
}

export type BorderBeamProps = {
  x: number;
  y: number;
  width: number;
  height: number;
  rx?: number;
  /** Render the moving border highlight. Pass `false` for reduced-motion. */
  animate: boolean;
  /** Loop duration in seconds. */
  duration?: number;
  /** Fraction (0..1) of the perimeter the bright arc covers. */
  arcSpan?: number;
  /** Stroke width of the moving arc. */
  beamWidth?: number;
  /** Opacity of the moving arc (0..1). */
  beamOpacity?: number;
};

/**
 * BorderBeam — a glowing arc travels around a rounded rect's perimeter.
 *
 * Built with `pathLength="1"` + a single short dash; we animate
 * `strokeDashoffset` from 1 → 0 to cycle the arc all the way around. The
 * rect remains visually invisible apart from the moving chunk because
 * the rest of the dasharray is empty space. Pair this with your real
 * border (drawn separately) for the static frame.
 */
export function BorderBeam({
  x,
  y,
  width,
  height,
  rx = 12,
  animate,
  duration = 4,
  arcSpan = 0.15,
  beamWidth = 1.25,
  beamOpacity = 0.7,
}: BorderBeamProps): JSX.Element {
  if (!animate) {
    // Reduced-motion: render nothing — the static border is owned by the caller.
    return <g aria-hidden />;
  }
  const dash = `${arcSpan} ${1 - arcSpan}`;
  return (
    <rect
      x={x}
      y={y}
      width={width}
      height={height}
      rx={rx}
      pathLength="1"
      fill="none"
      stroke={ACCENT}
      strokeOpacity={beamOpacity}
      strokeWidth={beamWidth}
      strokeLinecap="round"
      strokeDasharray={dash}
    >
      <animate
        attributeName="stroke-dashoffset"
        from="1"
        to="0"
        dur={`${duration}s`}
        repeatCount="indefinite"
      />
    </rect>
  );
}
