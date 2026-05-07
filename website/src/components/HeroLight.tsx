import { useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import HeroRoom from "./HeroRoom";
import BlurFadeIn from "./effects/BlurFadeIn";
import ShinyText from "./effects/ShinyText";

const INSTALL_CMD = "pip install quorus";

/**
 * HeroLight — cream split hero. Left: badge + headline + subhead + waitlist
 * + install command. Right: HeroRoom — a live browser-frame card showing a
 * Quorus room with four agents coordinating in real time.
 *
 * Choreography (May 2026 polish pass):
 *   The left column cascades in with BlurFadeIn at staggered delays
 *   (0, 0.1, 0.2, 0.3, 0.4) and the H1 carries a continuous ShinyText
 *   glint sweep so the hero reads as "designed", not "static". The right
 *   column wrapper enters at 0.5 to land just after the install row.
 *
 * Sizing:
 *   `min-h-[88vh]` makes the hero dominate the viewport now that the
 *   homepage compresses to four sections. Bottom-center scroll cue tells
 *   the eye there's more below the fold.
 */
export default function HeroLight() {
  return (
    <section
      aria-labelledby="hero-heading"
      className="relative w-full overflow-hidden min-h-[88vh]"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Single subtle radial — accent tint at the lower-left, not the
          AI-template centered halo. */}
      <div
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -left-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 30% 70%, rgba(13,77,74,0.05), transparent 60%)",
        }}
      />

      {/* Faint vertical column rule — editorial accent */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-[8%] hidden w-px lg:block"
        style={{ backgroundColor: "var(--color-border-light)" }}
      />

      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-12 px-6 pb-32 pt-36 lg:grid-cols-12 lg:gap-10 lg:pt-44">
        {/* Left column — copy + CTA. Cascading BlurFadeIn choreography. */}
        <div className="lg:col-span-6">
          <BlurFadeIn delay={0} inView={false}>
            <span
              className="inline-flex items-center gap-2 rounded-full border px-3 py-1"
              style={{ borderColor: "var(--color-border-light-strong)" }}
            >
              <span
                className="block h-1.5 w-1.5 rounded-full"
                style={{ backgroundColor: "var(--color-accent)" }}
              />
              <span
                className="font-mono text-[11px] tracking-wider"
                style={{ color: "var(--color-text-on-cream-secondary)" }}
              >
                OPEN BETA · v0.4 · MIT
              </span>
            </span>
          </BlurFadeIn>

          <BlurFadeIn delay={0.1} inView={false}>
            <ShinyText
              as="h1"
              tone="dark"
              durationSeconds={4}
              className="mt-7 block"
            >
              <span
                id="hero-heading"
                style={{
                  fontWeight: 600,
                  letterSpacing: "-0.022em",
                  lineHeight: 0.98,
                  fontSize: "clamp(44px, 6vw, 76px)",
                  display: "block",
                }}
              >
                Coordination Layer
                <br />
                for Agent Teams
              </span>
            </ShinyText>
          </BlurFadeIn>

          <BlurFadeIn delay={0.2} inView={false}>
            <p
              className="mt-6 max-w-xl text-[18px] leading-[1.55]"
              style={{ color: "var(--color-text-on-cream-secondary)" }}
            >
              Quorus gives your AI swarms rooms, shared state, and real-time
              coordination.{" "}
              <span style={{ color: "var(--color-text-on-cream)" }}>
                Any model. Any machine.
              </span>
            </p>
          </BlurFadeIn>

          <BlurFadeIn delay={0.3} inView={false}>
            <form
              onSubmit={(e) => e.preventDefault()}
              className="mt-9 flex w-full max-w-md flex-col gap-2 sm:flex-row"
              aria-label="Join the waitlist"
            >
              <input
                type="email"
                placeholder="you@company.com"
                className="h-11 flex-1 rounded-md border bg-white/60 px-4 text-[14px] outline-none transition-colors placeholder:text-slate-400/80 focus:border-[var(--color-accent)] focus:bg-white"
                style={{
                  borderColor: "var(--color-border-light-strong)",
                  color: "var(--color-text-on-cream)",
                }}
              />
              <button
                type="submit"
                className="h-11 rounded-md px-5 text-[13px] font-medium tracking-tight transition-transform duration-200 hover:-translate-y-px"
                style={{
                  backgroundColor: "var(--color-ink)",
                  color: "var(--color-cream)",
                }}
              >
                Join waitlist
              </button>
            </form>
          </BlurFadeIn>

          <BlurFadeIn delay={0.4} inView={false}>
            <div className="mt-5 max-w-md">
              <InstallCommand />
              <p
                className="mt-2 font-mono text-[11px]"
                style={{ color: "var(--color-text-on-cream-muted)" }}
              >
                Or `quorus init` after install. Python 3.10+. MIT.
              </p>
            </div>
          </BlurFadeIn>
        </div>

        {/* Right column — live Quorus room mock. Browser-frame card with a
            cycling message stream, participants, lock events, and rev tick.
            Wrapped in a delayed BlurFadeIn so it lands just after the
            install row, completing the cascade. */}
        <div className="lg:col-span-6">
          <BlurFadeIn delay={0.5} inView={false} duration={0.7}>
            <HeroRoom />
          </BlurFadeIn>
        </div>
      </div>

      {/* Scroll cue — anchors the eye to the section's bottom edge and
          tells the reader there's more below the fold. */}
      <ScrollCue />
    </section>
  );
}

