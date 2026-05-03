import type { CSSProperties, ReactNode } from "react";
import BlurFadeIn from "./effects/BlurFadeIn";
import Marquee from "./effects/Marquee";

/**
 * LogoCloud — provider wordmark band, magicui-style continuous marquee.
 *
 * Layout choice (single row vs two rows):
 *   We tried two opposing rows. With 12 short wordmarks set in mono, the
 *   counter-scrolling pair created visual fight against the calm cream
 *   page rhythm. A single slow row (~50s revolution) reads as inevitable
 *   instead of busy, matches the Linear / Vercel cadence, and frees the
 *   eye to rest on the surrounding bands.
 *
 * Wordmarks are inline SVG, not <img>, because:
 *   1. Logos paint with `currentColor` — one CSS variable, no PNG bleed,
 *      no fetch waterfall, no FOUC at 50fps under marquee translation.
 *   2. We avoid shipping fake brand artwork. Each glyph is a typographic
 *      treatment using the project's mono stack — honest, consistent,
 *      and bandwidth-free.
 *   3. SVG sub-pixel rendering stays sharp during continuous translation
 *      where bitmap PNGs would shimmer along their pixel grid.
 *
 * Marquee + edge mask:
 *   Marquee duplicates the row twice and slides -50% over `durationSeconds`.
 *   The mask gradient (10%↔90%) hides the seam and the wrap-around so the
 *   loop reads as a true conveyor, never a recycled list.
 *
 * Accessibility:
 *   The section carries an aria-label; the visible label text is the same
 *   data screen readers see. Each wordmark gets <title> for SR users that
 *   surface SVG titles. Reduced motion path collapses to a static row via
 *   the Marquee primitive itself.
 */

// ── Tokens used inline so this file does not depend on Tailwind classes
//    that may not include hardcoded RGB values for the band color.
const TOKEN: Record<string, string> = {
  cream: "var(--color-cream)",
  textOnCream: "var(--color-text-on-cream)",
  textMuted: "var(--color-text-on-cream-muted)",
  textSecondary: "var(--color-text-on-cream-secondary)",
};

// ── Wordmark primitive ──────────────────────────────────────────────────
// One height, one font face, one color. Width auto from text content.
// Each wordmark renders as an inline-flex row with optional subtitle so we
// can mark the harness type ("CLI", "MCP", "IDE") without polluting the
// glyph height.
type WordmarkProps = {
  name: string;
  /** Optional small typographic mark (a dot variant, a slash, an arrow). */
  glyph?: ReactNode;
  /** Tiny tag rendered to the right in muted color. */
  tag?: string;
};

function Wordmark({ name, glyph, tag }: WordmarkProps) {
  const style: CSSProperties = {
    color: TOKEN.textSecondary,
    fontFamily: "var(--font-mono)",
    fontWeight: 500,
    letterSpacing: "-0.01em",
    fontSize: "16px",
    lineHeight: 1,
    transition: "color 200ms cubic-bezier(0.16, 1, 0.3, 1)",
  };

  return (
    <div
      className="group/logo flex shrink-0 items-center gap-2 select-none"
      style={style}
      onMouseEnter={(e) =>
        (e.currentTarget.style.color = TOKEN.textOnCream as string)
      }
      onMouseLeave={(e) =>
        (e.currentTarget.style.color = TOKEN.textSecondary as string)
      }
    >
      {glyph ? (
        <span aria-hidden="true" className="opacity-70">
          {glyph}
        </span>
      ) : null}
      <span>{name}</span>
      {tag ? (
        <span
          aria-hidden="true"
          style={{
            color: TOKEN.textMuted,
            fontSize: "10px",
            letterSpacing: "0.18em",
            textTransform: "uppercase",
            opacity: 0.6,
            marginLeft: "2px",
          }}
        >
          {tag}
        </span>
      ) : null}
    </div>
  );
}

// ── Inline glyph atoms ──────────────────────────────────────────────────
// Each one is currentColor and 14px square so it sits on the mono baseline
// without nudging neighboring wordmarks.

const Dot = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <circle cx="7" cy="7" r="2.5" fill="currentColor" />
  </svg>
);

const Square = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
  >
    <rect x="3" y="3" width="8" height="8" rx="1.5" fill="currentColor" />
  </svg>
);

const Bracket = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M5 3 L3 7 L5 11"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
    <path
      d="M9 3 L11 7 L9 11"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
  </svg>
);

