import { useEffect, useRef, useState } from "react";
import { motion, useReducedMotion } from "framer-motion";
import HeroRoom from "./HeroRoom";

const INSTALL_CMD = "pip install quorus";

/**
 * HeroLight — cream split hero. Left: badge + headline + subhead + waitlist
 * + install command. Right: HeroRoom — a live browser-frame card showing a
 * Quorus room with four agents coordinating in real time.
 *
 * No disappearing animations. Every element is rendered visible by default.
 * Motion is layered ON TOP of always-visible content (BorderBeam, the
 * cycling message stream, the cursor follower, the type-in install) — never
 * hiding content until a scroll trigger fires.
 *
 * Sizing:
 *   `min-h-[88vh]` makes the hero dominate the viewport now that the
 *   homepage compresses to four sections. Bottom-center scroll cue tells
 *   the eye there's more below the fold.
 */
export default function HeroLight() {
  const sectionRef = useRef<HTMLElement | null>(null);
  const haloRef = useRef<HTMLDivElement | null>(null);
  const prefersReduced = useReducedMotion();

  // Pointer-driven halo parallax. We avoid framer-motion here so that the
  // halo's existing CSS positioning isn't overridden — instead we mutate
  // CSS variables on the section and let a transform in the halo's style
  // interpolate via CSS transition. Throttled via rAF; only one frame
  // queued at a time. Honors reduced motion (effect short-circuits).
  useEffect(() => {
    if (prefersReduced) return;
    const section = sectionRef.current;
    const halo = haloRef.current;
    if (!section || !halo) return;

    let rafId = 0;
    let queuedX = 0;
    let queuedY = 0;

    const apply = () => {
      rafId = 0;
      // Max travel: 8px in either direction.
      const tx = (queuedX - 0.5) * 16;
      const ty = (queuedY - 0.5) * 16;
      halo.style.transform = `translate3d(${tx}px, ${ty}px, 0)`;
    };

    const onMove = (e: PointerEvent) => {
      const rect = section.getBoundingClientRect();
      queuedX = (e.clientX - rect.left) / rect.width;
      queuedY = (e.clientY - rect.top) / rect.height;
      if (!rafId) rafId = requestAnimationFrame(apply);
    };

    const onLeave = () => {
      // Drift home smoothly via the CSS transition.
      halo.style.transform = "translate3d(0px, 0px, 0)";
    };

    section.addEventListener("pointermove", onMove, { passive: true });
    section.addEventListener("pointerleave", onLeave);

    return () => {
      section.removeEventListener("pointermove", onMove);
      section.removeEventListener("pointerleave", onLeave);
      if (rafId) cancelAnimationFrame(rafId);
    };
  }, [prefersReduced]);

  return (
    <section
      ref={sectionRef}
      aria-labelledby="hero-heading"
      className="relative w-full overflow-hidden min-h-[88vh]"
      style={{ backgroundColor: "var(--color-cream)" }}
    >
      {/* Subtle radial — accent tint at the lower-left, not the
          AI-template centered halo. Translated by pointer-driven
          parallax (see effect above); CSS transition smooths the drift. */}
      <div
        ref={haloRef}
        aria-hidden
        className="pointer-events-none absolute -bottom-32 -left-32 h-[480px] w-[480px] rounded-full"
        style={{
          background:
            "radial-gradient(circle at 30% 70%, rgba(13,77,74,0.05), transparent 60%)",
          transition: "transform 600ms cubic-bezier(0.22, 1, 0.36, 1)",
          willChange: "transform",
        }}
      />

      {/* Faint vertical column rule — editorial accent */}
      <div
        aria-hidden
        className="pointer-events-none absolute inset-y-0 right-[8%] hidden w-px lg:block"
        style={{ backgroundColor: "var(--color-border-light)" }}
      />

      <div className="relative mx-auto grid max-w-7xl grid-cols-1 items-center gap-12 px-6 pb-32 pt-36 lg:grid-cols-12 lg:gap-10 lg:pt-44">
        {/* Left column — copy + CTA. Always rendered. No fade-in choreography. */}
        <div className="lg:col-span-6">
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

          <h1
            id="hero-heading"
            className="mt-7"
            style={{
              color: "var(--color-text-on-cream)",
              fontWeight: 600,
              letterSpacing: "-0.022em",
              lineHeight: 0.98,
              fontSize: "clamp(44px, 6vw, 76px)",
            }}
          >
            Coordination Layer
            <br />
            for Agent Teams
          </h1>

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

          <div className="mt-5 max-w-md">
            <InstallCommand />
            <p
              className="mt-2 font-mono text-[11px]"
              style={{ color: "var(--color-text-on-cream-muted)" }}
            >
              Or `quorus init` after install. Python 3.10+. MIT.
            </p>
          </div>
        </div>

        {/* Right column — live Quorus room mock. Always rendered. */}
        <div className="lg:col-span-6">
          <HeroRoom />
        </div>
      </div>

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
      className="pointer-events-none absolute bottom-6 left-1/2 -translate-x-1/2 flex flex-col items-center gap-1.5"
    >
      <span
        className="font-mono text-[10px] tracking-[0.22em] uppercase"
        style={{ color: "var(--color-text-on-cream-muted)" }}
      >
        Scroll
      </span>
      <motion.svg
        width="12"
        height="12"
        viewBox="0 0 12 12"
        fill="none"
        animate={prefersReduced ? undefined : { y: [0, 4, 0] }}
        transition={{ duration: 1.6, repeat: Infinity, ease: "easeInOut" }}
      >
        <path
          d="M3 5 L6 8 L9 5"
          stroke="currentColor"
          strokeOpacity="0.55"
          strokeWidth="1.4"
          strokeLinecap="round"
          strokeLinejoin="round"
          style={{ color: "var(--color-text-on-cream-muted)" }}
        />
      </motion.svg>
    </div>
  );
}