/* ── Scroll cue ──────────────────────────────────────────────────────────── */

function ScrollCue() {
  const prefersReduced = useReducedMotion();

  return (
    <div
      aria-hidden
      className="pointer-events-none absolute inset-x-0 bottom-6 flex flex-col items-center gap-1.5"
    >
      <span
        className="font-mono text-[10px] tracking-[0.18em]"
        style={{ color: "var(--color-text-on-cream-muted)" }}
      >
        SCROLL
      </span>
      <motion.svg
        width="14"
        height="14"
        viewBox="0 0 14 14"
        fill="none"
        stroke="currentColor"
        strokeWidth="1.5"
        strokeLinecap="round"
        strokeLinejoin="round"
        style={{ color: "var(--color-text-on-cream-muted)" }}
        animate={prefersReduced ? undefined : { y: [0, 4, 0] }}
        transition={
          prefersReduced
            ? undefined
            : { duration: 1.6, repeat: Infinity, ease: "easeInOut" }
        }
      >
        <path d="M3 5l4 4 4-4" />
      </motion.svg>
    </div>
  );
}

/* ── Install command ─────────────────────────────────────────────────────── */

function InstallCommand() {
  const [copied, setCopied] = useState(false);

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard may be blocked in some contexts — silently no-op.
    }
  };

  return (
    <div
      className="group flex items-center gap-3 rounded-md border px-4 py-3 transition-colors"
      style={{
        backgroundColor: "var(--color-ink-2)",
        borderColor: "var(--color-border-dark)",
      }}
    >
      <span
        className="font-mono text-[12px]"
        style={{ color: "var(--color-accent-on-ink)" }}
      >
        $
      </span>
      <code
        className="flex-1 overflow-x-auto whitespace-nowrap font-mono text-[12px]"
        style={{ color: "var(--color-text-on-ink)" }}
      >
        {INSTALL_CMD}
      </code>
      <button
        type="button"
        onClick={onCopy}
        aria-label={copied ? "Copied" : "Copy install command"}
        className="rounded px-2 py-1 font-mono text-[11px] transition-colors"
        style={{
          color: copied
            ? "var(--color-accent-on-ink)"
            : "var(--color-text-on-ink-secondary)",
        }}
      >
        {copied ? "copied" : "copy"}
      </button>
    </div>
  );
}
