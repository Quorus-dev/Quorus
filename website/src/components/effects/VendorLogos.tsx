import { useId } from "react";
import type { CSSProperties, JSX } from "react";

/**
 * VendorLogos — hand-crafted SVG approximations of the public brand marks
 * for every coding-agent harness Quorus integrates with.
 *
 * Why hand-built SVG, not <img> or simple-icons:
 *   1. No new deps. The marketing site stays under its bundle budget.
 *   2. currentColor inheritance lets a single component flip between
 *      brand-color (cards, "see the vision") and monochrome (marquee,
 *      "calm rhythm") without two artwork variants.
 *   3. Sub-pixel SVG stays sharp at 16-24px where shrunken bitmap PNGs
 *      shimmer along their pixel grid (matters for the marquee).
 *   4. Nominative fair use — these are recognizable approximations used
 *      in a "works with X" context, not the registered marks themselves.
 *
 * Conventions:
 *   - viewBox 0 0 24 24, default size 20, aria-hidden
 *   - props: { size?: number; className?: string; monochrome?: boolean }
 *   - monochrome uses currentColor everywhere (single fill, inherits text)
 *   - color mode uses brand constants below; gradients only where the
 *     mark genuinely needs them (Gemini, Claude spark)
 */

// ── Brand color constants ────────────────────────────────────────────────
// Sourced from public brand assets. Used only when monochrome=false.
const BRAND = {
  claudeCoral: "#D97757",
  cursorBlack: "#000000",
  cursorWhite: "#FFFFFF",
  codexBlack: "#0F0F0F",
  geminiBlue: "#3B82F6",
  geminiPurple: "#8B5CF6",
  geminiPink: "#EC4899",
  geminiAmber: "#F59E0B",
  windsurfTeal: "#14B8A6",
  copilotInk: "#1F2937",
  clineYellow: "#EAB308",
  aiderSlate: "#475569",
  continueViolet: "#7C3AED",
  opencodeOrange: "#F97316",
  codyBlue: "#2563EB",
  oiSlate: "#0F172A",
} as const;

export type VendorMarkProps = {
  size?: number;
  className?: string;
  /** True = single color via currentColor. False = canonical brand colors. */
  monochrome?: boolean;
};

// Common SVG container — every mark uses identical viewBox + a11y props so
// they baseline-align uniformly when placed beside text.
type SvgRootProps = VendorMarkProps & {
  children: React.ReactNode;
};

function SvgRoot({
  size = 20,
  className,
  children,
}: SvgRootProps): JSX.Element {
  return (
    <svg
      width={size}
      height={size}
      viewBox="0 0 24 24"
      xmlns="http://www.w3.org/2000/svg"
      aria-hidden="true"
      focusable="false"
      className={className}
      style={{ display: "inline-block", flexShrink: 0 }}
    >
      {children}
    </svg>
  );
}

// ── Claude — Anthropic spark ─────────────────────────────────────────────
// 8-pointed burst evoking the Anthropic "spark" wordmark glyph. The four
// long arms (N/S/E/W) plus four shorter diagonal arms produce the asterisk-
// star-burst that reads as "Claude" at 16px.
export function ClaudeMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const fill = monochrome ? "currentColor" : BRAND.claudeCoral;
  // 8-pointed spark built from a single long-arm rhombus rotated 4× by 45°.
  // Long arms (N/E/S/W) at full length, short arms (NE/SE/SW/NW) at 65%
  // length with a thinner waist — same construction as the Anthropic
  // wordmark "A" glyph and the Claude in-product spark.
  const longArm = "M0 -10 L1.6 0 L0 10 L-1.6 0 Z";
  const shortArm = "M0 -6.5 L1 0 L0 6.5 L-1 0 Z";
  return (
    <SvgRoot size={size} className={className}>
      <g fill={fill} transform="translate(12 12)">
        {/* Cardinal long arms */}
        <path d={longArm} />
        <path d={longArm} transform="rotate(90)" />
        {/* Diagonal short arms */}
        <path d={shortArm} transform="rotate(45)" />
        <path d={shortArm} transform="rotate(135)" />
      </g>
    </SvgRoot>
  );
}