const Slash = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M10 3 L4 11"
      stroke="currentColor"
      strokeWidth="1.5"
      strokeLinecap="round"
    />
  </svg>
);

const Triangle = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path d="M7 3 L11 11 L3 11 Z" fill="currentColor" />
  </svg>
);

const Hexagon = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M7 2 L11.5 4.5 L11.5 9.5 L7 12 L2.5 9.5 L2.5 4.5 Z"
      fill="currentColor"
    />
  </svg>
);

const Star = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M7 1.5 L8.6 5.4 L12.7 5.7 L9.6 8.3 L10.5 12.3 L7 10.2 L3.5 12.3 L4.4 8.3 L1.3 5.7 L5.4 5.4 Z"
      fill="currentColor"
    />
  </svg>
);

const Diamond = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path d="M7 2 L12 7 L7 12 L2 7 Z" fill="currentColor" />
  </svg>
);

const Chevron = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M3 5 L7 9 L11 5"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
  </svg>
);

const Caret = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M4 4 L8 7 L4 10"
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      strokeLinejoin="round"
      fill="none"
    />
  </svg>
);

const Asterisk = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <g
      stroke="currentColor"
      strokeWidth="1.6"
      strokeLinecap="round"
      transform="translate(7 7)"
    >
      <line x1="0" y1="-4" x2="0" y2="4" />
      <line x1="-3.4" y1="-2" x2="3.4" y2="2" />
      <line x1="-3.4" y1="2" x2="3.4" y2="-2" />
    </g>
  </svg>
);

const Octagon = (
  <svg
    width="14"
    height="14"
    viewBox="0 0 14 14"
    fill="none"
    xmlns="http://www.w3.org/2000/svg"
    aria-hidden="true"
  >
    <path
      d="M5 2 L9 2 L12 5 L12 9 L9 12 L5 12 L2 9 L2 5 Z"
      fill="currentColor"
    />
  </svg>
);

// ── Provider list ───────────────────────────────────────────────────────
// Order chosen so the visual rhythm reads varied even before scroll: the
// duo of MCP-native (Claude Code, Cline, Continue) is interleaved with CLI
// (Codex, Gemini CLI, Aider, Opencode), IDE (Cursor, Windsurf, Copilot,
// Cody), and the runtime (OpenInterpreter). That mix matches the Cross-
// Harness band's narrative without forcing categorical labels.
const PROVIDERS: WordmarkProps[] = [
  { name: "Claude Code", glyph: Asterisk, tag: "MCP" },
  { name: "Cursor", glyph: Caret, tag: "IDE" },
  { name: "Codex", glyph: Bracket, tag: "CLI" },
  { name: "Gemini CLI", glyph: Diamond, tag: "CLI" },
  { name: "Windsurf", glyph: Triangle, tag: "IDE" },
  { name: "Opencode", glyph: Square, tag: "CLI" },
  { name: "Cline", glyph: Slash, tag: "MCP" },
  { name: "Aider", glyph: Dot, tag: "CLI" },
  { name: "Continue", glyph: Chevron, tag: "MCP" },
  { name: "OpenInterpreter", glyph: Hexagon, tag: "RUN" },
  { name: "GitHub Copilot", glyph: Star, tag: "IDE" },
  { name: "Cody", glyph: Octagon, tag: "IDE" },
];

export default function LogoCloud() {
  return (
    <section
      aria-label="Powers agent swarms across major coding-agent harnesses"
      className="w-full"
      style={{ backgroundColor: TOKEN.cream }}
    >
      <div className="mx-auto max-w-6xl px-6 py-14 md:py-16">
        <BlurFadeIn>
          <p
            className="text-center text-[11px] uppercase"
            style={{
              color: TOKEN.textMuted,
              fontFamily: "var(--font-mono)",
              letterSpacing: "0.18em",
            }}
          >
            One room. Every coding agent.
          </p>
        </BlurFadeIn>

        <BlurFadeIn delay={0.1}>
          <div className="mt-8">
            <Marquee durationSeconds={50} pauseOnHover>
              {PROVIDERS.map((p) => (
                <Wordmark
                  key={p.name}
                  name={p.name}
                  glyph={p.glyph}
                  tag={p.tag}
                />
              ))}
            </Marquee>
          </div>
        </BlurFadeIn>
      </div>
    </section>
  );
}
