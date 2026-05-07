import type { CSSProperties } from "react";
import BlurFadeIn from "./effects/BlurFadeIn";
import Marquee from "./effects/Marquee";
import {
  VENDOR_MARK,
  type VendorKey,
  type VendorMarkProps,
} from "./effects/VendorLogos.lookup";

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
  /** Vendor-keyed brand mark — rendered in monochrome to match wordmark color. */
  vendor: VendorKey;
  /** Tiny tag rendered to the right in muted color. */
  tag?: string;
};

function Wordmark({ name, vendor, tag }: WordmarkProps) {
  const Mark = VENDOR_MARK[vendor];
  const markProps: VendorMarkProps = { size: 18, monochrome: true };
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
      <span aria-hidden="true" className="opacity-80">
        <Mark {...markProps} />
      </span>
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

// ── Provider list ───────────────────────────────────────────────────────
// Each provider points at a vendor mark in VendorLogos.tsx — single source
// of truth for the brand artwork, rendered in monochrome here so the
// marquee stays calm against the cream background and varied multi-color
// marks don't compete for attention.
//
// Order chosen so the visual rhythm reads varied even before scroll: the
// duo of MCP-native (Claude Code, Cline, Continue) is interleaved with CLI
// (Codex, Gemini CLI, Aider, Opencode), IDE (Cursor, Windsurf, Copilot,
// Cody), and the runtime (OpenInterpreter).
const PROVIDERS: WordmarkProps[] = [
  { name: "Claude Code", vendor: "claude", tag: "MCP" },
  { name: "Cursor", vendor: "cursor", tag: "IDE" },
  { name: "Codex", vendor: "codex", tag: "CLI" },
  { name: "Gemini CLI", vendor: "gemini", tag: "CLI" },
  { name: "Windsurf", vendor: "windsurf", tag: "IDE" },
  { name: "Opencode", vendor: "opencode", tag: "CLI" },
  { name: "Cline", vendor: "cline", tag: "MCP" },
  { name: "Aider", vendor: "aider", tag: "CLI" },
  { name: "Continue", vendor: "continue", tag: "MCP" },
  { name: "OpenInterpreter", vendor: "openinterpreter", tag: "RUN" },
  { name: "GitHub Copilot", vendor: "copilot", tag: "IDE" },
  { name: "Cody", vendor: "cody", tag: "IDE" },
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
                  vendor={p.vendor}
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