// ── Cursor — black tilted-pointer square ─────────────────────────────────
// The Cursor brand mark is a rounded-square badge with a stylized cursor /
// arrow inside. We render the badge in black with a white cursor glyph; in
// monochrome mode we invert (currentColor badge, transparent cutout look).
export function CursorMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  if (monochrome) {
    // Single-color: just the cursor pointer silhouette, no badge.
    return (
      <SvgRoot size={size} className={className}>
        <path d="M5 3 L19 11 L13 12.5 L11 19 Z" fill="currentColor" />
      </SvgRoot>
    );
  }
  return (
    <SvgRoot size={size} className={className}>
      <rect
        x="2"
        y="2"
        width="20"
        height="20"
        rx="4.5"
        fill={BRAND.cursorBlack}
      />
      <path
        d="M8 6.5 L17.5 12.2 L13 13.4 L11.5 17.5 Z"
        fill={BRAND.cursorWhite}
      />
    </SvgRoot>
  );
}

// ── Codex — OpenAI hexaflake / rosette ───────────────────────────────────
// Six-petal rosette built from three rotated ellipses — the canonical
// OpenAI mark. Stroke-only at small sizes keeps it legible; thicker stroke
// at 24px reads as the filled brand mark.
export function CodexMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.codexBlack;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="1.6"
        transform="translate(12 12)"
      >
        <ellipse cx="0" cy="0" rx="9.25" ry="3.75" />
        <ellipse cx="0" cy="0" rx="9.25" ry="3.75" transform="rotate(60)" />
        <ellipse cx="0" cy="0" rx="9.25" ry="3.75" transform="rotate(120)" />
      </g>
    </SvgRoot>
  );
}

// ── Gemini — multi-color 4-pointed spark ─────────────────────────────────
// Google Gemini's mark is a 4-pointed star with a horizontal multi-color
// gradient (blue → purple → pink → amber). Concave cubic curves between
// the four points give the canonical "pinched" star silhouette.
export function GeminiMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const gradId = useId();
  const fill = monochrome ? "currentColor" : `url(#${gradId})`;
  // 4-pointed star: tips at 12-o'clock, 3, 6, 9 with concave waists at 1.5,
  // 4.5, 7.5, 10.5 — same construction as the Google "AI sparkle" glyph.
  const path =
    "M12 1.5 C12 7 13.5 9 15.5 10.4 C17.5 11.5 22.5 12 22.5 12 " +
    "C22.5 12 17.5 12.5 15.5 13.6 C13.5 15 12 17 12 22.5 " +
    "C12 17 10.5 15 8.5 13.6 C6.5 12.5 1.5 12 1.5 12 " +
    "C1.5 12 6.5 11.5 8.5 10.4 C10.5 9 12 7 12 1.5 Z";
  return (
    <SvgRoot size={size} className={className}>
      {!monochrome ? (
        <defs>
          <linearGradient
            id={gradId}
            x1="0"
            y1="0"
            x2="24"
            y2="24"
            gradientUnits="userSpaceOnUse"
          >
            <stop offset="0%" stopColor={BRAND.geminiBlue} />
            <stop offset="40%" stopColor={BRAND.geminiPurple} />
            <stop offset="75%" stopColor={BRAND.geminiPink} />
            <stop offset="100%" stopColor={BRAND.geminiAmber} />
          </linearGradient>
        </defs>
      ) : null}
      <path d={path} fill={fill} />
    </SvgRoot>
  );
}

// ── Windsurf — wave + sail glyph ─────────────────────────────────────────
// Abstract wave silhouette with an upright sail line — reads as "Windsurf"
// without lifting the official artwork.
export function WindsurfMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.windsurfTeal;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="1.8"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Sail — triangular leading edge */}
        <path d="M12 3 L12 16" />
        <path d="M12 3 L7 14 L12 16" fill={stroke} fillOpacity="0.18" />
        {/* Wave swell beneath */}
        <path d="M3 18.5 C6 17 8 20 12 18.5 C16 17 18 20 21 18.5" />
      </g>
    </SvgRoot>
  );
}