/* ── Install command ─────────────────────────────────────────────────────── */

function sleep(ms: number) {
  return new Promise<void>((resolve) => setTimeout(resolve, ms));
}

/**
 * Type-in install command.
 *
 * Critical contract: the FULL command is rendered immediately on first
 * paint (initial state = INSTALL_CMD). If JavaScript fails or the effect
 * never runs, the user still sees `pip install quorus` clearly. The
 * typewriter loop only kicks in *after* mount, briefly showing the full
 * command before erasing and retyping on a continuous loop.
 *
 * Reduced motion: bail out of the loop entirely; the static command stays.
 */
function InstallCommand() {
  const [displayed, setDisplayed] = useState(INSTALL_CMD);
  const [typing, setTyping] = useState(false);
  const [copied, setCopied] = useState(false);
  const prefersReduced = useReducedMotion();

  const onCopy = async () => {
    try {
      await navigator.clipboard.writeText(INSTALL_CMD);
      setCopied(true);
      setTimeout(() => setCopied(false), 1800);
    } catch {
      // Clipboard may be blocked in some contexts — silently no-op.
    }
  };

  useEffect(() => {
    if (prefersReduced) return;

    let mounted = true;

    const run = async () => {
      // Hold the full command for a beat so the user reads it before
      // we begin the loop.
      await sleep(900);
      if (!mounted) return;

      while (mounted) {
        // Erase
        setTyping(true);
        for (let i = INSTALL_CMD.length; i >= 0; i--) {
          if (!mounted) return;
          setDisplayed(INSTALL_CMD.slice(0, i));
          await sleep(35);
        }
        // Type
        for (let i = 0; i <= INSTALL_CMD.length; i++) {
          if (!mounted) return;
          setDisplayed(INSTALL_CMD.slice(0, i));
          await sleep(60);
        }
        setTyping(false);
        // Idle dwell — let the user actually read it.
        await sleep(4000);
      }
    };

    void run();

    return () => {
      mounted = false;
    };
  }, [prefersReduced]);

  return (
    <div
      data-magnetic
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
        {displayed}
        <Caret blinking={!typing && !prefersReduced} />
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

/**
 * Caret — a 1ch-wide bar that blinks at 1Hz when idle, holds steady while
 * typing/erasing (so each character feels punched in cleanly). Never
 * occupies layout space that would shift surrounding text.
 */
function Caret({ blinking }: { blinking: boolean }) {
  return (
    <span
      aria-hidden
      className={`ml-[1px] inline-block align-[-1px] ${blinking ? "cursor-blink" : ""}`}
      style={{
        width: "0.55ch",
        height: "1em",
        backgroundColor: "var(--color-accent-on-ink)",
        opacity: blinking ? undefined : 0.95,
        transform: "translateY(2px)",
      }}
    />
  );
}