// ── Copilot — coffee-cup ring glyph ──────────────────────────────────────
// GitHub Copilot's mark is the chat-bubble robot, but the simplest read at
// 18-22px is a hollow ring with two "antennae" suggesting the bot top.
export function CopilotMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.copilotInk;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="1.7"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Bot body — rounded rectangle */}
        <rect x="3.5" y="9" width="17" height="10" rx="4" />
        {/* Antennae */}
        <path d="M8 9 L8 5.5" />
        <path d="M16 9 L16 5.5" />
        {/* Eyes — short slits */}
        <path d="M9 13.5 L9 15.5" />
        <path d="M15 13.5 L15 15.5" />
      </g>
    </SvgRoot>
  );
}

// ── Cline — lightning bolt ───────────────────────────────────────────────
export function ClineMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const fill = monochrome ? "currentColor" : BRAND.clineYellow;
  return (
    <SvgRoot size={size} className={className}>
      <path d="M14 2 L5 13 L11 13 L10 22 L19 11 L13 11 Z" fill={fill} />
    </SvgRoot>
  );
}

// ── Aider — A monogram with horizontal crossbar ──────────────────────────
export function AiderMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.aiderSlate;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* A shape */}
        <path d="M4 21 L12 3 L20 21" />
        <path d="M7.5 14 L16.5 14" />
      </g>
    </SvgRoot>
  );
}

// ── Continue — right-pointing chevron ────────────────────────────────────
export function ContinueMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.continueViolet;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="2.2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        <path d="M6 4 L14 12 L6 20" />
        <path d="M13 4 L21 12 L13 20" opacity="0.55" />
      </g>
    </SvgRoot>
  );
}

// ── Opencode — curly braces ──────────────────────────────────────────────
export function OpencodeMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.opencodeOrange;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Left brace */}
        <path d="M9 3 C6 3 7 9 4 12 C7 15 6 21 9 21" />
        {/* Right brace */}
        <path d="M15 3 C18 3 17 9 20 12 C17 15 18 21 15 21" />
      </g>
    </SvgRoot>
  );
}

// ── Cody — Sourcegraph "C" monogram ──────────────────────────────────────
export function CodyMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const stroke = monochrome ? "currentColor" : BRAND.codyBlue;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={stroke}
        strokeWidth="2.3"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Open C — three-quarter arc */}
        <path d="M19 6.5 A 8 8 0 1 0 19 17.5" />
        {/* Notch / accent dot suggesting the bot eye */}
        <circle cx="17" cy="12" r="1.4" fill={stroke} stroke="none" />
      </g>
    </SvgRoot>
  );
}

// ── OpenInterpreter — terminal cursor / blinking caret ───────────────────
export function OpenInterpreterMark({
  size = 20,
  className,
  monochrome = false,
}: VendorMarkProps): JSX.Element {
  const color = monochrome ? "currentColor" : BRAND.oiSlate;
  return (
    <SvgRoot size={size} className={className}>
      <g
        fill="none"
        stroke={color}
        strokeWidth="2"
        strokeLinecap="round"
        strokeLinejoin="round"
      >
        {/* Chevron prompt > */}
        <path d="M4 7 L9 12 L4 17" />
        {/* Caret block to the right */}
        <rect
          x="12"
          y="15"
          width="8"
          height="2.5"
          fill={color}
          stroke="none"
          rx="0.4"
        />
      </g>
    </SvgRoot>
  );
}

// Note: a vendor-keyed lookup table (VENDOR_MARK) lives in the sibling
// file VendorLogos.lookup.ts. Keeping non-component exports out of this
// file preserves Vite's react-refresh boundary, which requires a file to
// only export components for HMR to work cleanly.

// Allow callers to render with foreignObject (HTML in SVG) without losing
// the inline-block alignment we set on the root. Re-exported as a type-only
// helper so the surrounding mono baseline stays intact.
export type VendorRootStyle = CSSProperties;
